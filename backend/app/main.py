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
from app.models.lesson import SectionType  # noqa: E402
from app.srs.database import SRSDatabase  # noqa: E402
from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, model_version_for  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402

logging.basicConfig(level=logging.INFO)
logging.getLogger("app.audio.renderer").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


async def _warm_lemmatizer(srs_db: SRSDatabase, content_store: ContentStore) -> None:
    """Fill the persistent sentence-analysis cache from stored lessons.

    Iterates every stored lesson's natural-speed L2 sentences through
    ``analyze_sentence_cached``:
    - **First run ever:** loads classla once, fills the persistent cache.
    - **Every subsequent restart:** all cache hits → the model is never loaded at
      startup → admin list and everything else are instant.

    Runs as a background ``asyncio.create_task`` so uvicorn binds the port immediately.
    Swallows its own errors so a missing model degrades to on-demand loading.
    """
    try:
        lemmatizer = get_lemmatizer()
        model_version = model_version_for(lemmatizer)
        if not model_version:
            return  # cheap lemmatizer; nothing to warm
        lessons = content_store.list_lessons()
        await anyio.to_thread.run_sync(_warm_from_lessons, lessons, srs_db, lemmatizer, model_version)
    except Exception:
        logger.warning("Lemmatizer warm-up failed — continuing with on-demand loading")


def _warm_from_lessons(
    lessons: list[tuple[str, str, int, object]],
    srs_db: SRSDatabase,
    lemmatizer: object,
    model_version: str,
) -> None:
    """Synchronous helper: run every stored L2 sentence through the analysis cache."""
    for _lesson_id, _curriculum_id, _day, lesson in lessons:
        natural_speed = next(
            (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
            None,
        )
        if natural_speed is None:
            continue
        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue
            analyze_sentence_cached(srs_db, lemmatizer, phrase.text, lesson.language_code, model_version)


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
        delivery_codec=settings.audio_delivery_codec,
        delivery_bitrate=settings.audio_delivery_bitrate,
    )
    app.state.audio_dir = Path("output/audio")

    # Warm the lemmatizer in the background so the first /listen or /transcript request
    # doesn't pay the model-load cost. Critically, this must NOT be awaited before the
    # yield: awaiting the classla pipeline load (~15s) here blocks uvicorn's startup
    # event, so the port never binds and every frontend /api/* request is refused until
    # "Done loading processors!". As a background task, the port binds immediately and
    # classla still warms eagerly. A no-op for the default lowercase lemmatizer.
    warmup_task = asyncio.create_task(_warm_lemmatizer(srs_db, content_store))

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
