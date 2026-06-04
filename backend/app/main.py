"""FastAPI application for TunaTale language learning."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.audio.edge_tts import EdgeTTSService  # noqa: E402
from app.audio.pause_calculator import NaturalPauseCalculator  # noqa: E402
from app.audio.preprocessing.slovene import SlovenePreprocessor  # noqa: E402
from app.audio.renderer import LessonRenderer  # noqa: E402
from app.config import settings  # noqa: E402
from app.generation.curriculum import CurriculumGenerator  # noqa: E402
from app.generation.story import StoryGenerator  # noqa: E402
from app.llm.cassette import CassetteLLMClient  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402
from app.models.language import Language  # noqa: E402
from app.srs.database import SRSDatabase  # noqa: E402
from app.srs.lemmatizer import get_lemmatizer  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402

logging.basicConfig(level=logging.INFO)
logging.getLogger("app.audio.renderer").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


async def _warm_lemmatizer() -> None:
    """Load the lemmatizer pipeline off the request path.

    For ``lemmatizer_type=classla`` this loads the PyTorch/classla pipeline (~15s).
    Run as a background task during ``lifespan`` so uvicorn binds the port immediately
    instead of refusing every ``/api/*`` request until the model finishes loading. A
    no-op-cost call for the default lowercase lemmatizer. Swallows its own errors so a
    missing model degrades to on-demand loading rather than aborting startup.
    """
    try:
        await anyio.to_thread.run_sync(get_lemmatizer().lemmatize, "hotel", "sl")
    except Exception:
        logger.warning("Lemmatizer warm-up failed — continuing with on-demand loading")


@asynccontextmanager
async def lifespan(app: FastAPI):
    real_client = LLMClient(groq_api_key=settings.groq_api_key, groq_model=settings.llm_model)
    cassette_path = Path("tests/cassettes/e2e.json")

    # Wrap with cassettes unless explicitly in live mode
    if settings.llm_mode != "live":
        llm = CassetteLLMClient(mode=settings.llm_mode, cassette_path=cassette_path, real_client=real_client)
    else:
        llm = real_client

    db_path = settings.database_url.removeprefix("sqlite:///")
    srs_db = SRSDatabase(db_path)
    content_store = ContentStore(db_path)

    language = Language.slovene()

    app.state.srs_db = srs_db
    app.state.content_store = content_store
    app.state.language = language
    app.state.llm = llm
    app.state.curriculum_generator = CurriculumGenerator(llm)
    app.state.story_generator = StoryGenerator(llm)
    app.state.renderer = LessonRenderer(
        tts=EdgeTTSService(),
        preprocessor=SlovenePreprocessor(),
        pause_calculator=NaturalPauseCalculator(),
    )
    app.state.audio_dir = Path("output/audio")

    # Warm the lemmatizer in the background so the first /listen or /transcript request
    # doesn't pay the model-load cost. Critically, this must NOT be awaited before the
    # yield: awaiting the classla pipeline load (~15s) here blocks uvicorn's startup
    # event, so the port never binds and every frontend /api/* request is refused until
    # "Done loading processors!". As a background task, the port binds immediately and
    # classla still warms eagerly. A no-op for the default lowercase lemmatizer.
    warmup_task = asyncio.create_task(_warm_lemmatizer())

    logger.info("TunaTale backend starting up")
    yield

    # Let the warm-up settle on shutdown. _warm_lemmatizer swallows its own exceptions,
    # so this never raises; by normal shutdown the pipeline is long since loaded.
    await warmup_task
    srs_db.close()
    content_store.close()
    logger.info("TunaTale backend shutting down")


app = FastAPI(title="TunaTale", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api import admin, anki, audio, curriculum, generation, srs  # noqa: E402

app.include_router(curriculum.router)
app.include_router(generation.router)
app.include_router(srs.router)
app.include_router(audio.router)
app.include_router(anki.router)
app.include_router(admin.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
