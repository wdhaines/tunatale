"""FastAPI application for TunaTale language learning."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.audio.edge_tts import EdgeTTSService  # noqa: E402
from app.audio.pause_calculator import NaturalPauseCalculator  # noqa: E402
from app.audio.renderer import LessonRenderer  # noqa: E402
from app.config import settings  # noqa: E402
from app.generation.pipeline import LessonPipeline  # noqa: E402
from app.generation.planner import CurriculumPlanner  # noqa: E402
from app.generation.story import StoryGenerator  # noqa: E402
from app.languages import get_language, get_preprocessor  # noqa: E402
from app.llm.activity import ActivityLog  # noqa: E402
from app.llm.cassette import CassetteLLMClient  # noqa: E402
from app.llm.client import LLMClient, reasoning_params_for_model  # noqa: E402
from app.llm.usage_ledger import UsageLedger  # noqa: E402
from app.models.lesson import SectionType  # noqa: E402
from app.srs.database import SRSDatabase  # noqa: E402
from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, model_version_for  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402

logging.basicConfig(level=logging.INFO)
logging.getLogger("app.audio.renderer").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


async def _warm_lemmatizer(srs_dbs: dict[str, SRSDatabase], content_stores: dict[str, ContentStore]) -> None:
    """Fill the persistent sentence-analysis cache from stored lessons.

    Iterates every stored lesson's natural-speed L2 sentences through
    ``analyze_sentence_cached``:
    - **First run ever:** loads classla once, fills the persistent cache.
    - **Every subsequent restart:** all cache hits → the model is never loaded at
      startup → admin list and everything else are instant.

    Warms **each configured language** with its own lemmatizer (multi-language mode
    runs both in one process), so the Slovene classla cache and the Norwegian stanza
    cache both fill. Runs as a background ``asyncio.create_task`` so uvicorn binds the
    port immediately. Swallows per-language errors so a missing model for one language
    degrades to on-demand loading without aborting the others.
    """
    for code, srs_db in srs_dbs.items():
        try:
            lemmatizer = get_lemmatizer(code)
            model_version = model_version_for(lemmatizer)
            if not model_version:
                continue  # cheap lemmatizer; nothing to warm
            lessons = content_stores[code].list_lessons()
            await anyio.to_thread.run_sync(_warm_from_lessons, lessons, srs_db, lemmatizer, model_version)
        except Exception:
            logger.warning("Lemmatizer warm-up failed for %s — continuing with on-demand loading", code)


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


def _language_db_map() -> dict[str, str]:
    """Map of language code → SQLite URL to open at startup.

    Multi-language when ``settings.database_urls`` is set (one connection per
    entry, resolved per request from the X-TT-Language header); otherwise the
    single ``database_url`` bound to ``target_language`` (single-language).
    """
    if settings.database_urls:
        return dict(settings.database_urls)
    return {settings.target_language: settings.database_url}


@asynccontextmanager
async def lifespan(app: FastAPI):
    activity_log = ActivityLog()
    real_client = LLMClient(
        groq_api_key=settings.groq_api_key,
        groq_model=settings.llm_model,
        groq_extra_body_params=reasoning_params_for_model(settings.llm_model),
        usage_ledger=UsageLedger(settings.llm_usage_ledger_path),
        on_call=activity_log.record_llm_call,
    )
    _BACKEND_DIR = Path(__file__).parent.parent
    cassette_path = _BACKEND_DIR / "tests/cassettes/e2e.json"

    # Wrap with cassettes unless explicitly in live mode
    if settings.llm_mode != "live":
        llm = CassetteLLMClient(mode=settings.llm_mode, cassette_path=cassette_path, real_client=real_client)
    else:
        llm = real_client

    # One connection set per configured language. The per-request middleware picks
    # the active one; the parity-sensitive queue/badge queries stay unmodified —
    # isolation is which connection serves the request, not a WHERE clause.
    db_map = _language_db_map()
    default_code = settings.target_language if settings.target_language in db_map else next(iter(db_map))

    srs_dbs: dict[str, SRSDatabase] = {}
    content_stores: dict[str, ContentStore] = {}
    languages = {}
    for code, url in db_map.items():
        path = url.removeprefix("sqlite:///")
        srs_dbs[code] = SRSDatabase(path)
        content_stores[code] = ContentStore(path)
        languages[code] = get_language(code)

    app.state.srs_dbs = srs_dbs
    app.state.content_stores = content_stores
    app.state.languages = languages
    # Singular defaults (the active language): the middleware's single-language
    # fallback, the lemmatizer warm-up, and any non-request-scoped consumer.
    app.state.srs_db = srs_dbs[default_code]
    app.state.content_store = content_stores[default_code]
    app.state.language = languages[default_code]

    app.state.activity_log = activity_log
    app.state.llm = llm
    app.state.curriculum_planner = CurriculumPlanner(llm)
    app.state.story_generator = StoryGenerator(llm)
    preprocessors = {code: get_preprocessor(code) for code in db_map}
    app.state.renderer = LessonRenderer(
        tts=EdgeTTSService(),
        preprocessors=preprocessors,
        pause_calculator=NaturalPauseCalculator(),
        delivery_codec=settings.audio_delivery_codec,
        delivery_bitrate=settings.audio_delivery_bitrate,
    )
    app.state.audio_dir = _BACKEND_DIR / "output/audio"

    pipeline = LessonPipeline(
        story_generator=app.state.story_generator,
        renderer=app.state.renderer,
        audio_dir=app.state.audio_dir,
        content_stores=content_stores,
        languages=languages,
        srs_dbs=srs_dbs,
        activity_log=activity_log,
        llm_client=real_client,
    )
    app.state.pipeline = pipeline
    if settings.pipeline_autostart:
        pipeline.start()

    # Warm the lemmatizer in the background so the first /listen or /transcript request
    # doesn't pay the model-load cost. Critically, this must NOT be awaited before the
    # yield: awaiting the classla pipeline load (~15s) here blocks uvicorn's startup
    # event, so the port never binds and every frontend /api/* request is refused until
    # "Done loading processors!". As a background task, the port binds immediately and
    # classla still warms eagerly. A no-op for the default lowercase lemmatizer.
    warmup_task = asyncio.create_task(_warm_lemmatizer(srs_dbs, content_stores))

    logger.info("TunaTale backend starting up")
    yield

    # Let the warm-up settle on shutdown. _warm_lemmatizer swallows its own exceptions,
    # so this never raises; by normal shutdown the pipeline is long since loaded.
    await warmup_task
    await pipeline.shutdown()
    for db in srs_dbs.values():
        db.close()
    for store in content_stores.values():
        store.close()
    logger.info("TunaTale backend shutting down")


app = FastAPI(title="TunaTale", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _resolve_language_state(request, call_next):
    """Bind the request's language connection set onto ``request.state``.

    The active language is the ``X-TT-Language`` header, defaulting to
    ``settings.target_language``. When the app has per-language maps
    (``srs_dbs``), the request is served from the matching connection (unknown
    codes fall back to the default language); otherwise — single-language tests
    that only set the singular ``app.state.srs_db`` — it falls back to those.
    Routes read ``request.state.{srs_db,content_store,language}`` so isolation is
    which connection serves the request, not a per-query filter.
    """
    code = request.headers.get("x-tt-language") or settings.target_language
    state = request.app.state
    srs_dbs = getattr(state, "srs_dbs", None)
    if srs_dbs is not None:
        if code not in srs_dbs:
            code = settings.target_language
        request.state.srs_db = srs_dbs[code]
        request.state.content_store = state.content_stores[code]
        request.state.language = state.languages[code]
    else:
        request.state.srs_db = getattr(state, "srs_db", None)
        request.state.content_store = getattr(state, "content_store", None)
        request.state.language = getattr(state, "language", None)
    request.state.language_code = code
    return await call_next(request)


from app.api import admin, anki, audio, curriculum, generation, srs  # noqa: E402
from app.api import llm as llm_api  # noqa: E402
from app.api import pipeline as pipeline_api  # noqa: E402

app.include_router(curriculum.router)
app.include_router(pipeline_api.router)
app.include_router(generation.router)
app.include_router(srs.router)
app.include_router(audio.router)
app.include_router(anki.router)
app.include_router(admin.router)
app.include_router(llm_api.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/languages")
async def languages(request: Request):
    """Configured languages (for the frontend selector) + the request's active one.

    Lists every language with an open connection; single-language deployments
    return one entry. ``active`` is the language the X-TT-Language header resolved
    to for this request.
    """
    langs = getattr(request.app.state, "languages", None)
    if langs is None:
        # Single-language test fallback: the singular app.state.language.
        lang = getattr(request.app.state, "language", None)
        items = [{"code": lang.code, "name": lang.name}] if lang is not None else []
    else:
        items = [{"code": code, "name": lang.name} for code, lang in langs.items()]
    return {"languages": items, "active": request.state.language_code}
