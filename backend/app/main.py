"""FastAPI application for TunaTale language learning."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.audio.assembler import AudioAssembler  # noqa: E402
from app.audio.edge_tts import EdgeTTSService  # noqa: E402
from app.audio.pause_calculator import NaturalPauseCalculator  # noqa: E402
from app.audio.preprocessing.slovene import SlovenePreprocessor  # noqa: E402
from app.audio.renderer import LessonRenderer  # noqa: E402
from app.config import settings  # noqa: E402
from app.generation.curriculum import CurriculumGenerator  # noqa: E402
from app.generation.story import StoryGenerator  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402
from app.models.language import Language  # noqa: E402
from app.srs.database import SRSDatabase  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    llm = LLMClient(groq_api_key=settings.groq_api_key, groq_model=settings.llm_model)

    db_path = settings.database_url.removeprefix("sqlite:///")
    srs_db = SRSDatabase(db_path)
    content_store = ContentStore(db_path)

    language = Language.slovene()

    app.state.srs_db = srs_db
    app.state.content_store = content_store
    app.state.language = language
    app.state.curriculum_generator = CurriculumGenerator(llm)
    app.state.story_generator = StoryGenerator(llm, srs_db)
    app.state.renderer = LessonRenderer(
        tts=EdgeTTSService(),
        preprocessor=SlovenePreprocessor(),
        pause_calculator=NaturalPauseCalculator(),
        assembler=AudioAssembler(),
    )
    app.state.audio_dir = Path("output/audio")

    logger.info("TunaTale backend starting up")
    yield

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

from app.api import audio, curriculum, generation, srs  # noqa: E402

app.include_router(curriculum.router)
app.include_router(generation.router)
app.include_router(srs.router)
app.include_router(audio.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
