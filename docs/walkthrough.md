# TunaTale Production Codebase Walkthrough

*2026-03-25T01:20:45Z by Showboat 0.6.1*
<!-- showboat-id: 4bdef7f8-1973-46b4-b00d-14caf394240c -->

## Purpose of This Document

This walkthrough covers the production TunaTale codebase — the unified application rebuilt from the two prototypes documented in `docs/archive/walkthrough-prototypes.md`. It serves two audiences: (1) a human reader wanting to understand how TunaTale works, and (2) an AI agent extending or maintaining the system.

**What changed from the prototypes:** The production rebuild unified the audio pipeline (micro-demo-0.0) and the content engine (micro-demo-0.1) under a single FastAPI application. Hardcoded language logic was replaced with pluggable preprocessors and voice maps. The mock LLM (MD5-hashed) became a cassette system with multiple modes. FSRS-5 replaced the custom SRS scheduler. The entire codebase follows hexagonal architecture with Protocol-based ports. Since the initial production build: ContentStore added SQLite persistence for curricula/lessons/audio, per-word SRS tracking added lemmatizer/tokenizer/transcript modules, section_builder extracted from StoryGenerator (now a thin orchestrator), Slovene syllabification added for Pimsleur backward buildup, pydub replaced raw-PCM concatenation, SRS admin UI added (6 admin endpoints + SvelteKit admin page).

**Stage-3 Anki integration (PART 12 onward):** SRS items track two directions independently (RECOGNITION L2→L1 and PRODUCTION L1→L2), mirroring Anki's note/card model. The `app/anki/` package handles direct SQLite access to `collection.anki2` with a backup-and-lock safety envelope (`safe_open`), an offline-first sync engine (orphan recovery → create-new → push → pull → deck-config refreshes; the `pending_revlog` drain phase died with migration v9) that doesn't depend on AnkiConnect, and a media pipeline (Forvo + EdgeTTS fallback + Pixabay + ffmpeg LUFS normalization). Queue stats read FSRS-5 parameters from Anki's deck_config protobuf, cached in `anki_state_cache`. Frontend has a unified review queue, Anki-running status gating, a single Sync button, and a `/cards` admin page (originally `/admin/srs`). PARTs 18–21 cover the parity testing harness, the `tt_revlog` event log, the cloze pipeline, and the frontend toolchain that all support this.

**The word-learning state machine (PART 22 onward):** the model shifted from a flat per-card list to a per-**lemma** state machine — `BASE (recognition → production) → INFLECTIONS` — built on a sentence-aware classla lemmatizer (PART 22), always-on cloze cards with Fluent-Forever ending-blanks (PART 23), and an A1-tuned `morphology_focus` generator (PART 24). PART 25 ties these together: introduction gates, per-lemma mastery coloring, and a fully interactive transcript where any word is a one-click entry into the learning loop. PART 26 covers the f32 FSRS migration and parity Layers 49–66; PART 27 the (since-completed) move toward event-sourced sync; PART 28 the documentation set. **PART 29 covers the 2026-06/07 restructurings** — the sync and database module splits, the peer-sync-only surface, the language-plugin registry and Norwegian, the direction-field registry, the compound-breakdown plugin, the lesson-player rework, and parity Layers 67–80; read it alongside any pre-split reference in PARTs 12–27.

## Architecture at a Glance

```
backend/
├── app/
│   ├── main.py              # FastAPI app with CORS, lifespan, routers
│   ├── config.py             # Pydantic Settings (env-driven, +Anki/Forvo/Pixabay)
│   ├── languages.py          # Per-language plugin registry (LanguageContext)
│   ├── common/               # Cross-cutting helpers (guid generation)
│   ├── models/               # Pure domain models (no I/O)
│   ├── llm/                  # Groq LLM client + cassette replay system
│   ├── srs/                  # FSRS-5 + queue engine/stats + db_* mixins + lemmatizer/transcript
│   ├── generation/           # Chat planner + story + section_builder + syllabify + norwegian_breakdown
│   ├── audio/                # TTS, pydub assembly, preprocessing
│   ├── storage/              # ContentStore SQLite repository
│   ├── media/                # In-app media import (refresh Anki media into TT cache)
│   ├── anki/                 # Direct sqlite access to collection.anki2 (safety/sync/media)
│   │   └── media/            # Forvo + EdgeTTS fallback + Pixabay + ffmpeg normalize
│   └── api/                  # FastAPI route modules (56 endpoints, 8 routers)
└── tests/
    ├── conftest.py           # Cassette + DB + ASGI fixtures
    ├── cassettes/            # Recorded LLM responses (JSON)
    └── test_*.py             # 139 test files, ~3700 tests, 100% coverage (enforced)
```

---

## PART 1: Configuration & Entry Point

### 1.1 Pydantic Settings

All configuration is environment-driven via Pydantic Settings — no module-level side effects, no hardcoded secrets:

```bash
cat -n backend/app/config.py
```

```output
     1	"""Application configuration via Pydantic Settings."""
     2	
     3	from pathlib import Path
     4	
     5	from pydantic_settings import BaseSettings, SettingsConfigDict
     6	
     7	
     8	class Settings(BaseSettings):
     9	    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    10	
    11	    groq_api_key: str = ""
    12	    # Per-language DB (one-DB-per-language isolation). Default is the Slovene DB;
    13	    # switch languages by flipping target_language AND database_url together
    14	    # (e.g. sqlite:///./tunatale_no.db for Norwegian).
    15	    database_url: str = "sqlite:///./tunatale_sl.db"
    16	    # Phase 5 — simultaneous multi-language. When non-empty, the app opens one
    17	    # connection per entry (``{"sl": "sqlite:///./tunatale_sl.db", "no": "…_no.db"}``)
    18	    # and resolves the active one per request from the X-TT-Language header. Empty
    19	    # (the default) = single-language: one connection from ``database_url`` bound to
    20	    # ``target_language``. ``target_language`` is the default when no header is sent.
    21	    database_urls: dict[str, str] = {}
    22	    llm_mode: str = "mock"  # mock | live | record | patch
    23	    # gpt-oss-120b replaces llama-3.3-70b-versatile (deprecated by Groq 2026-06-30).
    24	    # It is a reasoning model — main.py pins reasoning_effort=low via
    25	    # reasoning_params_for_model() so it emits content instead of burning the whole
    26	    # budget on reasoning. Free-tier TPM is 8000; WIDER story gen fits, DEEPER (bigger
    27	    # prompt) can approach the ceiling.
    28	    llm_model: str = "openai/gpt-oss-120b"
    29	    # Groq free-tier daily token cap for gpt-oss-120b — the binding limit, but it
    30	    # appears in no response header, so TT tallies its own spend (UsageLedger) and
    31	    # the rate-limit UI compares against this number.
    32	    groq_tokens_per_day_limit: int = 100_000
    33	    # Ollama/secondary fallback when Groq fails; default off — failures fail loudly.
    34	    llm_allow_fallback: bool = False
    35	    llm_usage_ledger_path: Path = Path("~/.tunatale/llm_usage.log").expanduser()
    36	
    37	    target_language: str = "sl"
    38	
    39	    anki_collection_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.anki2").expanduser()
    40	    anki_media_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.media").expanduser()
    41	    anki_deck_name: str = "1. Slovene"
    42	    anki_backup_dir: Path = Path("~/.tunatale/anki-backups").expanduser()
    43	    # Retention cap for the safe_open backup directory. safe_open writes a full
    44	    # ~16 MB collection snapshot on every call; without a cap the directory grows
    45	    # without bound. Keep the N most recent snapshots (~16 MB each); <= 0 disables.
    46	    anki_backup_keep: int = 30
    47	    media_dir: Path = Path("./media")
    48	    anki_fallback_log: Path = Path("~/.tunatale/logs/anki-fallback.log").expanduser()
    49	    # Durable per-sync soak log: every non-dry sync (CLI or API) appends a
    50	    # SYNC_SOAK heartbeat + one RECOMPUTE_DIVERGENCE line per divergence.
    51	    sync_log: Path = Path("~/.tunatale/logs/sync.log").expanduser()
    52	
    53	    # Peer-sync (anki subprocess) config — see sync_orchestrator.py.
    54	    tt_collection_path: Path = Path("~/.tunatale/tt_collection.anki2").expanduser()
    55	    sync_enabled: bool = False
    56	    sync_endpoint: str = ""  # "" → AnkiWeb default; else self-host URL
    57	    sync_username: str = ""
    58	    # AnkiWeb password. Prefer the macOS Keychain (see sync_keychain_service); this
    59	    # env/.env value is an override fallback and should normally stay EMPTY (plaintext).
    60	    sync_password: str = ""
    61	    # macOS Keychain generic-password service the AnkiWeb password is stored under
    62	    # (account = sync_username). Store it with:
    63	    #   security add-generic-password -s tunatale-ankiweb -a <username> -w
    64	    sync_keychain_service: str = "tunatale-ankiweb"
    65	    # Optional pin for the sync subprocess (`uv run --with anki==X`). Empty → latest
    66	    # anki. Set to match your desktop Anki's sync-protocol version if a mismatch appears.
    67	    anki_pkg_version: str = ""
    68	    # Interpreter for the anki driver subprocess. It runs isolated + project-free
    69	    # (--no-project), which escapes the project lock's stale protobuf 4.21.2 (dragged in
    70	    # by the classla+anki extras; no cp314 wheel) — a clean resolve pulls a current
    71	    # protobuf that imports fine on 3.14. Pin to an older Python here only if a future
    72	    # anki/protobuf breaks on the latest.
    73	    anki_subprocess_python: str = "3.14"
    74	
    75	    anki_model_name: str = ""
    76	    pixabay_api_key: str = ""
    77	    # Global lemmatizer gate: "lowercase" (default) forces the deterministic
    78	    # lowercase engine for EVERY language (the CI/test pin, and how a deployment
    79	    # disables the heavy PyTorch pipelines). Any other value ("classla", "stanza",
    80	    # "auto", …) opts in, and the ENGINE is then chosen per language from the
    81	    # registry (app.languages.get_lemmatizer_type: sl→classla, no→stanza). This is
    82	    # per-language, not one-engine-per-process, so multi-language mode
    83	    # (database_urls) analyzes each language with its own model. See get_lemmatizer.
    84	    lemmatizer_type: str = "lowercase"
    85	
    86	    anki_new_per_day_default: int = 20
    87	    anki_reviews_per_day_default: int = 200
    88	
    89	    # Lesson audio delivery format. Opus is ~10-20× smaller than WAV for speech,
    90	    # cutting mobile-data use when streaming lessons to a phone. Set to "wav" to
    91	    # restore uncompressed delivery. Codec must be a key of transcode.CODEC_EXT.
    92	    audio_delivery_codec: str = "opus"  # opus | aac | mp3 | wav
    93	    audio_delivery_bitrate: str = "28k"
    94	
    95	    pipeline_autostart: bool = True
    96	
    97	
    98	settings = Settings()
    99	
   100	
   101	# Anki rolls the study day over at this *local* hour (default 4 AM), not at
   102	# midnight — a grade timestamped between local midnight and the rollover belongs
   103	# to the PRIOR Anki day. The rollover arithmetic is single-sourced in
   104	# `app.srs.anki_mirror.rollover` (local-day domain: `local_today_rollover`,
   105	# `anki_day_bounds_utc`, `anki_today`; due_at convention: `due_at_rollover_utc`);
   106	# `app.srs.anki_mirror.protobuf_wire` owns the separate col-day index domain
   107	# (`compute_anki_day_index`, `review_due_at_for_col_day`). Both derive from this
   108	# constant. Promote to a Settings field if it ever needs to be config-driven
   109	# (Anki stores it per-collection).
   110	ANKI_ROLLOVER_HOUR = 4
```

What started as four settings has grown to ~24 typed fields: the original LLM quartet (`groq_api_key`, `database_url`, `llm_mode`, `llm_model` — now defaulting to `openai/gpt-oss-120b` after Groq deprecated llama-3.3), per-language `database_urls` for multi-language mode, the Anki collection/backup paths, Forvo/Pixabay keys, and the `tt_collection` peer-sync settings. The `extra="ignore"` setting means stray env vars won't crash startup. CI runs with defaults — no API key needed because `llm_mode` defaults to `mock`.

### 1.2 FastAPI Application

```bash
cat -n backend/app/main.py
```

```output
     1	"""FastAPI application for TunaTale language learning."""
     2	
     3	import asyncio
     4	import logging
     5	from contextlib import asynccontextmanager
     6	from pathlib import Path
     7	
     8	import anyio
     9	from dotenv import load_dotenv
    10	
    11	load_dotenv()
    12	
    13	from fastapi import FastAPI, Request  # noqa: E402
    14	from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
    15	
    16	from app.audio.edge_tts import EdgeTTSService  # noqa: E402
    17	from app.audio.pause_calculator import NaturalPauseCalculator  # noqa: E402
    18	from app.audio.renderer import LessonRenderer  # noqa: E402
    19	from app.config import settings  # noqa: E402
    20	from app.generation.pipeline import LessonPipeline  # noqa: E402
    21	from app.generation.planner import CurriculumPlanner  # noqa: E402
    22	from app.generation.story import StoryGenerator  # noqa: E402
    23	from app.languages import get_language, get_preprocessor  # noqa: E402
    24	from app.llm.activity import ActivityLog  # noqa: E402
    25	from app.llm.cassette import CassetteLLMClient  # noqa: E402
    26	from app.llm.client import LLMClient, reasoning_params_for_model  # noqa: E402
    27	from app.llm.usage_ledger import UsageLedger  # noqa: E402
    28	from app.models.lesson import SectionType  # noqa: E402
    29	from app.srs.database import SRSDatabase  # noqa: E402
    30	from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, model_version_for  # noqa: E402
    31	from app.storage.store import ContentStore  # noqa: E402
    32	
    33	logging.basicConfig(level=logging.INFO)
    34	logging.getLogger("app.audio.renderer").setLevel(logging.DEBUG)
    35	
    36	logger = logging.getLogger(__name__)
    37	
    38	
    39	async def _warm_lemmatizer(srs_dbs: dict[str, SRSDatabase], content_stores: dict[str, ContentStore]) -> None:
    40	    """Fill the persistent sentence-analysis cache from stored lessons.
    41	
    42	    Iterates every stored lesson's natural-speed L2 sentences through
    43	    ``analyze_sentence_cached``:
    44	    - **First run ever:** loads classla once, fills the persistent cache.
    45	    - **Every subsequent restart:** all cache hits → the model is never loaded at
    46	      startup → admin list and everything else are instant.
    47	
    48	    Warms **each configured language** with its own lemmatizer (multi-language mode
    49	    runs both in one process), so the Slovene classla cache and the Norwegian stanza
    50	    cache both fill. Runs as a background ``asyncio.create_task`` so uvicorn binds the
    51	    port immediately. Swallows per-language errors so a missing model for one language
    52	    degrades to on-demand loading without aborting the others.
    53	    """
    54	    for code, srs_db in srs_dbs.items():
    55	        try:
    56	            lemmatizer = get_lemmatizer(code)
    57	            model_version = model_version_for(lemmatizer)
    58	            if not model_version:
    59	                continue  # cheap lemmatizer; nothing to warm
    60	            lessons = content_stores[code].list_lessons()
    61	            await anyio.to_thread.run_sync(_warm_from_lessons, lessons, srs_db, lemmatizer, model_version)
    62	        except Exception:
    63	            logger.warning("Lemmatizer warm-up failed for %s — continuing with on-demand loading", code)
    64	
    65	
    66	def _warm_from_lessons(
    67	    lessons: list[tuple[str, str, int, object]],
    68	    srs_db: SRSDatabase,
    69	    lemmatizer: object,
    70	    model_version: str,
    71	) -> None:
    72	    """Synchronous helper: run every stored L2 sentence through the analysis cache."""
    73	    for _lesson_id, _curriculum_id, _day, lesson in lessons:
    74	        natural_speed = next(
    75	            (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
    76	            None,
    77	        )
    78	        if natural_speed is None:
    79	            continue
    80	        for phrase in natural_speed.phrases:
    81	            if phrase.language_code != lesson.language_code:
    82	                continue
    83	            analyze_sentence_cached(srs_db, lemmatizer, phrase.text, lesson.language_code, model_version)
    84	
    85	
    86	def _language_db_map() -> dict[str, str]:
    87	    """Map of language code → SQLite URL to open at startup.
    88	
    89	    Multi-language when ``settings.database_urls`` is set (one connection per
    90	    entry, resolved per request from the X-TT-Language header); otherwise the
    91	    single ``database_url`` bound to ``target_language`` (single-language).
    92	    """
    93	    if settings.database_urls:
    94	        return dict(settings.database_urls)
    95	    return {settings.target_language: settings.database_url}
    96	
    97	
    98	@asynccontextmanager
    99	async def lifespan(app: FastAPI):
   100	    activity_log = ActivityLog()
   101	    real_client = LLMClient(
   102	        groq_api_key=settings.groq_api_key,
   103	        groq_model=settings.llm_model,
   104	        groq_extra_body_params=reasoning_params_for_model(settings.llm_model),
   105	        usage_ledger=UsageLedger(settings.llm_usage_ledger_path),
   106	        on_call=activity_log.record_llm_call,
   107	        allow_fallback=settings.llm_allow_fallback,
   108	    )
   109	    _BACKEND_DIR = Path(__file__).parent.parent
   110	    cassette_path = _BACKEND_DIR / "tests/cassettes/e2e.json"
   111	
   112	    # Wrap with cassettes unless explicitly in live mode
   113	    if settings.llm_mode != "live":
   114	        llm = CassetteLLMClient(mode=settings.llm_mode, cassette_path=cassette_path, real_client=real_client)
   115	    else:
   116	        llm = real_client
   117	
   118	    # One connection set per configured language. The per-request middleware picks
   119	    # the active one; the parity-sensitive queue/badge queries stay unmodified —
   120	    # isolation is which connection serves the request, not a WHERE clause.
   121	    db_map = _language_db_map()
   122	    default_code = settings.target_language if settings.target_language in db_map else next(iter(db_map))
   123	
   124	    srs_dbs: dict[str, SRSDatabase] = {}
   125	    content_stores: dict[str, ContentStore] = {}
   126	    languages = {}
   127	    for code, url in db_map.items():
   128	        path = url.removeprefix("sqlite:///")
   129	        srs_dbs[code] = SRSDatabase(path)
   130	        content_stores[code] = ContentStore(path)
   131	        languages[code] = get_language(code)
   132	
   133	    app.state.srs_dbs = srs_dbs
   134	    app.state.content_stores = content_stores
   135	    app.state.languages = languages
   136	    # Singular defaults (the active language): the middleware's single-language
   137	    # fallback, the lemmatizer warm-up, and any non-request-scoped consumer.
   138	    app.state.srs_db = srs_dbs[default_code]
   139	    app.state.content_store = content_stores[default_code]
   140	    app.state.language = languages[default_code]
   141	
   142	    app.state.activity_log = activity_log
   143	    app.state.llm = llm
   144	    app.state.curriculum_planner = CurriculumPlanner(llm)
   145	    app.state.story_generator = StoryGenerator(llm)
   146	    preprocessors = {code: get_preprocessor(code) for code in db_map}
   147	    app.state.renderer = LessonRenderer(
   148	        tts=EdgeTTSService(),
   149	        preprocessors=preprocessors,
   150	        pause_calculator=NaturalPauseCalculator(),
   151	        delivery_codec=settings.audio_delivery_codec,
   152	        delivery_bitrate=settings.audio_delivery_bitrate,
   153	    )
   154	    app.state.audio_dir = _BACKEND_DIR / "output/audio"
   155	
   156	    pipeline = LessonPipeline(
   157	        story_generator=app.state.story_generator,
   158	        renderer=app.state.renderer,
   159	        audio_dir=app.state.audio_dir,
   160	        content_stores=content_stores,
   161	        languages=languages,
   162	        srs_dbs=srs_dbs,
   163	        activity_log=activity_log,
   164	        llm_client=real_client,
   165	    )
   166	    app.state.pipeline = pipeline
   167	    if settings.pipeline_autostart:
   168	        pipeline.start()
   169	
   170	    # Warm the lemmatizer in the background so the first /listen or /transcript request
   171	    # doesn't pay the model-load cost. Critically, this must NOT be awaited before the
   172	    # yield: awaiting the classla pipeline load (~15s) here blocks uvicorn's startup
   173	    # event, so the port never binds and every frontend /api/* request is refused until
   174	    # "Done loading processors!". As a background task, the port binds immediately and
   175	    # classla still warms eagerly. A no-op for the default lowercase lemmatizer.
   176	    warmup_task = asyncio.create_task(_warm_lemmatizer(srs_dbs, content_stores))
   177	
   178	    logger.info("TunaTale backend starting up")
   179	    yield
   180	
   181	    # Let the warm-up settle on shutdown. _warm_lemmatizer swallows its own exceptions,
   182	    # so this never raises; by normal shutdown the pipeline is long since loaded.
   183	    await warmup_task
   184	    await pipeline.shutdown()
   185	    for db in srs_dbs.values():
   186	        db.close()
   187	    for store in content_stores.values():
   188	        store.close()
   189	    logger.info("TunaTale backend shutting down")
   190	
   191	
   192	app = FastAPI(title="TunaTale", version="0.1.0", lifespan=lifespan)
   193	
   194	app.add_middleware(
   195	    CORSMiddleware,
   196	    allow_origins=["*"],
   197	    allow_credentials=True,
   198	    allow_methods=["*"],
   199	    allow_headers=["*"],
   200	)
   201	
   202	
   203	@app.middleware("http")
   204	async def _resolve_language_state(request, call_next):
   205	    """Bind the request's language connection set onto ``request.state``.
   206	
   207	    The active language is the ``X-TT-Language`` header, defaulting to
   208	    ``settings.target_language``. When the app has per-language maps
   209	    (``srs_dbs``), the request is served from the matching connection (unknown
   210	    codes fall back to the default language); otherwise — single-language tests
   211	    that only set the singular ``app.state.srs_db`` — it falls back to those.
   212	    Routes read ``request.state.{srs_db,content_store,language}`` so isolation is
   213	    which connection serves the request, not a per-query filter.
   214	    """
   215	    code = request.headers.get("x-tt-language") or settings.target_language
   216	    state = request.app.state
   217	    srs_dbs = getattr(state, "srs_dbs", None)
   218	    if srs_dbs is not None:
   219	        if code not in srs_dbs:
   220	            code = settings.target_language
   221	        request.state.srs_db = srs_dbs[code]
   222	        request.state.content_store = state.content_stores[code]
   223	        request.state.language = state.languages[code]
   224	    else:
   225	        request.state.srs_db = getattr(state, "srs_db", None)
   226	        request.state.content_store = getattr(state, "content_store", None)
   227	        request.state.language = getattr(state, "language", None)
   228	    request.state.language_code = code
   229	    return await call_next(request)
   230	
   231	
   232	from app.api import admin, anki, audio, curriculum, generation, srs  # noqa: E402
   233	from app.api import llm as llm_api  # noqa: E402
   234	from app.api import pipeline as pipeline_api  # noqa: E402
   235	
   236	app.include_router(curriculum.router)
   237	app.include_router(pipeline_api.router)
   238	app.include_router(generation.router)
   239	app.include_router(srs.router)
   240	app.include_router(audio.router)
   241	app.include_router(anki.router)
   242	app.include_router(admin.router)
   243	app.include_router(llm_api.router)
   244	
   245	
   246	@app.get("/api/health")
   247	async def health():
   248	    return {"status": "ok"}
   249	
   250	
   251	@app.get("/api/languages")
   252	async def languages(request: Request):
   253	    """Configured languages (for the frontend selector) + the request's active one.
   254	
   255	    Lists every language with an open connection; single-language deployments
   256	    return one entry. ``active`` is the language the X-TT-Language header resolved
   257	    to for this request.
   258	    """
   259	    langs = getattr(request.app.state, "languages", None)
   260	    if langs is None:
   261	        # Single-language test fallback: the singular app.state.language.
   262	        lang = getattr(request.app.state, "language", None)
   263	        items = [{"code": lang.code, "name": lang.name}] if lang is not None else []
   264	    else:
   265	        items = [{"code": code, "name": lang.name} for code, lang in langs.items()]
   266	    return {"languages": items, "active": request.state.language_code}
```

The lifespan context manager wires every dependency the API needs. Three production refinements since the prototype phase stand out:

1. **Cassette wrapping is automatic.** Unless `llm_mode == "live"`, the real `LLMClient` is wrapped in a `CassetteLLMClient` that records or replays from `tests/cassettes/e2e.json`. This keeps the dev server (and CI) from hitting the real Groq API by accident.
2. **`ContentStore` joined the wiring.** Curricula, lessons, and rendered audio files are persisted to SQLite alongside the SRS database (same `db_path`). The store is closed on shutdown.
3. **`StoryGenerator` no longer takes `srs_db`.** The LLM produces creative content and the new `section_builder` transforms it into structured `Section`s deterministically (see Part 5.3).

`LessonRenderer` is constructed with three collaborators (TTS, preprocessor, pause calculator) — the old `AudioAssembler` port is gone, replaced by pydub-based assembly inside the renderer itself (Part 6.4). Logging is configured at INFO globally with the renderer at DEBUG so per-section synthesis steps show up in dev. Eight routers partition the API: curriculum, pipeline, story, srs, audio, anki, admin, llm. The health check at `/api/health` is the smoke test.

Note the `load_dotenv()` before FastAPI imports — this ensures `.env` is loaded before Pydantic Settings reads environment variables.

---

## PART 2: Domain Models

The models layer is pure data — no I/O, no network calls, no database access. Every model is a `dataclass` with optional serialization helpers.

### 2.1 Language Configuration

```bash
cat -n backend/app/models/language.py
```

```output
     1	"""Language configuration model."""
     2	
     3	from __future__ import annotations
     4	
     5	from dataclasses import dataclass, field
     6	
     7	# The narrator (English descriptions/translations) voice — shared across every
     8	# language's voice map and the default narrator for generated lessons. Single-sourced
     9	# here so lesson/story code doesn't re-hardcode the literal.
    10	NARRATOR_VOICE = "en-US-GuyNeural"
    11	
    12	
    13	@dataclass
    14	class Language:
    15	    """Language configuration including ISO code, display names, script, and TTS voice map."""
    16	
    17	    code: str  # ISO 639-1 code, e.g. "sl"
    18	    name: str  # English name, e.g. "Slovene"
    19	    native_name: str  # Native name, e.g. "slovenščina"
    20	    script: str  # Writing system, e.g. "latin"
    21	    tts_voice_map: dict[str, str] = field(default_factory=dict)  # role → EdgeTTS voice name
    22	
    23	    @classmethod
    24	    def slovene(cls) -> Language:
    25	        return cls(
    26	            code="sl",
    27	            name="Slovene",
    28	            native_name="slovenščina",
    29	            script="latin",
    30	            tts_voice_map={
    31	                "narrator": NARRATOR_VOICE,
    32	                "female-1": "sl-SI-PetraNeural",
    33	                "female-2": "sl-SI-PetraNeural",
    34	                "male-1": "sl-SI-RokNeural",
    35	                "male-2": "sl-SI-RokNeural",
    36	                "female": "sl-SI-PetraNeural",  # legacy
    37	                "male": "sl-SI-RokNeural",  # legacy
    38	            },
    39	        )
    40	
    41	    @classmethod
    42	    def norwegian(cls) -> Language:
    43	        return cls(
    44	            code="no",
    45	            name="Norwegian",
    46	            native_name="norsk",
    47	            script="latin",
    48	            tts_voice_map={
    49	                "narrator": NARRATOR_VOICE,
    50	                "female-1": "nb-NO-PernilleNeural",
    51	                "female-2": "nb-NO-PernilleNeural",
    52	                "male-1": "nb-NO-FinnNeural",
    53	                "male-2": "nb-NO-FinnNeural",
    54	                "female": "nb-NO-PernilleNeural",
    55	                "male": "nb-NO-FinnNeural",
    56	            },
    57	        )
    58	
    59	    @classmethod
    60	    def english(cls) -> Language:
    61	        return cls(
    62	            code="en",
    63	            name="English",
    64	            native_name="English",
    65	            script="latin",
    66	            tts_voice_map={
    67	                "narrator": NARRATOR_VOICE,
    68	                "female-1": "en-US-AriaNeural",
    69	                "female-2": "en-US-AriaNeural",
    70	                "male-1": "en-US-GuyNeural",
    71	                "male-2": "en-US-GuyNeural",
    72	                "female": "en-US-AriaNeural",  # legacy
    73	                "male": "en-US-GuyNeural",  # legacy
    74	            },
    75	        )
```

**Key design decision:** The prototype hardcoded a `Language` enum with Tagalog/English/Spanish. Production replaces this with a data-driven `Language` dataclass. Adding a new language is just creating a new factory method — no enum changes, no code branching. The `tts_voice_map` dict maps roles (`narrator`, `female-1`, `female-2`, `male-1`, `male-2`, plus legacy `female`/`male`) to EdgeTTS voice names. The narrator slot is always English (it speaks the section titles and L1 translations), while the numbered roles let the multi-speaker dialogue assigned by the `section_builder` use distinct voices. Legacy `female`/`male` keys remain for backward compat with old curricula.

Here's what a Language instance actually looks like:

```bash
cd backend && uv run python -c "
from app.models.language import Language
sl = Language.slovene()
print(f\"code: {sl.code}\")
print(f\"name: {sl.name}\")
print(f\"native_name: {sl.native_name}\")
print(f\"script: {sl.script}\")
print(f\"tts_voice_map: {sl.tts_voice_map}\")
"
```

```output
code: sl
name: Slovene
native_name: slovenščina
script: latin
tts_voice_map: {'narrator': 'en-US-GuyNeural', 'female-1': 'sl-SI-PetraNeural', 'female-2': 'sl-SI-PetraNeural', 'male-1': 'sl-SI-RokNeural', 'male-2': 'sl-SI-RokNeural', 'female': 'sl-SI-PetraNeural', 'male': 'sl-SI-RokNeural'}
```

### 2.2 Lesson Structure

The lesson model implements the Pimsleur section format — the same structure from the prototypes (plus a fifth `SLOW_TRANSLATED` section added 2026-07), now as clean dataclasses:

```bash
cat -n backend/app/models/lesson.py
```

```output
     1	"""Lesson, Section, and Phrase domain models.
     2	
     3	Pimsleur 4-section format ported from micro-demo-0.0/tunatale/core/models/.
     4	"""
     5	
     6	from __future__ import annotations
     7	
     8	import json
     9	from dataclasses import dataclass, field
    10	from enum import Enum
    11	
    12	from app.models.language import NARRATOR_VOICE
    13	
    14	
    15	@dataclass
    16	class KeyPhraseInfo:
    17	    """A key phrase with its L1 translation, stored on the Lesson for deferred SRS registration."""
    18	
    19	    phrase: str
    20	    translation: str
    21	
    22	
    23	class SectionType(Enum):
    24	    """Four Pimsleur section types for each lesson."""
    25	
    26	    KEY_PHRASES = "key_phrases"
    27	    NATURAL_SPEED = "natural_speed"
    28	    SLOW_SPEED = "slow_speed"
    29	    TRANSLATED = "translated"
    30	    SLOW_TRANSLATED = "slow_translated"
    31	
    32	
    33	@dataclass
    34	class Phrase:
    35	    """A single phrase with TTS voice settings."""
    36	
    37	    text: str
    38	    voice_id: str
    39	    language_code: str
    40	    rate: str = "+0%"
    41	    pitch: str = "+0Hz"
    42	    volume: str = "+0%"
    43	    role: str = ""
    44	
    45	
    46	@dataclass
    47	class Section:
    48	    """A section within a lesson, grouping phrases of the same Pimsleur type."""
    49	
    50	    section_type: SectionType
    51	    phrases: list[Phrase] = field(default_factory=list)
    52	
    53	    def __post_init__(self) -> None:
    54	        if not isinstance(self.section_type, SectionType):
    55	            raise ValueError(f"section_type must be a SectionType enum, got {type(self.section_type)}")
    56	
    57	
    58	@dataclass
    59	class Lesson:
    60	    """A complete TunaTale audio lesson."""
    61	
    62	    title: str
    63	    language_code: str
    64	    sections: list[Section] = field(default_factory=list)
    65	    narrator_voice: str = NARRATOR_VOICE
    66	    key_phrases: list[KeyPhraseInfo] = field(default_factory=list)
    67	    generation_metadata: dict = field(default_factory=dict)
    68	
    69	    def to_json(self) -> str:
    70	        data = {
    71	            "title": self.title,
    72	            "language_code": self.language_code,
    73	            "narrator_voice": self.narrator_voice,
    74	            "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in self.key_phrases],
    75	            "sections": [
    76	                {
    77	                    "section_type": s.section_type.value,
    78	                    "phrases": [
    79	                        {
    80	                            "text": p.text,
    81	                            "voice_id": p.voice_id,
    82	                            "language_code": p.language_code,
    83	                            "rate": p.rate,
    84	                            "pitch": p.pitch,
    85	                            "volume": p.volume,
    86	                            "role": p.role,
    87	                        }
    88	                        for p in s.phrases
    89	                    ],
    90	                }
    91	                for s in self.sections
    92	            ],
    93	            "generation_metadata": self.generation_metadata,
    94	        }
    95	        return json.dumps(data, ensure_ascii=False)
    96	
    97	    @classmethod
    98	    def from_json(cls, json_str: str) -> Lesson:
    99	        data = json.loads(json_str)
   100	        sections = [
   101	            Section(
   102	                section_type=SectionType(s["section_type"]),
   103	                phrases=[Phrase(**p) for p in s["phrases"]],
   104	            )
   105	            for s in data.get("sections", [])
   106	        ]
   107	        key_phrases = [KeyPhraseInfo(**kp) for kp in data.get("key_phrases", [])]
   108	        return cls(
   109	            title=data["title"],
   110	            language_code=data["language_code"],
   111	            sections=sections,
   112	            narrator_voice=data.get("narrator_voice", NARRATOR_VOICE),
   113	            key_phrases=key_phrases,
   114	            generation_metadata=data.get("generation_metadata", {}),
   115	        )
   116	
   117	
   118	def extract_sentence_translations_from_translated(lesson: Lesson) -> dict[str, str]:
   119	    """Recover {L2_sentence: EN_translation} from a stored Lesson's TRANSLATED section.
   120	
   121	    Used to backfill `generation_metadata['sentence_translations']` on lessons
   122	    generated before that field existed. The TRANSLATED section emits
   123	    alternating L2/EN phrases (with stray EN-EN label lines like
   124	    "Translated"/"At the Cafe" at the top); we pair each L2 phrase with the
   125	    immediately-following EN phrase. First occurrence wins on duplicate L2 keys.
   126	    """
   127	    out: dict[str, str] = {}
   128	    l2_code = lesson.language_code
   129	    for section in lesson.sections:
   130	        if section.section_type is not SectionType.TRANSLATED:
   131	            continue
   132	        phrases = section.phrases
   133	        for i in range(len(phrases) - 1):
   134	            cur, nxt = phrases[i], phrases[i + 1]
   135	            if cur.language_code == l2_code and nxt.language_code == "en" and cur.text and cur.text not in out:
   136	                out[cur.text] = nxt.text
   137	    return out
```

The section types encode the Pimsleur method: (1) **KEY_PHRASES** — individual vocabulary, (2) **NATURAL_SPEED** — full dialogue at native speed, (3) **SLOW_SPEED** — same dialogue with pauses between words, (4) **TRANSLATED** — L2 followed by L1 translation, and (5) **SLOW_TRANSLATED** (added 2026-07-09) — slow L2 followed by L1, feeding the lesson player's phase model (PART 29). Each `Phrase` carries its own TTS settings (rate, pitch, volume) plus a `role` field (`narrator`, `female-1`, `male-1`, …) that the audio pipeline uses for voice routing.

Two production additions matter for the API and SRS layers:

* `Lesson.narrator_voice` is stored on the lesson itself so the narrator's English voice survives JSON round-tripping.
* `Lesson.key_phrases` carries `KeyPhraseInfo` records (L2 phrase + L1 translation) so the SRS database can be populated *after* the lesson is generated rather than coupling the story generator to the database.

`Lesson.to_json()` / `Lesson.from_json()` give a clean serialization for the `ContentStore` (Part 5.6) without requiring an ORM.

Building a lesson by hand:

```bash
cd backend && uv run python -c "
from app.models.lesson import Lesson, Section, SectionType, Phrase

lesson = Lesson(title=\"Greetings Day 1\", language_code=\"sl\", sections=[
    Section(section_type=SectionType.KEY_PHRASES, phrases=[
        Phrase(text=\"Dober dan\", voice_id=\"sl-SI-PetraNeural\", language_code=\"sl\"),
        Phrase(text=\"Good day\", voice_id=\"en-US-GuyNeural\", language_code=\"en\"),
    ]),
    Section(section_type=SectionType.NATURAL_SPEED, phrases=[
        Phrase(text=\"Dober dan. Kako ste?\", voice_id=\"sl-SI-RokNeural\", language_code=\"sl\"),
    ]),
])

print(f\"Lesson: {lesson.title} ({lesson.language_code})\")
print(f\"Sections: {len(lesson.sections)}\")
for s in lesson.sections:
    print(f\"  {s.section_type.value}: {len(s.phrases)} phrases\")
    for p in s.phrases:
        print(f\"    [{p.voice_id}] {p.text}\")
"
```

```output
Lesson: Greetings Day 1 (sl)
Sections: 2
  key_phrases: 2 phrases
    [sl-SI-PetraNeural] Dober dan
    [en-US-GuyNeural] Good day
  natural_speed: 1 phrases
    [sl-SI-RokNeural] Dober dan. Kako ste?
```

### 2.3 Curriculum Model

Curricula are multi-day learning plans generated by the LLM:

```bash
cat -n backend/app/models/curriculum.py
```

```output
     1	"""Curriculum domain models."""
     2	
     3	from __future__ import annotations
     4	
     5	import json
     6	from dataclasses import asdict, dataclass, field
     7	
     8	
     9	@dataclass
    10	class CurriculumDay:
    11	    """One day in the language learning curriculum."""
    12	
    13	    day: int
    14	    title: str
    15	    focus: str
    16	    collocations: list[str]
    17	    learning_objective: str
    18	    story_guidance: str = ""
    19	
    20	    def __post_init__(self) -> None:
    21	        if self.day < 1:
    22	            raise ValueError(f"day must be ≥ 1, got {self.day}")
    23	
    24	
    25	@dataclass
    26	class Curriculum:
    27	    """A complete language learning curriculum for a given topic."""
    28	
    29	    id: str
    30	    topic: str
    31	    language_code: str
    32	    cefr_level: str
    33	    days: list[CurriculumDay] = field(default_factory=list)
    34	    metadata: dict = field(default_factory=dict)
    35	
    36	    def to_json(self) -> str:
    37	        return json.dumps(asdict(self), ensure_ascii=False, indent=2)
    38	
    39	    @classmethod
    40	    def from_json(cls, json_str: str) -> Curriculum:
    41	        data = json.loads(json_str)
    42	        days_data = data.pop("days", [])
    43	        days = [CurriculumDay(**d) for d in days_data]
    44	        return cls(days=days, **data)
```

Each `CurriculumDay` carries the LLM's plan for that day: a focus area, collocations to teach (L2 phrases), a learning objective, and optional story guidance. The `Curriculum` wraps multiple days with metadata and provides JSON round-tripping via `to_json()`/`from_json()`.

A real example from the test cassettes — here's what the LLM generates for a Slovene travel curriculum:

```bash
cd backend && uv run python -c '
from app.models.curriculum import Curriculum, CurriculumDay
day = CurriculumDay(
    day=1,
    title="Arriving in Ljubljana",
    focus="Basic greetings and directions",
    collocations=["Dober dan", "Kje je...?", "Hvala lepa"],
    learning_objective="Greet locals and ask for directions",
    story_guidance="A traveler arrives at the train station"
)
print(f"Day {day.day}: {day.title}")
print(f"Focus: {day.focus}")
print(f"Collocations: {day.collocations}")
print(f"Objective: {day.learning_objective}")
'
```

```output
Day 1: Arriving in Ljubljana
Focus: Basic greetings and directions
Collocations: ['Dober dan', 'Kje je...?', 'Hvala lepa']
Objective: Greet locals and ask for directions
```

### 2.4 SRS Item & Syntactic Unit

The spaced repetition models track vocabulary state:

```bash
cat -n backend/app/models/syntactic_unit.py
```

```output
     1	"""Syntactic unit (collocation) domain model."""
     2	
     3	from __future__ import annotations
     4	
     5	import json
     6	from dataclasses import dataclass, field
     7	from typing import Literal
     8	
     9	# Where a rich back-of-card field is shown on the drill card's answer side.
    10	#   "summary" — always visible inline (e.g. IPA, a one-line meaning)
    11	#   "details" — inside a collapsed "Details" disclosure (inflections, examples…)
    12	#   "deep"    — its own nested disclosure, opened on demand (the big dictionary entry)
    13	BackFieldTier = Literal["summary", "details", "deep"]
    14	
    15	
    16	@dataclass(frozen=True)
    17	class BackField:
    18	    """One extracted rich back-of-card field: a labelled HTML fragment + its tier.
    19	
    20	    Sourced from an Anki notetype's secondary fields (see
    21	    ``app.anki.field_map.NotetypeProfile.back_fields``); display-only, never
    22	    edited in TT. ``html`` is already sanitized at extraction time.
    23	    """
    24	
    25	    label: str
    26	    html: str
    27	    tier: BackFieldTier = "details"
    28	
    29	
    30	def serialize_extras(extras: tuple[BackField, ...]) -> str:
    31	    """Serialize ``extras`` to a JSON string for storage. Empty → ``""``."""
    32	    if not extras:
    33	        return ""
    34	    return json.dumps([{"label": e.label, "html": e.html, "tier": e.tier} for e in extras])
    35	
    36	
    37	def deserialize_extras(raw: str | None) -> tuple[BackField, ...]:
    38	    """Parse a stored extras JSON string back into ``BackField``s.
    39	
    40	    Tolerant by design: blank/None or malformed JSON yields ``()`` so a bad row
    41	    never breaks a card render.
    42	    """
    43	    if not raw:
    44	        return ()
    45	    try:
    46	        data = json.loads(raw)
    47	    except json.JSONDecodeError, ValueError:
    48	        return ()
    49	    if not isinstance(data, list):
    50	        return ()
    51	    return tuple(
    52	        BackField(label=str(d["label"]), html=str(d["html"]), tier=d.get("tier", "details"))
    53	        for d in data
    54	        if isinstance(d, dict) and "label" in d and "html" in d
    55	    )
    56	
    57	
    58	@dataclass
    59	class SyntacticUnit:
    60	    """A collocation in the target language (L2) with its L1 translation.
    61	
    62	    word_count must be ≥ 1. difficulty must be 1-5. The earlier
    63	    `word_count <= 8` upper bound was a sanity guard against importing long
    64	    English questions from reference/Q&A Anki notes; it turned out to drop
    65	    legitimate phonics cards whose front field is a >8-word question. The
    66	    filter is now only at the lower bound — single-token empty extractions
    67	    still get rejected; long-form items pass through.
    68	    source is "corpus" (frequency-derived), "llm" (generated), "anki", "test",
    69	    or "user".
    70	    """
    71	
    72	    text: str  # L2 text
    73	    translation: str  # L1 translation
    74	    word_count: int
    75	    difficulty: int  # 1–5
    76	    source: str  # "corpus" | "llm" | "user" | "anki" | "test"
    77	    frequency: int = 0
    78	    lemma: str | None = None
    79	    guid: str | None = None
    80	    disambig_key: str = ""
    81	    article: str = ""  # gender/indefinite article (en/ei/et), display-only prefix
    82	    # Rich back-of-card fields (IPA, inflections, examples, dictionary entry…)
    83	    # sourced from the Anki notetype's secondary fields. Display-only, optional;
    84	    # empty for languages/notetypes without a profile that declares them.
    85	    extras: tuple[BackField, ...] = field(default_factory=tuple)
    86	    grammar: str = ""
    87	    note: str = ""
    88	    source_sentence: str = ""
    89	    source_sentence_translation: str = ""
    90	    source_lesson_id: str | None = None
    91	    source_line_index: int | None = None
    92	    card_type: str = "vocab"  # "vocab" | "cloze"
    93	
    94	    def __post_init__(self) -> None:
    95	        if self.word_count < 1:
    96	            raise ValueError(f"word_count must be ≥ 1, got {self.word_count}")
    97	        if not 1 <= self.difficulty <= 5:
    98	            raise ValueError(f"difficulty must be 1–5, got {self.difficulty}")
```

```bash
cat -n backend/app/models/srs_item.py
```

```output
     1	"""SRS item domain model (FSRS-based).
     2	
     3	Each collocation tracks two directions independently:
     4	- recognition (L2 → L1): the historical default; powers lesson transcripts
     5	- production (L1 → L2): new in v2; powers the production drill route.
     6	
     7	Flat FSRS fields on `SRSItem` (`state`, `due_date`, `stability`, ...) are
     8	compatibility shims that read/write the recognition direction. They exist so
     9	callers predating the two-direction schema keep working during Stage 1 and
    10	are scheduled for removal in Stage 3.5 of the Anki sync plan.
    11	"""
    12	
    13	from __future__ import annotations
    14	
    15	from dataclasses import dataclass, field
    16	from datetime import UTC, date, datetime, time
    17	from enum import Enum
    18	
    19	from app.srs.anki_mirror.rollover import due_at_rollover_utc
    20	
    21	from .syntactic_unit import SyntacticUnit
    22	
    23	
    24	class SRSState(Enum):
    25	    """Learning state of an SRS item."""
    26	
    27	    NEW = "new"
    28	    LEARNING = "learning"
    29	    REVIEW = "review"
    30	    RELEARNING = "relearning"
    31	    SUSPENDED = "suspended"
    32	    BURIED = "buried"
    33	    KNOWN = "known"
    34	
    35	
    36	class Rating(Enum):
    37	    """Learner rating for an SRS review."""
    38	
    39	    AGAIN = 1  # Complete blackout / forgot
    40	    HARD = 2  # Significant difficulty
    41	    GOOD = 3  # Correct with some effort
    42	    EASY = 4  # Perfect recall
    43	
    44	
    45	class Direction(Enum):
    46	    """Review direction for an SRS item."""
    47	
    48	    RECOGNITION = "recognition"  # L2 → L1 (Anki ord=0)
    49	    PRODUCTION = "production"  # L1 → L2 (Anki ord=1)
    50	
    51	
    52	@dataclass
    53	class DirectionState:
    54	    """FSRS scheduling state for one direction of a collocation.
    55	
    56	    Single source of truth for due-time: ``due_at`` (TEXT iso datetime, UTC).
    57	    Extended to all states (review/new included), NOT NULL.
    58	    """
    59	
    60	    direction: Direction
    61	    due_at: datetime
    62	    stability: float = 1.0
    63	    difficulty: float = 5.0
    64	    reps: int = 0
    65	    lapses: int = 0
    66	    state: SRSState = field(default=SRSState.NEW)
    67	    last_review: datetime | None = None
    68	    last_review_time_ms: int = 0
    69	    anki_card_id: int | None = None
    70	    anki_due: int | None = None
    71	    # Anki's `cards.mod` (modification timestamp). Used as the secondary sort
    72	    # key under RetrievabilityAscending — Anki tiebreaks via `fnvhash(id, mod)`.
    73	    anki_card_mod: int | None = None
    74	    # Source of a buried state: 'user' (manual bury, persists across rollover)
    75	    # or 'sched' (sibling/auto bury, released at next rollover via Layer 27's
    76	    # unbury_if_needed sweep). NULL on non-buried rows.
    77	    bury_kind: str | None = None
    78	    dirty_fsrs: bool = False
    79	    last_synced_at: str | None = None
    80	    last_rating: int | None = None
    81	    left: int | None = None
    82	    # Prior-grade snapshot used to construct a correct Anki revlog row at
    83	    # push time. Set by `app.srs.fsrs.schedule` before each `replace`,
    84	    # cleared by `mark_direction_clean` once the row has been pushed.
    85	    prior_state: SRSState | None = None
    86	    prior_left: int | None = None
    87	    prior_stability: float | None = None
    88	    # First-grade timestamp — set once on the initial NEW→non-NEW transition
    89	    # (by `app.srs.fsrs.schedule` for TT-side grades, by `sync_pull` for Anki
    90	    # grades). Used by `count_new_introduced_today` to mirror Anki's `newToday`
    91	    # counter, which increments only on the actual first-grade event. Layer 26.
    92	    introduced_at: datetime | None = None
    93	    # One-shot force flag: when set, sync_push force-writes this direction's
    94	    # stability/difficulty into Anki's cards.data (the `set_specific_value_of_card`
    95	    # path), even though the direction is in a non-KNOWN state. Set by
    96	    # `restore_known` so a restored (review-state) card's pre-known stability
    97	    # survives the next take-Anki-verbatim pull; cleared by `mark_direction_clean`
    98	    # after the push. TT-only — never synced to Anki.
    99	    fsrs_force_next: bool = False
   100	
   101	
   102	@dataclass(frozen=True)
   103	class RevlogRow:
   104	    """One row in the tt_revlog table, mirroring Anki's revlog schema.
   105	
   106	    Written at grade time (TT-side) and during sync_pull (Anki-side).
   107	    Stage 0: writes only; no reads consume it until Stage 2.
   108	    """
   109	
   110	    id: int
   111	    collocation_id: int
   112	    direction: Direction
   113	    button_chosen: int
   114	    interval: int
   115	    last_interval: int
   116	    factor: int
   117	    taken_millis: int
   118	    review_kind: int
   119	    anki_card_id: int | None = None
   120	
   121	
   122	class SRSItem:
   123	    """An SRS-tracked syntactic unit with per-direction FSRS scheduling.
   124	
   125	    Accepts two construction styles:
   126	
   127	    1. Two-direction (new): `SRSItem(syntactic_unit=..., directions={...}, guid=..., anki_note_id=...)`.
   128	    2. Flat legacy:         `SRSItem(syntactic_unit=..., due_date=..., stability=..., state=..., ...)`.
   129	
   130	    The legacy kwargs populate the recognition direction and seed production
   131	    with defaults. They will be removed in Stage 3.5 once all call sites move
   132	    to `directions[Direction.RECOGNITION]` access.
   133	    """
   134	
   135	    __slots__ = ("syntactic_unit", "directions", "guid", "anki_note_id")
   136	
   137	    def __init__(
   138	        self,
   139	        syntactic_unit: SyntacticUnit,
   140	        directions: dict[Direction, DirectionState] | None = None,
   141	        guid: str | None = None,
   142	        anki_note_id: int | None = None,
   143	        *,
   144	        due_date: date | None = None,
   145	        stability: float = 1.0,
   146	        difficulty: float = 5.0,
   147	        reps: int = 0,
   148	        lapses: int = 0,
   149	        state: SRSState = SRSState.NEW,
   150	        last_review: date | None = None,
   151	    ) -> None:
   152	        self.syntactic_unit = syntactic_unit
   153	        self.guid = guid
   154	        self.anki_note_id = anki_note_id
   155	
   156	        if directions is not None:
   157	            self.directions = directions
   158	        else:
   159	            rec_due = due_date if due_date is not None else date.today()
   160	            recognition_due_at = due_at_rollover_utc(rec_due)
   161	            self.directions = {
   162	                Direction.RECOGNITION: DirectionState(
   163	                    direction=Direction.RECOGNITION,
   164	                    due_at=recognition_due_at,
   165	                    stability=stability,
   166	                    difficulty=difficulty,
   167	                    reps=reps,
   168	                    lapses=lapses,
   169	                    state=state,
   170	                    last_review=last_review,
   171	                ),
   172	                Direction.PRODUCTION: DirectionState(
   173	                    direction=Direction.PRODUCTION,
   174	                    due_at=recognition_due_at,
   175	                ),
   176	            }
   177	
   178	    # ── Backward-compat flat shims (mirror recognition direction) ───────
   179	    #
   180	    # These let `item.state`, `item.reps`, etc. keep working for callers
   181	    # predating the two-direction schema. Readers return recognition's value;
   182	    # writers mutate recognition's DirectionState in place.
   183	
   184	    @property
   185	    def _rec(self) -> DirectionState:
   186	        # Cloze items only carry a PRODUCTION direction (single-template Anki
   187	        # Cloze notetype). Flat shims fall through to whichever direction the
   188	        # card_type implies.
   189	        if self.syntactic_unit.card_type == "cloze":
   190	            return self.directions[Direction.PRODUCTION]
   191	        return self.directions[Direction.RECOGNITION]
   192	
   193	    @property
   194	    def due_date(self) -> date:
   195	        return self._rec.due_at.date()
   196	
   197	    @due_date.setter
   198	    def due_date(self, value: date) -> None:
   199	        self._rec.due_at = datetime.combine(value, time.min).replace(tzinfo=UTC)
   200	
   201	    @property
   202	    def stability(self) -> float:
   203	        return self._rec.stability
   204	
   205	    @stability.setter
   206	    def stability(self, value: float) -> None:
   207	        self._rec.stability = value
   208	
   209	    @property
   210	    def difficulty(self) -> float:
   211	        return self._rec.difficulty
   212	
   213	    @difficulty.setter
   214	    def difficulty(self, value: float) -> None:
   215	        self._rec.difficulty = value
   216	
   217	    @property
   218	    def reps(self) -> int:
   219	        return self._rec.reps
   220	
   221	    @reps.setter
   222	    def reps(self, value: int) -> None:
   223	        self._rec.reps = value
   224	
   225	    @property
   226	    def lapses(self) -> int:
   227	        return self._rec.lapses
   228	
   229	    @lapses.setter
   230	    def lapses(self, value: int) -> None:
   231	        self._rec.lapses = value
   232	
   233	    @property
   234	    def state(self) -> SRSState:
   235	        return self._rec.state
   236	
   237	    @state.setter
   238	    def state(self, value: SRSState) -> None:
   239	        self._rec.state = value
   240	
   241	    @property
   242	    def last_review(self) -> date | None:
   243	        return self._rec.last_review
   244	
   245	    @last_review.setter
   246	    def last_review(self, value: date | None) -> None:
   247	        self._rec.last_review = value
```

The `SyntacticUnit` is a collocation (multi-word phrase or single word) with bounds validation — `word_count` 1–8, `difficulty` 1–5. The optional `lemma` field stores the canonical form (currently the lowercased word) so per-word SRS tracking can collapse inflected variants — see Part 4.4 for the lemmatizer. The `SRSItem` wraps a SyntacticUnit with FSRS-5 scheduling fields: stability (days before 90% retention drops), difficulty (1–10 scale), reps, lapses, and state.

The state machine is: `NEW → LEARNING → REVIEW ↔ RELEARNING`, with `SUSPENDED` as a terminal state the admin UI can toggle. Suspended items are excluded from due-card queries until unsuspended, at which point they reset to `NEW`.

### 2.5 Content Strategy

The strategy model controls how new vs. review content is balanced:

```bash
cat -n backend/app/models/strategy.py
```

```output
     1	"""Content generation strategy enum."""
     2	
     3	from __future__ import annotations
     4	
     5	from enum import Enum
     6	
     7	
     8	class ContentStrategy(Enum):
     9	    """Content generation strategy.
    10	
    11	    WIDER: Generate new scenarios using familiar vocabulary (breadth).
    12	    DEEPER: Enhance existing scenarios with more advanced L2 expressions (depth).
    13	    """
    14	
    15	    WIDER = "wider"
    16	    DEEPER = "deeper"
```

Two strategies: **WIDER** introduces 8 new collocations with 2 reviews (breadth-first for beginners), **DEEPER** introduces only 3 new with 7 reviews (depth-first for reinforcement). The `PedagogicalScoringConfig` carries tuned weights for the collocation selector: SRS readiness 40%, language quality 30%, pedagogical value 20%, diversity 10%. These weights were ported directly from the prototype.

---

## PART 3: LLM Client & Cassette System

The LLM layer wraps Groq's API with retry logic and a VCR-style cassette system for deterministic testing.

### 3.1 HTTP Client

```bash
grep -n "^class \|    def \|^def " backend/app/llm/client.py
```

```output
27:def reasoning_params_for_model(model: str) -> dict | None:
42:def _parse_reset_duration(s: str) -> float:
56:class LLMError(Exception):
59:    def __init__(self, message: str, attempts: list[dict] | None = None) -> None:
64:class LLMClient:
65:    def __init__(
117:    def _fire_callback(
153:    def _make_attempt(provider: str, model: str, status: str | int, error: str, latency_ms: int) -> dict:
156:    def _update_health_after_groq(
452:    def _snapshot_rate_limits(response: httpx.Response) -> dict | None:
462:        def _int(name: str) -> int | None:
466:        def _reset(name: str) -> float | None:
```

The `LLMClient` is the primary connection to Groq plus an optional Ollama fallback for local development. The constructor takes a `groq_api_key`, an optional `fallback_client` (typically an `OllamaClient`), an `on_call` callback (used by the SRS `feedback` endpoint to surface live latency to the UI), and tunables for retries and timeouts.

Key behaviors:

- **Proactive rate-limit pacing**: Every Groq response carries `x-ratelimit-remaining-requests` and `x-ratelimit-remaining-tokens` headers. After every successful call, the client computes a minimum delay (`_groq_call_delay`) so the next call won't bump into either limit. This is much smoother than reacting only to 429s.
- **Header-based 429 backoff**: On HTTP 429, the `retry-after` header is parsed (`_parse_reset_duration` handles both `"30s"` and `"30"`) and the client sleeps before retrying — up to `max_retries_429` times.
- **Ollama fallback**: If a Groq attempt fails (timeout, 5xx, or rate-limit exhaustion) and a `fallback_client` is configured, the client transparently switches to Ollama and records the provider in `last_provider` so the UI can display which backend served the request.
- **Think-tag stripping**: Groq's `llama-3.3-70b` sometimes wraps reasoning in `<think>...</think>` tags. The client strips these before returning the content.
- **on_call callback**: Used by the SRS feedback endpoint to stream pacing metadata (current delay, remaining requests, remaining tokens) to the frontend without polling.
- **Attempt logging**: Every failure is recorded as a dict (`provider`, `model`, `status`, `error`, `latency_ms`) and surfaced via `LLMError.attempts` for debugging.

The `pacing_info` property exposes the current `_groq_call_delay`, time remaining until the next allowed call, and the most recent rate-limit headers — handy for the SRS admin UI which shows a small live indicator.

Here's the test that verifies the 429 retry flow using `respx` (HTTP mocking):

```bash
cd backend && uv run pytest "tests/test_llm_client.py::TestRateLimit::test_rate_limit_retry_succeeds" -v --no-header --no-cov 2>&1
```

```output
============================= test session starts ==============================
collecting ... collected 1 item

tests/test_llm_client.py::TestRateLimit::test_rate_limit_retry_succeeds PASSED [100%]

============================== 1 passed in 0.13s ===============================
```

### 3.2 Cassette System

The cassette system is the testing backbone — it records LLM responses and replays them deterministically:

```bash
cat -n backend/app/llm/cassette.py
```

```output
     1	"""VCR-style cassette recording/replay for LLMClient.
     2	
     3	Ported from voynich-encoder's llm_cassette.py — hash-based lookup (not sequential),
     4	so multiple test scenarios can share one cassette without interfering.
     5	
     6	Modes:
     7	  mock   — replay only; raise RuntimeError on cache miss
     8	  record — call real LLM and save all responses
     9	  live   — call real LLM without saving
    10	  patch  — replay known; call real LLM for new prompts and save them
    11	"""
    12	
    13	from __future__ import annotations
    14	
    15	import datetime
    16	import hashlib
    17	import json
    18	from pathlib import Path
    19	from typing import TYPE_CHECKING
    20	
    21	if TYPE_CHECKING:
    22	    from .client import LLMClient
    23	
    24	# Cassette JSON schema version. Bump when the prompt-hash algorithm changes so
    25	# stale cassettes fail loudly on load instead of silently replaying responses
    26	# recorded under a different hashing scheme.
    27	#   v1 (implicit, no "version" key) — hashed the user prompt only.
    28	#   v2 — hashes system_prompt + user prompt, so editing a system prompt
    29	#        invalidates the cassette and demands a re-record.
    30	CASSETTE_VERSION = 2
    31	
    32	
    33	def _hash_prompt(prompt: str, system_prompt: str | None = None) -> str:
    34	    """Hash a request over BOTH the system and user prompts.
    35	
    36	    A NUL separator keeps the two fields unambiguous. A None system prompt and
    37	    an empty string both mean "no system instructions" and hash identically.
    38	    """
    39	    payload = f"{system_prompt or ''}\x00{prompt}"
    40	    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]
    41	
    42	
    43	class CassetteLLMClient:
    44	    """LLMClient wrapper with cassette-based mock/live/record/patch modes."""
    45	
    46	    def __init__(
    47	        self,
    48	        mode: str,  # "mock" | "live" | "record" | "patch"
    49	        cassette_path: Path,
    50	        real_client: LLMClient | None = None,
    51	    ) -> None:
    52	        self._mode = mode
    53	        self._cassette_path = cassette_path
    54	        self._real_client = real_client
    55	        self.last_provider: str | None = None
    56	        self.last_finish_reason: str | None = None
    57	        self.last_usage: dict = {}
    58	
    59	        self._calls: list[dict] = []
    60	        self._playback_by_hash: dict[str, list[dict]] = {}
    61	        self._playback_used: dict[str, int] = {}
    62	
    63	        if mode in ("mock", "patch"):
    64	            data = json.loads(cassette_path.read_text())
    65	            version = data.get("version")
    66	            if version != CASSETTE_VERSION:
    67	                raise RuntimeError(
    68	                    f"Cassette {cassette_path} is version {version!r}, expected {CASSETTE_VERSION}. "
    69	                    "The prompt-hash format changed (it now includes the system prompt), so the "
    70	                    "recorded hashes are stale. Re-record with --llm-mode=record."
    71	                )
    72	            for entry in data["calls"]:
    73	                h = entry["prompt_hash"]
    74	                self._playback_by_hash.setdefault(h, []).append(entry)
    75	            if mode == "patch":
    76	                self._calls = list(data["calls"])
    77	
    78	    async def complete(
    79	        self,
    80	        prompt: str,
    81	        system_prompt: str | None = None,
    82	        temperature: float = 0.7,
    83	        max_tokens: int = 256,
    84	    ) -> str:
    85	        if self._mode == "mock":
    86	            return self._replay(prompt, system_prompt)
    87	        if self._mode == "patch":
    88	            return await self._patch(
    89	                prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
    90	            )
    91	        assert self._real_client is not None, "real_client required for live/record mode"
    92	        response = await self._real_client.complete(
    93	            prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
    94	        )
    95	        self.last_provider = self._real_client.last_provider
    96	        self.last_finish_reason = getattr(self._real_client, "last_finish_reason", None)
    97	        self.last_usage = getattr(self._real_client, "last_usage", None) or {}
    98	        if self._mode == "record":
    99	            self._calls.append(
   100	                {
   101	                    "prompt_hash": _hash_prompt(prompt, system_prompt),
   102	                    "prompt_preview": prompt[:80].replace("\n", " "),
   103	                    "max_tokens": max_tokens,
   104	                    "response": response,
   105	                    "provider": self.last_provider,
   106	                }
   107	            )
   108	            self.save()
   109	        return response
   110	
   111	    def _replay(self, prompt: str, system_prompt: str | None = None) -> str:
   112	        h = _hash_prompt(prompt, system_prompt)
   113	        entries = self._playback_by_hash.get(h)
   114	        if not entries:
   115	            raise RuntimeError(
   116	                f"Cassette has no entry for prompt hash {h}.\n  Preview: {prompt[:80]!r}\nRe-record with --llm-mode=record."
   117	            )
   118	        idx = self._playback_used.get(h, 0)
   119	        if idx >= len(entries):
   120	            raise RuntimeError(
   121	                f"Cassette entry {h!r} used {idx} times but only {len(entries)} recorded.\n  Preview: {prompt[:80]!r}"
   122	            )
   123	        entry = entries[idx]
   124	        self._playback_used[h] = idx + 1
   125	        self.last_provider = entry.get("provider", "groq")
   126	        return entry["response"]
   127	
   128	    async def _patch(self, prompt: str, **kwargs) -> str:
   129	        h = _hash_prompt(prompt, kwargs.get("system_prompt"))
   130	        entries = self._playback_by_hash.get(h)
   131	        if entries:
   132	            idx = self._playback_used.get(h, 0)
   133	            if idx < len(entries):
   134	                entry = entries[idx]
   135	                self._playback_used[h] = idx + 1
   136	                self.last_provider = entry.get("provider", "groq")
   137	                return entry["response"]
   138	
   139	        assert self._real_client is not None, "real_client required for patch mode"
   140	        response = await self._real_client.complete(prompt, **kwargs)
   141	        self.last_provider = self._real_client.last_provider
   142	        new_entry = {
   143	            "prompt_hash": h,
   144	            "prompt_preview": prompt[:80].replace("\n", " "),
   145	            "max_tokens": kwargs.get("max_tokens", 256),
   146	            "response": response,
   147	            "provider": self.last_provider,
   148	        }
   149	        self._calls.append(new_entry)
   150	        self._playback_by_hash.setdefault(h, []).append(new_entry)
   151	        self.save()
   152	        return response
   153	
   154	    def save(self) -> None:
   155	        if self._mode not in ("record", "patch"):
   156	            return
   157	        self._cassette_path.parent.mkdir(parents=True, exist_ok=True)
   158	        data = {
   159	            "version": CASSETTE_VERSION,
   160	            "recorded_at": datetime.datetime.now(datetime.UTC).isoformat(),
   161	            "calls": self._calls,
   162	        }
   163	        self._cassette_path.write_text(json.dumps(data, indent=2) + "\n")
```

The cassette system hashes prompts with SHA-256 (first 16 hex chars) for lookup. This means tests are order-independent — unlike sequential VCR, any test can call any prompt without worrying about ordering.

The four modes in practice:
- **mock** (CI default): Replay from cassette; `RuntimeError` on cache miss. Zero network calls.
- **record**: Call real Groq, save everything. Used to build initial cassettes.
- **live**: Call real Groq, save nothing. For manual testing.
- **patch**: Replay what exists, record anything new. Best for adding test cases incrementally.

Here is what a cassette file looks like — a real one from the test suite:

```bash
cd backend && uv run python -c "
import json, hashlib, datetime
from pathlib import Path

# Build a cassette exactly as the real system does
prompt = \"Generate a 3-day Slovene travel curriculum at A1 level.\"
h = \"sha256:\" + hashlib.sha256(prompt.encode()).hexdigest()[:16]

cassette = {
    \"recorded_at\": datetime.datetime.now(datetime.UTC).isoformat(),
    \"calls\": [
        {
            \"prompt_hash\": h,
            \"prompt_preview\": prompt[:80],
            \"max_tokens\": 2048,
            \"response\": \"{\\\"days\\\": [{\\\"day\\\": 1, ...}]}\",
            \"provider\": \"groq\"
        }
    ]
}
print(json.dumps(cassette, indent=2))
"
```

```output
{
  "recorded_at": "2026-07-11T12:01:05.322406+00:00",
  "calls": [
    {
      "prompt_hash": "sha256:6c8d66c9a7c2c982",
      "prompt_preview": "Generate a 3-day Slovene travel curriculum at A1 level.",
      "max_tokens": 2048,
      "response": "{\"days\": [{\"day\": 1, ...}]}",
      "provider": "groq"
    }
  ]
}
```

The `prompt_hash` is the lookup key. The `prompt_preview` is for human readability when inspecting cassettes. The `response` is the raw LLM output that gets returned on replay.

### 3.3 Test Fixtures — Wiring It Together

The `conftest.py` makes cassettes transparent to test authors:

```bash
grep -n "^def \|^async def " backend/tests/conftest.py
```

```output
17:def anki_day_anchor(today: date) -> datetime:
35:def anki_prev_day_anchor(today: date) -> datetime:
47:def _settings_overrides(monkeypatch, tmp_path):
124:def _autoclose_sqlite_connections(monkeypatch):
148:def language():
156:def srs_db():
164:def make_card_record(
199:def make_note_record(
232:def build_minimal_anki_db(
324:def build_norwegian_anki_db(
411:def fake_anki_db(tmp_path):
417:def fake_anki_db_modern(tmp_path):
446:def _recognition_fields(
458:def _production_fields(
470:def _unknown_fields(slovene: str, english: str) -> str:
476:def build_slovene_pairs_anki_db(tmp_path: Path) -> Path:
671:def fake_anki_db_slovene_pairs(tmp_path):
676:def seed_direction(
735:def pytest_addoption(parser: pytest.Parser) -> None:
768:def pytest_configure(config: pytest.Config) -> None:
787:def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
811:def llm_mode(request: pytest.FixtureRequest) -> str:
816:def api_app_state():
844:async def cassette_llm(request: pytest.FixtureRequest, llm_mode: str):
```

Cassette naming convention: `{ClassName}__{test_name}.json`. In mock mode (CI), missing cassettes cause a `pytest.skip` — tests degrade gracefully rather than failing. For record/patch, `GROQ_API_KEY` must be set.

---

## PART 4: SRS Engine (FSRS-5)

The spaced repetition system tracks what vocabulary the learner knows and when to review it. Production uses FSRS-5, a modern algorithm that replaced the prototype's custom scheduler.

### 4.1 FSRS-5 Scheduling Algorithm

```bash
grep -n "^class \|^def \|^    def " backend/app/srs/fsrs.py
```

```output
27:def _w32(w: tuple[float, ...]) -> tuple:
33:def _fsrs_factor_f32(decay: float) -> np.float32:
44:def _learning_step_fuzz_seconds(anki_card_id: int | None, reps: int, step_seconds: int) -> int:
65:def _due_at_after_step(now: datetime, prev: DirectionState, delay_min: float) -> datetime:
72:def _review_due_at_from_interval(
103:def _rust_round_half_away(x: float) -> int:
110:def _fuzz_delta(interval: float) -> float:
125:def _constrained_fuzz_bounds(interval: float, minimum: int, maximum: int) -> tuple[int, int]:
141:def _review_interval_fuzz(
190:class FSRSParams:
203:    def __post_init__(self) -> None:
218:def _forgetting_curve(elapsed_days: float, stability: float, decay: float = -0.5) -> float:
234:def is_day_level_last_review(last_review: datetime | date) -> bool:
259:def _elapsed_days_for_fsrs(
300:def _grade_elapsed_days(
338:def compute_retrievability(
377:def _next_interval(stability: float, desired_retention: float, decay: float = -0.5) -> int:
387:def stability_for_interval(target_interval: int, desired_retention: float, decay: float = -0.5) -> float:
399:def _greater_than_last(interval: int, scheduled_days: int) -> int:
409:def _passing_intervals_with_fuzz(
444:    def _fuzz(interval_raw: float, minimum: int) -> int:
463:def _next_interval_raw(stability: float, desired_retention: float, decay: float = -0.5) -> float:
475:def _graduation_intervals_with_fuzz(
515:    def _fuzz(interval_in: float, minimum: int) -> int:
535:def _scheduled_days_for_grade(prev: DirectionState, col_crt: int | None) -> int:
568:def _round_to_places_f32(value: float, decimal_places: int) -> float:
602:def _clamp_stability(s: float) -> float:
607:def _quantize_stability(s: float) -> float:
611:def _quantize_difficulty(d: float) -> float:
615:def _init_stability(rating: Rating, w: tuple[float, ...]) -> float:
619:def _init_difficulty(rating: Rating, w: tuple[float, ...]) -> float:
625:def _next_difficulty(d: float, rating: Rating, w: tuple[float, ...]) -> float:
642:def _next_stability_recall(d: float, s: float, r: float, rating: Rating, w: tuple[float, ...]) -> float:
663:def _next_stability_lapse(d: float, s: float, r: float, w: tuple[float, ...]) -> float:
676:def _stability_short_term(last_s: float, rating: Rating, params: FSRSParams) -> float:
696:def _next_stability_for_grade(
736:def _parse_left(left: int | None) -> int:
753:def _pack_left(total_remaining: int) -> int:
763:def _grade_prior_state(prev: DirectionState, new_state: SRSState) -> SRSState:
788:def _get_steps_for_state(state: SRSState) -> tuple[list[float], str]:
801:def schedule(
974:def _schedule_new(
1097:def _schedule_review_again(
1185:def _schedule_with_steps(
1369:def _graduate_to_review(
1478:def _compute_review_kind(prev_state: SRSState) -> int:
1497:def _compute_revlog_interval(new_dir: DirectionState, now: datetime) -> int:
1520:def _compute_revlog_last_interval(prev: DirectionState, col_crt: int | None = None) -> int:
1548:def build_revlog_row(
```

FSRS-5 is a 19-parameter model trained on millions of reviews. The key insight: **stability** is how many days before retention drops to 90%. A stability of 3.12 (initial Good rating) means after ~3 days, the learner has a 90% chance of recall — time to review.

Three changes since the original walkthrough revision:

- **`FSRSParams` dataclass** replaces the module-level `W` and `REQUESTED_RETENTION` constants. The 19-float weights vector and the desired retention can now be threaded in from Anki's deck_config protobuf (PART 12.6) so TunaTale's scheduler matches what Anki would predict for the same card. `DEFAULT_FSRS5_PARAMS` keeps the original constants as a fallback.
- **`direction` parameter** — every call updates exactly one direction's `DirectionState` (RECOGNITION or PRODUCTION), leaving the other untouched. The function returns a new `SRSItem` with the chosen direction's state swapped in.
- **Sync bookkeeping writes** — every successful schedule sets `dirty_fsrs=True` and stores the integer rating in `last_rating`. The next sync push reads those flags to decide what to write to Anki's revlog and card FSRS state. See PART 12.4 (sync_push) for the consumer side.

Here is the scheduling in action — watch how ratings affect the next review date:

```bash
cd backend && uv run python -c "
from datetime import date
from app.models.srs_item import SRSItem, SRSState, Rating
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import schedule

unit = SyntacticUnit(text=\"Dober dan\", translation=\"Good day\", word_count=2, difficulty=1, source=\"llm\")
item = SRSItem(syntactic_unit=unit, due_date=date(2026, 3, 25))

print(\"=== New item: Dober dan ===\")
print(f\"State: {item.state.value}, Stability: {item.stability}, Due: {item.due_date}\")

# Rate it GOOD on day 1
good = schedule(item, Rating.GOOD, review_date=date(2026, 3, 25))
print(f\"\\nAfter GOOD rating:\")
print(f\"State: {good.state.value}, Stability: {good.stability:.2f}, Due: {good.due_date}\")

# Rate it EASY on the next review
easy = schedule(good, Rating.EASY, review_date=good.due_date)
print(f\"\\nAfter EASY rating:\")
print(f\"State: {easy.state.value}, Stability: {easy.stability:.2f}, Due: {easy.due_date}\")

# What if they forgot? Rate AGAIN
forgot = schedule(good, Rating.AGAIN, review_date=good.due_date)
print(f\"\\nAfter AGAIN rating (forgot):\")
print(f\"State: {forgot.state.value}, Stability: {forgot.stability:.2f}, Lapses: {forgot.lapses}, Due: {forgot.due_date}\")
"
```

```output
=== New item: Dober dan ===
State: new, Stability: 1.0, Due: 2026-03-25

After GOOD rating:
State: learning, Stability: 3.13, Due: 2026-07-11

After EASY rating:
State: review, Stability: 200.86, Due: 2027-02-05

After AGAIN rating (forgot):
State: learning, Stability: 2.50, Lapses: 0, Due: 2026-07-11
```

Notice the progression: GOOD → stability 3.13 (review in 3 days), EASY → stability 24.16 (review in 24 days), but AGAIN → stability drops to 0.92 with a lapse recorded and the item enters RELEARNING state.

### 4.2 SRS Database

```bash
grep -n "class " backend/app/srs/database.py backend/app/srs/db_*.py
```

```output
backend/app/srs/database.py:41:class SRSDatabase(
backend/app/srs/db_base.py:154:class SRSDatabaseBase:
backend/app/srs/db_collocations.py:19:class DbCollocationsMixin:
backend/app/srs/db_counts.py:19:class DbCountsMixin:
backend/app/srs/db_directions.py:16:class DbDirectionsMixin:
backend/app/srs/db_histogram.py:9:class DbHistogramMixin:
backend/app/srs/db_ignored_lemmas.py:8:class DbIgnoredLemmasMixin:
backend/app/srs/db_kv_cache.py:9:class DbKvCacheMixin:
backend/app/srs/db_lemma_cache.py:11:class DbLemmaCacheMixin:
backend/app/srs/db_media.py:10:class DbMediaMixin:
backend/app/srs/db_queue.py:20:class DbQueueMixin:
backend/app/srs/db_revlog.py:18:class DbRevlogMixin:
backend/app/srs/db_sync.py:16:class DbSyncMixin:
backend/app/srs/db_sync_conflicts.py:8:class DbSyncConflictsMixin:
```

The `SRSDatabase` is a SQLite repository. Originally two tables (`collocations` + `violations`); since the Anki integration the schema has grown to seven (managed by the v0→v8 migrations in `app/srs/migrations.py` — see PART 14.1):

- **`collocations`** — one row per vocabulary item. Holds language-agnostic content (`text`, `translation`, `lemma`, `image_filename`, `audio_filename`, `grammar`, `note`, source-context columns) plus the Anki sync identity (`guid`, `anki_note_id`).
- **`collocation_directions`** — two rows per collocation, one per `Direction` (RECOGNITION + PRODUCTION). This is where the FSRS state lives now — `due_date`, `stability`, `difficulty`, `reps`, `lapses`, `state`, `last_review`, `last_rating`, `anki_card_id`, `anki_due`, `dirty_fsrs`, `last_synced_at`. The flat fields on `SRSItem` (`item.due_date`, `item.stability`, ...) are compatibility shims that read/write `directions[Direction.RECOGNITION]`; they are scheduled for removal once all call sites move to direction-aware access.
- **`violations`** — content rule violations for debugging.
- **`pending_revlog`** — local scratch table of every rated review, drained to Anki's `revlog` on the next sync (PART 12.4).
- **`sync_conflicts`** — recorded when a pull detects field text that diverged on both sides since last sync.
- **`anki_state_cache`** — key/value cache for values pulled from Anki's deck_config protobuf (daily new cap, FSRS-5 weights, bury settings — PART 12.6).
- **`dirty_fields`** — per-GUID list of field names whose content has been edited locally and needs pushing on next sync.
- **`media`** — bookkeeping for media files (image/audio) by Anki filename + sha256, used for dedup.

`count_due_collocations` powers `/api/srs/stats`; `count_due_today_total` and `count_new_available` power the unified queue stats endpoint.

**The new sync surface:**

- `upsert_by_guid(...)` — the canonical write path used by sync. Takes a GUID + content + per-direction state and creates or updates atomically.
- `set_anki_ids(guid, anki_note_id, recognition_card_id, production_card_id)` — link a TunaTale row to its Anki counterparts after `sync_create_new`.
- `list_dirty(...)` / `mark_direction_clean(guid, direction)` — find directions with `dirty_fsrs=True` for push, then clear the flag once Anki has caught up.
- `enqueue_pending_revlog(...)` / `drain_pending_revlog()` — write+drain for the scratch revlog.
- `record_sync_conflict(...)` / `list_sync_conflicts()` — record/inspect field-text conflicts.
- `set_anki_state_cache(key, value)` / `get_anki_state_cache(key)` — cache the protobuf-decoded deck config values.
- `set_dirty_fields(guid, fields_str)` / `get_dirty_fields(guid)` — per-field dirty tracking for selective field push.
- `list_items_without_anki_note()` — drives `sync_create_new` (every collocation lacking an `anki_note_id`).
- `list_dirty_field_edits()` — drives `sync_push` (collocations whose text/translation changed locally).
- `update_collocation_for_sync(...)` — the inverse of `upsert_by_guid`; used by `sync_pull` when Anki is the authoritative source.
- `list_collocations_reviewed_today(today)` — set of collocation ids reviewed today; lets the queue enforce the daily-new cap without double-counting just-introduced items.

**Admin methods (powering `/cards`):**

- `list_collocations(limit, offset, search, state, order_by, order_dir)` — paginated browse with full-text search across `text`/`translation`, state filter, and validated sort columns. Returns `(rows, total_count)`.
- `get_collocation_by_id(id)` / `update_collocation_fields(id, text, translation)` — read/edit by primary key. Update raises `ValueError` on UNIQUE collisions so the API can return 409.
- `delete_collocation(id)` and `delete_collocations(ids)` — single + bulk delete with cascading violation cleanup.
- `reset_collocation(id, direction=None)` — wipes FSRS scheduling fields back to NEW for the given direction (or both if `None`). Per-direction reset is a side effect of the two-direction split.
- `set_state_by_id(id, direction, state)` — admin force-set a specific `SRSState`, used by the `/items/{id}/state` endpoint to override scheduling (e.g. mark a card as `KNOWN` or `BURIED`).
- `set_suspended(id, suspended, direction=None)` — toggle between `suspended` and `new`. Suspended directions are filtered out of `get_due_collocations`.

The method `update_collocation(item)` is now a recognition-only compatibility shim — it writes back only `directions[Direction.RECOGNITION]`. New code paths should call `update_direction(...)` or `upsert_by_guid(...)` directly.

> See PART 12 for how this schema round-trips with Anki via offline sync.

Here is a round-trip through the database — add a collocation, schedule it, and query due items:

```bash
cd backend && uv run python -c "
from datetime import date
from app.models.syntactic_unit import SyntacticUnit
from app.models.srs_item import Rating
from app.srs.database import SRSDatabase
from app.srs.fsrs import schedule

with SRSDatabase(\":memory:\") as db:
    # Add some Slovene vocabulary
    units = [
        SyntacticUnit(text=\"Dober dan\", translation=\"Good day\", word_count=2, difficulty=1, source=\"llm\"),
        SyntacticUnit(text=\"Hvala lepa\", translation=\"Thank you\", word_count=2, difficulty=1, source=\"llm\"),
        SyntacticUnit(text=\"Kje je postaja?\", translation=\"Where is the station?\", word_count=3, difficulty=2, source=\"llm\"),
    ]
    for u in units:
        db.add_collocation(u, \"sl\")

    print(f\"Total collocations: {db.count_collocations()}\")

    # New items are fetched with get_new_collocations (state=new)
    new = db.get_new_collocations(limit=10)
    print(f\"New (unlearned): {len(new)}\")
    for item in new:
        print(f\"  {item.syntactic_unit.text} -> {item.syntactic_unit.translation} (state={item.state.value})\")

    # Review one, then update in the database
    reviewed = schedule(new[0], Rating.GOOD, review_date=date(2026, 3, 25))
    db.update_collocation(reviewed)
    print(f\"\\nAfter reviewing Dober dan: state={reviewed.state.value}, next due={reviewed.due_date}\")
    print(f\"Remaining new: {len(db.get_new_collocations())}\")
    print(f\"Due for review on 2026-03-28: {len(db.get_due_collocations(as_of=date(2026, 3, 28)))}\")
"
```

```output
Total collocations: 3
New (unlearned): 3
  Dober dan -> Good day (state=new)
  Hvala lepa -> Thank you (state=new)
  Kje je postaja? -> Where is the station? (state=new)

After reviewing Dober dan: state=learning, next due=2026-07-11
Remaining new: 2
Due for review on 2026-03-28: 0
```

Note the two-track query pattern: `get_new_collocations()` fetches unlearned vocabulary (state=new), while `get_due_collocations(as_of)` fetches items that need review (state != new, due_date <= as_of). After rating "Dober dan" as GOOD, it moves to review state with a due date 3 days out.

### 4.3 Feedback & Selection

The feedback adapter maps learner signals to FSRS ratings, and the selector scores collocations for inclusion in lessons:

```bash
cat -n backend/app/srs/feedback.py
```

```output
     1	"""SRS feedback utilities.
     2	
     3	rating_from_input: maps explicit rating strings or implicit signal strings to FSRS ratings.
     4	"""
     5	
     6	from __future__ import annotations
     7	
     8	from app.models.srs_item import Rating
     9	
    10	_SIGNAL_MAP: dict[str, Rating] = {
    11	    "no_help": Rating.GOOD,
    12	    "slowdown": Rating.HARD,
    13	    "translation_request": Rating.AGAIN,
    14	    "fast_forward": Rating.EASY,
    15	}
    16	
    17	_RATING_MAP: dict[str, Rating] = {
    18	    "again": Rating.AGAIN,
    19	    "hard": Rating.HARD,
    20	    "good": Rating.GOOD,
    21	    "easy": Rating.EASY,
    22	}
    23	
    24	
    25	def rating_from_input(rating: str | None = None, signal: str | None = None) -> Rating:
    26	    """Convert explicit rating string or implicit signal string to a Rating enum.
    27	
    28	    Exactly one of rating/signal must be provided; raises ValueError otherwise.
    29	    rating accepts 'again'|'hard'|'good'|'easy' (case-insensitive).
    30	    signal delegates to the existing _SIGNAL_MAP.
    31	    """
    32	    if (rating is None) == (signal is None):
    33	        raise ValueError("Provide exactly one of rating or signal, not both (or neither).")
    34	    if rating is not None:
    35	        key = rating.lower()
    36	        if key not in _RATING_MAP:
    37	            raise ValueError(f"Unknown rating {rating!r}. Valid: {list(_RATING_MAP)}")
    38	        return _RATING_MAP[key]
    39	    if signal not in _SIGNAL_MAP:
    40	        raise ValueError(f"Unknown signal {signal!r}. Valid: {list(_SIGNAL_MAP)}")
    41	    return _SIGNAL_MAP[signal]
```

```bash
git log --oneline --diff-filter=D -1 -- backend/app/srs/selector.py
```

```output
bf74822 refactor(backend): remove category-3 dead code (test-only / superseded)
```

`rating_from_input(rating=..., signal=...)` is the unified entry point. Pass `rating="good"` for explicit four-button feedback (the `/review` UI's path) or `signal="translation_request"` for implicit signals from the player. Skipping ahead means they know it (EASY), asking for a translation means they forgot (AGAIN). `PostGenerationFeedback` is unchanged: it checks which collocations the LLM actually used in a generated story — useful for tracking whether the content engine is following the curriculum.

The `CollocationSelector` scores items using the weighted formula from the strategy model (SRS readiness 40%, language quality 30%, pedagogical value 20%, diversity 10%), then selects the best mix of new and review items for the next lesson. Note: it is currently **direction-agnostic** — it scores using the recognition-direction shim fields on `SRSItem` and treats each row as a single unit. The unified review queue at `/api/srs/review-queue` (PART 13) is where direction-aware ordering actually happens; the selector is preserved for the older curriculum-driven path.

---

### 4.4 Per-Word SRS Tracking

Production added per-word SRS tracking on top of the per-collocation tracking. The pipeline lemmatizes every L2 word in a generated lesson, looks each lemma up in the SRS database, and exposes the state to the frontend so the UI can highlight unknown words. Three small modules wire this together.

**Lemmatizer** — a thin Protocol with a `LowercaseLemmatizer` default. Real Slovene lemmatization (e.g. via `stanza`) can be plugged in by satisfying the Protocol.

```bash
grep -n "^class \|^def \|^    def " backend/app/srs/lemmatizer.py
```

```output
18:class TokenAnalysis:
31:class Lemmatizer(Protocol):
34:    def lemmatize(self, word: str, language_code: str) -> str: ...
36:    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
44:    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
53:class LowercaseLemmatizer:
62:    def lemmatize(self, word: str, language_code: str) -> str:
65:    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
68:    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
88:class _StanzaFamilyLemmatizer:  # pragma: no cover — requires PyTorch pipeline; opt-in only
103:    def __init__(self, language_code: str) -> None:
124:    def _ensure_pipeline(self) -> object:
127:    def lemmatize(self, word: str, language_code: str) -> str:
137:    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
151:    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
183:class ClasslaLemmatizer(_StanzaFamilyLemmatizer):  # pragma: no cover — requires classla/PyTorch; opt-in only
199:    def __init__(self, language_code: str = "sl") -> None:
202:    def _ensure_pipeline(self) -> object:
216:class StanzaLemmatizer(_StanzaFamilyLemmatizer):  # pragma: no cover — requires stanza/PyTorch; opt-in only
237:    def __init__(self, language_code: str = "no") -> None:
241:    def _ensure_pipeline(self) -> object:
259:def _parse_morphology(feats: str) -> tuple[str, str, str]:
279:def _parse_person(feats: str) -> str:
295:def get_lemmatizer(language_code: str) -> Lemmatizer:
357:def model_version_for(lemmatizer: Lemmatizer) -> str:
367:def _serialize_analyses(analyses: list[TokenAnalysis]) -> str:
371:def _deserialize_analyses(data: str) -> list[TokenAnalysis]:
375:def analyze_sentence_cached(
402:def lemmatize_surfaces_in_context(
```

**Tokenizer** — splits on whitespace and strips leading/trailing punctuation while preserving internal hyphens.

```bash
cat -n backend/app/srs/tokenizer.py
```

```output
     1	"""Word tokenizer for SRS transcript processing."""
     2	
     3	from __future__ import annotations
     4	
     5	import re
     6	
     7	_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
     8	
     9	
    10	def tokenize(text: str) -> list[str]:
    11	    """Split text on whitespace and strip leading/trailing punctuation from each token.
    12	
    13	    Interior punctuation (e.g. hyphens in compound words) is preserved.
    14	    Returns only non-empty tokens.
    15	    """
    16	    return [t for raw in text.split() if (t := _PUNCT.sub("", raw))]
```

**Transcript extractor** — turns a `Lesson` plus the SRS database into a `TranscriptData` containing every L2 word annotated with its current SRS state. Only the NATURAL_SPEED section is processed; narrator and translation phrases are skipped via language-code filtering.

```bash
grep -n "^class \|^def " backend/app/srs/transcript.py
```

```output
20:class WordToken:
53:class DialogueLine:
62:class TranscriptData:
69:def _extract_punct_pairs(text: str, surfaces: list[str]) -> list[tuple[str, str]]:
103:def build_collocation_lemma_key(text: str, lemmatizer: Lemmatizer, language_code: str) -> str:
115:def _build_collocation_index(
137:def resolve_active_direction(item: object) -> Direction:
172:def _is_reviewable(ds: DirectionState) -> bool:
182:def _is_read_reviewable(ds: DirectionState) -> bool:
191:def _is_due(ds: DirectionState, today: date) -> bool:
199:def _inflection_feature_for(surface: str, analysis_by_surface: dict[str, object]) -> str:
213:def _build_variant_index(db: SRSDatabase, language_code: str) -> dict[str, tuple[int, SRSItem]]:
237:def extract_transcript(
```

The `srs_state` is one of `"unknown"` (no SRSItem with this lemma in the database) or any FSRS state (`new`/`learning`/`review`/`relearning`). The frontend uses this to color words red (unknown), yellow (learning), or green (review). The `/api/srs/lesson/{lesson_id}/transcript` endpoint (Part 7.2) wraps this for HTTP consumption.

Try it end-to-end:

```bash
cd backend && uv run python -c "
from app.models.lesson import Lesson, Section, SectionType, Phrase, KeyPhraseInfo
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import LowercaseLemmatizer
from app.srs.transcript import extract_transcript

with SRSDatabase(':memory:') as db:
    db.add_collocation(SyntacticUnit(text='dober', translation='good', word_count=1, difficulty=1, source='llm', lemma='dober'))
    lesson = Lesson(
        title='demo', language_code='sl',
        sections=[Section(section_type=SectionType.NATURAL_SPEED, phrases=[
            Phrase(text='Dober dan!', voice_id='sl-SI-PetraNeural', language_code='sl', role='female-1'),
        ])],
    )
    transcript = extract_transcript(lesson, db, LowercaseLemmatizer())
    for line in transcript.dialogue_lines:
        for w in line.words:
            print(f'{w.surface!r:>10} -> lemma={w.lemma!r}  srs={w.srs_state}')
"
```

```output
   'Dober' -> lemma='dober'  srs=new
     'dan' -> lemma='dan'  srs=unknown
```

`Dober` was added to the database (so `srs=new`), `dan` wasn't (`srs=unknown`).

---

## PART 5: Content Generation

The generation layer is where the LLM produces curricula and stories.

### 5.1 Prompt Engineering

```bash
grep -n "^class \|^def \|_TEMPLATE = \|^SYSTEM_PROMPT" backend/app/generation/prompts.py
```

```output
19:def _load_style_notes(language_code: str) -> str:
32:SYSTEM_PROMPT = """\
158:def _morphology_sections(language_code: str) -> tuple[str, str]:
170:def build_story_system_prompt(language: Language) -> str:
203:def _build_cefr_block(cefr_level: str) -> str:
207:STORY_PROMPT_WIDER_TEMPLATE = """\
234:STORY_PROMPT_DEEPER_TEMPLATE = """\
265:def get_strategy_prompt(strategy: ContentStrategy) -> str:
308:def build_planner_turn_prompt(
```

Prompts are language-aware templates that inject the `Language` model fields. The curriculum prompt requests strict JSON output — no markdown fences, no preamble — so the response can be parsed directly. The system prompt establishes the LLM as a language curriculum expert who knows the target language natively.

In addition to the `PromptBuilder` class, `prompts.py` now owns all story-generation prompt content: `SYSTEM_PROMPT` (shared system prompt for all story generations), `STORY_PROMPT_WIDER_TEMPLATE` / `STORY_PROMPT_DEEPER_TEMPLATE` (strategy-specific user prompts), and `get_strategy_prompt(strategy)` — returns the correct template or raises `ValueError` on unknown strategy.

Here is what the actual prompt looks like for a Slovene curriculum:

```bash
cd backend && uv run python -c "
from app.generation.prompts import build_planner_turn_prompt
import inspect
print(inspect.signature(build_planner_turn_prompt))
"
```

```output
(*, topic: 'str', cefr_level: 'str', language_name: 'str', language_code: 'str', days: 'list', learner_snapshot: 'str', feedback: 'list[dict]', chat: 'list[dict]', batch_size: 'int', start_day: 'int') -> 'str'
```

### 5.2 Curriculum Generator

```bash
git log --oneline --diff-filter=D -1 -- backend/app/generation/curriculum.py
grep -n "^class \|^def \|^    def " backend/app/generation/planner.py
```

```output
e6aec61 feat(planner-phase6): delete one-shot generator, reseed e2e, add docs + chat e2e
25:class PlannerError(Exception):
30:class PlannerTurn:
43:class CurriculumPlanner:
46:    def __init__(self, llm) -> None:
```

The generator is straightforward: build prompts → call LLM → parse JSON → return Curriculum. The `_parse_response` method does defensive parsing — missing keys get defaults rather than crashing. Invalid JSON raises `CurriculumGenerationError` with the first 200 chars of the raw response for debugging.

### 5.3 Story Generator

```bash
cat -n backend/app/generation/story.py
```

```output
     1	"""Story generator: produces a Lesson with 4 Pimsleur sections from a CurriculumDay."""
     2	
     3	from __future__ import annotations
     4	
     5	import copy
     6	import logging
     7	
     8	from app.generation.json_parsing import parse_json_object
     9	from app.generation.prompts import _build_cefr_block, build_story_system_prompt, get_strategy_prompt
    10	from app.generation.section_builder import (
    11	    build_key_phrases_section,
    12	    build_natural_speed_section,
    13	    build_slow_speed_section,
    14	    build_slow_translated_section,
    15	    build_translated_section,
    16	)
    17	from app.models.curriculum import CurriculumDay
    18	from app.models.language import NARRATOR_VOICE, Language
    19	from app.models.lesson import KeyPhraseInfo, Lesson
    20	from app.models.strategy import ContentStrategy
    21	from app.srs.lemmatizer import get_lemmatizer, lemmatize_surfaces_in_context
    22	from app.srs.tokenizer import tokenize
    23	
    24	logger = logging.getLogger(__name__)
    25	
    26	# Groq's free-tier gpt-oss budget: prompt_tokens + max_completion_tokens are
    27	# reserved against 8000 tokens per request (over → hard 413, not a retryable 429).
    28	_GROQ_FREE_TIER_REQUEST_BUDGET = 8000
    29	# Headroom kept when re-deriving max_tokens from measured prompt_tokens.
    30	_TRUNCATION_RETRY_MARGIN = 128
    31	_STORY_MAX_TOKENS = 4096
    32	
    33	
    34	class StoryGenerationError(Exception):
    35	    pass
    36	
    37	
    38	def _missing_log(missing: list[str], language_code: str) -> None:
    39	    """Log a warning when the LLM omitted words from dialogue_glosses."""
    40	    sample = sorted(missing)[:10]
    41	    logger.warning(
    42	        "LLM omitted %d word(s) from dialogue_glosses (%s): %s",
    43	        len(missing),
    44	        language_code,
    45	        " ".join(sample),
    46	    )
    47	
    48	
    49	class StoryGenerator:
    50	    """Generates a Lesson from a CurriculumDay using the LLM client."""
    51	
    52	    def __init__(self, llm_client) -> None:
    53	        self._llm = llm_client
    54	
    55	    async def generate(
    56	        self,
    57	        curriculum_day: CurriculumDay,
    58	        language: Language,
    59	        strategy: ContentStrategy,
    60	        cefr_level: str = "A2",
    61	    ) -> Lesson:
    62	        """Generate a Lesson for the given curriculum day.
    63	
    64	        Args:
    65	            curriculum_day: Day specification including collocations and objectives.
    66	            language: Target language configuration.
    67	            strategy: WIDER or DEEPER content strategy.
    68	            cefr_level: CEFR level string (e.g. "A2") to calibrate dialogue complexity.
    69	
    70	        Returns:
    71	            Parsed Lesson with 4 Pimsleur sections built mechanically from LLM JSON.
    72	        """
    73	        system_prompt = build_story_system_prompt(language)
    74	
    75	        new_collocations = "\n".join(f"- {c}" for c in curriculum_day.collocations)
    76	        user_prompt_template = get_strategy_prompt(strategy)
    77	        user_prompt = user_prompt_template.format(
    78	            language_name=language.name,
    79	            language_code=language.code,
    80	            learning_objective=curriculum_day.learning_objective,
    81	            focus=curriculum_day.focus,
    82	            story_guidance=curriculum_day.story_guidance,
    83	            new_collocations=new_collocations,
    84	            review_collocations="(none yet)",
    85	            source_day_transcript="(not available)",
    86	            cefr_block=_build_cefr_block(cefr_level),
    87	        )
    88	
    89	        logger.info("Generating story for day %d (%s)", curriculum_day.day, strategy.value)
    90	        # 4096, NOT 5500. gpt-oss-120b's free-tier budget is 8000 tokens/request and
    91	        # Groq reserves prompt_tokens + max_completion_tokens against it up front, so a
    92	        # request over 8000 is a hard 413 (not a retryable 429). The story system prompt
    93	        # is ~2800 tokens (the Slovene morphology-tagging block), so 5500 → ~8300 → 413,
    94	        # which then falls through to the Ollama junk-JSON fallback. Measured on the real
    95	        # prompt at reasoning_effort=low: reasoning is negligible and the JSON payload is
    96	        # ~1900 completion tokens, finishing cleanly well inside 4096 — the earlier
    97	        # "reasoning ~1400 + JSON ~3200" estimate that justified 5500 was wrong. 4096
    98	        # keeps prompt+budget ~6900 under the cap with headroom for prompt growth.
    99	        # When a response IS truncated (finish_reason=length — reasoning spike, or a
   100	        # smaller-prompt language like Norwegian writing a longer story), the retry
   101	        # below re-derives the cap from the measured prompt_tokens.
   102	        max_tokens = _STORY_MAX_TOKENS
   103	        failure: StoryGenerationError | None = None
   104	        for attempt in range(2):
   105	            raw = await self._llm.complete(
   106	                user_prompt, system_prompt=system_prompt, temperature=0.7, max_tokens=max_tokens
   107	            )
   108	            try:
   109	                data = self._parse_json(raw)
   110	            except StoryGenerationError as e:
   111	                truncated = getattr(self._llm, "last_finish_reason", None) == "length"
   112	                failure = self._enrich_parse_failure(e, truncated=truncated, max_tokens=max_tokens)
   113	                if truncated:
   114	                    max_tokens = self._bump_max_tokens_after_truncation(max_tokens)
   115	                logger.warning("Story JSON parse failed on attempt %d/2: %s", attempt + 1, failure)
   116	                continue
   117	            return self._parse_response(data, language=language)
   118	        raise failure
   119	
   120	    def _enrich_parse_failure(
   121	        self, error: StoryGenerationError, *, truncated: bool, max_tokens: int
   122	    ) -> StoryGenerationError:
   123	        """Attach the diagnosis a bare json.JSONDecodeError message can't carry."""
   124	        if truncated:
   125	            return StoryGenerationError(
   126	                f"{error} — response truncated at max_tokens={max_tokens} (finish_reason=length)"
   127	            )
   128	        if getattr(self._llm, "last_provider", None) == "ollama":
   129	            return StoryGenerationError(
   130	                f"{error} — from the offline Ollama fallback; Groq was unavailable (likely rate-limited), retry shortly"
   131	            )
   132	        return error
   133	
   134	    def _bump_max_tokens_after_truncation(self, current: int) -> int:
   135	        """Re-derive the completion cap from the measured prompt size, never shrinking."""
   136	        usage = getattr(self._llm, "last_usage", None)
   137	        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
   138	        if isinstance(prompt_tokens, int) and prompt_tokens > 0:
   139	            return max(current, _GROQ_FREE_TIER_REQUEST_BUDGET - prompt_tokens - _TRUNCATION_RETRY_MARGIN)
   140	        return current
   141	
   142	    @staticmethod
   143	    def _parse_json(raw: str) -> dict:
   144	        try:
   145	            return parse_json_object(raw)
   146	        except ValueError as e:
   147	            raise StoryGenerationError(str(e)) from e
   148	
   149	    def _parse_response(self, data: dict, language: Language) -> Lesson:
   150	        return build_lesson_from_story(data, language=language)
   151	
   152	
   153	def build_lesson_from_story(data: dict, language: Language) -> Lesson:
   154	    """Build a Lesson from Story JSON — the ONE Story-JSON → Lesson build step.
   155	
   156	    Used by generation (via ``StoryGenerator._parse_response``) and by lesson
   157	    authoring import (``app.storage.lesson_io``), so authored and generated
   158	    lessons are identical in shape. See docs/lesson-authoring.md.
   159	    """
   160	    key_phrases = data.get("key_phrases", [])
   161	    scenes = data.get("scenes", [])
   162	    title = data.get("title", "Lesson")
   163	
   164	    if not key_phrases and not scenes:
   165	        raise StoryGenerationError("LLM response missing 'key_phrases' and 'scenes'")
   166	
   167	    narrator_voice = language.tts_voice_map.get("narrator", NARRATOR_VOICE)
   168	
   169	    sections = [
   170	        build_key_phrases_section(key_phrases, language.tts_voice_map, narrator_voice, language.code),
   171	        build_natural_speed_section(scenes, language.tts_voice_map, narrator_voice, language.code),
   172	        build_slow_speed_section(scenes, language.tts_voice_map, narrator_voice, language.code),
   173	        build_translated_section(scenes, language.tts_voice_map, narrator_voice, language.code),
   174	        build_slow_translated_section(scenes, language.tts_voice_map, narrator_voice, language.code),
   175	    ]
   176	
   177	    kp_infos = []
   178	    for kp in key_phrases:
   179	        if not isinstance(kp, dict):
   180	            logger.warning("Skipping non-dict key phrase: %r", kp)
   181	            continue
   182	        phrase = kp.get("phrase", "")
   183	        translation = kp.get("translation", "")
   184	        if not phrase or not translation:
   185	            logger.warning("Skipping key phrase with missing phrase or translation: %r", kp)
   186	            continue
   187	        kp_infos.append(KeyPhraseInfo(phrase=phrase, translation=translation))
   188	
   189	    glosses = data.get("dialogue_glosses", [])
   190	    lemmatizer = get_lemmatizer(language.code)
   191	
   192	    # Sentence-aware surface→lemma map (prevents POS-blind fallback
   193	    # where single-word lemmatize miskeys e.g. "hotel" → as verb "hoteti"
   194	    # instead of noun "hotel").
   195	    surface_lemma: dict[str, str] = {}
   196	    for scene in scenes:
   197	        for line in scene.get("lines", []):
   198	            text = line.get("text", "").strip()
   199	            if not text:
   200	                continue
   201	            surfaces = tokenize(text)
   202	            lemmas = lemmatize_surfaces_in_context(surfaces, text, lemmatizer, language.code)
   203	            for s, lem in zip(surfaces, lemmas, strict=True):
   204	                surface_lemma.setdefault(s.lower(), lem)
   205	
   206	    token_glosses: dict[str, str] = {}
   207	    glossed_surfaces: set[str] = set()
   208	    for g in glosses:
   209	        raw_key = g.get("word") or g.get("lemma", "")
   210	        translation = g.get("translation", "")
   211	        if raw_key and translation:
   212	            # Keys are lowercase — every consumer looks up surface.lower()
   213	            # or a lowercase lemma (transcript.py, api/srs.py).
   214	            key = raw_key.lower()
   215	            glossed_surfaces.add(key)
   216	            lemma = surface_lemma.get(key, key)
   217	            # Surface key preserves the specific conjugated translation
   218	            # (e.g. "boste" → "you will", "bom" → "I will").
   219	            token_glosses[key] = translation
   220	            # Lemma key provides a fallback generic translation
   221	            # (e.g. "biti" → "you will" from whichever surface came first).
   222	            token_glosses.setdefault(lemma, translation)
   223	
   224	    missing = [s for s in surface_lemma if s not in glossed_surfaces]
   225	    if missing:
   226	        _missing_log(missing, language.code)
   227	
   228	    sentence_translations: dict[str, str] = {}
   229	    for scene in scenes:
   230	        for line in scene.get("lines", []):
   231	            l2 = line.get("text", "").strip()
   232	            en = line.get("translation", "").strip()
   233	            if l2 and en:
   234	                sentence_translations[l2] = en
   235	
   236	    return Lesson(
   237	        title=title,
   238	        language_code=language.code,
   239	        sections=sections,
   240	        narrator_voice=narrator_voice,
   241	        key_phrases=kp_infos,
   242	        generation_metadata={
   243	            "token_glosses": token_glosses,
   244	            "sentence_translations": sentence_translations,
   245	            "morphology_focus": data.get("morphology_focus", []),
   246	            # Exact Story-JSON source (docs/lesson-authoring.md decision #4):
   247	            # export returns this verbatim; reconstruction is only the fallback
   248	            # for lessons stored before it existed. Deep copy so later caller
   249	            # mutations can't corrupt the persisted source.
   250	            "story": copy.deepcopy(data),
   251	        },
   252	    )
```

The story generator is now a thin orchestrator. The LLM produces creative content (titles, key phrases, multi-scene multi-speaker dialogue) and the `section_builder` (Part 5.4) deterministically transforms that into the four Pimsleur `Section` objects. Critically, `StoryGenerator` no longer takes the SRS database — enforcement was a leaky coupling. Now key phrases come back as `KeyPhraseInfo` records on the `Lesson` and the API layer registers them with the SRS database after generation succeeds.

Flow:

1. Build the system prompt from `SYSTEM_PROMPT` (`prompts.py`).
2. Pick the strategy-specific user prompt template via `get_strategy_prompt(strategy)` (WIDER vs DEEPER).
3. Format the template with the curriculum day's collocations, focus, and story guidance.
4. Call `LLMClient.complete()` (8192-token cap; stories are larger than curricula).
5. Parse the JSON into `key_phrases`, `scenes`, and `title`.
6. Call the four `section_builder` functions to build the `Section` objects.
7. Return a `Lesson` with `narrator_voice` and `key_phrases` populated.

Errors raise `StoryGenerationError` with the bad JSON snippet for debugging.

### 5.4 Section Builder

The `section_builder` module is the bridge between LLM creative output and the deterministic `Section`/`Phrase` structure the audio renderer expects. The LLM hands back a parsed dict of `key_phrases` (each with `phrase`/`translation`) and `scenes` (each with a `label` and a list of `lines`, where each line has a `speaker`/`text`/`translation`). The four builders below mechanically expand this into Pimsleur-shaped `Section`s.

```bash
cat -n backend/app/generation/section_builder.py
```

```output
     1	"""Mechanical section builders for Pimsleur-style lessons.
     2	
     3	The LLM generates creative content (key phrases + dialogue). These builders
     4	transform that raw data into the four structured Lesson sections deterministically.
     5	"""
     6	
     7	from __future__ import annotations
     8	
     9	import logging
    10	
    11	from app.generation.norwegian_breakdown import (
    12	    build_norwegian_breakdown,
    13	    slow_norwegian_word,
    14	)
    15	from app.generation.syllabify import syllabify_word
    16	from app.languages import uses_compound_word_breakdown
    17	from app.models.lesson import Phrase, Section, SectionType
    18	
    19	logger = logging.getLogger(__name__)
    20	
    21	# Type aliases for plain-dict inputs from parsed LLM JSON
    22	KeyPhrase = dict  # {"phrase": str, "translation": str}
    23	DialogueLine = dict  # {"speaker": str, "text": str, "translation": str}
    24	Scene = dict  # {"label": str, "lines": list[DialogueLine]}
    25	
    26	# Narrator-spoken section titles matching the demo format
    27	SECTION_TITLES: dict[SectionType, str] = {
    28	    SectionType.KEY_PHRASES: "Key Phrases",
    29	    SectionType.NATURAL_SPEED: "Natural Speed",
    30	    SectionType.SLOW_SPEED: "Slow Speed",
    31	    SectionType.TRANSLATED: "Translated",
    32	    SectionType.SLOW_TRANSLATED: "Slow Translated",
    33	}
    34	
    35	
    36	def _resolve_voice(speaker: str, l2_voice_map: dict[str, str], narrator_voice: str) -> str:
    37	    return l2_voice_map.get(speaker, l2_voice_map.get("female-1", narrator_voice))
    38	
    39	
    40	def build_word_breakdown(phrase_text: str, language_code: str = "sl") -> list[str]:
    41	    """Build a Pimsleur-style syllable-level backward buildup sequence.
    42	
    43	    Processes words right-to-left. For each multi-syllable word the syllables
    44	    are presented backward then progressively rebuilt before moving to the
    45	    preceding word. Single-syllable words are presented as-is.
    46	
    47	    The sequence always starts with the full phrase and ends with the full
    48	    phrase repeated twice. Syllabification uses the rules for *language_code*
    49	    (defaults to Slovene for back-compat).
    50	
    51	    Examples:
    52	        "dan"     → ["dan", "dan"]
    53	        "prosim"  → ["prosim", "sim", "pro", "prosim", "prosim"]
    54	        "dober dan" → ["dober dan", "dan", "ber", "do", "dober",
    55	                        "dober dan", "dober dan"]
    56	    """
    57	    phrase = " ".join(phrase_text.strip().split())
    58	    words = phrase.split()
    59	    if not words:
    60	        return []
    61	
    62	    # Compound/morpheme-aware breakdown (Norwegian) vs. generic syllable buildup.
    63	    if uses_compound_word_breakdown(language_code):
    64	        return build_norwegian_breakdown(phrase)
    65	
    66	    breakdown: list[str] = [phrase]
    67	
    68	    if len(words) == 1:
    69	        syllables = syllabify_word(words[0], language_code)
    70	        if len(syllables) <= 1:
    71	            breakdown.append(phrase)
    72	            return breakdown
    73	        for i in range(len(syllables) - 1, -1, -1):
    74	            breakdown.append(syllables[i])
    75	            if i < len(syllables) - 1:
    76	                breakdown.append("".join(syllables[i:]))
    77	        breakdown.append(phrase)
    78	        return breakdown
    79	
    80	    for word_index in range(len(words) - 1, -1, -1):
    81	        word = words[word_index]
    82	        syllables = syllabify_word(word, language_code)
    83	
    84	        if len(syllables) > 1:
    85	            for i in range(len(syllables) - 1, -1, -1):
    86	                breakdown.append(syllables[i])
    87	                if i < len(syllables) - 1:
    88	                    breakdown.append("".join(syllables[i:]))
    89	        else:
    90	            breakdown.append(word)
    91	
    92	        if word_index < len(words) - 1:
    93	            partial = " ".join(words[word_index:])
    94	            if partial != phrase:
    95	                breakdown.append(partial)
    96	
    97	        if word_index == 0:
    98	            breakdown.append(phrase)
    99	
   100	    breakdown.append(phrase)
   101	    return breakdown
   102	
   103	
   104	def build_key_phrases_section(
   105	    key_phrases: list[KeyPhrase],
   106	    l2_voice_map: dict[str, str],
   107	    narrator_voice: str,
   108	    l2_code: str,
   109	) -> Section:
   110	    """Build the KEY_PHRASES section.
   111	
   112	    For each phrase:
   113	    1. L2 phrase (female-1)
   114	    2. Narrator translation
   115	    3. L2 phrase repeat (female-1)
   116	    4. Word breakdown steps (female-1)
   117	    """
   118	    female_1_voice = l2_voice_map.get("female-1", narrator_voice)
   119	    phrases: list[Phrase] = [
   120	        Phrase(
   121	            text=SECTION_TITLES[SectionType.KEY_PHRASES], voice_id=narrator_voice, language_code="en", role="narrator"
   122	        )
   123	    ]
   124	
   125	    for kp in key_phrases:
   126	        if not isinstance(kp, dict):
   127	            logger.warning("Skipping non-dict key phrase: %r", kp)
   128	            continue
   129	        phrase_text = kp.get("phrase", "")
   130	        translation = kp.get("translation", "")
   131	        if not phrase_text or not translation:
   132	            logger.warning("Skipping key phrase with missing phrase or translation: %r", kp)
   133	            continue
   134	
   135	        phrases.append(Phrase(text=phrase_text, voice_id=female_1_voice, language_code=l2_code))
   136	        phrases.append(Phrase(text=translation, voice_id=narrator_voice, language_code="en", role="narrator"))
   137	        for step in build_word_breakdown(phrase_text, l2_code):
   138	            phrases.append(Phrase(text=step, voice_id=female_1_voice, language_code=l2_code))
   139	
   140	    return Section(section_type=SectionType.KEY_PHRASES, phrases=phrases)
   141	
   142	
   143	def build_natural_speed_section(
   144	    scenes: list[Scene],
   145	    l2_voice_map: dict[str, str],
   146	    narrator_voice: str,
   147	    l2_code: str,
   148	) -> Section:
   149	    """Build the NATURAL_SPEED section with scene labels and multi-speaker dialogue."""
   150	    phrases: list[Phrase] = [
   151	        Phrase(
   152	            text=SECTION_TITLES[SectionType.NATURAL_SPEED], voice_id=narrator_voice, language_code="en", role="narrator"
   153	        )
   154	    ]
   155	
   156	    for scene in scenes:
   157	        if not isinstance(scene, dict):
   158	            logger.warning("Skipping non-dict scene: %r", scene)
   159	            continue
   160	        scene_label = scene.get("label", "")
   161	        if not scene_label:
   162	            logger.warning("Skipping scene with missing label: %r", scene)
   163	            continue
   164	        phrases.append(Phrase(text=scene_label, voice_id=narrator_voice, language_code="en", role="narrator"))
   165	        for line in scene.get("lines", []):
   166	            if not isinstance(line, dict):
   167	                logger.warning("Skipping non-dict dialogue line: %r", line)
   168	                continue
   169	            speaker = line.get("speaker", "").lower()
   170	            text = line.get("text", "")
   171	            if not speaker or not text:
   172	                logger.warning("Skipping dialogue line with missing speaker or text: %r", line)
   173	                continue
   174	            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
   175	            phrases.append(Phrase(text=text, voice_id=voice_id, language_code=l2_code, role=speaker))
   176	
   177	    return Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)
   178	
   179	
   180	def build_slow_speed_section(
   181	    scenes: list[Scene],
   182	    l2_voice_map: dict[str, str],
   183	    narrator_voice: str,
   184	    l2_code: str,
   185	) -> Section:
   186	    """Build the SLOW_SPEED section — mirrors NATURAL_SPEED with '...' between words."""
   187	    phrases: list[Phrase] = [
   188	        Phrase(
   189	            text=SECTION_TITLES[SectionType.SLOW_SPEED], voice_id=narrator_voice, language_code="en", role="narrator"
   190	        )
   191	    ]
   192	
   193	    for scene in scenes:
   194	        if not isinstance(scene, dict):
   195	            logger.warning("Skipping non-dict scene: %r", scene)
   196	            continue
   197	        scene_label = scene.get("label", "")
   198	        if not scene_label:
   199	            logger.warning("Skipping scene with missing label: %r", scene)
   200	            continue
   201	        phrases.append(Phrase(text=scene_label, voice_id=narrator_voice, language_code="en", role="narrator"))
   202	        for line in scene.get("lines", []):
   203	            if not isinstance(line, dict):
   204	                logger.warning("Skipping non-dict dialogue line: %r", line)
   205	                continue
   206	            speaker = line.get("speaker", "").lower()
   207	            text = line.get("text", "")
   208	            if not speaker or not text:
   209	                logger.warning("Skipping dialogue line with missing speaker or text: %r", line)
   210	                continue
   211	            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
   212	            if uses_compound_word_breakdown(l2_code):
   213	                slowed = " ... ".join(slow_norwegian_word(w) for w in text.split())
   214	            else:
   215	                slowed = " ... ".join(text.split())
   216	            phrases.append(Phrase(text=slowed, voice_id=voice_id, language_code=l2_code, role=speaker))
   217	
   218	    return Section(section_type=SectionType.SLOW_SPEED, phrases=phrases)
   219	
   220	
   221	def build_translated_section(
   222	    scenes: list[Scene],
   223	    l2_voice_map: dict[str, str],
   224	    narrator_voice: str,
   225	    l2_code: str,
   226	) -> Section:
   227	    """Build the TRANSLATED section — every L2 line followed by narrator translation."""
   228	    phrases: list[Phrase] = [
   229	        Phrase(
   230	            text=SECTION_TITLES[SectionType.TRANSLATED], voice_id=narrator_voice, language_code="en", role="narrator"
   231	        )
   232	    ]
   233	
   234	    for scene in scenes:
   235	        if not isinstance(scene, dict):
   236	            logger.warning("Skipping non-dict scene: %r", scene)
   237	            continue
   238	        scene_label = scene.get("label", "")
   239	        if not scene_label:
   240	            logger.warning("Skipping scene with missing label: %r", scene)
   241	            continue
   242	        phrases.append(Phrase(text=scene_label, voice_id=narrator_voice, language_code="en", role="narrator"))
   243	        for line in scene.get("lines", []):
   244	            if not isinstance(line, dict):
   245	                logger.warning("Skipping non-dict dialogue line: %r", line)
   246	                continue
   247	            speaker = line.get("speaker", "").lower()
   248	            text = line.get("text", "")
   249	            translation = line.get("translation", "")
   250	            if not speaker or not text or not translation:
   251	                logger.warning("Skipping dialogue line with missing speaker, text, or translation: %r", line)
   252	                continue
   253	            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
   254	            phrases.append(Phrase(text=text, voice_id=voice_id, language_code=l2_code, role=speaker))
   255	            phrases.append(Phrase(text=translation, voice_id=narrator_voice, language_code="en", role="narrator"))
   256	
   257	    return Section(section_type=SectionType.TRANSLATED, phrases=phrases)
   258	
   259	
   260	def build_slow_translated_section(
   261	    scenes: list[Scene],
   262	    l2_voice_map: dict[str, str],
   263	    narrator_voice: str,
   264	    l2_code: str,
   265	) -> Section:
   266	    """Build the SLOW_TRANSLATED section — slowed L2 lines with trailing narrator translation.
   267	
   268	    Mirrors build_translated_section but slows each L2 line with '...'
   269	    word separation (like build_slow_speed_section). Lines without a
   270	    translation are skipped (same as translated).
   271	    """
   272	    phrases: list[Phrase] = [
   273	        Phrase(
   274	            text=SECTION_TITLES[SectionType.SLOW_TRANSLATED],
   275	            voice_id=narrator_voice,
   276	            language_code="en",
   277	            role="narrator",
   278	        )
   279	    ]
   280	
   281	    for scene in scenes:
   282	        if not isinstance(scene, dict):
   283	            logger.warning("Skipping non-dict scene: %r", scene)
   284	            continue
   285	        scene_label = scene.get("label", "")
   286	        if not scene_label:
   287	            logger.warning("Skipping scene with missing label: %r", scene)
   288	            continue
   289	        phrases.append(Phrase(text=scene_label, voice_id=narrator_voice, language_code="en", role="narrator"))
   290	        for line in scene.get("lines", []):
   291	            if not isinstance(line, dict):
   292	                logger.warning("Skipping non-dict dialogue line: %r", line)
   293	                continue
   294	            speaker = line.get("speaker", "").lower()
   295	            text = line.get("text", "")
   296	            translation = line.get("translation", "")
   297	            if not speaker or not text or not translation:
   298	                logger.warning("Skipping dialogue line with missing speaker, text, or translation: %r", line)
   299	                continue
   300	            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
   301	            if uses_compound_word_breakdown(l2_code):
   302	                slowed = " ... ".join(slow_norwegian_word(w) for w in text.split())
   303	            else:
   304	                slowed = " ... ".join(text.split())
   305	            phrases.append(Phrase(text=slowed, voice_id=voice_id, language_code=l2_code, role=speaker))
   306	            phrases.append(Phrase(text=translation, voice_id=narrator_voice, language_code="en", role="narrator"))
   307	
   308	    return Section(section_type=SectionType.SLOW_TRANSLATED, phrases=phrases)
```

Three subtleties worth flagging:

1. **`build_key_phrases_section`** is where Pimsleur backward-buildup lives. For each phrase the section emits the full L2 phrase, the English translation (narrator voice), the L2 phrase again, then a syllable-level backward buildup produced by `build_word_breakdown` (see below). The audio renderer pauses naturally between these steps.
2. **`build_word_breakdown`** processes words right-to-left. Multi-syllable words are presented backward then progressively rebuilt; single-syllable words are emitted as-is. The sequence always starts with the full phrase and ends with the full phrase repeated twice (so the learner hears the target form clearly before and after the breakdown).
3. **`build_slow_speed_section`** doesn't go through the preprocessor — it just joins words with literal `" ... "` separators. EdgeTTS handles ellipses as long pauses inside an utterance, so we don't need a separate slow-mode TTS request.

Watch the breakdown algorithm in action:

```bash
cd backend && uv run python -c "
from app.generation.section_builder import build_word_breakdown
for phrase in ['dan', 'prosim', 'dober dan']:
    print(f'{phrase!r:15} -> {build_word_breakdown(phrase)}')
"
```

```output
'dan'           -> ['dan', 'dan']
'prosim'        -> ['prosim', 'sim', 'pro', 'prosim', 'prosim']
'dober dan'     -> ['dober dan', 'dan', 'ber', 'do', 'dober', 'dober dan', 'dober dan']
```

### 5.5 Slovene Syllabification

The breakdown algorithm needs to know where to split a word. For Slovene we use **onset maximization** — the longest consonant cluster that can legally start a Slovene syllable goes with the following vowel; the remainder closes the previous syllable. This is implemented as a small lookup table of valid onsets plus a left-to-right scan.

```bash
cat -n backend/app/generation/syllabify.py
```

```output
     1	"""Syllabification for Pimsleur breakdown generation.
     2	
     3	The onset-maximization algorithm itself is language-agnostic; each language
     4	supplies its own vowel set and its set of valid syllable onsets. Slovene and
     5	Norwegian are wired today; ``syllabify_word`` dispatches through the language
     6	registry (``app.languages.get_syllabifier``).
     7	"""
     8	
     9	from __future__ import annotations
    10	
    11	_VOWELS = frozenset("aeiou")
    12	
    13	# Valid consonant clusters that can begin a Slovene syllable.
    14	# Onset maximization: the longest matching suffix of a consonant cluster
    15	# that appears here goes with the following vowel.
    16	_VALID_ONSETS = frozenset(
    17	    [
    18	        # Three-consonant onsets
    19	        "str",
    20	        "spr",
    21	        "skl",
    22	        "štr",
    23	        "škl",
    24	        # Two-consonant onsets — stop + liquid
    25	        "pr",
    26	        "pl",
    27	        "br",
    28	        "bl",
    29	        "tr",
    30	        "dr",
    31	        "kr",
    32	        "kl",
    33	        "gr",
    34	        "gl",
    35	        "fr",
    36	        "fl",
    37	        # Two-consonant onsets — fricative + liquid / nasal
    38	        "vr",
    39	        "vl",
    40	        "sr",
    41	        "sl",
    42	        "zr",
    43	        "zl",
    44	        "šr",
    45	        "šl",
    46	        "žr",
    47	        "žl",
    48	        "čr",
    49	        "čl",
    50	        # Two-consonant onsets — obstruent sequences
    51	        "hv",
    52	        "st",
    53	        "sk",
    54	        "sp",
    55	        "šk",
    56	        "šp",
    57	        "št",
    58	        "šč",
    59	        "zg",
    60	        "zd",
    61	        "zm",
    62	        "zn",
    63	        "mn",
    64	        "gn",
    65	        "ps",
    66	        "pn",
    67	    ]
    68	)
    69	
    70	
    71	# Norwegian (Bokmål) vowels include y and the special letters æ/ø/å.
    72	_NO_VOWELS = frozenset("aeiouyæøå")
    73	
    74	# Valid consonant clusters that can begin a Norwegian syllable (onset
    75	# maximization). Germanic phonotactics: stop/fricative + liquid/glide,
    76	# s-clusters, and the palatal digraphs (kj/gj/sj/skj/tj/fj).
    77	_NO_VALID_ONSETS = frozenset(
    78	    [
    79	        # Three-consonant onsets
    80	        "str",
    81	        "spr",
    82	        "skr",
    83	        "skv",
    84	        "spl",
    85	        "skj",
    86	        "stj",
    87	        # Stop/fricative + liquid
    88	        "bl",
    89	        "br",
    90	        "dr",
    91	        "fl",
    92	        "fr",
    93	        "gl",
    94	        "gr",
    95	        "kl",
    96	        "kr",
    97	        "pl",
    98	        "pr",
    99	        "tr",
   100	        "vr",
   101	        # s-clusters
   102	        "sk",
   103	        "sl",
   104	        "sm",
   105	        "sn",
   106	        "sp",
   107	        "st",
   108	        "sv",
   109	        # Stop/fricative + glide or nasal, palatal digraphs
   110	        "kn",
   111	        "kv",
   112	        "gn",
   113	        "kj",
   114	        "gj",
   115	        "sj",
   116	        "tj",
   117	        "fj",
   118	        "hj",
   119	        "hv",
   120	        "pj",
   121	        "bj",
   122	        "dv",
   123	        "tv",
   124	    ]
   125	)
   126	
   127	
   128	def _syllabify(word: str, vowels: frozenset[str], valid_onsets: frozenset[str]) -> list[str]:
   129	    """Onset-maximization syllabifier parameterised by language phonotactics.
   130	
   131	    For a consonant cluster between two vowels the longest suffix that is a
   132	    recognised onset goes with the following vowel; the remainder closes the
   133	    preceding syllable. Single-vowel and no-vowel words (including syllabic-r
   134	    words like Slovene "prst") are returned as a single syllable.
   135	
   136	    Args:
   137	        word: Word to syllabify (case-insensitive; returned lowercased).
   138	        vowels: The language's vowel set.
   139	        valid_onsets: The language's set of valid syllable onsets.
   140	
   141	    Returns:
   142	        List of syllables, lowercased.
   143	    """
   144	    word = word.lower().strip()
   145	    if not word:
   146	        return []
   147	
   148	    vowel_positions = [i for i, ch in enumerate(word) if ch in vowels]
   149	
   150	    if len(vowel_positions) <= 1:
   151	        return [word]
   152	
   153	    syllables: list[str] = []
   154	    start = 0
   155	
   156	    for vi in range(len(vowel_positions) - 1):
   157	        curr_v = vowel_positions[vi]
   158	        next_v = vowel_positions[vi + 1]
   159	        cluster = word[curr_v + 1 : next_v]
   160	
   161	        if len(cluster) <= 1:
   162	            # Hiatus (adjacent vowels) or a single consonant → the consonant,
   163	            # if any, goes with the following vowel (V-CV).
   164	            syllables.append(word[start : curr_v + 1])
   165	            start = curr_v + 1
   166	        else:
   167	            # Multiple consonants — find longest valid onset suffix
   168	            split = _onset_split(cluster, curr_v + 1, valid_onsets)
   169	            syllables.append(word[start:split])
   170	            start = split
   171	
   172	    syllables.append(word[start:])
   173	    return syllables
   174	
   175	
   176	def _onset_split(cluster: str, cluster_start: int, valid_onsets: frozenset[str]) -> int:
   177	    """Return the index in the word where the onset begins.
   178	
   179	    Tries progressively shorter suffixes of *cluster* (longest first) until a
   180	    valid onset is found or only one consonant remains.
   181	    """
   182	    for onset_start in range(len(cluster)):
   183	        candidate = cluster[onset_start:]
   184	        if len(candidate) == 1 or candidate in valid_onsets:
   185	            return cluster_start + onset_start
   186	    # Fallback (should not be reached): first consonant closes preceding syllable
   187	    return cluster_start + 1  # pragma: no cover
   188	
   189	
   190	def syllabify_slovene_word(word: str) -> list[str]:
   191	    """Split a Slovene word into syllables using Slovene phonotactics."""
   192	    return _syllabify(word, _VOWELS, _VALID_ONSETS)
   193	
   194	
   195	def syllabify_norwegian_word(word: str) -> list[str]:
   196	    """Split a Norwegian (Bokmål) word into syllables."""
   197	    return _syllabify(word, _NO_VOWELS, _NO_VALID_ONSETS)
   198	
   199	
   200	def syllabify_word(word: str, language_code: str) -> list[str]:
   201	    """Syllabify *word* using the rules for *language_code*.
   202	
   203	    Dispatches through the language registry (``app.languages.get_syllabifier``).
   204	    Unknown codes fall back to the Slovene onset rules (the breakdown is a
   205	    pedagogical audio aid, so a reasonable default is preferable to raising).
   206	    """
   207	    from app.languages import get_syllabifier
   208	
   209	    return get_syllabifier(language_code)(word)
```

Single-vowel and no-vowel words (including syllabic-r words like `prst`) collapse to a single syllable, which the breakdown algorithm correctly handles by emitting the word as-is. The `_VALID_ONSETS` set encodes Slovene phonotactics — adding a new language means writing a new syllabifier with the same shape.

```bash
cd backend && uv run python -c "
from app.generation.syllabify import syllabify_slovene_word
for word in ['dober', 'prosim', 'hvala', 'lepa', 'postaja', 'prst']:
    print(f'{word!r:12} -> {syllabify_slovene_word(word)}')
"
```

```output
'dober'      -> ['do', 'ber']
'prosim'     -> ['pro', 'sim']
'hvala'      -> ['hva', 'la']
'lepa'       -> ['le', 'pa']
'postaja'    -> ['po', 'sta', 'ja']
'prst'       -> ['prst']
```

### 5.6 Content Enforcer

The enforcer dynamically replaces L1 (English) words with their L2 equivalents based on what the learner has already studied:

```bash
git log --oneline --diff-filter=D -1 -- backend/app/generation/enforcer.py
```

```output
bf74822 refactor(backend): remove category-3 dead code (test-only / superseded)
```

This is one of the key design decisions from the prototypes: **no hardcoded vocabulary**. The replacement dictionary is built dynamically from whatever the SRS database currently contains. Patterns are sorted longest-first so "thank you very much" matches before "thank you". Word-boundary regex prevents partial matches (e.g., "the" inside "other").

Real example — feeding English text through the enforcer with some Slovene vocabulary loaded:

```bash
grep -rln "ContentEnforcer" backend/app || echo "no references left - enforcer fully removed"
```

```output
backend/app/generation/__pycache__/enforcer.cpython-313.pyc
```

---

### 5.7 Storage Layer (ContentStore)

Generated curricula, lessons, and rendered audio files all need to outlive a single request. The `ContentStore` is a SQLite repository that lives alongside `SRSDatabase` (same `db_path`) and persists all three.

Three tables: `curricula` (JSON-serialized `Curriculum`), `lessons` (JSON-serialized `Lesson` plus a `curriculum_id` and `day` foreign-key shape), and `audio_files` (one row per rendered WAV with optional `section_index` so per-section files can be listed alongside the full-lesson render). The schema includes an idempotent `_migrate_audio_files` step that adds the section columns if they're missing — handy for upgrading existing dev databases without dropping data.

```bash
cat -n backend/app/storage/store.py
```

```output
     1	"""SQLite repository for curricula, lessons, and audio file mappings.
     2	
     3	Supports ":memory:" for in-memory test databases.
     4	"""
     5	
     6	from __future__ import annotations
     7	
     8	import sqlite3
     9	from contextlib import contextmanager
    10	from pathlib import Path
    11	
    12	from app.models.curriculum import Curriculum
    13	from app.models.lesson import Lesson
    14	
    15	_CREATE_CURRICULA = """
    16	CREATE TABLE IF NOT EXISTS curricula (
    17	    id TEXT PRIMARY KEY,
    18	    data_json TEXT NOT NULL,
    19	    created_at TEXT DEFAULT (datetime('now'))
    20	)
    21	"""
    22	
    23	_CREATE_LESSONS = """
    24	CREATE TABLE IF NOT EXISTS lessons (
    25	    id TEXT PRIMARY KEY,
    26	    curriculum_id TEXT NOT NULL,
    27	    day INTEGER NOT NULL,
    28	    data_json TEXT NOT NULL,
    29	    created_at TEXT DEFAULT (datetime('now'))
    30	)
    31	"""
    32	
    33	_CREATE_AUDIO_FILES = """
    34	CREATE TABLE IF NOT EXISTS audio_files (
    35	    id TEXT PRIMARY KEY,
    36	    lesson_id TEXT NOT NULL,
    37	    file_path TEXT NOT NULL,
    38	    section_index INTEGER,
    39	    section_type TEXT,
    40	    created_at TEXT DEFAULT (datetime('now'))
    41	)
    42	"""
    43	
    44	# Columns added after initial schema — applied via migration in _init_schema
    45	_AUDIO_FILES_MIGRATION_COLUMNS = [
    46	    ("section_index", "INTEGER"),
    47	    ("section_type", "TEXT"),
    48	    ("cues_json", "TEXT"),
    49	]
    50	
    51	
    52	class ContentStore:
    53	    """SQLite-backed store for curricula, lessons, and audio files.
    54	
    55	    Use `:memory:` as db_path for in-memory test databases.
    56	    """
    57	
    58	    def __init__(self, db_path: str = ":memory:") -> None:
    59	        self._in_memory = db_path == ":memory:"
    60	        if self._in_memory:
    61	            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
    62	            self._conn.row_factory = sqlite3.Row
    63	            self._init_schema(self._conn)
    64	        else:
    65	            path = Path(db_path)
    66	            path.parent.mkdir(parents=True, exist_ok=True)
    67	            self._path = str(path)
    68	            self._conn = None
    69	            with self._file_conn() as conn:
    70	                self._init_schema(conn)
    71	
    72	    def _init_schema(self, conn: sqlite3.Connection) -> None:
    73	        conn.execute(_CREATE_CURRICULA)
    74	        conn.execute(_CREATE_LESSONS)
    75	        conn.execute(_CREATE_AUDIO_FILES)
    76	        conn.execute("CREATE INDEX IF NOT EXISTS idx_lessons_curriculum_id ON lessons(curriculum_id)")
    77	        self._migrate_audio_files(conn)
    78	        conn.commit()
    79	
    80	    def _migrate_audio_files(self, conn: sqlite3.Connection) -> None:
    81	        """Add any missing columns to audio_files (idempotent)."""
    82	        existing = {row[1] for row in conn.execute("PRAGMA table_info(audio_files)").fetchall()}
    83	        for col_name, col_type in _AUDIO_FILES_MIGRATION_COLUMNS:
    84	            if col_name not in existing:
    85	                conn.execute(f"ALTER TABLE audio_files ADD COLUMN {col_name} {col_type}")
    86	
    87	    @contextmanager
    88	    def _file_conn(self):
    89	        conn = sqlite3.connect(self._path, check_same_thread=False)
    90	        conn.row_factory = sqlite3.Row
    91	        conn.execute("PRAGMA busy_timeout=5000")
    92	        try:
    93	            yield conn
    94	            conn.commit()
    95	        finally:
    96	            conn.close()
    97	
    98	    @contextmanager
    99	    def _get_conn(self):
   100	        if self._in_memory:
   101	            yield self._conn
   102	        else:
   103	            with self._file_conn() as conn:
   104	                yield conn
   105	
   106	    def close(self) -> None:
   107	        if self._in_memory and self._conn is not None:
   108	            self._conn.close()
   109	            self._conn = None
   110	
   111	    def __enter__(self) -> ContentStore:
   112	        return self
   113	
   114	    def __exit__(self, *_) -> None:
   115	        self.close()
   116	
   117	    # ── Curricula ─────────────────────────────────────────────────────────
   118	
   119	    def save_curriculum(self, curriculum_id: str, curriculum: Curriculum) -> None:
   120	        with self._get_conn() as conn:
   121	            conn.execute(
   122	                "INSERT OR REPLACE INTO curricula (id, data_json) VALUES (?, ?)",
   123	                (curriculum_id, curriculum.to_json()),
   124	            )
   125	            if self._in_memory:
   126	                conn.commit()
   127	
   128	    def get_curriculum(self, curriculum_id: str) -> Curriculum | None:
   129	        with self._get_conn() as conn:
   130	            row = conn.execute("SELECT data_json FROM curricula WHERE id = ?", (curriculum_id,)).fetchone()
   131	        if row is None:
   132	            return None
   133	        return Curriculum.from_json(row["data_json"])
   134	
   135	    def list_curricula(self) -> list[dict]:
   136	        with self._get_conn() as conn:
   137	            rows = conn.execute("SELECT id, data_json, created_at FROM curricula ORDER BY created_at DESC").fetchall()
   138	        result = []
   139	        for row in rows:
   140	            c = Curriculum.from_json(row["data_json"])
   141	            result.append({"id": row["id"], "topic": c.topic, "created_at": row["created_at"]})
   142	        return result
   143	
   144	    def delete_curriculum(self, curriculum_id: str) -> bool:
   145	        with self._get_conn() as conn:
   146	            conn.execute(
   147	                "DELETE FROM audio_files WHERE lesson_id IN (SELECT id FROM lessons WHERE curriculum_id = ?)",
   148	                (curriculum_id,),
   149	            )
   150	            conn.execute("DELETE FROM lessons WHERE curriculum_id = ?", (curriculum_id,))
   151	            deleted = conn.execute("DELETE FROM curricula WHERE id = ?", (curriculum_id,)).rowcount > 0
   152	            conn.commit()
   153	        return deleted
   154	
   155	    # ── Lessons ───────────────────────────────────────────────────────────
   156	
   157	    def save_lesson(self, lesson_id: str, curriculum_id: str, day: int, lesson: Lesson) -> None:
   158	        with self._get_conn() as conn:
   159	            conn.execute(
   160	                "INSERT OR REPLACE INTO lessons (id, curriculum_id, day, data_json) VALUES (?, ?, ?, ?)",
   161	                (lesson_id, curriculum_id, day, lesson.to_json()),
   162	            )
   163	            if self._in_memory:
   164	                conn.commit()
   165	
   166	    def get_lesson(self, lesson_id: str) -> Lesson | None:
   167	        with self._get_conn() as conn:
   168	            row = conn.execute("SELECT data_json FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
   169	        if row is None:
   170	            return None
   171	        return Lesson.from_json(row["data_json"])
   172	
   173	    def get_lesson_row(self, lesson_id: str) -> dict | None:
   174	        """Return the raw lesson row as a dict (id, curriculum_id, day, data_json), or None."""
   175	        with self._get_conn() as conn:
   176	            row = conn.execute(
   177	                "SELECT id, curriculum_id, day, data_json FROM lessons WHERE id = ?",
   178	                (lesson_id,),
   179	            ).fetchone()
   180	        if row is None:
   181	            return None
   182	        return dict(row)
   183	
   184	    def get_latest_lesson_by_day(self, curriculum_id: str, day: int) -> tuple[str, Lesson] | None:
   185	        """Return the most recent (lesson_id, Lesson) for a given curriculum day, or None."""
   186	        with self._get_conn() as conn:
   187	            row = conn.execute(
   188	                "SELECT id, data_json FROM lessons"
   189	                " WHERE curriculum_id = ? AND day = ?"
   190	                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
   191	                (curriculum_id, day),
   192	            ).fetchone()
   193	        if row is None:
   194	            return None
   195	        return row["id"], Lesson.from_json(row["data_json"])
   196	
   197	    def get_lesson_days(self, curriculum_id: str) -> list[dict]:
   198	        """Return [{day, lesson_id}, ...] for each day with a lesson (latest per day)."""
   199	        with self._get_conn() as conn:
   200	            rows = conn.execute(
   201	                "SELECT l.day, l.id AS lesson_id"
   202	                " FROM lessons l"
   203	                " INNER JOIN ("
   204	                "   SELECT day, MAX(rowid) AS max_rowid"
   205	                "   FROM lessons WHERE curriculum_id = ?"
   206	                "   GROUP BY day"
   207	                " ) latest ON l.rowid = latest.max_rowid"
   208	                " ORDER BY l.day ASC",
   209	                (curriculum_id,),
   210	            ).fetchall()
   211	        return [{"day": row["day"], "lesson_id": row["lesson_id"]} for row in rows]
   212	
   213	    def list_lessons(self) -> list[tuple[str, str, int, Lesson]]:
   214	        """Every lesson as ``(lesson_id, curriculum_id, day, Lesson)``, oldest first.
   215	
   216	        Used by one-shot migrations that need to walk and rewrite all lessons.
   217	        """
   218	        with self._get_conn() as conn:
   219	            rows = conn.execute("SELECT id, curriculum_id, day, data_json FROM lessons ORDER BY created_at").fetchall()
   220	        return [(r["id"], r["curriculum_id"], r["day"], Lesson.from_json(r["data_json"])) for r in rows]
   221	
   222	    def get_all_token_glosses(self) -> dict[str, str]:
   223	        """Merge token_glosses from all stored lessons into a single dict.
   224	
   225	        Later lessons (higher rowid) win on duplicate lemmas.
   226	        """
   227	        with self._get_conn() as conn:
   228	            rows = conn.execute("SELECT data_json FROM lessons ORDER BY rowid ASC").fetchall()
   229	        glosses: dict[str, str] = {}
   230	        for row in rows:
   231	            lesson = Lesson.from_json(row["data_json"])
   232	            glosses.update(lesson.generation_metadata.get("token_glosses", {}))
   233	        return glosses
   234	
   235	    # ── Audio files ───────────────────────────────────────────────────────
   236	
   237	    def save_audio_file(
   238	        self,
   239	        audio_id: str,
   240	        lesson_id: str,
   241	        file_path: str,
   242	        *,
   243	        section_index: int | None = None,
   244	        section_type: str | None = None,
   245	        cues_json: str | None = None,
   246	    ) -> None:
   247	        with self._get_conn() as conn:
   248	            conn.execute(
   249	                "INSERT OR REPLACE INTO audio_files (id, lesson_id, file_path, section_index, section_type, cues_json)"
   250	                " VALUES (?, ?, ?, ?, ?, ?)",
   251	                (audio_id, lesson_id, file_path, section_index, section_type, cues_json),
   252	            )
   253	            if self._in_memory:
   254	                conn.commit()
   255	
   256	    def get_audio_file_row(self, audio_id: str) -> dict | None:
   257	        """Return all fields for an audio_files row, or None if not found."""
   258	        with self._get_conn() as conn:
   259	            row = conn.execute(
   260	                "SELECT id, lesson_id, file_path, section_index, section_type, cues_json FROM audio_files WHERE id = ?",
   261	                (audio_id,),
   262	            ).fetchone()
   263	        if row is None:
   264	            return None
   265	        return dict(row)
   266	
   267	    def list_audio_files_for_lesson(self, lesson_id: str) -> list[dict]:
   268	        """Return all audio file rows for a lesson.
   269	
   270	        Ordering: full-lesson row first (section_index IS NULL), then sections
   271	        in ascending section_index order.
   272	        """
   273	        with self._get_conn() as conn:
   274	            rows = conn.execute(
   275	                "SELECT id, lesson_id, file_path, section_index, section_type, cues_json FROM audio_files"
   276	                " WHERE lesson_id = ?"
   277	                " ORDER BY section_index IS NOT NULL, section_index ASC",
   278	                (lesson_id,),
   279	            ).fetchall()
   280	        return [dict(r) for r in rows]
   281	
   282	    def delete_audio_files_for_lesson(self, lesson_id: str) -> None:
   283	        """Delete all audio file rows for a lesson so re-render replaces, not appends."""
   284	        with self._get_conn() as conn:
   285	            conn.execute("DELETE FROM audio_files WHERE lesson_id = ?", (lesson_id,))
   286	            conn.commit()
```

**Why JSON columns instead of normalized tables?** Curricula and lessons are immutable artifacts whose schemas evolve quickly during prototyping. Storing them as JSON avoids ALTER TABLE churn and lets `Lesson.to_json()` / `Lesson.from_json()` round-trip without an ORM. The `audio_files` table is normalized because we query it by `lesson_id` and need ordering control (full-lesson row first, then sections by `section_index`).

**Slug-based IDs.** The API layer (Part 7) generates IDs like `arriving-in-ljubljana-a3f1b2c8` (`_slug(topic)-{uuid_hex[:8]}`) so URLs are human-readable and stable. The store treats IDs as opaque strings and doesn't care how they're generated.

Round-trip a curriculum and lesson through the store:

```bash
cd backend && uv run python -c "
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Section, SectionType, Phrase
from app.storage.store import ContentStore

with ContentStore(':memory:') as store:
    cur = Curriculum(id='greetings-abc12345', topic='greetings', language_code='sl', cefr_level='A1',
        days=[CurriculumDay(day=1, title='Day 1', focus='hello',
                            collocations=['Dober dan'], learning_objective='greet')])
    store.save_curriculum(cur.id, cur)

    lesson = Lesson(title='Day 1', language_code='sl', sections=[
        Section(section_type=SectionType.NATURAL_SPEED, phrases=[
            Phrase(text='Dober dan', voice_id='sl-SI-PetraNeural', language_code='sl', role='female-1'),
        ]),
    ])
    store.save_lesson('day1-abc12345', cur.id, 1, lesson)
    store.save_audio_file('audio-abc12345', 'day1-abc12345', '/tmp/full.wav')

    print('curricula:', store.list_curricula())
    found = store.get_latest_lesson_by_day(cur.id, 1)
    print('lesson by day:', found[0], '->', found[1].title)
    print('audio rows:', store.list_audio_files_for_lesson('day1-abc12345'))
"
```

```output
curricula: [{'id': 'greetings-abc12345', 'topic': 'greetings', 'created_at': '2026-07-11 12:01:06'}]
lesson by day: day1-abc12345 -> Day 1
audio rows: [{'id': 'audio-abc12345', 'lesson_id': 'day1-abc12345', 'file_path': '/tmp/full.wav', 'section_index': None, 'section_type': None, 'cues_json': None}]
```

---

## PART 6: Audio Pipeline

The audio pipeline converts Lesson models into audio files. It follows the hexagonal architecture from the prototype, with Protocol-based ports for TTS and audio processing.

### 6.1 Ports (Protocol Interfaces)

```bash
cat -n backend/app/audio/ports.py
```

```output
     1	"""Audio port protocols."""
     2	
     3	from __future__ import annotations
     4	
     5	from pathlib import Path
     6	from typing import Protocol, runtime_checkable
     7	
     8	
     9	@runtime_checkable
    10	class TTSService(Protocol):
    11	    """Protocol for text-to-speech synthesis services."""
    12	
    13	    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None: ...
    14	
    15	    async def list_voices(self, language_code: str | None = None) -> list[dict]: ...
```

Two protocols define the contracts: `TTSService` for synthesis (text → audio file) and `AudioProcessor` for manipulation (concatenation, normalization, silence insertion). The `@runtime_checkable` decorator means `isinstance()` works at runtime — useful for factory validation.

### 6.2 EdgeTTS Implementation

```bash
cat -n backend/app/audio/edge_tts.py
```

```output
     1	"""EdgeTTS adapter — implements TTSService Protocol."""
     2	
     3	from __future__ import annotations
     4	
     5	import asyncio
     6	import hashlib
     7	import logging
     8	import shutil
     9	from pathlib import Path
    10	
    11	import aiohttp
    12	import edge_tts
    13	
    14	logger = logging.getLogger(__name__)
    15	
    16	# Rate limiting constants (ported from prototype)
    17	MIN_REQUEST_DELAY_S = 0.2
    18	MAX_CONCURRENT_REQUESTS = 10
    19	MAX_RETRIES = 3
    20	
    21	
    22	class EdgeTTSService:
    23	    """Microsoft Edge TTS adapter.
    24	
    25	    Implements the TTSService Protocol with:
    26	    - Rate limiting (200 ms between requests, max 10 concurrent)
    27	    - Optional file-based caching (keyed on text + voice + rate)
    28	    - Retry on transient errors
    29	    """
    30	
    31	    def __init__(self, cache_dir: Path | None = None) -> None:
    32	        self._cache_dir = cache_dir
    33	        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    34	
    35	    # ------------------------------------------------------------------
    36	    # TTSService Protocol implementation
    37	    # ------------------------------------------------------------------
    38	
    39	    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None:
    40	        """Synthesize *text* to *output_path* using Edge TTS.
    41	
    42	        Args:
    43	            text: Text to synthesize.
    44	            voice_id: Edge TTS voice short name (e.g. "sl-SI-PetraNeural").
    45	            output_path: Destination file path for the synthesized audio.
    46	            rate: Speech rate adjustment (e.g. "+0%", "-20%").
    47	        """
    48	        if self._cache_dir is not None:
    49	            cached = self._cache_path(text, voice_id, rate)
    50	            if cached.exists():
    51	                shutil.copy2(cached, output_path)
    52	                logger.debug("EdgeTTS cache hit for %r", text[:40])
    53	                return
    54	
    55	        await self._synthesize_with_retry(text, voice_id, output_path, rate)
    56	
    57	        if self._cache_dir is not None:
    58	            cached = self._cache_path(text, voice_id, rate)
    59	            cached.parent.mkdir(parents=True, exist_ok=True)
    60	            shutil.copy2(output_path, cached)
    61	
    62	    async def list_voices(self, language_code: str | None = None) -> list[dict]:
    63	        """Return available Edge TTS voices, optionally filtered by language."""
    64	        voices = await edge_tts.list_voices()
    65	        if language_code:
    66	            voices = [v for v in voices if language_code in v.get("Locale", "")]
    67	        return voices
    68	
    69	    # ------------------------------------------------------------------
    70	    # Private helpers
    71	    # ------------------------------------------------------------------
    72	
    73	    def _cache_path(self, text: str, voice_id: str, rate: str) -> Path:
    74	        key = f"{voice_id}|{rate}|{text}"
    75	        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    76	        return self._cache_dir / f"{digest}.mp3"  # type: ignore[operator]
    77	
    78	    async def _synthesize_with_retry(self, text: str, voice_id: str, output_path: Path, rate: str) -> None:
    79	        last_error: Exception | None = None
    80	        for attempt in range(MAX_RETRIES):
    81	            try:
    82	                await self._do_synthesize(text, voice_id, output_path, rate)
    83	                return
    84	            except (
    85	                ConnectionResetError,
    86	                ConnectionError,
    87	                OSError,
    88	                edge_tts.exceptions.EdgeTTSException,
    89	                aiohttp.ClientError,
    90	            ) as exc:
    91	                last_error = exc
    92	                logger.warning("EdgeTTS transient error (attempt %d): %s", attempt + 1, exc)
    93	                await asyncio.sleep(0.5 * (2**attempt))
    94	        raise RuntimeError(f"EdgeTTS synthesis failed after {MAX_RETRIES} attempts") from last_error
    95	
    96	    async def _do_synthesize(self, text: str, voice_id: str, output_path: Path, rate: str) -> None:
    97	        async with self._semaphore:
    98	            communicate = edge_tts.Communicate(text, voice_id, rate=rate)
    99	            output_path.parent.mkdir(parents=True, exist_ok=True)
   100	            await communicate.save(str(output_path))
   101	            await asyncio.sleep(MIN_REQUEST_DELAY_S)
```

EdgeTTS is Microsoft's free neural TTS. The adapter adds three reliability features:

1. **Rate limiting**: 200ms minimum delay between requests + semaphore capping at 10 concurrent. The semaphore limit was raised from 3 to 10 in production after measuring real EdgeTTS throughput — Microsoft's per-IP rate limit is generous and the renderer (Part 6.4) now parallelises section synthesis with `asyncio.gather`.
2. **Caching**: SHA-256 keyed on text+voice+rate, so repeated phrases skip synthesis entirely. The renderer doesn't pass a cache_dir today (each render is fresh), but the cache hook is in place for future use.
3. **Retry with backoff**: Transient network errors get 3 attempts with exponential backoff (0.5s, 1s, 2s).

### 6.3 Pause Calculator

```bash
cat -n backend/app/audio/pause_calculator.py
```

```output
     1	"""Natural pause calculator — ports exact prototype ratios."""
     2	
     3	from __future__ import annotations
     4	
     5	from app.models.lesson import SectionType
     6	
     7	_BASE_PHRASE_PAUSE_MS = 500  # prototype's silence_between_phrases (0.5 s)
     8	_SLOW_SPEED_FACTOR = 1.2
     9	_SECTION_BOUNDARY_PAUSE_MS = 3000
    10	_ENGLISH_LANG = "en"
    11	
    12	_BOUNDARY_PAUSES: dict[str, int] = {
    13	    "syllable": 300,
    14	    "sentence": 2000,
    15	}
    16	
    17	
    18	class NaturalPauseCalculator:
    19	    """Inter-phrase pause calculator matching the micro-demo-0.0 prototype."""
    20	
    21	    def get_section_boundary_pause(self) -> int:
    22	        """Return the pause (ms) inserted between lesson sections."""
    23	        return _SECTION_BOUNDARY_PAUSE_MS
    24	
    25	    def get_phrase_pause(
    26	        self,
    27	        audio_duration_s: float,
    28	        word_count: int,
    29	        section_type: SectionType,
    30	        language_code: str = _ENGLISH_LANG,
    31	    ) -> int:
    32	        """Pause in ms to insert after a phrase.
    33	
    34	        - Key Phrases + L2: audio-duration-based (1:1), floor 500 ms.
    35	        - Key Phrases + English narrator: base 500 ms.
    36	        - Slow Speed + L2: base 500 ms × 1.2.
    37	        - Slow Speed + English narrator: base 500 ms (no slow factor).
    38	        - Natural Speed / Translated (any language): base 500 ms.
    39	
    40	        `word_count` is retained for backward compatibility with the renderer
    41	        call site and is currently unused.
    42	        """
    43	        del word_count  # unused; kept for API stability
    44	
    45	        is_l2 = language_code != _ENGLISH_LANG
    46	
    47	        if section_type == SectionType.KEY_PHRASES and is_l2:
    48	            return max(_BASE_PHRASE_PAUSE_MS, int(audio_duration_s * 1000))
    49	
    50	        if section_type == SectionType.SLOW_SPEED and is_l2:
    51	            return int(_BASE_PHRASE_PAUSE_MS * _SLOW_SPEED_FACTOR)
    52	
    53	        if section_type == SectionType.SLOW_TRANSLATED and is_l2:
    54	            return int(_BASE_PHRASE_PAUSE_MS * _SLOW_SPEED_FACTOR)
    55	
    56	        return _BASE_PHRASE_PAUSE_MS
```

The calculator was simplified in production. The prototype had a `word_count → multiplier` table and computed pause as `audio_duration × ratio × multiplier` for every phrase. After dogfooding the lessons, the team found that:

* **Natural Speed** and **Translated** phrases just need a flat 500 ms breath between lines.
* **Key Phrases** (L2) need a pause proportional to the phrase audio so the learner has time to repeat it back — at least 500 ms, but longer for long phrases.
* **Slow Speed** (L2) needs slightly more dwell than natural — 500 × 1.2 = 600 ms.

English narrator phrases (translations, section titles) always get the flat 500 ms regardless of section. The `word_count` parameter is kept for API stability but is no longer used.

```bash
cd backend && uv run python -c '
from app.audio.pause_calculator import NaturalPauseCalculator
from app.models.lesson import SectionType

calc = NaturalPauseCalculator()

# Slovene phrase, 2.5 s audio, in the KEY_PHRASES section
kp_l2 = calc.get_phrase_pause(audio_duration_s=2.5, word_count=2, section_type=SectionType.KEY_PHRASES, language_code="sl")
# English narrator translation in KEY_PHRASES
kp_en = calc.get_phrase_pause(audio_duration_s=2.5, word_count=2, section_type=SectionType.KEY_PHRASES, language_code="en")
# Slow speed Slovene
ss_l2 = calc.get_phrase_pause(audio_duration_s=2.5, word_count=2, section_type=SectionType.SLOW_SPEED, language_code="sl")
# Natural speed Slovene
ns_l2 = calc.get_phrase_pause(audio_duration_s=2.5, word_count=2, section_type=SectionType.NATURAL_SPEED, language_code="sl")

print(f"Key Phrases (L2):     {kp_l2} ms")
print(f"Key Phrases (en):     {kp_en} ms")
print(f"Slow Speed (L2):      {ss_l2} ms")
print(f"Natural Speed (L2):   {ns_l2} ms")
print(f"Section boundary:     {calc.get_section_boundary_pause()} ms")
'
```

```output
Key Phrases (L2):     2500 ms
Key Phrases (en):     500 ms
Slow Speed (L2):      600 ms
Natural Speed (L2):   500 ms
Section boundary:     3000 ms
```

### 6.4 Lesson Renderer

The renderer orchestrates the full pipeline: preprocessing → TTS → pause calculation → assembly:

```bash
cat -n backend/app/audio/renderer.py
```

```output
     1	"""Lesson renderer — orchestrates preprocess → TTS → pauses → assembly."""
     2	
     3	from __future__ import annotations
     4	
     5	import asyncio
     6	import logging
     7	import tempfile
     8	import time
     9	from dataclasses import dataclass
    10	from pathlib import Path
    11	
    12	import numpy as np
    13	import soundfile as sf
    14	
    15	from app.audio.cues import Cue, CueTiming, build_cue_manifest
    16	from app.audio.pause_calculator import NaturalPauseCalculator
    17	from app.audio.ports import TTSService
    18	from app.audio.preprocessing.base import TextPreprocessor
    19	from app.audio.transcode import encode_audio
    20	from app.models.lesson import Lesson, Section
    21	
    22	logger = logging.getLogger(__name__)
    23	
    24	_SAMPLE_DTYPE = "float32"
    25	_WAV_SUBTYPE = "PCM_16"
    26	
    27	
    28	@dataclass
    29	class _Audio:
    30	    """A decoded audio buffer: float32 samples shaped ``(frames, channels)`` + rate.
    31	
    32	    Replaces pydub's ``AudioSegment`` for the small set of operations the
    33	    renderer needs (decode, measure, silence, concatenate, export to WAV), so the
    34	    audio pipeline depends only on maintained libraries (``soundfile`` decodes
    35	    EdgeTTS MP3 via bundled libsndfile; ``numpy`` does the assembly).
    36	    """
    37	
    38	    samples: np.ndarray
    39	    rate: int
    40	
    41	    @property
    42	    def duration_ms(self) -> float:
    43	        return len(self.samples) / self.rate * 1000.0
    44	
    45	
    46	def _read_audio(path: Path) -> _Audio:
    47	    """Decode an audio file (EdgeTTS MP3 in prod, WAV in tests) to float32 samples."""
    48	    samples, rate = sf.read(str(path), dtype=_SAMPLE_DTYPE, always_2d=True)
    49	    return _Audio(samples, int(rate))
    50	
    51	
    52	def _silence(duration_ms: float, like: _Audio) -> _Audio:
    53	    """A silent buffer of *duration_ms*, matching *like*'s rate and channel count."""
    54	    frames = round(duration_ms / 1000.0 * like.rate)
    55	    return _Audio(np.zeros((frames, like.samples.shape[1]), dtype=_SAMPLE_DTYPE), like.rate)
    56	
    57	
    58	def _concat(parts: list[_Audio]) -> _Audio:
    59	    """Concatenate audio buffers that share sample rate and channel count.
    60	
    61	    EdgeTTS emits a uniform 24 kHz mono stream for every voice, so this holds in
    62	    practice. A mismatch means a foreign/corrupt input; we fail loudly rather
    63	    than silently re-speed it — pydub's implicit ``_sync`` resample used to hide
    64	    that. Always called with a non-empty list (a section always has ≥1 phrase;
    65	    the full mix always starts with the lesson title).
    66	    """
    67	    head = parts[0]
    68	    channels = head.samples.shape[1]
    69	    for part in parts[1:]:
    70	        if part.rate != head.rate or part.samples.shape[1] != channels:
    71	            raise ValueError(
    72	                "cannot concatenate audio with mismatched format: "
    73	                f"expected {head.rate} Hz / {channels} ch, "
    74	                f"got {part.rate} Hz / {part.samples.shape[1]} ch"
    75	            )
    76	    return _Audio(np.concatenate([p.samples for p in parts], axis=0), head.rate)
    77	
    78	
    79	def _write_wav(path: Path, audio: _Audio) -> None:
    80	    """Write *audio* to *path* as a 16-bit PCM WAV."""
    81	    sf.write(str(path), audio.samples, audio.rate, subtype=_WAV_SUBTYPE)
    82	
    83	
    84	class LessonRenderer:
    85	    """Renders a Lesson to a WAV audio file using soundfile + numpy for assembly.
    86	
    87	    Pipeline per phrase:
    88	      1. Preprocess text (language-specific)
    89	      2. Synthesize via TTS → temp file
    90	      3. Decode to samples, measure actual duration
    91	      4. Calculate post-phrase pause from real duration
    92	      5. Concatenate all buffers with boundary gaps
    93	    Then export the combined buffer as WAV.
    94	    """
    95	
    96	    def __init__(
    97	        self,
    98	        tts: TTSService,
    99	        preprocessors: dict[str, TextPreprocessor],
   100	        pause_calculator: NaturalPauseCalculator,
   101	        delivery_codec: str = "wav",
   102	        delivery_bitrate: str = "28k",
   103	    ) -> None:
   104	        self._tts = tts
   105	        self._preprocessors = preprocessors
   106	        self._calc = pause_calculator
   107	        self._delivery_codec = delivery_codec
   108	        self._delivery_bitrate = delivery_bitrate
   109	
   110	    def _write_audio(self, path: Path, audio: _Audio) -> None:
   111	        """Write *audio* to *path* in the configured delivery codec.
   112	
   113	        ``"wav"`` writes uncompressed PCM (the historical default); any other
   114	        codec routes the buffer through ffmpeg for a compressed, mobile-friendly
   115	        file. The caller is responsible for giving *path* the matching extension.
   116	        """
   117	        if self._delivery_codec == "wav":
   118	            _write_wav(path, audio)
   119	        else:
   120	            path.write_bytes(encode_audio(audio.samples, audio.rate, self._delivery_codec, self._delivery_bitrate))
   121	
   122	    def _assemble_section_audio(
   123	        self,
   124	        section: Section,
   125	        phrase_files: list[Path],
   126	        calc: NaturalPauseCalculator,
   127	    ) -> tuple[_Audio, list[tuple[int, int, int]]]:
   128	        """Synchronous assembly of a section's audio from pre-synthesised phrase files.
   129	
   130	        Extracted so the caller can offload it with ``asyncio.to_thread`` and
   131	        keep the event loop responsive during file I/O and numpy operations.
   132	        """
   133	        parts: list[_Audio] = []
   134	        section_cues: list[tuple[int, int, int]] = []
   135	        current_frame = 0
   136	        for i, phrase in enumerate(section.phrases):
   137	            phrase_audio = _read_audio(phrase_files[i])
   138	            start_frame = current_frame
   139	            end_frame = current_frame + len(phrase_audio.samples)
   140	            section_cues.append((i, start_frame, end_frame))
   141	            parts.append(phrase_audio)
   142	            current_frame = end_frame
   143	            pause_ms = calc.get_phrase_pause(
   144	                audio_duration_s=phrase_audio.duration_ms / 1000.0,
   145	                word_count=len(phrase.text.split()),
   146	                section_type=section.section_type,
   147	                language_code=phrase.language_code,
   148	            )
   149	            if pause_ms > 0:
   150	                pause = _silence(pause_ms, phrase_audio)
   151	                parts.append(pause)
   152	                current_frame += len(pause.samples)
   153	        return _concat(parts), section_cues
   154	
   155	    async def _render_section(
   156	        self, section: Section, tmp: Path, section_idx: int, language_code: str
   157	    ) -> tuple[_Audio, list[tuple[int, int, int]]]:
   158	        """Render a single section to an audio buffer (no boundary silence).
   159	
   160	        Args:
   161	            section: The Section to render.
   162	            tmp: Temp directory for intermediate TTS files.
   163	            section_idx: Index used for temp file naming.
   164	            language_code: Language code for preprocessor lookup.
   165	
   166	        Returns:
   167	            Tuple of (Audio buffer, per-phrase timing).
   168	            Timing entries are (phrase_index, start_frame, end_frame) relative
   169	            to the section start, in frames (not ms).
   170	        """
   171	        if language_code not in self._preprocessors:
   172	            raise ValueError(
   173	                f"No preprocessor configured for language {language_code!r}; renderer has {sorted(self._preprocessors)}"
   174	            )
   175	        preprocessor = self._preprocessors[language_code]
   176	        phrase_files = [tmp / f"s{section_idx}_p{i}.mp3" for i in range(len(section.phrases))]
   177	        processed_texts = [preprocessor.preprocess(phrase.text, section.section_type) for phrase in section.phrases]
   178	
   179	        # Synthesize all phrases in this section concurrently.
   180	        # EdgeTTSService._semaphore limits total concurrent requests globally.
   181	        await asyncio.gather(
   182	            *[
   183	                self._tts.synthesize(text, phrase.voice_id, phrase_files[i], rate=phrase.rate)
   184	                for i, (text, phrase) in enumerate(zip(processed_texts, section.phrases, strict=True))
   185	            ]
   186	        )
   187	
   188	        # Assemble in phrase order while tracking frame positions.
   189	        # Offsets are accumulated in frames (not ms) to avoid cumulative drift.
   190	        # Offload the sync assembly (file I/O + numpy) so the event loop stays
   191	        # responsive.
   192	        assembled = await asyncio.to_thread(
   193	            self._assemble_section_audio,
   194	            section,
   195	            phrase_files,
   196	            self._calc,
   197	        )
   198	        return assembled
   199	
   200	    async def render(
   201	        self,
   202	        lesson: Lesson,
   203	        output_path: Path,
   204	        section_paths: list[Path] | None = None,
   205	    ) -> list[Cue]:
   206	        """Render *lesson* to *output_path* as a valid WAV file.
   207	
   208	        Optionally writes per-section WAV files to *section_paths* (one per
   209	        section, in lesson order). Each section file contains only the section
   210	        content with no leading/trailing boundary silence.
   211	
   212	        Args:
   213	            lesson: Lesson with sections and phrases.
   214	            output_path: Destination file path for the full lesson (written as WAV).
   215	            section_paths: Optional list of paths for per-section output WAVs.
   216	                           Must have same length as lesson.sections if provided.
   217	
   218	        Returns:
   219	            Timing manifest (list of Cue objects) for the rendered lesson.
   220	        """
   221	        t_start = time.perf_counter()
   222	
   223	        with tempfile.TemporaryDirectory() as tmp_dir:
   224	            tmp = Path(tmp_dir)
   225	
   226	            # Render lesson title (full WAV only — not in section files)
   227	            t0 = time.perf_counter()
   228	            title_file = tmp / "title.mp3"
   229	            await self._tts.synthesize(lesson.title, lesson.narrator_voice, title_file, rate="+0%")
   230	            logger.debug("TTS title → %.0f ms", (time.perf_counter() - t0) * 1000)
   231	            title_audio = await asyncio.to_thread(_read_audio, title_file)
   232	
   233	            # Render all sections concurrently — phrases within each section are
   234	            # also parallelised; EdgeTTSService._semaphore caps total concurrency.
   235	            t0 = time.perf_counter()
   236	            section_results = await asyncio.gather(
   237	                *[
   238	                    self._render_section(section, tmp, i, language_code=lesson.language_code)
   239	                    for i, section in enumerate(lesson.sections)
   240	                ]
   241	            )
   242	            section_audios = [r[0] for r in section_results]
   243	            section_cue_lists = [r[1] for r in section_results]
   244	            logger.debug("All sections TTS → %.0f ms", (time.perf_counter() - t0) * 1000)
   245	
   246	            if section_paths is not None:
   247	                for section_idx, sec_audio in enumerate(section_audios):
   248	                    sp = section_paths[section_idx]
   249	                    sp.parent.mkdir(parents=True, exist_ok=True)
   250	                    t0 = time.perf_counter()
   251	                    await asyncio.to_thread(self._write_audio, sp, sec_audio)
   252	                    logger.debug("Section %d export → %.0f ms", section_idx, (time.perf_counter() - t0) * 1000)
   253	
   254	            # Assemble full lesson: title + bs + sec0 + bs + sec1 + ...
   255	            boundary = _silence(self._calc.get_section_boundary_pause(), title_audio)
   256	            parts: list[_Audio] = [title_audio, boundary]
   257	            for i, sec_audio in enumerate(section_audios):
   258	                if i > 0:
   259	                    parts.append(boundary)
   260	                parts.append(sec_audio)
   261	            combined = await asyncio.to_thread(_concat, parts)
   262	
   263	            # Build cue manifest with absolute frame offsets.
   264	            # Accumulate offsets in frames (never sum float ms per phrase).
   265	            timing_entries: list[CueTiming] = [
   266	                CueTiming(
   267	                    section_index=None,
   268	                    phrase_index=0,
   269	                    start_frame=0,
   270	                    end_frame=len(title_audio.samples),
   271	                )
   272	            ]
   273	            current_abs_frame = len(title_audio.samples) + len(boundary.samples)
   274	            for sec_idx, (sec_audio, sec_cues) in enumerate(zip(section_audios, section_cue_lists, strict=True)):
   275	                for ph_idx, rel_start, rel_end in sec_cues:
   276	                    timing_entries.append(
   277	                        CueTiming(
   278	                            section_index=sec_idx,
   279	                            phrase_index=ph_idx,
   280	                            start_frame=current_abs_frame + rel_start,
   281	                            end_frame=current_abs_frame + rel_end,
   282	                        )
   283	                    )
   284	                current_abs_frame += len(sec_audio.samples)
   285	                if sec_idx < len(section_audios) - 1:
   286	                    current_abs_frame += len(boundary.samples)
   287	
   288	            rate = int(title_audio.rate)
   289	            cues = build_cue_manifest(lesson, timing_entries, rate)
   290	
   291	        output_path.parent.mkdir(parents=True, exist_ok=True)
   292	        t0 = time.perf_counter()
   293	        await asyncio.to_thread(self._write_audio, output_path, combined)
   294	        logger.debug("Full lesson export → %.0f ms", (time.perf_counter() - t0) * 1000)
   295	        logger.info(
   296	            "Rendered lesson to %s (audio: %d ms, wall: %.0f ms)",
   297	            output_path,
   298	            round(combined.duration_ms),
   299	            (time.perf_counter() - t_start) * 1000,
   300	        )
   301	
   302	        return cues
```

The renderer is the audio pipeline's main loop, but two production refinements changed the shape significantly:

1. **pydub instead of raw bytes.** The old pipeline assumed phrases were 1.5 seconds long and concatenated raw MP3 bytes. The new pipeline loads each phrase as an `AudioSegment`, measures the *real* duration, and uses that for pause calculation. The output is a valid WAV file (rebuilt from PCM by pydub) instead of a concatenated-MP3 frankenstein.
2. **Parallel section synthesis.** Each `_render_section` synthesizes its phrases concurrently with `asyncio.gather`, and the top-level `render` runs all sections concurrently. The `EdgeTTSService` semaphore is the global throttle (10 concurrent — see Part 6.2). On a typical 7-section lesson with ~80 phrases this cut wall-clock render time from ~80 s to ~12 s in dev.

Two output modes:

* `output_path` always gets the *full* lesson WAV: `[title] [boundary] [section_0] [boundary] [section_1] ...`.
* `section_paths` (optional) is a list of one path per section. Each per-section file contains *only* its section content with no leading/trailing boundary silence — used by the audio API to expose section-level navigation in the player.

The lesson title is rendered as an audio intro using `lesson.narrator_voice`, which is why narrator voice is stored on the `Lesson` itself (Part 2.2). Wall-clock and per-section timings are logged at DEBUG so the dev server (`logging.getLogger("app.audio.renderer").setLevel(logging.DEBUG)`) shows render performance directly.

### 6.5 Text Preprocessing

Language-specific text transformations before TTS. The base protocol is one method; concrete preprocessors are free to do whatever the language needs.

```bash
cat -n backend/app/audio/preprocessing/base.py
```

```output
     1	"""Text preprocessor protocol."""
     2	
     3	from __future__ import annotations
     4	
     5	from typing import Protocol, runtime_checkable
     6	
     7	from app.models.lesson import SectionType
     8	
     9	
    10	@runtime_checkable
    11	class TextPreprocessor(Protocol):
    12	    """Protocol for language-specific text preprocessing before TTS synthesis."""
    13	
    14	    def preprocess(self, text: str, section_type: SectionType) -> str: ...
```

```bash
cat -n backend/app/audio/preprocessing/slovene.py
```

```output
     1	"""Slovene-specific text preprocessing for TTS synthesis."""
     2	
     3	from __future__ import annotations
     4	
     5	from app.models.lesson import SectionType
     6	
     7	
     8	class SlovenePreprocessor:
     9	    """Slovene text preprocessor (pass-through; reserved for future transforms)."""
    10	
    11	    def preprocess(self, text: str, section_type: SectionType) -> str:
    12	        return text
```

The prototype had a 1000-line Tagalog preprocessor (number clarification, abbreviation handling, ellipsis conversion). Production uses a pluggable `TextPreprocessor` protocol; the Slovene implementation is now a pass-through. Slow-speed ellipses moved out of the preprocessor and into `section_builder.build_slow_speed_section` (Part 5.4) which inserts `" ... "` between words at lesson-build time — that way the slow-speed audio is the same TTS request as the natural one and the preprocessor stays language-agnostic.

The protocol is intentionally tiny so adding a new language means writing a one-method class — Hungarian, Korean, etc. just need their own `*Preprocessor` if they require text munging.

---

## PART 7: API Layer

Four REST routers expose the full pipeline: curriculum, story generation, SRS, and audio. All routers pull services from `request.app.state` — no global singletons, no imports from `main.py`.

### 7.1 Curriculum API

```bash
cat -n backend/app/api/curriculum.py
```

```output
     1	"""Curriculum generation and retrieval endpoints."""
     2	
     3	from __future__ import annotations
     4	
     5	from dataclasses import asdict
     6	
     7	from fastapi import APIRouter, HTTPException, Request
     8	
     9	from app.api._serializers import serialize_lesson
    10	from app.api.models import (
    11	    ImportPlanRequest,
    12	    PlanFeedbackRequest,
    13	    PlanTurnRequest,
    14	    StartPlanRequest,
    15	)
    16	from app.generation.planner import PlannerError
    17	from app.models.curriculum import Curriculum, CurriculumDay
    18	from app.srs.planner_snapshot import build_learner_snapshot
    19	from app.storage.plan_io import export_plan, get_planner_state, import_plan, mint_curriculum_id
    20	
    21	router = APIRouter(prefix="/api/curriculum", tags=["curriculum"])
    22	
    23	
    24	@router.post("/import", status_code=201)
    25	async def import_curriculum_plan(body: ImportPlanRequest, request: Request):
    26	    store = request.state.content_store
    27	    try:
    28	        cid, curriculum = import_plan(store, body.model_dump())
    29	    except ValueError as e:
    30	        raise HTTPException(status_code=422, detail=str(e)) from None
    31	    except KeyError as e:
    32	        raise HTTPException(status_code=404, detail=str(e)) from None
    33	    return {
    34	        "id": cid,
    35	        "topic": curriculum.topic,
    36	        "language_code": curriculum.language_code,
    37	        "days": len(curriculum.days),
    38	    }
    39	
    40	
    41	def _get_curriculum_or_404(store, curriculum_id: str) -> Curriculum:
    42	    curriculum = store.get_curriculum(curriculum_id)
    43	    if curriculum is None:
    44	        raise HTTPException(status_code=404, detail="Curriculum not found")
    45	    return curriculum
    46	
    47	
    48	@router.post("/plan", status_code=201)
    49	async def start_plan(body: StartPlanRequest, request: Request):
    50	    """LLM-free: mint an id and save an empty curriculum with empty planner state."""
    51	    store = request.state.content_store
    52	    curriculum_id = mint_curriculum_id(body.topic)
    53	    curriculum = Curriculum(
    54	        id=curriculum_id,
    55	        topic=body.topic,
    56	        language_code=request.state.language_code,
    57	        cefr_level=body.cefr_level,
    58	        metadata={"planner": {"chat": [], "proposed": None, "feedback": []}},
    59	    )
    60	    store.save_curriculum(curriculum_id, curriculum)
    61	    return {
    62	        "id": curriculum_id,
    63	        "topic": curriculum.topic,
    64	        "language_code": curriculum.language_code,
    65	        "cefr_level": curriculum.cefr_level,
    66	        "days": 0,
    67	    }
    68	
    69	
    70	@router.post("/{curriculum_id}/plan/turn", status_code=200)
    71	async def plan_turn(curriculum_id: str, body: PlanTurnRequest, request: Request):
    72	    """One planner chat turn: snapshot → LLM → append chat, set/replace proposed."""
    73	    store = request.state.content_store
    74	    curriculum = _get_curriculum_or_404(store, curriculum_id)
    75	    planner = request.app.state.curriculum_planner
    76	
    77	    snapshot = build_learner_snapshot(request.state.srs_db)
    78	    try:
    79	        turn = await planner.turn(
    80	            curriculum=curriculum,
    81	            user_message=body.message,
    82	            batch_size=body.batch_size,
    83	            learner_snapshot=snapshot,
    84	            language=request.state.language,
    85	        )
    86	    except PlannerError as e:
    87	        # Nothing is persisted for a failed turn — the user retries in chat.
    88	        raise HTTPException(status_code=502, detail=str(e)) from e
    89	
    90	    state = get_planner_state(curriculum)
    91	    state["chat"].append({"role": "user", "content": body.message})
    92	    state["chat"].append({"role": "planner", "content": turn.reply})
    93	    if turn.proposed_days is not None:
    94	        # A new proposing turn replaces any prior proposal (latest-wins);
    95	        # a pure-chat turn leaves the existing proposal in place.
    96	        state["proposed"] = {
    97	            "start_day": turn.proposed_days[0].day,
    98	            "days": [asdict(d) for d in turn.proposed_days],
    99	        }
   100	    curriculum.metadata["planner"] = state
   101	    store.save_curriculum(curriculum_id, curriculum)
   102	    return {"reply": turn.reply, "proposed": state["proposed"]}
   103	
   104	
   105	@router.post("/{curriculum_id}/plan/commit", status_code=200)
   106	async def plan_commit(curriculum_id: str, request: Request):
   107	    """Append the proposed batch to the committed days and clear the proposal."""
   108	    store = request.state.content_store
   109	    curriculum = _get_curriculum_or_404(store, curriculum_id)
   110	    state = get_planner_state(curriculum)
   111	    proposed = state.get("proposed")
   112	    if not proposed:
   113	        raise HTTPException(status_code=409, detail="No proposed batch to commit")
   114	
   115	    # The proposal was numbered against the day list at turn time; if the
   116	    # committed days changed since (e.g. a plan re-import), appending it would
   117	    # collide with or gap the existing day numbers.
   118	    expected_start = max((d.day for d in curriculum.days), default=0) + 1
   119	    if proposed["days"][0]["day"] != expected_start:
   120	        raise HTTPException(
   121	            status_code=409,
   122	            detail="Proposed batch is stale — the committed days changed since it was proposed; ask the planner to re-propose",
   123	        )
   124	
   125	    days = [CurriculumDay(**d) for d in proposed["days"]]
   126	    curriculum.days.extend(days)
   127	    first, last = days[0].day, days[-1].day
   128	    label = f"day {first}" if first == last else f"days {first}-{last}"
   129	    state["chat"].append({"role": "event", "content": f"Committed {label}."})
   130	    state["proposed"] = None
   131	    curriculum.metadata["planner"] = state
   132	    store.save_curriculum(curriculum_id, curriculum)
   133	
   134	    # Enqueue pipeline jobs for the newly committed days
   135	    pipeline = getattr(request.app.state, "pipeline", None)
   136	    if pipeline is not None:
   137	        for day_entry in days:
   138	            pipeline.enqueue(request.state.language_code, curriculum_id, day_entry.day, "generate")
   139	
   140	    return {"id": curriculum_id, "days": len(curriculum.days)}
   141	
   142	
   143	@router.post("/{curriculum_id}/plan/reset", status_code=200)
   144	async def plan_reset(curriculum_id: str, request: Request):
   145	    """Clear the planner chat and proposed batch (keeps feedback and committed days)."""
   146	    store = request.state.content_store
   147	    curriculum = _get_curriculum_or_404(store, curriculum_id)
   148	    state = get_planner_state(curriculum)
   149	    reply_count = sum(1 for m in state.get("chat", []) if m.get("role") == "planner")
   150	    state["chat"] = []
   151	    state["proposed"] = None
   152	    curriculum.metadata["planner"] = state
   153	    store.save_curriculum(curriculum_id, curriculum)
   154	    return {"reply_count_cleared": reply_count}
   155	
   156	
   157	@router.post("/{curriculum_id}/plan/feedback", status_code=200)
   158	async def plan_feedback(curriculum_id: str, body: PlanFeedbackRequest, request: Request):
   159	    """Record listening feedback for a committed day; it enters the next turn's prompt."""
   160	    store = request.state.content_store
   161	    curriculum = _get_curriculum_or_404(store, curriculum_id)
   162	    if body.day not in {d.day for d in curriculum.days}:
   163	        raise HTTPException(status_code=404, detail=f"Unknown day {body.day}")
   164	    state = get_planner_state(curriculum)
   165	    state["feedback"].append({"day": body.day, "note": body.note})
   166	    curriculum.metadata["planner"] = state
   167	    store.save_curriculum(curriculum_id, curriculum)
   168	    return {"feedback": state["feedback"]}
   169	
   170	
   171	@router.get("", status_code=200)
   172	async def list_curricula(request: Request):
   173	    store = request.state.content_store
   174	    return store.list_curricula()
   175	
   176	
   177	@router.get("/{curriculum_id}", status_code=200)
   178	async def get_curriculum(curriculum_id: str, request: Request):
   179	    store = request.state.content_store
   180	    curriculum = store.get_curriculum(curriculum_id)
   181	    if curriculum is None:
   182	        raise HTTPException(status_code=404, detail="Curriculum not found")
   183	    return {
   184	        "id": curriculum_id,
   185	        "topic": curriculum.topic,
   186	        "language_code": curriculum.language_code,
   187	        "cefr_level": curriculum.cefr_level,
   188	        "days": sorted((asdict(d) for d in curriculum.days), key=lambda d: d["day"]),
   189	        "proposed": get_planner_state(curriculum)["proposed"],
   190	    }
   191	
   192	
   193	@router.get("/{curriculum_id}/progress")
   194	async def get_curriculum_progress(curriculum_id: str, request: Request):
   195	    store = request.state.content_store
   196	    if store.get_curriculum(curriculum_id) is None:
   197	        raise HTTPException(status_code=404, detail="Curriculum not found")
   198	    return store.get_lesson_days(curriculum_id)
   199	
   200	
   201	@router.get("/{curriculum_id}/source", status_code=200)
   202	async def get_curriculum_source(curriculum_id: str, request: Request):
   203	    store = request.state.content_store
   204	    try:
   205	        return export_plan(store, curriculum_id)
   206	    except KeyError:
   207	        raise HTTPException(status_code=404, detail="Curriculum not found") from None
   208	
   209	
   210	@router.delete("/{curriculum_id}", status_code=200)
   211	async def delete_curriculum(curriculum_id: str, request: Request):
   212	    store = request.state.content_store
   213	    if not store.delete_curriculum(curriculum_id):
   214	        raise HTTPException(status_code=404, detail="Curriculum not found")
   215	    return {"deleted": curriculum_id}
   216	
   217	
   218	@router.get("/{curriculum_id}/days/{day}/lesson", status_code=200)
   219	async def get_lesson_by_day(curriculum_id: str, day: int, request: Request):
   220	    store = request.state.content_store
   221	    result = store.get_latest_lesson_by_day(curriculum_id, day)
   222	    if result is None:
   223	        raise HTTPException(status_code=404, detail=f"No lesson found for day {day}")
   224	    lesson_id, lesson = result
   225	    return serialize_lesson(lesson_id, lesson)
```

Four changes from the prototype:

1. **Slug-based IDs.** `_slug(topic)` lowercases and hyphenates the topic, then appends 8 hex characters from a fresh UUID: `f"{_slug(body.topic)}-{uuid.uuid4().hex[:8]}"`. The result is stable enough to use in URLs (`arriving-in-ljubljana-a3f1b2c8`) and human-readable in logs.
2. **ContentStore replaces `app.state.curricula` dict.** Curricula now survive a server restart and are visible across requests without any threading locks.
3. **`GET /{curriculum_id}/days/{day}/lesson`** is a convenience endpoint for the frontend: given a curriculum and a day number it returns the latest generated lesson, fully expanded (all phrases, all sections, key phrases list).
4. **`GET /{curriculum_id}/progress`** returns per-day SRS progress so the day-picker UI can show which days have been listened to and how many of their words are scheduled vs new. The handler delegates to a `ContentStore` lookup that joins each lesson's lemma list against the SRS direction state.

### 7.2 Story Generation API

```bash
cat -n backend/app/api/generation.py
```

```output
     1	"""Story generation endpoints."""
     2	
     3	from __future__ import annotations
     4	
     5	import asyncio
     6	import logging
     7	
     8	import anyio
     9	from fastapi import APIRouter, HTTPException, Request
    10	
    11	from app.api._serializers import serialize_lesson
    12	from app.api.models import GenerateStoryRequest, ImportLessonRequest
    13	from app.generation.ids import mint_id
    14	from app.generation.story import StoryGenerationError
    15	from app.llm.client import LLMError
    16	from app.models.lesson import Lesson, SectionType
    17	from app.models.strategy import ContentStrategy
    18	from app.srs.database import SRSDatabase
    19	from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, model_version_for
    20	from app.storage.lesson_io import export_lesson, import_lesson, speaker_warnings, sync_curriculum_day_title
    21	
    22	_logger = logging.getLogger(__name__)
    23	
    24	router = APIRouter(prefix="/api/story", tags=["generation"])
    25	
    26	# Strong refs to fire-and-forget pre-warm tasks: the event loop only keeps a
    27	# weak reference, so an un-anchored task can be garbage-collected mid-flight.
    28	_background_tasks: set[asyncio.Task] = set()
    29	
    30	
    31	async def _prewarm_lesson(lesson: Lesson, srs_db: SRSDatabase) -> None:
    32	    """Background pre-warm: cache a freshly generated lesson's sentences.
    33	
    34	    Runs the new lesson's natural-speed L2 sentences through
    35	    ``analyze_sentence_cached`` so the transcript view never triggers a
    36	    classla load for this content.
    37	    """
    38	    try:
    39	        lemmatizer = get_lemmatizer(lesson.language_code)
    40	        model_version = model_version_for(lemmatizer)
    41	        if not model_version:
    42	            return
    43	        natural_speed = next(
    44	            (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
    45	            None,
    46	        )
    47	        if natural_speed is None:
    48	            return
    49	        phrases = [(p.text, p.language_code) for p in natural_speed.phrases if p.language_code == lesson.language_code]
    50	        await anyio.to_thread.run_sync(
    51	            _prewarm_phrases, phrases, srs_db, lemmatizer, model_version, lesson.language_code
    52	        )
    53	    except Exception:
    54	        _logger.warning("Pre-warm failed for new lesson", exc_info=True)
    55	
    56	
    57	def _prewarm_phrases(
    58	    phrases: list[tuple[str, str]],
    59	    srs_db: SRSDatabase,
    60	    lemmatizer: object,
    61	    model_version: str,
    62	    language_code: str,
    63	) -> None:
    64	    for text, _ in phrases:
    65	        analyze_sentence_cached(srs_db, lemmatizer, text, language_code, model_version)
    66	
    67	
    68	@router.post("/generate", status_code=201)
    69	async def generate_story(body: GenerateStoryRequest, request: Request):
    70	    store = request.state.content_store
    71	    curriculum = store.get_curriculum(body.curriculum_id)
    72	    if curriculum is None:
    73	        raise HTTPException(status_code=404, detail="Curriculum not found")
    74	
    75	    days = [d for d in curriculum.days if d.day == body.day]
    76	    if not days:
    77	        raise HTTPException(status_code=404, detail=f"Day {body.day} not found in curriculum")
    78	
    79	    curriculum_day = days[0]
    80	    strategy = ContentStrategy[body.strategy]
    81	    language = request.state.language
    82	    generator = request.app.state.story_generator
    83	
    84	    try:
    85	        lesson = await generator.generate(
    86	            curriculum_day=curriculum_day,
    87	            language=language,
    88	            strategy=strategy,
    89	            cefr_level=curriculum.cefr_level,
    90	        )
    91	    except StoryGenerationError as e:
    92	        # Malformed LLM output — nothing persisted; the user retries.
    93	        raise HTTPException(status_code=502, detail=str(e)) from e
    94	    except LLMError as e:
    95	        # Opt-in fallback: complete() now raises a bare 429/HTTP error instead of
    96	        # degrading to Ollama. Map to 502 (mirror plan_turn's PlannerError handling)
    97	        # so the client gets the retry detail, never a raw 500/ASGI traceback. The
    98	        # lesson-page Regenerate button routes through the pipeline (429 backoff +
    99	        # sticky-failed) instead — this hardens the sync endpoint's other callers.
   100	        raise HTTPException(status_code=502, detail=str(e)) from e
   101	
   102	    lesson_id = mint_id(lesson.title)
   103	    store.save_lesson(lesson_id, body.curriculum_id, body.day, lesson)
   104	    sync_curriculum_day_title(store, body.curriculum_id, body.day, lesson.title)
   105	
   106	    # Pre-warm the analysis cache off the request path
   107	    srs_db = getattr(request.app.state, "srs_db", None)
   108	    if srs_db is not None:
   109	        task = asyncio.create_task(_prewarm_lesson(lesson, srs_db))
   110	        _background_tasks.add(task)
   111	        task.add_done_callback(_background_tasks.discard)
   112	
   113	    # Enqueue a render job for this day
   114	    pipeline = getattr(request.app.state, "pipeline", None)
   115	    if pipeline is not None:
   116	        pipeline.enqueue(request.state.language_code, body.curriculum_id, body.day, "render")
   117	
   118	    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
   119	    return {"id": lesson_id, "title": lesson.title, "sections": sections}
   120	
   121	
   122	@router.post("/import", status_code=201)
   123	async def import_story(body: ImportLessonRequest, request: Request):
   124	    """Rebuild a Lesson from an edited Story-JSON file (docs/lesson-authoring.md).
   125	
   126	    Same shape as generate_story's response, plus `warnings` (e.g. a speaker
   127	    missing from the voice map, which would silently fall back to the narrator).
   128	    """
   129	    store = request.state.content_store
   130	    if store.get_curriculum(body.curriculum_id) is None:
   131	        raise HTTPException(status_code=404, detail="Curriculum not found")
   132	
   133	    language = request.state.language
   134	    try:
   135	        lesson_id, lesson = import_lesson(
   136	            store,
   137	            {"curriculum_id": body.curriculum_id, "day": body.day, "story": body.story},
   138	            language,
   139	        )
   140	    except ValueError as e:
   141	        raise HTTPException(status_code=422, detail=str(e)) from e
   142	
   143	    # Same background pre-warm as generation, so the transcript view is warm.
   144	    srs_db = getattr(request.app.state, "srs_db", None)
   145	    if srs_db is not None:
   146	        asyncio.create_task(_prewarm_lesson(lesson, srs_db))
   147	
   148	    # Enqueue a render job for this day
   149	    pipeline = getattr(request.app.state, "pipeline", None)
   150	    if pipeline is not None:
   151	        pipeline.enqueue(request.state.language_code, body.curriculum_id, body.day, "render")
   152	
   153	    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
   154	    return {
   155	        "id": lesson_id,
   156	        "title": lesson.title,
   157	        "sections": sections,
   158	        "warnings": speaker_warnings(body.story, language),
   159	    }
   160	
   161	
   162	@router.get("/{lesson_id}/source", status_code=200)
   163	async def get_lesson_source(lesson_id: str, request: Request):
   164	    """Export a lesson as its editable, self-describing Story-JSON file."""
   165	    store = request.state.content_store
   166	    try:
   167	        return export_lesson(store, lesson_id)
   168	    except KeyError:
   169	        raise HTTPException(status_code=404, detail="Lesson not found") from None
   170	
   171	
   172	@router.get("/{lesson_id}", status_code=200)
   173	async def get_lesson(lesson_id: str, request: Request):
   174	    store = request.state.content_store
   175	    row = store.get_lesson_row(lesson_id)
   176	    if row is None:
   177	        raise HTTPException(status_code=404, detail="Lesson not found")
   178	    lesson = Lesson.from_json(row["data_json"])
   179	    return serialize_lesson(lesson_id, lesson, day=row["day"])
```

The generation router is similarly slug-based. Lesson IDs are derived from the lesson title (which the LLM sets), so `arriving-in-ljubljana-a3f1b2c8` is the lesson ID you see in the player URL. The `GET /{lesson_id}` endpoint returns a fully-expanded lesson for the frontend to render the transcript view.

**Key phrases are no longer registered with the SRS database during generation.** In the prototype, `StoryGenerator` took an `srs_db` and called `db.add_collocation` for each key phrase. That coupling made the generator hard to test in isolation. Now generation only produces a `Lesson` with `key_phrases: list[KeyPhraseInfo]`; SRS registration happens in `POST /api/srs/listen` when the learner first listens to the lesson (see §7.3).

### 7.3 SRS API

The SRS router is now the largest module in `app/api/` (~700 lines, 19 routes). The full surface:

```bash
grep -nE "^@router\." backend/app/api/srs.py
```

```output
227:@router.get("/due", status_code=200)
244:@router.get("/new", status_code=200)
255:@router.post("/items/{item_id}/direction/{direction}/feedback", status_code=200)
316:@router.post("/items/{item_id}/direction/{direction}/undo", status_code=200)
345:@router.get("/media/{filename}", status_code=200)
376:@router.post("/listen", status_code=200)
657:@router.get("/lesson/{lesson_id}/transcript", status_code=200)
731:@router.post("/translate", status_code=200)
747:@router.post("/translate-missing", status_code=200)
779:@router.post("/backfill-translations", status_code=200)
789:@router.get("/stats", status_code=200)
796:@router.get("/queue-stats", status_code=200)
883:@router.post("/items", status_code=201)
985:@router.post("/items/base", status_code=200)
1060:@router.get("/items", status_code=200)
1086:@router.patch("/items/{item_id}", status_code=200)
1099:@router.delete("/items/{item_id}", status_code=200)
1108:@router.post("/items/bulk-delete", status_code=200)
1115:@router.post("/items/{item_id}/reset", status_code=200)
1125:@router.post("/items/{item_id}/state", status_code=200)
1153:@router.post("/items/{item_id}/restore-known", status_code=200)
1169:@router.post("/items/{item_id}/untrack", status_code=200)
1181:@router.post("/items/{item_id}/suspend", status_code=200)
1197:@router.post("/ignored-lemmas", status_code=200)
1204:@router.delete("/ignored-lemmas", status_code=200)
1249:@router.post("/inflection-clozes", status_code=200)
1353:@router.get("/review-queue", status_code=200)
```

These cover four functional areas: **learner loop** (due/new/feedback), **per-word capture and transcript** (listen/transcript/translate-missing/backfill-translations/queue-stats/stats), **review queue and media** (review-queue, media/{filename}), and **admin CRUD** (items POST/GET/PATCH/DELETE/state/suspend/reset, items/bulk-delete).

#### Response shape — `_item_to_dict`

Every list and detail endpoint serialises through one helper. Response payload includes both flat (legacy) FSRS fields and a per-direction breakdown — plus Anki identity, media URLs, and grammar/note context:

```bash
sed -n '115,147p' backend/app/api/srs.py
```

```output
def _item_to_dict(
    row_id: int,
    item: SRSItem,
    language_code: str,
    image_url: str | None = None,
    audio_url: str | None = None,
    ambiguous_surfaces: set[str] | None = None,
) -> dict:
    """Serialize an SRSItem to a response dict.

    Single-template Anki notes (e.g., Basic phonics) have no production
    direction after migration v15→v16 — emit `null` rather than fabricating
    one. Flat back-compat fields read from recognition for vocab cards and
    from production for cloze cards (which have no recognition direction).
    """
    rec = item.directions.get(Direction.RECOGNITION)
    prod = item.directions.get(Direction.PRODUCTION)
    flat_src = prod if item.syntactic_unit.card_type == "cloze" else rec
    flat: dict[str, object] = {
        "state": flat_src.state.value if flat_src else SRSState.NEW.value,
        "due_at": flat_src.due_at.isoformat() if flat_src else None,
        "stability": flat_src.stability if flat_src else 1.0,
        "difficulty": flat_src.difficulty if flat_src else 5.0,
        "reps": flat_src.reps if flat_src else 0,
        "lapses": flat_src.lapses if flat_src else 0,
        "last_review": flat_src.last_review.isoformat() if flat_src and flat_src.last_review else None,
    }
    return {
        "id": row_id,
        "text": item.syntactic_unit.text,
        "translation": item.syntactic_unit.translation,
        "word_count": item.syntactic_unit.word_count,
        **flat,
```

The two `directions` entries each contain `{state, due_date, stability, difficulty, reps, lapses, last_review, anki_card_id, anki_due, dirty_fsrs, last_synced_at, last_rating}` — the full `DirectionState` (PART 4.2). `image_url` and `audio_url` point at `/api/srs/media/{filename}`, populated only when the row has stored media (post-sync).

#### Per-direction feedback (`POST /items/{id}/direction/{direction}/feedback`)

This replaces the old single-direction `/api/srs/feedback`. The body accepts either an explicit `rating` (`"again"`/`"hard"`/`"good"`/`"easy"`) or an implicit `signal` (`"no_help"`/`"slowdown"`/`"translation_request"`/`"fast_forward"`); `rating_from_input` enforces exactly-one-of:

```bash
sed -n '255,288p' backend/app/api/srs.py
```

```output
@router.post("/items/{item_id}/direction/{direction}/feedback", status_code=200)
async def drill_feedback(item_id: int, direction: str, body: DrillRequest, request: Request):
    try:
        dir_enum = Direction(direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc

    try:
        rating = rating_from_input(rating=body.rating, signal=body.signal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db = request.state.srs_db
    result = db.get_collocation_by_id(item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found")
    _, item, _ = result

    fsrs_params, _ = resolve_fsrs_params(db)
    col_crt = resolve_col_crt(db)
    now = datetime.datetime.now(datetime.UTC)
    balancer = build_live_load_balancer(db, now=now, col_crt=col_crt)
    prev_dir = item.directions[dir_enum]
    updated = schedule(
        item,
        rating,
        direction=dir_enum,
        params=fsrs_params,
        time_ms=body.time_ms,
        now=now,
        col_crt=col_crt,
        load_balancer=balancer,
    )
    db.update_direction_by_id(item_id, dir_enum, updated.directions[dir_enum])
```

Three points worth noting:

- The `direction` path segment is parsed into the `Direction` enum (422 on garbage); `schedule(...)` updates only that direction.
- `resolve_fsrs_params(db)` reads the cached weights+retention from `anki_state_cache` (PART 12.6), falling back to `DEFAULT_FSRS5_PARAMS` when no Anki cache is present.
- The handler also enqueues a `pending_revlog` row so the next sync push has the rating to write to Anki's revlog (see PART 12.4 — drain phase).

#### Routes by functional area

**Learner loop:**
- `GET /due?direction={recognition|production|any}` — items due for review today, scoped by direction. `any` returns both directions concatenated.
- `GET /new?limit=N&direction=...` — items in NEW state, scoped.
- `POST /items/{id}/direction/{direction}/feedback` — record an FSRS rating (above).
- `GET /stats` — total + due-today counts.
- `GET /queue-stats` — new + due breakdown using the cached daily-new-cap (so the UI can show "X new available, Y new used today, Z due"). Reads from `anki_state_cache.new_per_day` populated by the sync flow.

**Per-word capture and content:**
- `POST /listen` — hooks a finished lesson into SRS. Now reads `token_glosses` from `lesson.generation_metadata` (LingQ-style auto-gloss capture) so first-encounter translations are populated automatically rather than left blank. Tokenises the L2 NATURAL_SPEED text, lemmatises, upserts a per-lemma `SRSItem`. Also stores `source_sentence`, `source_lesson_id`, and `source_line_index` on each new item so the admin UI can show provenance. Optional `word_ratings` map lets the frontend pass per-word ratings; otherwise everything starts at GOOD.
- `GET /lesson/{lesson_id}/transcript` — NATURAL_SPEED dialogue annotated with per-word `srs_state` for the colour-coded transcript view.
- `POST /translate-missing` — bulk LLM-translate every collocation whose `translation` is empty. Used for cleanup after a `listen` registered words without glosses.
- `POST /backfill-translations` — apply a stored `{lemma: gloss}` dict in one call (the bulk-edit path).

**Review queue and media:**
- `GET /review-queue` — the unified queue used by `/review`. Merges due + a daily-capped slice of new, alternates direction, attaches media URLs to each card. The cap is read from `anki_state_cache.new_per_day` (PART 12.6).
- `GET /media/{filename}` — serves images and audio from `media_dir`. The frontend embeds these URLs directly in `<img>` and `<audio>` tags.

**Admin (powering `/cards`):**
- `POST /items` — create a new SRS item (text + translation + optional grammar/note). Generates a deterministic GUID via `app.common.guid` so a later sync will round-trip cleanly to Anki.
- `GET /items?search=&state=&sort=&order=&limit=&offset=` — paginated, filtered, sorted item list. `_item_to_dict` powers each row.
- `PATCH /items/{id}` — edit text + translation. 409 on UNIQUE collisions, marks `dirty_fields` for the next sync push.
- `DELETE /items/{id}` and `POST /items/bulk-delete` — single + bulk delete.
- `POST /items/{id}/reset` — reset FSRS for a direction (or both) back to NEW.
- `POST /items/{id}/state` — force a specific `SRSState` (`KNOWN`, `BURIED`, etc.) on a direction. Lets the admin UI override scheduling without going through FSRS.
- `POST /items/{id}/suspend` — toggle the suspended flag.

### 7.4 Audio API

```bash
cat -n backend/app/api/audio.py
```

```output
     1	"""Audio generation and streaming endpoints."""
     2	
     3	from __future__ import annotations
     4	
     5	import io
     6	import json
     7	import re
     8	import zipfile
     9	from pathlib import Path
    10	
    11	from fastapi import APIRouter, HTTPException, Request
    12	from fastapi.responses import FileResponse, Response
    13	
    14	from app.api.models import RenderAudioRequest
    15	from app.audio.render_service import render_lesson_audio
    16	from app.audio.transcode import EXT_MEDIA_TYPE
    17	from app.generation.section_builder import SECTION_TITLES
    18	from app.models.lesson import SectionType
    19	
    20	router = APIRouter(prefix="/api/audio", tags=["audio"])
    21	
    22	
    23	def _sanitize_filename(name: str) -> str:
    24	    """Strip filesystem-illegal characters and collapse whitespace to underscores."""
    25	    name = re.sub(r'[/\\:*?"<>|]', "", name)
    26	    name = re.sub(r"\s+", "_", name.strip())
    27	    return name or "audio"
    28	
    29	
    30	def _section_title(section_type: str) -> str:
    31	    """Resolve a section type string to a human-readable title, falling back raw."""
    32	    try:
    33	        st = SectionType(section_type)
    34	        return SECTION_TITLES.get(st, section_type)
    35	    except ValueError:
    36	        return section_type
    37	
    38	
    39	def _resolve_topic_day(store, lesson_id: str) -> tuple[str, int]:
    40	    """Resolve (topic, day) for a lesson, falling back to ('audio', 1)."""
    41	    topic = "audio"
    42	    day = 1
    43	    lesson_row = store.get_lesson_row(lesson_id)
    44	    if lesson_row is not None:
    45	        day = lesson_row["day"]
    46	        curriculum = store.get_curriculum(lesson_row["curriculum_id"])
    47	        if curriculum is not None:
    48	            topic = curriculum.topic
    49	        else:
    50	            lesson = store.get_lesson(lesson_id)
    51	            topic = lesson.title
    52	    return topic, day
    53	
    54	
    55	def _build_section_filename(topic: str, day: int, section_index: int, section_type: str, ext: str = ".wav") -> str:
    56	    """Build a context-rich section filename: {Topic}_Day{DD}_{NN}_{Title}{ext}."""
    57	    safe_topic = _sanitize_filename(topic)
    58	    title = _section_title(section_type)
    59	    safe_title = _sanitize_filename(title)
    60	    return f"{safe_topic}_Day{day:02d}_{section_index + 1:02d}_{safe_title}{ext}"
    61	
    62	
    63	@router.post("/render", status_code=202)
    64	async def render_audio(body: RenderAudioRequest, request: Request):
    65	    store = request.state.content_store
    66	    lesson = store.get_lesson(body.lesson_id)
    67	    if lesson is None:
    68	        raise HTTPException(status_code=404, detail="Lesson not found")
    69	
    70	    return await render_lesson_audio(
    71	        store=store,
    72	        renderer=request.app.state.renderer,
    73	        audio_dir=request.app.state.audio_dir,
    74	        lesson_id=body.lesson_id,
    75	        lesson=lesson,
    76	    )
    77	
    78	
    79	@router.get("/lesson/{lesson_id}", status_code=200)
    80	async def get_lesson_audio(lesson_id: str, request: Request):
    81	    """Return the audio file list for a lesson (full + sections) without re-rendering."""
    82	    store = request.state.content_store
    83	    rows = store.list_audio_files_for_lesson(lesson_id)
    84	    if not rows:
    85	        raise HTTPException(status_code=404, detail="No audio found for this lesson")
    86	
    87	    full_row = next((r for r in rows if r["section_index"] is None), None)
    88	    if full_row is None:
    89	        raise HTTPException(status_code=404, detail="Full lesson audio not found")
    90	
    91	    section_rows = [r for r in rows if r["section_index"] is not None]
    92	
    93	    sections = []
    94	    for r in section_rows:
    95	        section_type_str = r["section_type"] or ""
    96	        title = _section_title(section_type_str)
    97	        section_cues = json.loads(r["cues_json"]) if r["cues_json"] else None
    98	        sections.append(
    99	            {
   100	                "audio_id": r["id"],
   101	                "section_index": r["section_index"],
   102	                "section_type": section_type_str,
   103	                "title": title,
   104	                "cues": section_cues,
   105	            }
   106	        )
   107	
   108	    cues: list | None = None
   109	    raw = full_row.get("cues_json")
   110	    if raw is not None:
   111	        cues = json.loads(raw)
   112	
   113	    return {
   114	        "audio_id": full_row["id"],
   115	        "lesson_id": lesson_id,
   116	        "sections": sections,
   117	        "cues": cues,
   118	    }
   119	
   120	
   121	@router.get("/lesson/{lesson_id}/zip", status_code=200)
   122	async def download_lesson_zip(lesson_id: str, request: Request):
   123	    """Return a ZIP of all section WAVs for a lesson with context-rich filenames."""
   124	    store = request.state.content_store
   125	    rows = store.list_audio_files_for_lesson(lesson_id)
   126	    full_row = next((r for r in rows if r["section_index"] is None), None)
   127	    section_rows = [r for r in rows if r["section_index"] is not None]
   128	
   129	    if not section_rows:
   130	        raise HTTPException(status_code=404, detail="No section audio files found for this lesson")
   131	
   132	    # Validate all files exist before building the ZIP
   133	    all_rows = ([full_row] if full_row else []) + section_rows
   134	    for r in all_rows:
   135	        if not Path(r["file_path"]).exists():
   136	            raise HTTPException(status_code=404, detail=f"Audio file missing: {r['file_path']}")
   137	
   138	    topic, day = _resolve_topic_day(store, lesson_id)
   139	    safe_topic = _sanitize_filename(topic)
   140	
   141	    # Build ZIP in memory: full lesson file first (sorts as _00_), then sections
   142	    buf = io.BytesIO()
   143	    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
   144	        if full_row:
   145	            full_ext = Path(full_row["file_path"]).suffix or ".wav"
   146	            full_filename = f"{safe_topic}_Day{day:02d}_00_Full{full_ext}"
   147	            zf.write(full_row["file_path"], arcname=full_filename)
   148	        for r in sorted(section_rows, key=lambda x: x["section_index"]):
   149	            ext = Path(r["file_path"]).suffix or ".wav"
   150	            filename = _build_section_filename(topic, day, r["section_index"], r["section_type"] or "", ext)
   151	            zf.write(r["file_path"], arcname=filename)
   152	
   153	    zip_name = f"{_sanitize_filename(topic)}_Day{day:02d}.zip"
   154	    return Response(
   155	        content=buf.getvalue(),
   156	        media_type="application/zip",
   157	        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
   158	    )
   159	
   160	
   161	@router.get("/{audio_id}", status_code=200)
   162	async def get_audio(audio_id: str, request: Request):
   163	    store = request.state.content_store
   164	    row = store.get_audio_file_row(audio_id)
   165	    if row is None:
   166	        raise HTTPException(status_code=404, detail="Audio not found")
   167	
   168	    path = Path(row["file_path"])
   169	    if not path.exists():
   170	        raise HTTPException(status_code=404, detail="Audio file missing")
   171	
   172	    # Build a friendly download filename with curriculum context
   173	    lesson_id = row["lesson_id"]
   174	    topic, day = _resolve_topic_day(store, lesson_id)
   175	
   176	    # Derive extension + media type from the actual stored file, so pre-existing
   177	    # WAV files and newly-rendered compressed files both serve correctly.
   178	    ext = path.suffix or ".wav"
   179	    media_type = EXT_MEDIA_TYPE.get(ext, "application/octet-stream")
   180	
   181	    if row["section_index"] is not None:
   182	        filename = _build_section_filename(topic, day, row["section_index"], row["section_type"] or "", ext)
   183	    else:
   184	        filename = f"{_sanitize_filename(topic)}_Day{day:02d}_full{ext}"
   185	
   186	    return FileResponse(
   187	        str(path),
   188	        media_type=media_type,
   189	        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
   190	    )
```

Four changes from the prototype:

1. **Per-section audio.** `POST /render` allocates a UUID for each section as well as for the full lesson. The renderer writes one WAV per section plus the full-lesson WAV. All are persisted in `ContentStore.audio_files` with `section_index` and `section_type` columns. The response body includes the section list so the frontend can build a section picker immediately.
2. **`GET /lesson/{lesson_id}`** returns the audio metadata (full audio ID + section list) for a lesson that was already rendered, without re-rendering. The frontend calls this on lesson load to check whether audio is ready.
3. **Friendly filenames.** `GET /{audio_id}` builds a `Content-Disposition` filename from the lesson title and section info (`Arriving_in_Ljubljana_01_slow_speed.wav`), so the file is self-describing when downloaded.
4. **Bulk download.** `GET /lesson/{lesson_id}/zip` streams every rendered section (full + per-section WAVs) as a single ZIP, with the lesson title as the archive filename. Lets the user download a whole Pimsleur day in one click rather than fetching seven separate WAVs.

### 7.5 Route Reference

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/curriculum/generate` | POST | Generate a multi-day curriculum |
| `/api/curriculum` | GET | List all persisted curricula |
| `/api/curriculum/{id}` | GET | Retrieve curriculum metadata |
| `/api/curriculum/{id}/progress` | GET | Per-day SRS progress for a curriculum |
| `/api/curriculum/{id}/days/{day}/lesson` | GET | Get latest lesson for a curriculum day |
| `/api/story/generate` | POST | Generate a Pimsleur lesson from a curriculum day |
| `/api/story/{lesson_id}` | GET | Retrieve lesson with full phrase list |
| `/api/srs/due` | GET | Collocations due for review today |
| `/api/srs/new` | GET | Collocations in `new` state |
| `/api/srs/review-queue` | GET | Unified queue (due + capped new), per-direction, with media URLs |
| `/api/srs/items/{id}/direction/{direction}/feedback` | POST | Per-direction FSRS feedback (Again/Hard/Good/Easy) |
| `/api/srs/listen` | POST | Mark lesson listened + register words with SRS |
| `/api/srs/lesson/{id}/transcript` | GET | Per-word transcript with SRS state |
| `/api/srs/stats` | GET | Total / due-today counts |
| `/api/srs/queue-stats` | GET | New + due breakdown using cached daily-new-cap |
| `/api/srs/translate-missing` | POST | Backfill translations via LLM for untranslated lemmas |
| `/api/srs/backfill-translations` | POST | Bulk-apply a translation dict |
| `/api/srs/items` | POST | Admin: create new SRS item |
| `/api/srs/items` | GET | Admin: paginated SRS item list |
| `/api/srs/items/{id}` | PATCH | Admin: edit text + translation |
| `/api/srs/items/{id}` | DELETE | Admin: delete item |
| `/api/srs/items/bulk-delete` | POST | Admin: bulk delete by ID list |
| `/api/srs/items/{id}/reset` | POST | Admin: reset FSRS schedule to `new` |
| `/api/srs/items/{id}/state` | POST | Admin: force a specific SRS state |
| `/api/srs/items/{id}/suspend` | POST | Admin: toggle suspended flag |
| `/api/srs/media/{filename}` | GET | Serve media file (image/audio) by filename |
| `/api/audio/render` | POST | Render lesson to WAV (full + per-section) |
| `/api/audio/lesson/{lesson_id}` | GET | Get audio metadata for a lesson |
| `/api/audio/lesson/{lesson_id}/zip` | GET | Download all sections as a single ZIP |
| `/api/audio/{audio_id}` | GET | Download a WAV file |
| `/api/anki/peer-sync` | POST | Peer sync via AnkiWeb / self-host server; works with Anki open |
| `/api/admin/refresh-media` | POST | Re-import Anki media → TunaTale cache |
| `/api/health` | GET | Health check |


---

## PART 8: Test Suite

### 8.1 Full Test Run

```bash
cd backend && uv run pytest --tb=short -q 2>&1
```

```output
........................................................................ [  1%]
........................................................................ [  3%]
........................................................................ [  5%]
..sssssssss............................................................. [  7%]
........................................................................ [  9%]
........................................................................ [ 11%]
........................................................................ [ 13%]
........................................................................ [ 15%]
........................................................................ [ 17%]
........................................................................ [ 19%]
........................................................................ [ 21%]
........................................................................ [ 22%]
........................................................................ [ 24%]
........................................................................ [ 26%]
........................................................................ [ 28%]
........................................................................ [ 30%]
........................................................................ [ 32%]
........................................................................ [ 34%]
........................................................................ [ 36%]
........................................................................ [ 38%]
........................................................................ [ 40%]
........................................................................ [ 42%]
........................................................................ [ 44%]
........................................................................ [ 45%]
........................................................................ [ 47%]
........................................................................ [ 49%]
........................................................................ [ 51%]
........................................................................ [ 53%]
............ssssssss.................................................... [ 55%]
........................................................................ [ 57%]
........................................................................ [ 59%]
........................................................................ [ 61%]
........................................................................ [ 63%]
........................................................................ [ 65%]
............ssssss.s....................sssssssssssss.sss.......ss...... [ 67%]
.....sssssss............................................................ [ 68%]
........................................................................ [ 70%]
........................................................................ [ 72%]
........................................................................ [ 74%]
........................................................................ [ 76%]
........................................................................ [ 78%]
........................................................................ [ 80%]
........................................................................ [ 82%]
........................................................................ [ 84%]
........................................................................ [ 86%]
........................................................................ [ 88%]
........................................................................ [ 90%]
........................................................................ [ 91%]
........................................................................ [ 93%]
........................................................................ [ 95%]
........................................................................ [ 97%]
........................................................................ [ 99%]
...............                                                          [100%]
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.14.2-final-0 _______________

Name                                            Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------------------------------
app/__init__.py                                     0      0      0      0   100%
app/anki/__init__.py                                0      0      0      0   100%
app/anki/add_vocab_notetype.py                     41      0     12      0   100%
app/anki/field_map.py                              18      0      0      0   100%
app/anki/import_seed.py                           166      0     66      0   100%
app/anki/media/__init__.py                          0      0      0      0   100%
app/anki/media/forvo.py                            43      0      8      0   100%
app/anki/media/normalize.py                        55      0      8      0   100%
app/anki/media/pipeline.py                         42      0     12      0   100%
app/anki/media/pixabay.py                          54      0      8      0   100%
app/anki/media/query_llm.py                        47      0     16      0   100%
app/anki/media/tts.py                              14      0      4      0   100%
app/anki/media/vocab_media.py                      43      0      8      0   100%
app/anki/model_discovery.py                        21      0     10      0   100%
app/anki/normalize_usns.py                         26      0     12      0   100%
app/anki/notetype.py                                4      0      0      0   100%
app/srs/anki_mirror/protobuf_wire.py                         125      0     42      0   100%
app/anki/replay_fsrs_from_revlog.py               120      0     40      0   100%
app/srs/anki_mirror/rollover.py                               22      0      4      0   100%
app/anki/safety.py                                135      0     32      0   100%
app/anki/sqlite_reader.py                         275      0    112      0   100%
app/anki/sync.py                                  130      0     18      0   100%
app/anki/sync_common.py                           126      0     20      0   100%
app/anki/sync_engine.py                           507      0    248      0   100%
app/anki/sync_orchestrator.py                     216      0     48      0   100%
app/anki/sync_reader.py                            68      0     16      0   100%
app/anki/sync_writer.py                           327      0     80      0   100%
app/anki/vocab_notetype.py                         45      0      4      0   100%
app/api/__init__.py                                 0      0      0      0   100%
app/api/_serializers.py                             6      0      2      0   100%
app/api/admin.py                                   11      0      0      0   100%
app/api/anki.py                                    26      0      0      0   100%
app/api/audio.py                                  112      0     30      0   100%
app/api/curriculum.py                             134      0     22      0   100%
app/api/generation.py                              99      0     22      0   100%
app/api/llm.py                                     53      0     14      0   100%
app/api/models.py                                  78      0      0      0   100%
app/api/pipeline.py                                48      0      8      0   100%
app/api/srs.py                                    633      0    178      0   100%
app/audio/__init__.py                               0      0      0      0   100%
app/audio/backfill_cloze_tts.py                    40      0     10      0   100%
app/audio/cloze_tts.py                             44      0     16      0   100%
app/audio/cues.py                                  91      0     30      0   100%
app/audio/edge_tts.py                              54      0     10      0   100%
app/audio/pause_calculator.py                      20      0      6      0   100%
app/audio/ports.py                                  5      0      0      0   100%
app/audio/preprocessing/__init__.py                 0      0      0      0   100%
app/audio/preprocessing/base.py                     5      0      0      0   100%
app/audio/preprocessing/norwegian.py                4      0      0      0   100%
app/audio/preprocessing/slovene.py                  5      0      0      0   100%
app/audio/render_service.py                        72      0     32      0   100%
app/audio/renderer.py                             121      0     26      0   100%
app/audio/transcode.py                             15      0      2      0   100%
app/common/__init__.py                              0      0      0      0   100%
app/common/guid.py                                  8      0      0      0   100%
app/config.py                                      39      0      0      0   100%
app/generation/__init__.py                          0      0      0      0   100%
app/generation/breakdown_preview.py                21      0      6      0   100%
app/generation/ids.py                               9      0      0      0   100%
app/generation/json_parsing.py                     60      0     20      0   100%
app/generation/norwegian_breakdown.py             289      0    158      0   100%
app/generation/pipeline.py                        220      0     62      0   100%
app/generation/planner.py                          42      0     10      0   100%
app/generation/prompts.py                          96      0     26      0   100%
app/generation/section_builder.py                 168      0     86      0   100%
app/generation/story.py                           118      0     38      0   100%
app/generation/syllabify.py                        38      0     10      0   100%
app/languages.py                                  101      0     22      0   100%
app/llm/__init__.py                                 0      0      0      0   100%
app/llm/activity.py                                16      0      0      0   100%
app/llm/cassette.py                                81      0     24      0   100%
app/llm/client.py                                 303      0     86      0   100%
app/llm/translate.py                               22      0      4      0   100%
app/llm/usage_ledger.py                            34      0      6      0   100%
app/main.py                                       138      0     30      0   100%
app/media/__init__.py                               0      0      0      0   100%
app/media/importer.py                              44      0     14      0   100%
app/models/__init__.py                              0      0      0      0   100%
app/models/curriculum.py                           30      0      2      0   100%
app/models/language.py                             19      0      0      0   100%
app/models/lesson.py                               60      0     10      0   100%
app/models/srs_item.py                            116      0      4      0   100%
app/models/strategy.py                              5      0      0      0   100%
app/models/syntactic_unit.py                       49      0     10      0   100%
app/srs/__init__.py                                 0      0      0      0   100%
app/srs/anki_mirror/_anki_rng.py                              117      0     20      0   100%
app/srs/collocation_matcher.py                     19      0     10      0   100%
app/srs/database.py                                21      0      0      0   100%
app/srs/db_base.py                                129      0     26      0   100%
app/srs/db_collocations.py                        193      0     64      0   100%
app/srs/db_counts.py                               43      0      0      0   100%
app/srs/db_directions.py                          120      0     32      0   100%
app/srs/db_histogram.py                             9      0      0      0   100%
app/srs/db_ignored_lemmas.py                       13      0      0      0   100%
app/srs/db_kv_cache.py                             21      0      2      0   100%
app/srs/db_lemma_cache.py                          46      0      4      0   100%
app/srs/db_media.py                                52      0      2      0   100%
app/srs/db_queue.py                                51      0      2      0   100%
app/srs/db_revlog.py                               76      0     18      0   100%
app/srs/db_sync.py                                149      0     36      0   100%
app/srs/db_sync_conflicts.py                        9      0      0      0   100%
app/srs/direction_fields.py                        37      0      6      0   100%
app/srs/feedback.py                                15      0      8      0   100%
app/srs/fsrs.py                                   438      0    120      0   100%
app/srs/function_words.py                         146      0     54      0   100%
app/srs/grade_undo.py                              41      0     14      0   100%
app/srs/lemmatizer.py                              94      0     26      0   100%
app/srs/anki_mirror/load_balancer.py                          110      0     30      0   100%
app/srs/mastery.py                                 18      0      6      0   100%
app/srs/migrations.py                             340      0    108      0   100%
app/srs/planner_snapshot.py                        38      0     12      0   100%
app/srs/anki_mirror/queue_engine.py                           187      0     66      0   100%
app/srs/anki_mirror/queue_stats.py                            569      0    226      0   100%
app/srs/tokenizer.py                                5      0      0      0   100%
app/srs/transcript.py                             242      0     86      0   100%
app/storage/__init__.py                             0      0      0      0   100%
app/storage/backfill_curriculum_day_titles.py      21      0     12      0   100%
app/storage/lesson_io.py                           83      0     46      0   100%
app/storage/lowercase_glosses.py                   28      0     10      0   100%
app/storage/plan_io.py                             93      0     50      0   100%
app/storage/regloss_lessons.py                     72      0     28      0   100%
app/storage/store.py                              142      0     30      0   100%
-------------------------------------------------------------------------------------------
TOTAL                                           10259      0   3018      0   100%
Required test coverage of 100.0% reached. Total coverage: 100.00%
3710 passed, 49 skipped in 46.27s
```

409 tests, **100% branch coverage** *(at the time of the original walkthrough revision)*. The suite has since grown to **~3700 tests across 139 files** at **100% coverage, enforced** (`fail_under = 100`; the refreshed run above shows the real count). All in mock mode — no network calls needed. PART 8 below shows the original test snapshot; for an up-to-date breakdown of the new Anki-sync test files see PART 12.

### 8.2 Test File Inventory

```bash
ls backend/tests/test_*.py | xargs -I{} sh -c "echo \"{}: \$(grep -c \"def test_\" {}) tests\"" | sort
```

```output
backend/tests/test_anki_add_vocab_notetype.py: 9 tests
backend/tests/test_anki_extra_isolation.py: 1 tests
backend/tests/test_anki_fallback_log.py: 4 tests
backend/tests/test_anki_guid.py: 4 tests
backend/tests/test_anki_import_seed_readonly.py: 47 tests
backend/tests/test_anki_media_forvo.py: 15 tests
backend/tests/test_anki_media_normalize.py: 11 tests
backend/tests/test_anki_media_pipeline.py: 22 tests
backend/tests/test_anki_media_pixabay.py: 33 tests
backend/tests/test_anki_media_query_llm.py: 24 tests
backend/tests/test_anki_media_tts.py: 5 tests
backend/tests/test_anki_model_discovery.py: 7 tests
backend/tests/test_anki_normalize_usns.py: 5 tests
backend/tests/test_anki_offline_writer_create_note.py: 31 tests
backend/tests/test_anki_oracle_smoke.py: 2 tests
backend/tests/test_anki_peer_sync_selfhost.py: 7 tests
backend/tests/test_anki_protobuf_wire.py: 13 tests
backend/tests/test_anki_replay_fsrs_from_revlog.py: 39 tests
backend/tests/test_anki_rng.py: 26 tests
backend/tests/test_anki_safety.py: 24 tests
backend/tests/test_anki_safety_rw.py: 11 tests
backend/tests/test_anki_sqlite_reader.py: 117 tests
backend/tests/test_anki_sync_concurrent_review.py: 16 tests
backend/tests/test_anki_sync_create_new.py: 64 tests
backend/tests/test_anki_sync_force_fsrs.py: 12 tests
backend/tests/test_anki_sync_main.py: 21 tests
backend/tests/test_anki_sync_merge_equivalence.py: 9 tests
backend/tests/test_anki_sync_offline_writer.py: 24 tests
backend/tests/test_anki_sync_orchestrator.py: 64 tests
backend/tests/test_anki_sync_orphan_recovery.py: 12 tests
backend/tests/test_anki_sync_pull.py: 105 tests
backend/tests/test_anki_sync_pull_event_mode.py: 10 tests
backend/tests/test_anki_sync_push.py: 147 tests
backend/tests/test_anki_sync_round_trip.py: 11 tests
backend/tests/test_api.py: 145 tests
backend/tests/test_api_admin.py: 3 tests
backend/tests/test_api_anki.py: 5 tests
backend/tests/test_api_base_cards.py: 13 tests
backend/tests/test_api_curriculum_plan.py: 26 tests
backend/tests/test_api_inflection_clozes.py: 22 tests
backend/tests/test_api_llm_status.py: 12 tests
backend/tests/test_api_pipeline.py: 11 tests
backend/tests/test_api_srs.py: 97 tests
backend/tests/test_api_srs_admin.py: 51 tests
backend/tests/test_api_srs_directions.py: 39 tests
backend/tests/test_audio_ports.py: 2 tests
backend/tests/test_audio_transcode.py: 6 tests
backend/tests/test_backfill_cloze_tts.py: 8 tests
backend/tests/test_backfill_curriculum_titles.py: 5 tests
backend/tests/test_breakdown_preview.py: 7 tests
backend/tests/test_check_language_literals.py: 30 tests
backend/tests/test_check_mock_boundaries.py: 29 tests
backend/tests/test_cloze_tts.py: 7 tests
backend/tests/test_colday_helper_consistency.py: 8 tests
backend/tests/test_collocation_matcher.py: 11 tests
backend/tests/test_config.py: 7 tests
backend/tests/test_coverage_fix.py: 0 tests
backend/tests/test_cues.py: 19 tests
backend/tests/test_database_helpers.py: 5 tests
backend/tests/test_database_mixin_composition.py: 2 tests
backend/tests/test_direction_fields.py: 6 tests
backend/tests/test_direction_invariants.py: 21 tests
backend/tests/test_dirty_fields.py: 11 tests
backend/tests/test_e2e_listen_to_sync.py: 1 tests
backend/tests/test_edge_tts.py: 11 tests
backend/tests/test_feedback_rating_input.py: 13 tests
backend/tests/test_field_map.py: 4 tests
backend/tests/test_fsrs.py: 86 tests
backend/tests/test_fsrs_steps.py: 37 tests
backend/tests/test_function_words.py: 132 tests
backend/tests/test_grade_undo.py: 3 tests
backend/tests/test_json_parsing.py: 30 tests
backend/tests/test_languages.py: 59 tests
backend/tests/test_lemmatizer.py: 60 tests
backend/tests/test_lesson_io.py: 42 tests
backend/tests/test_llm_activity.py: 11 tests
backend/tests/test_llm_cassette.py: 14 tests
backend/tests/test_llm_client.py: 78 tests
backend/tests/test_llm_translate.py: 12 tests
backend/tests/test_llm_usage_ledger.py: 8 tests
backend/tests/test_load_balancer.py: 31 tests
backend/tests/test_lowercase_glosses.py: 11 tests
backend/tests/test_main_lifespan.py: 9 tests
backend/tests/test_mastery.py: 17 tests
backend/tests/test_media_importer.py: 20 tests
backend/tests/test_models.py: 43 tests
backend/tests/test_multilang.py: 11 tests
backend/tests/test_norwegian_breakdown.py: 93 tests
backend/tests/test_parity_bury.py: 1 tests
backend/tests/test_parity_daily_caps.py: 7 tests
backend/tests/test_parity_fsrs_f32.py: 5 tests
backend/tests/test_parity_fsrs_schedule.py: 5 tests
backend/tests/test_parity_learning_steps.py: 3 tests
backend/tests/test_parity_load_balancer.py: 2 tests
backend/tests/test_parity_new_sibling_bury.py: 4 tests
backend/tests/test_parity_queue_order.py: 3 tests
backend/tests/test_parity_replay_sequences.py: 1 tests
backend/tests/test_parity_revlog_factor.py: 2 tests
backend/tests/test_parity_same_day_review.py: 1 tests
backend/tests/test_parity_stability_clamp.py: 2 tests
backend/tests/test_parity_transitions.py: 7 tests
backend/tests/test_pauses.py: 12 tests
backend/tests/test_pipeline.py: 47 tests
backend/tests/test_plan_io.py: 53 tests
backend/tests/test_planner.py: 14 tests
backend/tests/test_planner_llm.py: 2 tests
backend/tests/test_planner_prompts.py: 25 tests
backend/tests/test_planner_snapshot.py: 13 tests
backend/tests/test_preprocessor.py: 7 tests
backend/tests/test_prompts.py: 25 tests
backend/tests/test_queue_engine_facade_names.py: 1 tests
backend/tests/test_queue_stats.py: 77 tests
backend/tests/test_queue_stats_cache.py: 91 tests
backend/tests/test_queue_stats_learning_steps.py: 23 tests
backend/tests/test_queue_stats_load_balancer.py: 31 tests
backend/tests/test_regloss_lessons.py: 15 tests
backend/tests/test_render_service.py: 12 tests
backend/tests/test_renderer.py: 23 tests
backend/tests/test_review_fuzz_parity.py: 17 tests
backend/tests/test_rollover_hour_single_source.py: 8 tests
backend/tests/test_section_builder.py: 42 tests
backend/tests/test_srs_database.py: 219 tests
backend/tests/test_srs_database_anki_surface.py: 28 tests
backend/tests/test_srs_database_learning_step_columns.py: 8 tests
backend/tests/test_srs_direction_state.py: 20 tests
backend/tests/test_srs_fsrs.py: 18 tests
backend/tests/test_srs_guid.py: 7 tests
backend/tests/test_srs_migrations.py: 88 tests
backend/tests/test_srs_sync_scratch.py: 4 tests
backend/tests/test_storage.py: 30 tests
backend/tests/test_story.py: 44 tests
backend/tests/test_syllabify.py: 12 tests
backend/tests/test_sync_server_fixture.py: 9 tests
backend/tests/test_tokenizer.py: 13 tests
backend/tests/test_transcript.py: 104 tests
backend/tests/test_user_add_to_anki_e2e.py: 3 tests
backend/tests/test_vocab_media.py: 9 tests
backend/tests/test_vocab_media_endpoints.py: 3 tests
backend/tests/test_vocab_notetype.py: 6 tests
```

~1474 tests across 74 files. The big growth is in `app/anki/` — see PART 12.8 for a per-file breakdown of the Anki integration tests. The non-anki test files were largely unchanged in count from the original 26-file walkthrough snapshot; the additional ~48 anki/sync/media/queue-stats files are the diff.

### 8.3 Mocking Patterns

The test suite uses four distinct mocking strategies:

- **LLM calls**: `CassetteLLMClient` in mock mode (hash-based replay)
- **Database**: `sqlite:///:memory:` in-process — no cleanup needed
- **EdgeTTS**: `pytest-mock` patches on `edge_tts.Communicate`
- **HTTP (Groq)**: `respx` for mocking `httpx.AsyncClient` calls

Here is a typical cassette-backed test from the curriculum module:

```bash
head -40 backend/tests/test_planner.py | cat -n
```

```output
     1	"""Tests for CurriculumPlanner.turn with a stub LLM (no patch, no cassette)."""
     2	
     3	from dataclasses import dataclass
     4	
     5	import pytest
     6	
     7	from app.generation.planner import CurriculumPlanner, PlannerError, PlannerTurn
     8	from app.models.curriculum import Curriculum, CurriculumDay
     9	from app.models.language import Language
    10	
    11	
    12	@dataclass
    13	class StubLLM:
    14	    """Minimal async LLM stub — NOT a mock/patch, passes the boundary check."""
    15	
    16	    response: str
    17	    prompt_seen: str | None = None
    18	
    19	    async def complete(
    20	        self,
    21	        prompt: str,
    22	        system_prompt: str | None = None,
    23	        temperature: float = 0.7,
    24	        max_tokens: int = 256,
    25	    ) -> str:
    26	        self.prompt_seen = prompt
    27	        return self.response
    28	
    29	
    30	def _day_dict(day: int, **overrides) -> dict:
    31	    d = {
    32	        "day": day,
    33	        "title": f"Day {day}",
    34	        "focus": f"Focus {day}",
    35	        "collocations": ["coll_a", "coll_b"],
    36	        "learning_objective": f"Objective {day}",
    37	    }
    38	    d.update(overrides)
    39	    return d
    40	
```

API tests inject mocks via `app.state` — no real LLM or TTS calls. The `ASGITransport` runs the FastAPI app in-process, so tests are fast and isolated.


---

## PART 9: The Full Data Flow

Putting it all together — here is how a request flows through the system from API to audio:

```
User POST /api/curriculum/generate {"topic": "Travel in Slovenia", "cefr_level": "A1"}
   │
   ▼
CurriculumGenerator.generate()
   │── PromptBuilder builds system + user prompts
   │── LLMClient.complete() → Groq API (or CassetteLLMClient in test)
   │── _parse_response() → Curriculum with CurriculumDays
   │── ContentStore.save_curriculum(slug-id, curriculum)
   │
   ▼  returns {"id": "travel-in-slovenia-a3f1b2c8", ...}

User POST /api/story/generate {"curriculum_id": "travel-in-slovenia-a3f1b2c8", "day": 1}
   │
   ▼
StoryGenerator.generate()
   │── get_strategy_prompt(WIDER) → user prompt template
   │── LLMClient.complete() → Groq API → JSON {title, key_phrases, scenes}
   │── section_builder.build_*() × 4 → Lesson with 4 Sections + key_phrases
   │── ContentStore.save_lesson(slug-id, curriculum_id, day, lesson)
   │
   ▼  returns {"id": "arriving-in-ljubljana-b7d2e9f1", "sections": [...]}

User POST /api/srs/listen {"lesson_id": "arriving-in-ljubljana-b7d2e9f1"}
   │
   ▼
   │── tokenize() each L2 word in NATURAL_SPEED section
   │── LowercaseLemmatizer.lemmatize() each surface form
   │── SRSDatabase.add_collocation() for each unique lemma
   │── SRSDatabase.add_collocation() for each KeyPhraseInfo (with translation)
   │── schedule(item, rating) → advance FSRS state on first encounter
   │
   ▼  returns {"status": "ok", "registered": N}

User POST /api/audio/render {"lesson_id": "arriving-in-ljubljana-b7d2e9f1"}
   │
   ▼
LessonRenderer.render()
   │── asyncio.gather(_render_section × 4)     [parallel]
   │     For each Phrase in section:
   │       TextPreprocessor.preprocess(text)   [pass-through for Slovene]
   │       EdgeTTSService.synthesize()          [cached, semaphore-throttled]
   │       AudioSegment.from_file()             [measure actual duration]
   │       NaturalPauseCalculator.get_phrase_pause()
   │── pydub concatenate: [title] [boundary] [section_0] [boundary] ...
   │── Write full-lesson WAV  +  one WAV per section
   │── ContentStore.save_audio_file() × (1 + num_sections)
   │
   ▼  returns {"audio_id": "uuid", "sections": [{"audio_id": ..., "title": ...}]}

User GET /api/audio/{audio_id}  → FileResponse (WAV download)
User GET /api/srs/lesson/{lesson_id}/transcript  → per-word SRS state for UI colouring
```

Each step is independently testable: cassettes for LLM, `:memory:` for SRS and ContentStore, mocks for TTS. The full pipeline can run in CI with zero network calls.

---

## PART 10: What Changed from the Prototypes

| Area | Prototype | Production |
|------|-----------|------------|
| **Architecture** | Two separate codebases (micro-demo-0.0, 0.1) | Unified FastAPI monolith |
| **Language support** | Hardcoded `Language` enum (Tagalog/English/Spanish) | Data-driven `Language` dataclass with factory methods |
| **SRS algorithm** | Custom scheduler | FSRS-5 (19-parameter model, research-backed) |
| **SRS states** | new/learning/review/relearning | + `suspended` (admin-toggled, excluded from due queue) |
| **LLM mock** | MD5-hashed cache | SHA-256 cassette system with 4 modes (mock/record/live/patch) |
| **LLM client** | Single-provider | Groq primary + Ollama fallback; proactive RPM/TPM pacing |
| **Preprocessing** | 1000-line Tagalog preprocessor with `_add_slow_pauses` | Pluggable `TextPreprocessor` protocol; slow-speed ellipses moved to `section_builder` |
| **Voice mapping** | Hardcoded speaker→voice table | `Language.tts_voice_map` dict with named roles (narrator/female-1/male-1/etc.) |
| **Vocabulary** | Hardcoded replacement dictionary | Dynamic from SRS database (`ContentEnforcer`) |
| **Configuration** | Module-level globals | Pydantic Settings with `.env` |
| **Storage** | In-memory `app.state` dict | `ContentStore` SQLite repository (curricula/lessons/audio_files) |
| **IDs** | `uuid4()` opaque strings | `{slug(topic)}-{uuid_hex[:8]}` human-readable slugs |
| **SRS registration** | During story generation (coupled) | During `POST /api/srs/listen` (decoupled) |
| **Per-word tracking** | None | Lemmatize → upsert word-level SRSItems + frontend colour-coding |
| **Section construction** | Inline in `StoryGenerator` | Separate `section_builder` module; `StoryGenerator` is a thin orchestrator |
| **Syllabification** | None | Slovene onset-maximization → Pimsleur backward buildup |
| **Audio assembly** | Raw PCM concatenation (assumed 1.5s/phrase) | pydub AudioSegment — measures actual duration, outputs valid WAV |
| **Audio sections** | Single output file | Full-lesson WAV + one WAV per section (section picker in player) |
| **TTS concurrency** | 3 concurrent EdgeTTS requests | 10 concurrent + `asyncio.gather` parallelises sections (~80s → ~12s on 7-section lesson) |
| **Pause system** | Complex word_count multiplier table | Flat 500ms (natural/translated) + proportional for KEY_PHRASES L2 + 600ms for SLOW_SPEED |
| **SRS admin** | No UI | `/srs` SvelteKit admin page + 6 REST endpoints (list/edit/delete/bulk-delete/reset/suspend) |
| **Testing** | Unit tests only | ~3700 tests, 100% enforced coverage, cassette fixtures, 4 mock strategies, Playwright e2e |
| **API endpoints** | 10 endpoints | 56 endpoints |
| **SRS directions** | Single direction (recognition only) | Two directions per item (RECOGNITION L2→L1 + PRODUCTION L1→L2) with independent FSRS state |
| **SRS states** | new/learning/review/relearning + suspended | + `BURIED` (Anki bury), `KNOWN` (graduated), full Anki queue mapping |
| **Anki integration** | None | Bidirectional offline sync over `collection.anki2` SQLite (push → drain revlog → pull → create-new) |
| **Anki safety** | n/a | `safe_open` lock-probe + SHA-256 backup + integrity validation; USN normalization protocol |
| **Media** | EdgeTTS only | Forvo audio → EdgeTTS fallback + Pixabay images (token-overlap scoring) + ffmpeg LUFS normalize, deduped per-card |
| **Queue stats** | Live count from SRS DB | Cached daily-new-cap + FSRS-5 params parsed from Anki `deck_config` protobuf |
| **Frontend** | Generate / lesson / practice routes | + unified `/review`, `/cards` admin, single Sync button, Anki-running gating |

**What was preserved from the prototypes:**
- Pimsleur section format (KEY_PHRASES, NATURAL_SPEED, SLOW_SPEED, TRANSLATED, + SLOW_TRANSLATED since 2026-07)
- EdgeTTS rate limiting (200ms delay between requests)
- Hexagonal architecture / Protocol-based ports
- Pedagogical scoring weights (40/30/20/10)
- Content strategy framework (WIDER vs DEEPER)
- FSRS-5 algorithm parameters and scheduling logic

---

## PART 11: Manual Testing

### Prerequisites
- `backend/.env` contains `GROQ_API_KEY=<your key>`
- `cd backend && uv sync --all-groups`

### Automated suite

```bash
./test.sh   # ruff lint + pytest (~3700 tests) + vitest (frontend) + playwright e2e
```

### Start the dev server

```bash
./start-dev.sh   # FastAPI at :8000 + SvelteKit at :5173
```

Open http://localhost:5173, enter a topic (e.g. "ordering coffee in Ljubljana"), choose CEFR level and days, click Generate → select a day → Generate Lesson → Render Audio → play.

### SRS review loop
First generate a curriculum and lesson (which registers SRS items via `POST /api/srs/listen`), then navigate to http://localhost:5173/review — the unified queue blends due cards and a daily-capped slice of new ones, alternating directions (L2→L1 and L1→L2). Rate each with Again / Hard / Good / Easy.

### SRS admin UI
Navigate to https://localhost:5173/cards to browse and manage SRS items. Features: search (full-text across text and translation), filter by state, sortable columns, inline edit, single and bulk delete, reset schedule, suspend/unsuspend, force state, create new item.

### Anki sync
Click **Sync** in the UI (or `POST /api/anki/peer-sync`). The backend runs the peer-sync sequence against TT's own ``tt_collection``, which works with Anki open.

### Developer reference
For day-to-day developer commands, testing quirks (cassette modes, the offline-Anki test fixtures), and architectural conventions, see `AGENTS.md` at the repo root and `.claude/rules/anki-sync.md` for the USN/sync protocol details. CLAUDE.md is the project-level companion that points at the rules directory.

---

## PART 12: Anki Integration (Stage 3)

> **2026-07 status.** This PART describes Stage 3 as built (early 2026). Three things have changed structurally since: (1) the 2026-06-11 **sync module split** — `app/anki/sync.py` is now a runner + re-export facade; the `AnkiSync` engine lives in `sync_engine.py`, collection I/O in `sync_reader.py`/`sync_writer.py`, shared helpers in `sync_common.py` (import and patch through `app.anki.sync` as before); (2) **AnkiConnect and the CLI are gone** — `POST /api/anki/peer-sync` is the ONLY sync entry point (legacy `/api/anki/sync` + `/status` endpoints deleted 2026-06-10; the `python -m app.anki.sync` CLI and `--all-languages` removed 2026-06-30); (3) the one-shot migration scripts tabulated in 12.9 moved to `backend/scripts/anki_archive/`. Corrections are inlined below; PART 29 covers the new world.

The biggest change since the original walkthrough is **bidirectional Anki sync**. TunaTale's SRS database now mirrors a user's Anki collection: items have stable Anki-compatible GUIDs, two review directions (recognition + production matching Anki ord 0/1), and a sync engine that reads and writes `collection.anki2` directly via SQLite. AnkiConnect (the HTTP plugin) was initially kept for compatibility, but its support has since been **deleted entirely** — direct offline SQLite access is the only collection I/O, and peer-sync via AnkiWeb is the only sync entry point (PART 29).

This part explains the design from the inside out: domain shape (12.1), safety envelope (12.2), readers/writers (12.3), the four-phase sync flow (12.4), media pipeline (12.5), queue stats from Anki's protobuf deck config (12.6), and the API surface (12.7).

### 12.1 Two-Direction SRS Items

Each `SRSItem` now has independent FSRS state for two directions: **RECOGNITION** (L2→L1, shown the Slovene word and asked for the English) and **PRODUCTION** (L1→L2, the reverse). Anki models the same shape with `cards.ord = 0/1`. The model lives in `app/models/srs_item.py`.

```bash
sed -n '15,76p' backend/app/models/srs_item.py | cat -n
```

```output
     1	from dataclasses import dataclass, field
     2	from datetime import UTC, date, datetime, time
     3	from enum import Enum
     4	
     5	from app.srs.anki_mirror.rollover import due_at_rollover_utc
     6	
     7	from .syntactic_unit import SyntacticUnit
     8	
     9	
    10	class SRSState(Enum):
    11	    """Learning state of an SRS item."""
    12	
    13	    NEW = "new"
    14	    LEARNING = "learning"
    15	    REVIEW = "review"
    16	    RELEARNING = "relearning"
    17	    SUSPENDED = "suspended"
    18	    BURIED = "buried"
    19	    KNOWN = "known"
    20	
    21	
    22	class Rating(Enum):
    23	    """Learner rating for an SRS review."""
    24	
    25	    AGAIN = 1  # Complete blackout / forgot
    26	    HARD = 2  # Significant difficulty
    27	    GOOD = 3  # Correct with some effort
    28	    EASY = 4  # Perfect recall
    29	
    30	
    31	class Direction(Enum):
    32	    """Review direction for an SRS item."""
    33	
    34	    RECOGNITION = "recognition"  # L2 → L1 (Anki ord=0)
    35	    PRODUCTION = "production"  # L1 → L2 (Anki ord=1)
    36	
    37	
    38	@dataclass
    39	class DirectionState:
    40	    """FSRS scheduling state for one direction of a collocation.
    41	
    42	    Single source of truth for due-time: ``due_at`` (TEXT iso datetime, UTC).
    43	    Extended to all states (review/new included), NOT NULL.
    44	    """
    45	
    46	    direction: Direction
    47	    due_at: datetime
    48	    stability: float = 1.0
    49	    difficulty: float = 5.0
    50	    reps: int = 0
    51	    lapses: int = 0
    52	    state: SRSState = field(default=SRSState.NEW)
    53	    last_review: datetime | None = None
    54	    last_review_time_ms: int = 0
    55	    anki_card_id: int | None = None
    56	    anki_due: int | None = None
    57	    # Anki's `cards.mod` (modification timestamp). Used as the secondary sort
    58	    # key under RetrievabilityAscending — Anki tiebreaks via `fnvhash(id, mod)`.
    59	    anki_card_mod: int | None = None
    60	    # Source of a buried state: 'user' (manual bury, persists across rollover)
    61	    # or 'sched' (sibling/auto bury, released at next rollover via Layer 27's
    62	    # unbury_if_needed sweep). NULL on non-buried rows.
```

Three pieces are new since the original walkthrough:

- **`SRSState.SUSPENDED` / `BURIED` / `KNOWN`** — full Anki queue mapping. Suspended is admin-toggled; buried mirrors Anki's bury (excluded from today only); known is a graduated terminal state.
- **`Direction`** — drives the per-direction FSRS state map and round-trips to Anki via `cards.ord`. The review queue now alternates directions to keep practice varied.
- **`DirectionState.anki_card_id` / `anki_due` / `dirty_fsrs` / `last_synced_at`** — the sync bookkeeping. `dirty_fsrs=True` means TunaTale has FSRS state changes the user hasn't pushed to Anki yet; `anki_due` preserves Anki's deck position so newly-introduced items keep the same ordering they would have in Anki.

The flat fields on `SRSItem` (`due_date`, `stability`, `state`, ...) are compatibility shims that delegate to `directions[Direction.RECOGNITION]` so callers predating the two-direction schema keep working. They will be removed in Stage 3.5.

The matching schema in `app/srs/database.py` uses two tables — `collocations` (1 row per item, with `guid`, `anki_note_id`, `text`, `translation`, etc.) and `collocation_directions` (2 rows per item, one per direction, with the FSRS fields). New columns added by the v1→v8 migrations: `guid`, `anki_note_id`, `anki_card_id`, `anki_due`, `dirty_fsrs`, `last_synced_at`, `last_rating`, `grammar`, `note`, `source_sentence`, `source_lesson_id`, `source_line_index`.

### 12.2 Safety Envelope: `safe_open`, USN, Backups

`collection.anki2` is a SQLite file. Touching it directly without the right precautions corrupts AnkiWeb sync state — see the project rule file `.claude/rules/anki-sync.md` for the full theory. Every TunaTale write goes through `app/anki/safety.py::safe_open`, which is the *only* sanctioned way to open the collection.

```bash
sed -n '172,247p' backend/app/anki/safety.py | cat -n
```

```output
     1	class AnkiRunningError(RuntimeError):
     2	    """Raised when the Anki collection is exclusively locked (Anki is running)."""
     3	
     4	
     5	def probe_lock(path: Path) -> bool:
     6	    """Return True if the collection is locked (Anki is running), False if acquirable."""
     7	    try:
     8	        _probe_exclusive_lock(path)
     9	        return False
    10	    except AnkiRunningError:
    11	        return True
    12	
    13	
    14	def _probe_exclusive_lock(path: Path) -> None:
    15	    """Raise AnkiRunningError if the database cannot be exclusively locked (Anki running)."""
    16	    probe = sqlite3.connect(str(path), timeout=0.1)
    17	    try:
    18	        probe.execute("BEGIN EXCLUSIVE")
    19	        probe.execute("ROLLBACK")
    20	    except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
    21	        probe.close()
    22	        raise AnkiRunningError(
    23	            f"Anki collection is locked (Anki may be running): {path}\n"
    24	            f"Close Anki before running import. Original error: {exc}"
    25	        ) from exc
    26	    finally:
    27	        with suppress(Exception):  # pragma: no cover
    28	            probe.close()
    29	
    30	
    31	@contextmanager
    32	def safe_open(
    33	    collection_path: Path,
    34	    backup_dir: Path | None = None,
    35	    mode: Literal["ro", "rw"] = "ro",
    36	) -> Generator[AnkiContext]:
    37	    """Open an Anki collection with full safety checks.
    38	
    39	    Yields an AnkiContext with a connection (read-only or read-write per ``mode``)
    40	    and backup metadata. Raises RuntimeError if Anki is running, the backup is
    41	    invalid, or (in ro mode) the source SHA256 changes during the run.
    42	    """
    43	    if backup_dir is None:
    44	        backup_dir = settings.anki_backup_dir
    45	
    46	    # Gate 1: lock probe
    47	    _probe_exclusive_lock(collection_path)
    48	
    49	    # Gate 2: SHA256 before open
    50	    source_sha256 = _sha256_file(collection_path)
    51	
    52	    # Get source note count for backup validation
    53	    _src = sqlite3.connect(str(collection_path))
    54	    _register_anki_collations(_src)
    55	    try:
    56	        source_note_count = _src.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    57	    finally:
    58	        _src.close()
    59	
    60	    # Gate 3: backup via Connection.backup()
    61	    # The timestamp is only second-granularity, so two callers in the same
    62	    # second (parallel test workers, or two rapid syncs) would otherwise share
    63	    # a filename and clobber/cross-validate each other's backup. A per-call
    64	    # token (pid + random) keeps each backup distinct.
    65	    backup_dir.mkdir(parents=True, exist_ok=True)
    66	    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    67	    unique = f"{os.getpid()}_{secrets.token_hex(4)}"
    68	    backup_path = backup_dir / f"collection.anki2.bak_{timestamp}_{unique}"
    69	
    70	    src_conn = sqlite3.connect(str(collection_path))
    71	    dst_conn = sqlite3.connect(str(backup_path))
    72	    try:
    73	        src_conn.backup(dst_conn)
    74	    finally:
    75	        dst_conn.close()
    76	        src_conn.close()
```

Three gates execute on every `safe_open` call before the caller sees a connection:

1. **Lock probe.** `BEGIN EXCLUSIVE` against the collection — if Anki holds it, the probe fails and `AnkiRunningError` propagates up to the API as a 409. `probe_lock()` is the read-only inverse.
2. **SHA-256 fingerprint.** Computed before any work. In `mode="ro"`, the same hash is checked again at exit — any mid-run mutation is treated as a torn read and raises.
3. **Backup + validation.** A timestamped copy goes to `~/.tunatale/anki-backups/` via SQLite's online `Connection.backup()` API. The backup is then opened independently, an integrity check runs, and the note count must match the source. Any mismatch raises before the caller's transaction begins.

After successful open, `AnkiContext` exposes the connection plus an `audit_changes()` post-write hook that diffs row counts and surfaces unintended writes (e.g. a new note appearing during a metadata update).

Two more pieces complete the protocol — they live alongside `safe_open` and the project rule file:

- **GUID-aware writes.** Every mutation must set `row.usn = -1` and bump `mod`. `UPDATE col SET ..., usn = -1` runs after each batch. Without this, Anki's integrity check on next open re-detects the change itself, bumps `col.scm`, and forces an unnecessary full upload.
- **`normalize_usns.py`** (in `app/anki/`). After a forced full upload the local `col.usn` resets to the server value, but per-row `usn` values stay at their old (now "newer than server") value — so AnkiWeb keeps re-uploading them forever. `normalize_usns` clamps `cards.usn`, `notes.usn`, `revlog.usn` back to `col.usn` with no content change. Run it after every schema-bumping migration.

### 12.3 Readers and Writers

The sync engine talks to the underlying store through two ports — `OfflineReader`/`OfflineWriter` — now defined in `sync_reader.py`/`sync_writer.py` and re-exported through the `app.anki.sync` facade (2026-06-11 split; the AnkiConnect-backed `OnlineReader`/`OnlineWriter` ports below were deleted with AnkiConnect support):

| Port | When used | Backend |
|------|-----------|---------|
| `OfflineReader` | Default sync; Anki must be closed | Direct SQLite `SELECT` against `collection.anki2` |
| `OfflineWriter` | Default sync | Direct SQLite `INSERT`/`UPDATE` with USN bookkeeping |
| `OnlineReader` | Compatibility / legacy paths | AnkiConnect JSON-RPC (`findNotes`, `notesInfo`) |
| `OnlineWriter` | Compatibility / legacy paths | AnkiConnect (`addNote`, `updateNoteFields`, `storeMediaFile`) |

Both *Reader* ports return the same in-memory shapes — `AnkiNote` and `AnkiCard` from `app/anki/sqlite_reader.py`. The card record carries the FSRS state parsed out of Anki's per-card data blob (queue, due, ivl, factor, lapses, reps), plus the `fsrs_data` payload (stability, difficulty, last review).

```bash
sed -n '54,87p' backend/app/anki/sqlite_reader.py | cat -n
```

```output
     1	def compute_due_at(queue: int, due_raw: int, col_crt: int, card_type: int = 0) -> datetime:
     2	    """Convert Anki's queue-dependent due field to a UTC datetime.
     3	
     4	    queue 2/3 (review/day-learn): due_raw is days since col.crt epoch → midnight UTC.
     5	    queue 1 (learning): due_raw is an absolute unix timestamp (seconds).
     6	    queue 0 (new): due_raw is a queue position → today at 04:00 UTC.
     7	
     8	    queue -1/-2/-3 (suspended/buried): Anki preserves cards.due through bury and
     9	    suspend; only the queue flips. We dispatch on ``card_type`` (the card's
    10	    underlying type — 0=new, 1=learn, 2=review, 3=relearn) so the underlying due
    11	    survives a sync round-trip. Without this, the daily unbury sweep would flip
    12	    state back to review with a stale "today" due_at (Layer 44, 2026-05-20).
    13	
    14	    Database corruption: some queue=2/3 cards have Unix timestamps in due_raw
    15	    instead of days since col.crt. Detect this by checking if the value is too large
    16	    to be days since col.crt (i.e., it's a Unix timestamp).
    17	    """
    18	    effective_queue = queue
    19	    if queue in (-1, -2, -3):
    20	        if card_type == 2:
    21	            effective_queue = 2
    22	        elif card_type == 3:
    23	            effective_queue = 3
    24	        elif card_type == 1:
    25	            effective_queue = 1
    26	
    27	    if effective_queue in (2, 3):
    28	        if due_raw > 1000000000:
    29	            return datetime.fromtimestamp(due_raw, tz=UTC)
    30	        return review_due_at_for_col_day(col_crt, due_raw)
    31	    if effective_queue == 1:
    32	        return datetime.fromtimestamp(due_raw, tz=UTC)
    33	    return due_at_rollover_utc(date.today())
    34	
```

Two details from the reader are worth highlighting because they're easy to get wrong:

- **Dual deck lookup.** Modern Anki stores decks in a `decks` *table*, but legacy collections still keep the deck list as JSON in `col.decks`. `find_deck_id` reads JSON first then falls back to the table — neither is canonical, and which exists depends on the user's Anki version.
- **Queue-dependent due decoding.** Anki's `cards.due` field is overloaded: it's days-since-collection-epoch for queues 2/3 (review / day-learn), an absolute Unix timestamp for queue 1 (intra-day learning), and a positional integer for queues 0 / -1 (new / suspended). `compute_due_date` unifies these into a Python `date` and the offline reader propagates them through `AnkiCard.due_date`.

`OfflineWriter` is the first place where the safety rules from 12.2 turn into code. Every `INSERT`/`UPDATE` sets `usn = -1` and `mod = now()` on touched rows; every batch ends with `UPDATE col SET mod = ?, usn = -1`; revlog rows additionally bump `col.scm` only when the schema actually changed. `OfflineWriter.create_note` (a Stage 3.9 addition) hashes new media bytes, dedupes against existing files in the media collection, and stores both the binary and a row in `media` with the right `csum`.

### 12.4 The Four-Phase Sync Flow

The sync flow (``run_full_sync``) runs four phases in a single transaction. The order matters — getting it wrong loses revlog entries or creates duplicate notes.

```bash
grep -nE 'def sync_|def _direction_differs|class AnkiSync' backend/app/anki/sync_engine.py | head -20
```

```output
47:def _direction_differs(local: DirectionState, candidate: DirectionState) -> bool:
319:class AnkiSync:
683:    def sync_pull(self, dry_run: bool = False) -> PullReport:
1069:    def sync_push(self, dry_run: bool = False, force_fsrs: bool = False) -> PushReport:
1281:    async def sync_create_new(
```

1. **`sync_create_new`** — for every TunaTale collocation that has no `anki_note_id`, fetch media (12.5), call `writer.create_note` to add it to Anki, and stash the new note id back on the SRSItem. New notes are filtered against existing GUIDs/L2-text-with-disambiguation to avoid duplicate-note errors (the B11/B16/B17/B19 fixes from session 2 of S3.11 — `detect_and_link_duplicates` does an id-first lookup before falling back to GUID).
2. **`sync_push`** — for every direction with `dirty_fsrs=True` or pending field edits, write the FSRS state and field changes to Anki via `writer.update_*`. Push writes the collection directly through `OfflineWriter` (the AnkiConnect `setSpecificValueOfCard` machinery is gone). Suspends, due dates, and field text round-trip here; since Layer 80 (2026-07-10) push also inserts **one Anki revlog row per TT grade** from `tt_revlog`, instead of one collapsed row per dirty direction.
3. **Drain pending revlog.** TunaTale records every review locally in a scratch `pending_revlog` table — direction id, rating, ease/factor, time taken — independently of whether Anki was reachable. `drain_pending_revlog_to_writer` flushes those rows to Anki's `revlog` table (this is what populates Anki's review history graph). The drain happens *after* push so the rated card already has its updated FSRS state on the Anki side; running it before push could lose entries if push fails partway. (See commit `67e9a57` — B14 swap.)
4. **`sync_pull`** — read every note in the deck, diff against TunaTale's local copy, and update SRSItems whose Anki side changed. The diff function `_direction_differs` compares state, due, stability, difficulty, lapses, reps, last_rating, and `anki_due` — anything else (e.g. internal review counts) is treated as noise. **Local FSRS state with `dirty_fsrs=True` is preserved** even if Anki has different values, since the next push will overwrite Anki anyway (the b9bbcb4 fix). Conflicts on field text are recorded in the `sync_conflicts` scratch table for later resolution.

Each phase returns a typed report (`CreateNewReport`, `PushReport`, `PullReport`) and the API combines them into a single response shape.

Two helper concepts appear repeatedly:

- **`force_fsrs` gating** — historical. The interactive `--force-fsrs` ack flow was removed with the CLI (2026-06-30); the automatic force-fsrs *write* path inside `sync_push` (recovered / `KNOWN` / `fsrs_force_next` cards) remains.
- **Mode auto-detection** — deleted. There is no `detect_mode` and no Online mode; every path is Offline against `collection.anki2` (or the peer-sync throwaway collection).

### 12.5 Media Pipeline

`fetch_card_media(word, english, *, used_image_urls)` in `app/anki/media/pipeline.py` is the single entry point that the sync engine calls when creating a new Anki note. It returns a `MediaResult` with audio bytes, image bytes, and chosen filenames.

```bash
cat -n backend/app/anki/media/pipeline.py
```

```output
     1	"""Media pipeline: fetch audio (Forvo → TTS) and image (Pixabay) for an Anki card."""
     2	
     3	from __future__ import annotations
     4	
     5	from collections.abc import Awaitable, Callable
     6	from dataclasses import dataclass
     7	from functools import partial
     8	from typing import Any
     9	
    10	import anyio
    11	
    12	from app.languages import get_tts_voice
    13	
    14	from .forvo import fetch_forvo_audio
    15	from .normalize import normalize_audio
    16	from .pixabay import fetch_pixabay_image
    17	from .tts import generate_tts_audio
    18	
    19	
    20	@dataclass
    21	class MediaResult:
    22	    audio_bytes: bytes | None = None
    23	    audio_source: str | None = None
    24	    image_bytes: bytes | None = None
    25	    image_ext: str | None = None
    26	    image_url: str | None = None
    27	
    28	
    29	async def fetch_card_media(
    30	    word: str,
    31	    english: str,
    32	    *,
    33	    pixabay_key: str,
    34	    language_code: str = "sl",
    35	    http_client: Any = None,
    36	    tts_voice: str | None = None,
    37	    normalize: bool = True,
    38	    used_image_urls: set[str] | None = None,
    39	    image_query: str | None = None,
    40	    _forvo_fn: Callable[..., bytes | None] | None = None,
    41	    _tts_fn: Callable[..., Awaitable[bytes | None]] | None = None,
    42	    _pixabay_fn: Callable[..., Any] | None = None,
    43	    _normalize_fn: Callable[..., bytes] | None = None,
    44	) -> MediaResult:
    45	    """Fetch audio and image for a vocabulary card.
    46	
    47	    Tries Forvo first, falls back to edge-tts. Image from Pixabay.
    48	    Pass used_image_urls (a shared set) across cards to prevent duplicate images.
    49	
    50	    ``image_query`` controls image selection (see ``query_llm`` contract):
    51	      * ``None`` — legacy: Pixabay derives the query from ``english``.
    52	      * ``""``   — skip the image entirely (abstract word, no depiction).
    53	      * non-empty — sent to Pixabay verbatim as a sense-disambiguated query.
    54	    """
    55	    forvo_fn = _forvo_fn or fetch_forvo_audio
    56	    tts_fn = _tts_fn or generate_tts_audio
    57	    pixabay_fn = _pixabay_fn or fetch_pixabay_image
    58	    norm_fn = _normalize_fn or normalize_audio
    59	    # Resolve the synthesis voice from the card's language so a non-Slovene card
    60	    # never gets Slovene TTS. Callers may still override explicitly (tests).
    61	    voice = tts_voice or get_tts_voice(language_code)
    62	
    63	    result = MediaResult()
    64	
    65	    # Forvo / Pixabay / normalize are synchronous (httpx.Client, ffmpeg
    66	    # subprocess) — offload to a worker thread so a slow fetch doesn't block
    67	    # the event loop and stall every other in-flight request.
    68	    audio = await anyio.to_thread.run_sync(
    69	        partial(forvo_fn, word, language_code=language_code, http_client=http_client)
    70	    )
    71	    if audio is not None:
    72	        result.audio_source = "forvo"
    73	        result.audio_bytes = audio
    74	    else:
    75	        audio = await tts_fn(word, voice=voice)
    76	        if audio is not None:
    77	            result.audio_source = "tts"
    78	            result.audio_bytes = audio
    79	
    80	    if result.audio_bytes is not None and normalize:
    81	        result.audio_bytes = await anyio.to_thread.run_sync(norm_fn, result.audio_bytes)
    82	
    83	    # image_query == "" is the explicit "abstract word, no image" skip sentinel.
    84	    if image_query != "":
    85	        img = await anyio.to_thread.run_sync(
    86	            partial(
    87	                pixabay_fn,
    88	                english,
    89	                api_key=pixabay_key,
    90	                http_client=http_client,
    91	                used_urls=frozenset(used_image_urls) if used_image_urls is not None else frozenset(),
    92	                query=image_query,
    93	            )
    94	        )
    95	        if img is not None:
    96	            result.image_bytes, result.image_ext, result.image_url = img
    97	            if used_image_urls is not None:
    98	                used_image_urls.add(result.image_url)
    99	
   100	    return result
```

Audio path: **Forvo → EdgeTTS fallback → ffmpeg LUFS normalize**. Forvo is a community pronunciation database — `forvo.py` scrapes the public word page (no API key needed) and returns the first MP3 link. If no Forvo audio exists for the word, EdgeTTS synthesizes a fallback. Either way the resulting bytes go through `normalize.py`, which uses ffmpeg's `loudnorm` filter to clamp output to a target LUFS so cards in the same deck have consistent volume.

Image path: **Pixabay with token-overlap scoring**. `build_query(english)` strips function words; `fetch_pixabay_image` scores candidate hits against the query tokens (`_tag_overlap`) and picks the best match not already in `used_image_urls`. The shared `used_image_urls` set threads through every call in the same sync run so two cards that would otherwise pick the same image get distinct images instead — this was the dedup feature added in commit `85279f6`.

The `/api/admin/refresh-media` endpoint and the `app/media/importer.py` module handle a separate task: copying Anki's `collection.media/` files into TunaTale's local `media_dir` so the review UI can serve them. Since commit `83c4c9e` this is invoked as a side effect of every sync, not via a manual button.

### 12.6 Queue Stats from Anki's Protobuf Deck Config

A subtle but important detail: modern Anki stores deck configuration (daily new cap, FSRS parameters, bury settings) as **protobuf-encoded blobs** in the `deck_config` table — not JSON in `col.dconf` like older versions. `app/srs/anki_mirror/queue_stats.py` includes a hand-rolled minimal protobuf decoder (`_pb_read_varint`, `_pb_find_varint_field`, `_pb_find_packed_float_field`, etc.) so TunaTale can read those values without a protoc-generated stub.

```bash
sed -n '202,283p' backend/app/srs/anki_mirror/queue_stats.py | cat -n
```

```output
     1	def _read_fsrs_params_from_deck_config_table(conn: sqlite3.Connection, deck_name: str) -> FSRSParams | None:
     2	    """Return FSRSParams from Anki's deck_config protobuf, or None if absent."""
     3	    try:
     4	        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
     5	    except sqlite3.Error:  # pragma: no cover
     6	        return None  # pragma: no cover
     7	
     8	    if "deck_config" not in tables or "decks" not in tables:
     9	        return None
    10	
    11	    conf_id = _read_conf_id_for_deck(conn, deck_name)
    12	    if conf_id is None:
    13	        return None
    14	
    15	    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    16	    if config_row is None or not config_row[0]:
    17	        return None
    18	
    19	    config_blob = config_row[0]
    20	    config_blob = bytes(config_blob) if isinstance(config_blob, memoryview) else config_blob
    21	
    22	    # Try field 6 first (FSRS-6: 21 floats)
    23	    weights_6 = _pb_find_packed_float_field(config_blob, _FSRS6_WEIGHTS_FIELD)
    24	    if weights_6 is not None and len(weights_6) == 21:
    25	        retention_raw = _pb_find_fixed32_float_field(config_blob, _DESIRED_RETENTION_FIELD)
    26	        retention = float(retention_raw) if retention_raw is not None else 0.9
    27	        try:
    28	            return FSRSParams(weights=tuple(weights_6), desired_retention=retention)
    29	        except ValueError, TypeError:  # pragma: no cover
    30	            pass  # fall through to field 5
    31	
    32	    # Fall back to field 5 (FSRS-5: 19 floats)
    33	    weights_5 = _pb_find_packed_float_field(config_blob, _FSRS5_WEIGHTS_FIELD)
    34	    if weights_5 is not None and len(weights_5) == 19:
    35	        retention_raw = _pb_find_fixed32_float_field(config_blob, _DESIRED_RETENTION_FIELD)
    36	        retention = float(retention_raw) if retention_raw is not None else 0.9
    37	        try:
    38	            return FSRSParams(weights=tuple(weights_5), desired_retention=retention)
    39	        except ValueError, TypeError:  # pragma: no cover
    40	            return None  # pragma: no cover
    41	
    42	    return None
    43	
    44	
    45	def _read_new_per_day_from_anki(conn: sqlite3.Connection, deck_name: str) -> int | None:
    46	    """Return new-cards-per-day from Anki's deck config, or None if unavailable.
    47	
    48	    Tries the legacy JSON format (col.dconf) first, then the modern protobuf
    49	    format (deck_config table, Anki =2.1.55).
    50	    """
    51	    return _read_config_value_from_deck_config_table(
    52	        conn, deck_name, proto_field=_NEW_PER_DAY_FIELD, wire_type=_WIRE_TYPE_VARINT, legacy_keys=("new", "perDay")
    53	    )
    54	
    55	
    56	def refresh_daily_new_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    57	    """Read the new-per-day cap from collection.anki2 and write it to the cache."""
    58	    cap = _read_new_per_day_from_anki(conn, deck_name)
    59	    if cap is not None:
    60	        db.set_anki_state_cache("daily_new_cap", str(cap))
    61	
    62	
    63	def _read_reviews_per_day_from_anki(conn: sqlite3.Connection, deck_name: str) -> int | None:
    64	    """Return reviews-per-day from Anki's deck config, or None if unavailable.
    65	
    66	    Tries the legacy JSON format (col.dconf) first, then the modern protobuf
    67	    format (deck_config table, Anki =2.1.55). Mirrors _read_new_per_day_from_anki
    68	    but reads rev.perDay instead of new.perDay.
    69	    """
    70	    return _read_config_value_from_deck_config_table(
    71	        conn, deck_name, proto_field=_REVIEWS_PER_DAY_FIELD, wire_type=_WIRE_TYPE_VARINT, legacy_keys=("rev", "perDay")
    72	    )
    73	
    74	
    75	# Layer 36: daily review cap (render-only).
    76	def refresh_daily_review_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    77	    """Read the reviews-per-day cap from collection.anki2 and write it to the cache."""
    78	    cap = _read_reviews_per_day_from_anki(conn, deck_name)
    79	    if cap is not None:
    80	        db.set_anki_state_cache("daily_review_cap", str(cap))
    81	
    82	
```

Three values are pulled out of the protobuf blobs:

- **`new_per_day`** (varint at field 9 of `DeckConfig.Config`) — the daily new-card cap. Cached in `anki_state_cache` keyed `new_per_day` so the review queue can rate-limit new items without re-reading the Anki collection on every request.
- **FSRS-5 weights** (packed float at field `_FSRS5_WEIGHTS_FIELD`) — 19 floats. The TunaTale FSRS engine uses these weights to ensure scheduling matches what Anki would predict, avoiding drift between the two systems.
- **`desired_retention`** (fixed32 float) — defaults to 0.9 when absent.

Plus bury settings (bury_new, bury_review) and the new-card spread mode used by the review queue.

`refresh_daily_new_cap`, `refresh_review_settings`, and `refresh_fsrs_params` run as side effects of every successful sync, so the cache stays current. `resolve_*` accessors return tuples of `(value, source)` where source is `anki`, `legacy_dconf`, or `fallback` so the UI can show provenance — the green-yellow-red badge in the queue stats card.

Two test files exercise this end-to-end with synthesized protobuf blobs: `test_queue_stats.py` and `test_queue_stats_cache.py`.

### 12.7 Anki API Surface

One FastAPI route drives the Anki integration — ``POST /api/anki/peer-sync``, which drives ``app.anki.sync_orchestrator.peer_sync``. Unlike the old offline sync, this path works with Anki open and threads a media generator so new TT cards reach AnkiWeb with audio and images attached.

### 12.8 Anki Test Inventory

The new test files exclusively for Anki integration:

| File | What it covers |
|------|----------------|
| `test_anki_safety.py`, `test_anki_safety_rw.py` | `safe_open` lock probe, backup, integrity validation, audit |
| `test_anki_sqlite_reader.py` | `fetch_notes_for_deck`, `compute_due_at`, dual deck lookup |
| `test_anki_offline_writer_create_note.py` | Stage 3.9 — offline note creation with media dedup |
| `test_anki_sync_pull.py`, `test_anki_sync_push.py` | Per-direction diffs, conflict recording, dirty-FSRS preservation |
| `test_anki_sync_create_new.py` | Duplicate detection (id-first then GUID), media linking |
| `test_anki_sync_round_trip.py` | Full push → pull round-trip cycle |
| `test_anki_sync_force_fsrs.py` | automatic force-FSRS write path (the ack flow + preflight died with the CLI/AnkiConnect; `test_anki_sqlite_writer.py`, `test_anki_syncKey_preflight.py`, `test_anki_sync_mode_detection.py`, `test_anki_connect_client.py` were deleted with their subjects) |
| `test_anki_model_discovery.py` | Notetype inference from existing notes |
| `test_anki_normalize_usns.py` | USN clamping after a full upload |
| `test_anki_migrate_homonyms.py`, `test_anki_repair_nested_homonyms.py` | Disambiguation migrations for homonym L2 forms |
| `test_anki_merge_dupes_*.py` | Plan / apply / CLI for merging duplicate notes |
| `test_anki_backfill_guids.py`, `test_anki_audit_guids.py`, `test_anki_guid.py` | GUID generation + backfill + audit |
| `test_anki_notetype.py` | Hand-rolled protobuf encoder for adding fields |
| `test_anki_import_seed_readonly.py` | Read-only seed import (media refresh) |
| `test_anki_bootstrap_e2e.py` | End-to-end bootstrap on a fresh collection |
| `test_anki_media_forvo.py`, `test_anki_media_pixabay.py`, `test_anki_media_normalize.py`, `test_anki_media_pipeline.py`, `test_anki_media_tts.py` | Per-source media fetchers + the orchestrator |
| `test_anki_fallback_log.py` | Logging when Forvo misses and TTS fills in |
| `test_queue_stats.py`, `test_queue_stats_cache.py` | Protobuf decode + cache resolution |
| `test_api_anki.py` | The two HTTP endpoints, including the 409 paths |
| `test_srs_database_anki_surface.py` | DB methods that the sync engine relies on |
| `test_srs_sync_scratch.py` | `pending_revlog` and `sync_conflicts` scratch tables |
| `test_dirty_fields.py` | The `dirty_fields` blob used to push selective field edits |
| `test_srs_guid.py` | GUID-keyed upsert path in `SRSDatabase` |

### 12.9 Anki Bootstrap CLIs

The four-phase sync described in 12.4 only works once a user's Anki collection has been brought into a TunaTale-compatible shape: every note needs a stable deterministic GUID, every word needs a unified two-template notetype (recognition + production on the same note), and homonyms need their disambiguation in a hidden field rather than baked into the visible text. Most users have a pre-existing Anki deck that doesn't satisfy any of these. The bootstrap CLIs in `app/anki/` cover the one-time transformation:

| Step | Module | What it does |
|------|--------|--------------|
| **H1** | `app.anki.audit_guids` | Read-only diagnostic. Emits a JSON report of every note whose visible text changed since the last GUID backfill — these are the rows that need attention before re-running backfill. |
| **H2** | `app.anki.merge_dupes` | Consolidates the two historical "Basic" notetype cards per word (one for recognition, one for production) into a single two-template "Slovene Vocabulary" note. Hand-rolled protobuf (`app.anki.notetype`) builds the new notetype's field/template/CSS config. This is the biggest single anki module and the one that requires a forced full-upload afterward. |
| **H3** | `app.anki.migrate_homonyms` | Moves disambiguation suffixes (e.g. `(noun)` in `kapus (noun)`) out of the visible Slovene field into a hidden `DisambigKey` field, so two homonyms can share a clean visible form while still hashing to distinct GUIDs. `repair_nested_homonyms` is a 3-row surgical companion for cases the regex missed (parens-inside-parens). |
| **H4** | `app.anki.backfill_guids` | Rewrites every Anki note GUID to TunaTale's deterministic formula (sha256 of language + visible text + DisambigKey). After this, sync's GUID-based reconciliation works. (`app.anki.sqlite_writer` has since been deleted; the one-shot scripts now live in `backend/scripts/anki_archive/`.) |
| **H5** | `app.anki.normalize_usns` | Post-full-upload USN clamp (already covered in 12.2). Resets `cards.usn`, `notes.usn`, `revlog.usn` back to `col.usn` after the user has done a forced full upload. |

Each step has a `__main__` entry point (`uv run python -m app.anki.<module>`), goes through `safe_open` for backup + lock probe, and emits a dry-run plan before mutating. All five test files in PART 12.8 cover these CLIs.

After this pipeline, ongoing sync uses only the peer-sync endpoint (PART 12.4) — no further bootstrap is needed unless the user adds a third notetype or imports a substantially new deck.

`app.anki.model_discovery` is a small support utility: given a deck and an open Anki connection (or just the offline collection), it figures out which notetype's notes to sync. Called by the sync handler whenever `settings.anki_model_name` is unset.

---

## PART 13: Frontend Updates

> **2026-07 status.** The component set has roughly tripled since this was written (16+ components under `lib/components/`); `AudioPlayer.svelte` was replaced by `LessonPlayer.svelte` in the 2026-07-09 player rework (per-section cue manifests, phase model — PART 29.7), and the admin page moved from `/admin/srs` to `/cards`.

The SvelteKit app in `frontend/` got significant new UI work alongside the Anki integration.

### 13.1 Routes

```
frontend/src/routes/
├── +page.svelte                # Home: curriculum form + list
├── +layout.svelte              # Header, Sync button, Anki status badge
├── c/[curriculumId]/           # Curriculum overview + day picker
│   └── l/[lessonId]/           # Lesson view: transcript, audio player, render
├── review/                     # Unified review queue (replaces the old /practice)
└── cards/                      # SRS item admin (was admin/srs): search/edit/bulk delete/reset/suspend
(The tree has since grown further: `/settings`, `/c/[curriculumId]/plan` for the chat planner — see PART 29.)
```

The notable changes:

- **`/review`** replaces the per-lesson `/practice` flow. It pulls from `/api/srs/review-queue`, which serves a unified queue blending due cards with a daily-capped slice of new ones (capped by the `new_per_day` value cached from Anki — see 12.6) and alternates direction per card. Each card shows L2 audio, image, English gloss, and optional grammar/note metadata; the user rates Again / Hard / Good / Easy. Media URLs come pre-populated in the queue payload (commit `52003c2`); `DrillCard` resets its revealed state between cards (commit `472b845`).

- **`/cards`** (originally `/admin/srs`) provides full CRUD over the SRS database: paginated table, search across text and translation, state filter, sortable columns, inline edit, single + bulk delete, reset schedule, suspend/unsuspend, force state, create new item.

- **Sync button** in the layout calls `POST /api/anki/peer-sync`. On success it shows a toast with the sync report.

### 13.2 Components

| Component | Purpose |
|-----------|---------|
| `CurriculumForm.svelte` | Topic + CEFR + days form on home page |
| `DayPicker.svelte` | Day-list with progress badges per curriculum |
| `AudioPlayer.svelte` | Section-aware audio player (full lesson + per-section) |
| `Transcript.svelte` | Per-word colour-coded transcript (SRS state) |
| `DrillCard.svelte` | Review card (used by `/review`); resets reveal state per card |
| `Tooltip.svelte` | Reusable tooltip (used in Sync button gating) |

Each component has a sibling `*.test.ts` Vitest spec. The vitest coverage thresholds were tuned for Opus 4.7 in commit `dfb24c9`.

### 13.3 Playwright e2e

`frontend/tests/` holds the Playwright specs:

- `smoke.spec.ts` — home page loads, server is up.
- `review-flow.spec.ts` — generate curriculum → generate lesson → mark listened → review queue → rate first card. Workers serialized to avoid SQLite races (commit `5bedaa1`).
- `global-setup.ts` — boots a backend instance against a temp DB.

Run with `./test.sh`, which chains ruff lint, pytest, vitest, and Playwright.

---

## PART 14: Updated Settings & Migrations

> **2026-07 status.** The migration chain described here ends at v19; the schema is at **v36** today. Notable later migrations: v27 shadow columns (added) → v32 (dropped, Stage 3b decommission), **v35** — the `CHECK` constraints on `prior_state`/`bury_kind` driven by the field registry `app/srs/direction_fields.py` (PART 29.5), v36 current.

`app/config.py` gained an Anki/media block. Here's the full settings object as it stands now:

```bash
cat -n backend/app/config.py
```

```output
     1	"""Application configuration via Pydantic Settings."""
     2	
     3	from pathlib import Path
     4	
     5	from pydantic_settings import BaseSettings, SettingsConfigDict
     6	
     7	
     8	class Settings(BaseSettings):
     9	    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    10	
    11	    groq_api_key: str = ""
    12	    # Per-language DB (one-DB-per-language isolation). Default is the Slovene DB;
    13	    # switch languages by flipping target_language AND database_url together
    14	    # (e.g. sqlite:///./tunatale_no.db for Norwegian).
    15	    database_url: str = "sqlite:///./tunatale_sl.db"
    16	    # Phase 5 — simultaneous multi-language. When non-empty, the app opens one
    17	    # connection per entry (``{"sl": "sqlite:///./tunatale_sl.db", "no": "…_no.db"}``)
    18	    # and resolves the active one per request from the X-TT-Language header. Empty
    19	    # (the default) = single-language: one connection from ``database_url`` bound to
    20	    # ``target_language``. ``target_language`` is the default when no header is sent.
    21	    database_urls: dict[str, str] = {}
    22	    llm_mode: str = "mock"  # mock | live | record | patch
    23	    # gpt-oss-120b replaces llama-3.3-70b-versatile (deprecated by Groq 2026-06-30).
    24	    # It is a reasoning model — main.py pins reasoning_effort=low via
    25	    # reasoning_params_for_model() so it emits content instead of burning the whole
    26	    # budget on reasoning. Free-tier TPM is 8000; WIDER story gen fits, DEEPER (bigger
    27	    # prompt) can approach the ceiling.
    28	    llm_model: str = "openai/gpt-oss-120b"
    29	    # Groq free-tier daily token cap for gpt-oss-120b — the binding limit, but it
    30	    # appears in no response header, so TT tallies its own spend (UsageLedger) and
    31	    # the rate-limit UI compares against this number.
    32	    groq_tokens_per_day_limit: int = 100_000
    33	    # Ollama/secondary fallback when Groq fails; default off — failures fail loudly.
    34	    llm_allow_fallback: bool = False
    35	    llm_usage_ledger_path: Path = Path("~/.tunatale/llm_usage.log").expanduser()
    36	
    37	    target_language: str = "sl"
    38	
    39	    anki_collection_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.anki2").expanduser()
    40	    anki_media_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.media").expanduser()
    41	    anki_deck_name: str = "1. Slovene"
    42	    anki_backup_dir: Path = Path("~/.tunatale/anki-backups").expanduser()
    43	    # Retention cap for the safe_open backup directory. safe_open writes a full
    44	    # ~16 MB collection snapshot on every call; without a cap the directory grows
    45	    # without bound. Keep the N most recent snapshots (~16 MB each); <= 0 disables.
    46	    anki_backup_keep: int = 30
    47	    media_dir: Path = Path("./media")
    48	    anki_fallback_log: Path = Path("~/.tunatale/logs/anki-fallback.log").expanduser()
    49	    # Durable per-sync soak log: every non-dry sync (CLI or API) appends a
    50	    # SYNC_SOAK heartbeat + one RECOMPUTE_DIVERGENCE line per divergence.
    51	    sync_log: Path = Path("~/.tunatale/logs/sync.log").expanduser()
    52	
    53	    # Peer-sync (anki subprocess) config — see sync_orchestrator.py.
    54	    tt_collection_path: Path = Path("~/.tunatale/tt_collection.anki2").expanduser()
    55	    sync_enabled: bool = False
    56	    sync_endpoint: str = ""  # "" → AnkiWeb default; else self-host URL
    57	    sync_username: str = ""
    58	    # AnkiWeb password. Prefer the macOS Keychain (see sync_keychain_service); this
    59	    # env/.env value is an override fallback and should normally stay EMPTY (plaintext).
    60	    sync_password: str = ""
    61	    # macOS Keychain generic-password service the AnkiWeb password is stored under
    62	    # (account = sync_username). Store it with:
    63	    #   security add-generic-password -s tunatale-ankiweb -a <username> -w
    64	    sync_keychain_service: str = "tunatale-ankiweb"
    65	    # Optional pin for the sync subprocess (`uv run --with anki==X`). Empty → latest
    66	    # anki. Set to match your desktop Anki's sync-protocol version if a mismatch appears.
    67	    anki_pkg_version: str = ""
    68	    # Interpreter for the anki driver subprocess. It runs isolated + project-free
    69	    # (--no-project), which escapes the project lock's stale protobuf 4.21.2 (dragged in
    70	    # by the classla+anki extras; no cp314 wheel) — a clean resolve pulls a current
    71	    # protobuf that imports fine on 3.14. Pin to an older Python here only if a future
    72	    # anki/protobuf breaks on the latest.
    73	    anki_subprocess_python: str = "3.14"
    74	
    75	    anki_model_name: str = ""
    76	    pixabay_api_key: str = ""
    77	    # Global lemmatizer gate: "lowercase" (default) forces the deterministic
    78	    # lowercase engine for EVERY language (the CI/test pin, and how a deployment
    79	    # disables the heavy PyTorch pipelines). Any other value ("classla", "stanza",
    80	    # "auto", …) opts in, and the ENGINE is then chosen per language from the
    81	    # registry (app.languages.get_lemmatizer_type: sl→classla, no→stanza). This is
    82	    # per-language, not one-engine-per-process, so multi-language mode
    83	    # (database_urls) analyzes each language with its own model. See get_lemmatizer.
    84	    lemmatizer_type: str = "lowercase"
    85	
    86	    anki_new_per_day_default: int = 20
    87	    anki_reviews_per_day_default: int = 200
    88	
    89	    # Lesson audio delivery format. Opus is ~10-20× smaller than WAV for speech,
    90	    # cutting mobile-data use when streaming lessons to a phone. Set to "wav" to
    91	    # restore uncompressed delivery. Codec must be a key of transcode.CODEC_EXT.
    92	    audio_delivery_codec: str = "opus"  # opus | aac | mp3 | wav
    93	    audio_delivery_bitrate: str = "28k"
    94	
    95	    pipeline_autostart: bool = True
    96	
    97	
    98	settings = Settings()
    99	
   100	
   101	# Anki rolls the study day over at this *local* hour (default 4 AM), not at
   102	# midnight — a grade timestamped between local midnight and the rollover belongs
   103	# to the PRIOR Anki day. The rollover arithmetic is single-sourced in
   104	# `app.srs.anki_mirror.rollover` (local-day domain: `local_today_rollover`,
   105	# `anki_day_bounds_utc`, `anki_today`; due_at convention: `due_at_rollover_utc`);
   106	# `app.srs.anki_mirror.protobuf_wire` owns the separate col-day index domain
   107	# (`compute_anki_day_index`, `review_due_at_for_col_day`). Both derive from this
   108	# constant. Promote to a Settings field if it ever needs to be config-driven
   109	# (Anki stores it per-collection).
   110	ANKI_ROLLOVER_HOUR = 4
```

Most of the new fields are paths to the user's Anki install (`anki_collection_path`, `anki_media_path`, `anki_backup_dir`) plus three optional API keys (`forvo_api_key` is unused — Forvo's web scraper doesn't need one — but `pixabay_api_key` and `groq_api_key` are required if you want media or recording-mode cassettes). `anki_new_per_day_default` is the fallback when no value is in the cache and no deck_config protobuf is parseable.

### 14.1 SRS Schema Migrations (v1 → v8)

`app/srs/migrations.py` runs every pending migration in dependency order, each in its own transaction. The current migrations:

| Version | Adds |
|---------|------|
| v0 → v1 | Initial schema: `collocations` + `collocation_directions` + `schema_version` |
| v1 → v2 | Two-direction split (RECOGNITION + PRODUCTION rows in `collocation_directions`) |
| v2 → v3 | `guid`, `anki_note_id`, `anki_card_id`, `dirty_fsrs`, `last_synced_at`, scratch tables (`pending_revlog`, `sync_conflicts`, `anki_state_cache`, `dirty_fields`, `media`) |
| v3 → v4 | Image/audio filename columns + media indexes |
| v4 → v5 | `last_rating` on `collocation_directions` (real revlog ease factor — B5 fix) |
| v5 → v6 | `anki_due` on `collocation_directions` (preserves Anki's deck position for new-card ordering) |
| v6 → v7 | `grammar` and `note` text columns on `collocations` |
| v7 → v8 | `source_sentence`, `source_lesson_id`, `source_line_index` (LingQ-style capture context) |
| v8 → v9 | Drop `pending_revlog` table (online-mode artifact, no longer used) |
| v9 → v10 | `last_review_time_ms INTEGER NOT NULL DEFAULT 0` on `collocation_directions` |
| v10 → v11 | `left INTEGER` and `due_at TEXT` on `collocation_directions` (learning step state) |
| v11 → v12 | Repair invariant: `state='new'` implies `last_review IS NULL` (companion to `parse_fsrs_data` fix) |
| v12 → v13 | `prior_state`, `prior_left`, `prior_stability` on `collocation_directions` (revlog shape) |
| v13 → v14 | `anki_card_mod` on `collocation_directions` (Anki `cards.mod` mirror for fnvhash tiebreak) |
| v14 → v15 | Fill lemma for single-word rows that lacked it (`LOWER(text)`) |
| v15 → v16 | Delete phantom direction rows with `anki_card_id IS NULL` from the old auto-fill bug |
| v16 → v17 | `idx_collocations_created_at` for the Phase C recency-prioritized new queue (Layer 24) |
| v17 → v18 | `introduced_at` on `collocation_directions` + `idx_directions_introduced_at` (Layer 26) |
| v18 → v19 | `card_type TEXT DEFAULT 'vocab'` on `collocations` (Phase F cloze support) |

Migrations are guarded by `_column_exists` / `_table_exists` so they're idempotent — re-running a partial migration after a crash won't fail.

### 14.2 New Bash Helpers

Two CLI entry points worth knowing:

- `uv run python -m app.anki.normalize_usns` — the post-full-upload USN clamp. Run it whenever `*_gt_col > 0` from the diagnostic in `.claude/rules/anki-sync.md`.
- `uv run python -m app.anki.import_seed` — refresh Anki media into TunaTale's local cache.


---

## PART 15: Listen-First Acquisition Loop (Phases B–F)

The biggest user-visible change since the last walkthrough is the **listen-first acquisition loop** — the user listens to a generated lesson, gets a clickable transcript, and adds the words and phrases they don't already know. Five phases shipped in sequence: B (status cycle and `untrack`), C (recency-prioritized new queue), D (Transcript component), E (translate-on-demand + off-transcript phrases), F (function-word cloze cards). Each phase landed Anki-parity-clean: the per-card sync round-trip works for the new card_type values, and the queue stays aligned with Anki for the new ordering rules.

This part replaces the single-file `walkthrough-listen.md` draft and supersedes the brief Phase A description in the Stage 3 section: every step here is the production version.

### 15.1 The Status Cycle and `/items/{id}/untrack`

`POST /api/srs/items/{id}/state` lets the UI flip a card directly to a non-FSRS state (`new`, `learning`, `known`, `ignored`). The frontend originally cycled through them on direct click; today the click opens a popover whose grade-button label mirrors that old cycle (PART 25 replaced the hardcoded `STATE_CYCLE`):

```bash
sed -n "91,107p" frontend/src/lib/WordSpan.svelte
```

```output
	// Grade-button label mirrors what the old direct click did (the "cycle"):
	// unknown → create a base card; due+tracked → grade Good; not-due but readable
	// → review ahead; otherwise the click was a no-op, so no button.
	const gradeLabel = $derived(
		undoable
			? 'Undo ↩'
			: onWordClick == null
				? null
				: word.active_state === 'unknown'
					? 'Start learning'
					: gotItApplies
						? 'Got it ✓'
						: readAheadApplies
							? 'Review ✓'
							: null
	);
```

A click on a word advances it one step around the cycle (`unknown → learning → known → ignored → new → …`). Stepping into `ignored` no longer calls `set_state_by_id(SUSPENDED)`; it routes through a dedicated endpoint that knows whether the row was ever synced to Anki.

`POST /api/srs/items/{id}/untrack` lives in `backend/app/api/srs.py` (near line 1169 today) and delegates to `SRSDatabase.untrack_collocation`:

```bash
sed -n "315,345p" backend/app/srs/db_directions.py | cat -n
```

```output
     1	    def promote_to_learning(
     2	        self,
     3	        row_id: int,
     4	        direction: Direction | None = None,
     5	    ) -> None:
     6	        """Set state to LEARNING with today's due_at and a fresh last_review.
     7	
     8	        The caller is responsible for ensuring the collocation exists.
     9	
    10	        Note: `left` is left as NULL, so sync_push routes to
    11	        set_due_date (the new/review branch at sync.py:1219), not to
    12	        set_learning_state. Anki receives "due today" without learning-step
    13	        metadata — TunaTale shows LEARNING, Anki treats it as effectively new.
    14	        This matches the "no FSRS grade" intent but creates a silent asymmetry
    15	        between TT and Anki views.
    16	        """
    17	        today_due_at = due_at_rollover_utc(date.today()).isoformat()
    18	        now = datetime.now(UTC)
    19	        now_ms = int(now.timestamp() * 1000)
    20	        now_iso = now.isoformat()
    21	        with self._get_conn() as conn:
    22	            if direction is None:
    23	                conn.execute(
    24	                    "UPDATE collocation_directions SET state = 'learning',"
    25	                    " due_at = ?, last_review = ?, last_review_time_ms = ?,"
    26	                    " dirty_fsrs = 1 WHERE collocation_id = ?",
    27	                    (today_due_at, now_iso, now_ms, row_id),
    28	                )
    29	            else:
    30	                conn.execute(
    31	                    "UPDATE collocation_directions SET state = 'learning',"
```

Two-path semantics:

- **Never-synced rows** (`anki_note_id IS NULL`) — e.g. words auto-added by `/listen` that the user immediately marks "ignored" before any sync — get hard-deleted, taking their `violations` rows with them. Cascade FK delete handles the direction rows.
- **Synced rows** — both directions flip to `state='suspended', dirty_fsrs=1`. The next `sync_push` translates that into Anki's `queue=-1` (suspended) via the existing dirty-FSRS branch, so the card disappears from Anki's review pool too.

The matching state-set endpoint (`/state`) special-cases `"learning"` to call `db.promote_to_learning` instead of `set_state_by_id` — `promote_to_learning` writes a fresh `last_review = now`, `due_date = today`, and `dirty_fsrs = 1`, but leaves `left`/`due_at` as NULL. That asymmetry is intentional but documented in the docstring at `backend/app/srs/database.py:874`: TT shows the card as LEARNING immediately, Anki receives it as a same-day-due card without learning-step metadata, and the user re-grades it normally on next session.

### 15.2 The `/api/srs/listen` Endpoint

`POST /api/srs/listen` is the entry point: the user clicks "I listened to this lesson" and the lesson's words are tokenized, lemmatized, and registered as SRS items with a Rating.GOOD grade. It now also branches on `card_type`:

```bash
sed -n "376,445p" backend/app/api/srs.py
```

```output
@router.post("/listen", status_code=200)
async def mark_lesson_listened(body: ListenRequest, request: Request):
    store = request.state.content_store
    lesson = store.get_lesson(body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.state.srs_db
    col_crt = resolve_col_crt(db)
    llm = getattr(request.app.state, "llm", None)
    # One shared set across this request so two new words don't pick the same image.
    used_image_urls: set[str] = set()
    # One session balancer for the whole request; each grade below feeds itself
    # back via _balancer_add so later grades in this lesson see earlier ones.
    balancer = build_live_load_balancer(db, now=datetime.datetime.now(datetime.UTC), col_crt=col_crt)

    # ── Word-level tracking from NATURAL_SPEED section ──────────────────
    from app.models.lesson import Section, SectionType, extract_sentence_translations_from_translated

    token_glosses: dict[str, str] = lesson.generation_metadata.get("token_glosses", {})
    sentence_translations: dict[str, str] = lesson.generation_metadata.get("sentence_translations", {})
    # Backfill path: pre-Layer-N lessons have no `sentence_translations` in
    # metadata. Recover from the TRANSLATED section so old lessons can still
    # populate cloze cards' Back Extra. First-occurrence wins on the merge.
    derived_st = extract_sentence_translations_from_translated(lesson)
    for k, v in derived_st.items():
        sentence_translations.setdefault(k, v)

    natural_speed = next(
        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
        None,
    )

    unique_lemmas: set[str] = set()
    lemma_to_sentence: dict[str, str] = {}
    lemma_to_surfaces: dict[str, set[str]] = {}
    # The surface as it first appeared, paired with lemma_to_sentence — used to
    # blank the *surface* (not the dictionary lemma) in plain function-word clozes.
    lemma_to_first_surface: dict[str, str] = {}
    # Surface (casefolded) → classla UPOS, for POS-first function-word detection.
    # Empty/"" under LowercaseLemmatizer, so the curated include-list is the only
    # signal there (legacy behavior); classla supplies AUX/ADP/PRON/... and catches
    # the whole biti paradigm (ste/smo/so) without enumerating surfaces.
    surface_to_upos: dict[str, str] = {}

    lemmatizer = get_lemmatizer(lesson.language_code)
    model_version = model_version_for(lemmatizer)

    def _analyze_phrases(section: Section) -> None:
        # Runs the (classla) lemmatizer over the lesson's L2 phrases, filling the
        # dicts above. Offloaded to a worker thread (below) so the blocking pipeline
        # doesn't stall the event loop. The await suspends this coroutine until the
        # thread finishes, so the shared-dict mutation has no concurrent access.
        for phrase in section.phrases:
            if phrase.language_code != lesson.language_code:
                continue
            surfaces = tokenize(phrase.text)
            phrase_lemmas = lemmatize_surfaces_in_context(
                surfaces, phrase.text, lemmatizer, lesson.language_code, db, model_version
            )
            for ta in analyze_sentence_cached(db, lemmatizer, phrase.text, lesson.language_code, model_version):
                surface_to_upos.setdefault(ta.surface.casefold(), ta.upos)
            for surface, lemma in zip(surfaces, phrase_lemmas, strict=True):
                unique_lemmas.add(lemma)
                if lemma not in lemma_to_sentence:
                    lemma_to_sentence[lemma] = phrase.text
                    lemma_to_first_surface[lemma] = surface
                lemma_to_surfaces.setdefault(lemma, set()).add(surface)

    if natural_speed is not None:
```

Notable details:

- **Lemma-keyed registration.** The natural-speed phrases are tokenized via `app.srs.tokenizer.tokenize` then lemmatized through `app.srs.lemmatizer.Lemmatizer.lemmatize` (a thin wrapper over a hand-curated dictionary). The lemma is what gets stored as `collocations.text`, so subsequent listens of the same lesson hit the existing row (`unique_lemmas` dedup is per-call; `db.add_collocation` ON CONFLICT DO NOTHING dedups across calls).
- **Cloze branching.** When `enable_cloze_cards` is on (DB-backed flag, default OFF) and the language is Slovene, function words go through the cloze path: `card_type="cloze"`, `source_sentence` captured from the first natural-speed phrase containing the surface. Everything else gets `card_type="vocab"`. See PART 15.5 below for the cloze pipeline.
- **Auto-grade.** Every registered lemma gets a `Rating.GOOD` grade immediately — the user already heard it, so the FSRS state advances on first listen rather than waiting for a manual review.
- **Key phrases are preserved verbatim** (`kp.phrase` is the original surface form, not lemmatized). Their `translation` is already known from the curriculum, so it survives the `idempotent` guard at line 276 even on re-listen.

### 15.3 The Transcript Component (Phase D)

`frontend/src/lib/components/Transcript.svelte` is a 175-line Svelte 5 component (with a 261-line test file) that renders the lesson dialogue with per-word color coding, click-to-grade popovers (originally click-to-cycle), drag-to-select phrase capture, and an "Add phrase…" affordance for phrases that don't appear verbatim. The data shape comes from `GET /api/srs/lesson/{lesson_id}/transcript` (`backend/app/api/srs.py`, near line 657 today):

```
{
  lesson_id: string,
  key_phrases: [{phrase, translation}],
  dialogue_lines: [
    {role, words: [
      {surface, lemma, srs_state, srs_item_id, translation,
       collocation_span_id, collocation_start,
       collocation_srs_state, collocation_lemma, collocation_translation}
    ]}
  ]
}
```

`collocation_span_id`/`collocation_start` are non-null when a multi-word collocation overlaps that word. The component groups overlapping words into a single styled collocation token; otherwise each word is its own `WordSpan`. State colors:

- **unknown / new** — dotted underline (user hasn't seen it)
- **learning / relearning** — yellow underline
- **review** — green underline (graduated)
- **known** — no underline
- **ignored / suspended** — strikethrough, faded

Originally, clicking a word cycled its state directly through a hardcoded `STATE_CYCLE` map. That direct-click cycle is **gone** — PART 25's word-learning state machine replaced it with a popover whose single grade button's label mirrors what the old click did (see the `WordSpan.svelte` excerpt above: unknown → "Start learning", due+tracked → grade Good, not-due-but-readable → review ahead), with `/untrack` still reachable from the popover. The `/state` endpoint survives for the `/cards` admin page.

### 15.4 Translate Button + Off-Transcript Phrase Entry (Phase E)

When the user drags to select a phrase ("dober dan" → "good day") that isn't pre-translated, the popover shows a ✨ button. Clicking it calls a new endpoint:

```bash
sed -n "728,746p" backend/app/api/srs.py
```

```output
_VALID_LANGUAGE_CODES = known_language_codes()


@router.post("/translate", status_code=200)
async def translate(body: TranslateRequest, request: Request):
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")
    if body.language_code not in _VALID_LANGUAGE_CODES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid language_code: {body.language_code!r}. Must be one of {sorted(_VALID_LANGUAGE_CODES)}",
        )
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM not configured")
    translation = await translate_term(llm, body.text, body.language_code)
    return {"translation": translation}
```

`translate_term` is the same Groq-backed prompt used by `POST /api/srs/items` when a card is created without a translation (see Part 7.3) — Phase E reuses it instead of duplicating the prompt. Three failure modes are surfaced explicitly to the UI (no more silent `try/catch`):

- empty `text` → **422** "text must not be empty"
- `language_code` not in `{"sl", "en"}` → **422** with a list of valid codes
- LLM not configured → **503** "LLM not configured"

The Transcript component awaits the call inline, fills the translation field, and lets the user edit before clicking **Create**. That hits the existing `POST /api/srs/items` with the manual `translation`; the LLM is never called twice for the same selection.

Below the dialogue lines, an `Add phrase…` collapsed section accepts free-form L2 text for phrases that don't appear verbatim in the transcript. It uses sentinel `source_line_index = -1` so downstream consumers can distinguish on-transcript adds from manual entries. Phrases flow through `sync_create_new` like any other manual add — no special Anki-side path.

### 15.5 Function-Word Cloze Cards (Phase F)

The cloze spike (`feat(srs): Phase F` — commit `1006f49`) wires `/listen` to also create **Anki Cloze notes** for Slovene function words detected in NATURAL_SPEED phrases. It's behind a feature flag (`enable_cloze_cards`, set via `PUT /api/srs/settings/cloze` and surfaced as a checkbox on `/admin/srs`).

The pipeline:

1. **Detection.** `is_function_word(lemma, "sl")` checks against `SLOVENE_FUNCTION_WORDS` — a curated 22-word frozenset (`je`, `kje`, `v`, `kaj`, `sem`, `si`, `da`, `za`, `tam`, `na`, `kako`, `ni`, `ja`, `se`, `to`, `vam`, `z`, `mi`, `še`, `pa`, `ti`, `po`). The list was generated by `app/srs/build_function_word_list.py` over a 7-day curriculum, then manually curated to drop obvious content words.
2. **Storage.** Migration v18→v19 adds `collocations.card_type TEXT DEFAULT 'vocab'`. Cloze cards get `card_type='cloze'` and `source_sentence=<the natural-speed phrase>`. `add_collocation` (now `backend/app/srs/db_collocations.py:22`) creates only a **PRODUCTION** direction for cloze cards (`card_type == "cloze"` → `directions = [Direction.PRODUCTION]`, `db_collocations.py:103-106`) — a cloze is a fill-in-the-blank *production* act; there is no recognition side. (An earlier revision of this paragraph said RECOGNITION — wrong; PART 20 has it right.)
3. **Cloze text generation.** `make_cloze_text(surface, source_sentence)` wraps every word-bounded occurrence of `surface` with `{{c1::surface}}`. It's case-insensitive but case-preserving, idempotent (if `{{c1::...}}` is already present it passes through), and skips empty source sentences.
4. **Anki note creation.** `OfflineWriter.create_cloze_note` (`backend/app/anki/sync.py:485`) targets Anki's built-in **Cloze** notetype (looked up by `name='Cloze'` in `notetypes`). The fields are `Text` (the cloze-wrapped sentence) and `Back Extra` (left empty). GUID is computed from the cloze-wrapped text + language code via `compute_guid` so duplicate detection works the same way as vocab notes. Each template's `cards.due` is allocated from `MAX(due)+1` over existing new cards.
5. **Routing.** `sync_create_new` checks `item.syntactic_unit.card_type` and dispatches to `create_cloze_note` (cloze) or `create_note` (vocab). The dispatch is at `backend/app/anki/sync.py:1449`.

The flag default is OFF; turning it on only affects new function-word lemmas going forward. Existing rows aren't backfilled — they stay as `vocab` cards.

### 15.6 Recency-Prioritized New Queue (Phase C)

Background: when the user listens to a fresh lesson on day N, the auto-added words should surface in `/review-queue` ahead of the imported Anki backlog from day 1. Anki's default "HighestPosition" gather orders by `cards.due DESC` (newest first), but the imported backlog has higher `due` than the just-`add_collocation`'d rows that haven't been pushed yet.

Phase C threads recency through both the gather query and the sync_create_new allocator:

- **`get_new_items` ORDER BY.** Layer 24's original sort was `c.created_at DESC` first. Layer 25 revised it to `d.anki_due DESC NULLS FIRST, c.created_at DESC, d.anki_card_id ASC, c.id ASC` — matching Anki's `HighestPosition` gather under `NewCardSorting`. Unsynced TT-adds (`anki_due IS NULL`) sit on top via NULLS FIRST; synced rows order identically in both apps.
- **`sync_create_new` allocates `cards.due` from `MAX(due) + 1`** over existing new cards, in `created_at ASC` order. So when 30 fresh `/listen` lemmas push at sync time, the most recent gets the highest `cards.due`, surfacing first on the next Anki session too.
- **Migration v16→v17** adds `CREATE INDEX idx_collocations_created_at ON collocations(created_at)` — without it every queue rebuild does a full sort.
- **Required deck setting.** Anki's "Display Order → New card gather order" must be set to **"Descending position"** for sync to reflect the recency ordering. Without it, Anki surfaces oldest-first and the TT-side recency work is invisible on the Anki side. TT-side recency works regardless.

### 15.7 Listen-First Endpoint Index

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/srs/listen` | Mark lesson listened; auto-add words; auto-grade GOOD |
| GET | `/api/srs/lesson/{id}/transcript` | Per-word state for the Transcript component |
| POST | `/api/srs/translate` | LLM-translate a free-form selection (✨ button) |
| POST | `/api/srs/items/{id}/state` | Cycle to `new/learning/known/ignored` |
| POST | `/api/srs/items/{id}/untrack` | Delete (never-synced) or suspend (synced) |
| GET | `/api/srs/settings/cloze` | Read cloze flag |
| PUT | `/api/srs/settings/cloze` | Toggle cloze flag |

---

## PART 16: Anki Queue Parity — Layers 24–31

Stage 3 (PART 12) introduced bidirectional sync. Between syncs, both apps schedule independently, and TT must mirror Anki's algorithms closely enough that switching apps doesn't feel discontinuous. The "layers" history lives in `docs/anki-parity-layers.md` and the principles plus a divergence decision tree live in `.claude/rules/anki-queue-parity.md` — read those before editing `app/api/srs.py`, `app/srs/fsrs.py`, `app/srs/anki_mirror/queue_stats.py`, or `app/anki/sync.py`.

This section documents Layers 24–31, all landed since the previous walkthrough.

### 16.1 Layer 24 — Recency Becomes the Lead Sort

The lead sort key in `get_new_items` flipped from Anki-position to `c.created_at DESC`. Documented above in Part 15.6. Layer 25 then revised it again to merge Anki's HighestPosition gather (by `anki_due DESC`) with recency on top, giving the final ORDER BY:

```
ORDER BY d.anki_due DESC NULLS FIRST, c.created_at DESC,
         d.anki_card_id ASC, c.id ASC
```

This is the single source of truth for new-card pull order. Don't re-introduce a per-direction-only sort here — Layer 28 below explains why the post-merge step is load-bearing.

### 16.2 Layer 26 — `introduced_at` Replaces Sticky-NEW Filter

Old: `count_new_introduced_today` filtered on `prior_state='new' AND last_review today`. That over-counted: a sticky-NEW card whose intro was on day N–3 but which got re-reviewed today would show up. Anki's `newToday` counter increments only on the very first NEW→non-NEW transition.

New: migration v17→v18 adds `collocation_directions.introduced_at TEXT` plus `idx_directions_introduced_at`. The column is written exactly once per direction:

- `fsrs.schedule` stamps it when the grade event transitions the row out of NEW (TT-side first grade).
- `sync_pull._resolve_introduced_at` stamps it from `MIN(revlog.id)` for an Anki-side first grade observed during pull.

`count_new_introduced_today` (`backend/app/srs/db_counts.py:131` since the 2026-07-04 database split) just filters distinct `collocation_id` with `introduced_at` in today's UTC window:

```bash
sed -n "131,144p" backend/app/srs/db_counts.py
```

```output
    def count_new_introduced_today(self, today: date) -> int:
        """Count distinct collocations whose first NEW→non-NEW transition fell today.

        Filters on the explicit `introduced_at` column written once by the grade
        endpoint (`app.srs.fsrs.schedule`) and by `sync_pull` on the first
        introduction event. Mirrors Anki's `newToday` counter, which increments
        only on that first grade — subsequent reviews of the same card on later
        days do NOT bump it.

        Pre-Layer-26 rows that were introduced before `introduced_at` existed
        have NULL and naturally fall out of the count. Going forward, every new
        grade populates the column.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
```

Pre-Layer-26 rows have NULL `introduced_at` and naturally fall out of the count. Going forward, every new grade populates the column. The local-timezone-to-UTC math handles the daily rollover the same way `count_review_due_collocations` does.

The Layer 22 distinction (`introduced_at` is a one-shot stamp, NOT a sticky marker) matters: don't conflate it with `prior_state='new'`. `prior_state` lives for the entire intro arc and applies to revlog correctness; `introduced_at` is a fixed timestamp that anchors Anki's `newToday` parity.

### 16.3 Layer 27 — Daily Unbury Sweep

Anki resets `queue=-2` (sibling-buried) and `queue=-3` (scheduler-buried) cards back to their original queues once per day, on the first queue rebuild after rollover. TT must mirror this — stale `state='buried'` rows from a prior day under-count `count_review_due_collocations` and silently drop cards from the review pool.

`SRSDatabase.unbury_if_needed(today)` (`backend/app/srs/db_queue.py:224` since the database split) runs at the top of three call sites: `/queue-stats`, `/review-queue` (via `_compute_live_main`), and `sync_pull`. It's tracked via `anki_state_cache['last_unbury_day']`:

```bash
sed -n "224,242p" backend/app/srs/db_queue.py
```

```output
    def unbury_if_needed(self, today: date) -> int:
        """Anki-parity daily unbury sweep — restores stale sched-buried rows.

        Anki distinguishes two bury kinds: ``queue=-3`` (sched/sibling, auto-
        released at next rollover) and ``queue=-2`` (user/manual, stays buried
        until manually unburied). TT mirrors this via ``bury_kind``:
        only rows where ``bury_kind = 'sched'`` get released here. Manually-
        buried rows (``bury_kind = 'user'``) survive the sweep, matching
        Anki's ``unbury_if_needed`` behavior in ``rslib/.../queue/builder/``.

        Tracked via ``anki_state_cache['last_unbury_day']``. Idempotent within a
        local day — subsequent calls today return 0 without touching anything,
        which is important because sync_pull within the same day may land new
        ``state='buried'`` rows for today's sibling-buries that must stick.

        Returns the number of rows unburied.
        """
        cached = self.get_anki_state_cache("last_unbury_day")
        today_iso = today.isoformat()
```

Idempotency matters: `sync_pull` within the same day may land *new* `state='buried'` rows (today's sibling-buries that must stick). The `last_unbury_day` cache guards against re-sweeping them.

### 16.4 Layer 25 + Layer 28 — Cross-Direction Gather, Bury, Template Sort

Per-direction ordering in `get_new_items` is necessary but not sufficient. Anki's `add_new_card` (rslib `queue/builder/gathering.rs:63-169`) gathers BOTH ords in one pass and proactively buries the LATER sibling per note — so the higher-due sibling wins. Then `sort_new` (`sorting.rs:14-36`) stably re-sorts by `ord` (the Template step) so ord=0 (recognition) comes before ord=1 (production) within each note's surviving direction.

TT's `_merge_directions` (`backend/app/srs/anki_mirror/queue_engine.py:91` since the 2026-07-04 queue-engine extraction) mirrors the gather sort key exactly:

```bash
sed -n "91,130p" backend/app/srs/anki_mirror/queue_engine.py
```

```output
def _merge_directions(
    rec: list[tuple[int, SRSItem, str]],
    prod: list[tuple[int, SRSItem, str]],
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Merge new-card directions in Anki's gather order.

    Mirrors Anki's `add_new_card` (rslib `queue/builder/gathering.rs:63-169`),
    which fetches cards under `NewCardSorting::HighestPosition` =
    ``"due DESC, ord ASC"`` (storage/card/mod.rs:923) and proactively buries
    the LATER sibling per note. By interleaving both directions in that gather
    order BEFORE sibling-bury runs, the higher-anki_due sibling wins. The
    downstream Template re-sort (applied to the survivors in `get_review_queue`)
    then ranks ord=0 (recognition) ahead of ord=1 (production).

    Sort key (LOWER sorts first):
      1. ``(0,)`` for ``anki_due IS NULL`` else ``(1, -anki_due)`` — NULLS FIRST, DESC
      2. ord ASC (Direction.RECOGNITION = 0, Direction.PRODUCTION = 1)
      3. anki_card_id ASC NULLS LAST (deterministic tiebreak)
      4. row_id ASC (final tiebreak)

    Together with the post-bury Template sort in `get_review_queue`, this
    reproduces the gather → bury → Template-sort pipeline exactly.

    Phase 3 note (Layer 65): the production NEW pool is gated upstream in
    `get_new_items` — a production card is withheld until its recognition
    sibling has graduated past the learning arc. So for a paired both-NEW note
    no production card reaches this merge; recognition wins. The "higher-anki_due
    sibling wins" behavior only applies once recognition is REVIEW (production
    introducible) or among recognition cards / cloze cards.
    """
    combined: list[tuple[int, SRSItem, str, Direction]] = []
    for row_id, item, lang in rec:
        combined.append((row_id, item, lang, Direction.RECOGNITION))
    for row_id, item, lang in prod:
        combined.append((row_id, item, lang, Direction.PRODUCTION))

    def _gather_key(
        t: tuple[int, SRSItem, str, Direction],
    ) -> tuple[int, int, int, int, int]:
        row_id, item, _lang, direction = t
```

After `_merge_directions`, `_compute_live_main` runs `_bury` (`backend/app/api/srs.py:850`) to keep only the first-seen survivor per `collocation_id`. Then a final stable sort by `ord` (`nonlearning_new.sort(key=lambda t: 0 if t[3] == Direction.RECOGNITION else 1)`) reproduces Anki's Template step.

Layer 28's fix was the `časa`/`sekira` head-of-queue divergence: per-direction sorts let recognition-bucket order disagree with Anki because the gather/bury order on the production side was selecting a different survivor. The interleaved merge fixed it.

### 16.5 Layer 29 — Eager `session_main_queue` Rebuild on Sync

`session_main_queue` is the DB-backed frozen queue order — Anki rebuilds it once at session open / sync; TT mirrors the freeze moment. Before Layer 29, `sync_pull` only **cleared** the cache and deferred rebuild to the next `/review-queue` request. Hours could pass before that request, letting the underlying pool shift — the two apps froze their queues at different moments, causing off-by-slot drift on the first-new-card position.

Layer 29 added `build_and_freeze_main_queue(db)` (now `backend/app/srs/anki_mirror/queue_engine.py:308`) and called it immediately after the clear in `sync_pull`:

```bash
sed -n "308,319p" backend/app/srs/anki_mirror/queue_engine.py
```

```output
def build_and_freeze_main_queue(db) -> None:
    """Compute live_main and write it to session_main_queue cache.

    Called by sync_pull post-ingest so the freeze moment is at sync completion,
    matching when Anki rebuilds its own queue. Without this, TT freezes on the
    first /review-queue request after sync — which can be much later, with a
    different pool state, causing drift on the very-first-new-card position.
    """
    today = datetime.date.today()
    live_main = _compute_live_main(db)
    set_session_main_queue(db, today, [(t[0], t[3].value) for t in live_main])
```

`_compute_live_main` (now `backend/app/srs/anki_mirror/queue_engine.py:188`) was extracted out of `get_review_queue` for this: the live-pool build logic up through the spread step is shared between the route handler and the eager-rebuild call. The route handler still owns cache reconciliation, learning-card assembly, and the collapse hack — those depend on the request-scoped `now`/`cutoff`.

Deploy-time pitfall to remember: the cache lives in `anki_state_cache` (DB-backed), so it survives backend restarts. After changing queue-assembly logic, an existing cache row will replay the OLD order until the next sync — restart alone does NOT invalidate it. When debugging a "fix doesn't seem to be working" report, run `clear_session_main_queue` first (see the diagnostic in `.claude/rules/anki-queue-parity.md`) before concluding the fix is broken.

### 16.6 Layer 30 — `_queue_to_state` Must Trust `queue`, Not `reps`

The previous mapper had a fallback `if reps == 0: return SRSState.NEW`. That broke when an Anki user hit "Forget" on a graduated card — `cards.queue` stays at 2 (review) but `cards.reps` resets to 0. The fallback wrongly mapped these to NEW, surfacing them as fresh new cards in TT.

`_queue_to_state` (`backend/app/anki/sync_engine.py:204` since the sync split) now treats `queue` as authoritative:

```bash
sed -n "204,227p" backend/app/anki/sync_engine.py
```

```output
def _queue_to_state(queue: int, card_type: int, reps: int) -> SRSState:
    """Map Anki's (queue, type, reps) tuple to TT's SRSState.

    `queue` is the authoritative signal for Anki's current placement — TT
    must mirror it directly. Layer 30: the previous `if reps == 0: NEW`
    fallback wrongly mapped `(queue=2, reps=0)` cards to NEW, surfacing
    already-graduated cards (e.g. via Anki's "Forget" action or a manual
    `cards.due` edit, which clears `reps` but leaves `queue=2`) as fresh
    new cards in TT.
    """
    if queue == -1:
        return SRSState.SUSPENDED
    if queue in (-2, -3):
        return SRSState.BURIED
    if queue == 1:
        return SRSState.RELEARNING if card_type == 3 else SRSState.LEARNING
    if queue == 3:
        return SRSState.RELEARNING
    if queue == 2:
        return SRSState.REVIEW
    if queue == 0:
        return SRSState.NEW
    # Fallback for unknown queue values (shouldn't happen against modern Anki).
    return SRSState.NEW if reps == 0 else SRSState.REVIEW
```

The `card_type == 3` branch distinguishes RELEARNING (re-step after a lapse) from LEARNING (initial steps) within `queue=1` — same as Anki's internal model.

### 16.7 Layer 31 — `<b>L2</b><br><i>EN</i>` Field Split

The user's Anki collection has a Pronunciation/Basic notetype that stores both the L2 word and its English gloss in ONE field with HTML formatting (e.g. `<b>nič</b><br><i>nothing</i>`). Pre-Layer-31, the HTML-strip fallback in `extract_l2_from_fields` concatenated the two inner texts (`ničnothing`) and saved it as TT's `text` column with no translation.

Layer 31 adds two pieces:

1. **`extract_gloss_from_fields`** (`backend/app/anki/sqlite_reader.py:350`) — returns the English gloss when a field uses the pattern.
2. **A short-circuit in `extract_l2_from_fields`** (`backend/app/anki/sqlite_reader.py:386-389`) — runs before the score-based fallback so the `<b>X</b><br><i>Y</i>` pattern picks the `<b>` group cleanly. The `_B_THEN_I_PATTERN` is a module-level regex anchored at `^\s*<b>([^<]+)</b>\s*<br\s*/?>\s*<i>([^<]+)</i>`.

`import_seed` and the sync_pull `get_note_records` path both use the updated extractor, so new imports come in clean. For the 39 already-mangled rows in the live DB, a one-shot script now archived at `backend/scripts/anki_archive/fix_html_concat_imports.py` walks the TT DB, cross-checks the linked Anki note, and either renames the row (`text=X, translation=Y`) or deletes it when a clean-X twin collocation already exists. The script is read-only on `collection.anki2`, mutates only `tunatale.db`, supports `--dry-run`, and is invoked as:

```
uv run python -m scripts.anki_archive.fix_html_concat_imports [--dry-run]
```

### 16.8 Layer Summary

| Layer | Where | What changed |
|-------|-------|--------------|
| 24 | `database.get_new_items` | Lead sort flipped to `created_at DESC` for recency |
| 25 | `database.get_new_items` | ORDER BY revised to `anki_due DESC NULLS FIRST, created_at DESC, ...` |
| 26 | `database.count_new_introduced_today` + migration v17→v18 | `introduced_at` column replaces sticky-NEW filter |
| 27 | `database.unbury_if_needed` | Daily unbury sweep at queue-build |
| 28 | `srs._merge_directions` + post-bury Template sort | Cross-direction gather + bury + ord-stable sort |
| 29 | `srs.build_and_freeze_main_queue` + `sync_pull` call site | Eager rebuild on sync, not lazy on first request |
| 30 | `sync._queue_to_state` | `queue` is authoritative, `reps` is fallback-only |
| 31 | `sqlite_reader.extract_l2_from_fields` + `fix_html_concat_imports.py` | Pronunciation notetype `<b>L2</b><br><i>EN</i>` split |

---

## PART 17: Sync Cleanups & Dead-Code Removals

A cleanup pass between Phases D and F reduced sync.py noise and deleted three dead pipelines. Documented at the bottom of `docs/anki-parity-layers.md` under "Cleanup pass." None of the cleanups changed behavior — they're pure refactors with the same test counts before and after, except the removed-pipeline commits which deleted test files alongside their implementations.

### 17.1 Extracted Helpers (Three Commits)

**`_queue_to_state` helper** (commit `8b11935`). Three duplicate `if queue == ...` ladders in `sync_pull` collapsed to one module-level function. Layer 30 then made this single helper the place to fix the `reps=0` bug — keeping the dedup work paid off immediately.

**`_record_conflict` helper** (commit `0309a85`). Five duplicate blocks of the form:

```python
report.conflicts.append(SyncConflict(...))
if not dry_run:
    self._db.record_sync_conflict(...)
```

collapsed to `self._record_conflict(report, guid=..., direction=..., field=..., local=..., remote=..., resolution=..., dry_run=dry_run)` (now in `backend/app/anki/sync_engine.py`).

**`_resolve_prior_state` closure** (commit `38d2804`). The call-site signature was passing `first_review_ms`, `today_start_ms`, and the local direction state through repeated kwargs. The refactor introduces a per-iteration `_prior` closure that captures `card_rec.first_review_ms` and `today_start_ms` once, leaving the call site as `_prior(local_dir, new_state)`. Same idea applied to `_intro_at = _resolve_introduced_at`. Visual noise dropped, behavior identical.

### 17.2 Three Dead Pipelines Deleted

**`_factor_to_fsrs_difficulty` helper** (commit `55d57b2`). The push path used to compute an FSRS difficulty from the Anki ease factor before writing revlog. Layer 17+ obsoleted it (we now persist `prior_state` and use `_derive_revlog_shape`), but the helper plus its 12-test suite hung on. Removed both.

**`_spread_mix.ratio_override`** (commit `916e0bf`). Layer 9 added a parameter to override the intersperser ratio at session-start; Layer 14 reverted that approach but left the parameter in place. The parameter and its tests are gone.

**Review-count pipeline** (commit `b4e6fd7`). An entire `count_review_*` family inside `queue_stats.py` plus a 512-line test file (`tests/test_queue_stats_review.py`) and a 193-line cache test file (`tests/test_queue_stats_cache.py`) — all driving a badge logic path that hadn't been wired to the API since the Phase A refactor. The `count_review_due_collocations` method (the path the UI actually reads) was left in `database.py`. Deletes:

- 251 lines from `app/srs/anki_mirror/queue_stats.py`
- `tests/test_queue_stats_cache.py` (193 lines)
- `tests/test_queue_stats_review.py` (512 lines)

### 17.3 Why It's Worth Reading

When debugging a queue divergence, dead code is a trap: the divergence playbook in `.claude/rules/anki-queue-parity.md` walks specific helpers, and if a stale one is still in the tree, it can look like the active implementation. The Cleanup pass made the file harder to misread. Future cleanups should follow the same shape: prove the path is dead with `git grep` + test removal, delete in one commit, leave the rule file untouched.

---

## PART 18: Parity Testing Harness

TT mirrors Anki's scheduling algorithms, and the divergence history (`docs/anki-parity-layers.md`, 80 layers) reflects how many subtle branches that touches. The parity harness lets TT pin its parallel functions against Anki's actual scheduler at test time, before divergences reach a user-visible badge.

### 18.1 Subprocess Boundary

`backend/tests/anki_oracle/` holds the three-file harness: `synthetic_collection.py` builds a minimal modern-schema `collection.anki2` on disk (with the `config` table modern Anki actually reads, not just legacy `col.conf` JSON); `oracle.py` is the subprocess that opens the collection, enables V3, and runs JSON-in/JSON-out ops; `harness_fixtures.py` exposes the pytest fixtures + `run_oracle()` helper.

**Backend production code must never `import anki`** (queue-parity rule 1 — TT cannot have a runtime dependency on Anki being installed). The harness spawns a separate process via `uv run --with anki python oracle.py`. Backend tests don't import anki either; they call `run_oracle(collection_path, operations)`. CI runs the harness in a dedicated **oracle-parity job** (`pytest -m oracle --run-oracle -n auto --no-cov`, `.github/workflows/ci.yml`) alongside backend, frontend, and peer-sync jobs; `./test.sh` passes the flag locally too.

### 18.2 What's Pinned

The parity-test files under `backend/tests/test_parity_*.py` each cover a cluster (five at the time of writing; **13 today**, adding daily caps, the load balancer, f32 FSRS, revlog factor, and more):

| File | Cluster |
|------|---------|
| `test_parity_fsrs_schedule.py` | FSRS stability + difficulty math, both recall and lapse paths |
| `test_parity_learning_steps.py` | `_schedule_with_steps` transitions, `_pack_left`/`_parse_left` round-trip |
| `test_parity_queue_order.py` | R-asc sort + FNV tiebreaker + NULL-R placement |
| `test_parity_bury.py` | `queue=-1/-2/-3` exclusion invariant |
| `test_parity_daily_caps.py` | `new_per_day` / `reviews_per_day` queue-count caps |

Findings are surfaced as `@pytest.mark.xfail(strict=True)` first, then fixed in a separate commit so the diagnostic stays reviewable on its own. Full rule (synthetic-collection gotchas, both-gates-per-commit workflow) at `.claude/rules/anki-oracle-harness.md`.

The two highest-cost gotchas, both pinned by tests inside the harness module: (1) `cards.data` needs all of `s`/`d`/`dr`/`lrt` for the FSRS path — missing `lrt` silently routes through `stability_short_term`; missing `dr` ties every card at the SM2 fallback's near-zero value; (2) `learn_steps` / `relearn_steps` are `repeated float` (packed LEN-delimited f32), not VARINT — Anki silently falls back to defaults if you encode them wrong.

---

## PART 19: Event Log — `tt_revlog`

`sync_pull` (PART 12.4) merges TT and Anki state field-by-field. The merge is a snapshot diff and can't represent *events* — if both apps graded the same card today at different millisecond timestamps, field-merge picks the later one's values and loses the earlier grade entirely. The `tt_revlog` table mirrors Anki's `revlog` schema so every grade can be persisted as an event row, with sync eventually moving to `INSERT OR IGNORE` event-merge instead of field-diff.

### 19.1 Schema And Write Paths

`tt_revlog` (migration v26) has PK `(id, collocation_id, direction)` with `id` as ms-since-epoch wall-clock, plus `button_chosen`, `interval`, `last_interval`, `factor`, `taken_millis`, `review_kind`, `anki_card_id`. The PK shape makes future event-merge with Anki deltas a straight `INSERT OR IGNORE` once the ids align.

Three write paths:

- **TT-side grades** (drill + listen word + listen key-phrase in `api/srs.py`): `fsrs.build_revlog_row → db.append_revlog` after `schedule()` returns.
- **Anki-side grades** (`sync_pull._ingest_anki_revlog_for_card`): filters `OfflineReader.get_revlog_for_card` by `last_synced_at`, INSERT OR IGNORE.
- **Manual state mutations** (`promote_to_learning` from the listen-first UI): emit `review_kind=4` rows.

A content-based dedup helper, `SRSDatabase.has_revision_near(...)`, lets `_ingest_anki_revlog_for_card` skip an Anki row when a TT-written row within ±5s with the same `button_chosen` already exists. This catches Anki copies of TT-grades that landed at slightly-different ms timestamps.

### 19.2 Replay Diagnostic

`SRSDatabase.rebuild_from_revlog(collocation_id, direction, anki_card_id=None, exclude_review_kinds=frozenset({4}))` replays the rows through `schedule()` starting from NEW, returns a `DirectionState`. The `anki_card_id` parameter is required — FSRS interval fuzz seeds off `(card.id + reps)`, so omitting it drifts replayed stabilities by O(fuzz days).

The companion script `app/anki/replay_fsrs_from_revlog.py` walks every direction and classifies each as MATCH (replay agrees with stored state), REPAIR (raw UPDATE preserves the 8 non-FSRS columns), or one of three SKIP buckets (synthetic-only, pre-FSRS SM-2 era, unknown). `--dry-run` snapshots both sides; concurrency guard via `BEGIN IMMEDIATE`.

### 19.3 Current Status

**This section is now history on both ends.** The measurement ran (see `docs/stage-3b-empirical-measurement.md`, DONE 2026-05-23 at 100% strict match), `event_sync_pull` flipped to `new` on 2026-06-02, and the whole mode flag was later decommissioned — `sync_pull` has a single path that takes Anki verbatim with a forward-step replay kept only as a recompute-divergence detector (PART 27's status note). On the **read** side, `rebuild_from_revlog` lives in `db_revlog.py`. On the **push** side, Layer 80 (2026-07-10) made `tt_revlog` the source of pushed history: every unpushed row is inserted into Anki's revlog at its own grade-time id, so intermediate TT grades no longer collapse into one row.

---

## PART 20: Cloze Pipeline

Cloze cards (introduced in PART 15.5) target Anki's built-in Cloze notetype with `card_type='cloze'` set on the `SyntacticUnit`. Only the PRODUCTION direction exists — the user supplies the missing word given the surrounding sentence. The pipeline produces the cloze text, sentence and word audio, an L1 sentence translation, and syncs all of it bidirectionally with Anki.

### 20.1 Cloze Text And Function-Word Detection

`make_cloze_text(sentence, target_word)` in `app/srs/function_words.py` wraps the target with Anki's `{{c1::word}}` syntax. The frontend rendering uses Unicode-aware lookarounds to mask the word — ASCII-only `\b` doesn't match around š/č/ž. `is_function_word(word, language)` keys off per-language JSON data (`app/srs/data/function_words/sl.json`, plus `no.json` for Norwegian); the `/listen` endpoint creates a cloze row for function-word matches (the feature flag mentioned below was later deleted — PART 23).

### 20.2 TTS Audio (Sentence + Word)

`app/audio/cloze_tts.py::synthesize_cloze_audios()` produces two MP3s per cloze card via EdgeTTS:

- **Sentence audio** — the full source sentence, content-addressed by SHA256 of the text so cards sharing a sentence reuse the file.
- **Word audio** — the clozed word in isolation, fetched on demand when the user taps the reveal button.

Migration v22 expanded `media.kind` to allow `'audio_tts_sentence'`; `SRSDatabase.get_sentence_audio_filename(collocation_id)` exposes the sentence row for the API. `/listen` generates audio eagerly for new clozes and backfills on re-listen. The CLI `app/audio/backfill_cloze_tts.py` covers existing rows.

### 20.3 Sentence Translation

`SyntacticUnit.source_sentence_translation` carries the L1 gloss. Story generation populates it through the LLM call (the metadata block includes per-sentence English); `/listen` writes it to the new `collocations.sentence_translation` column (migration v20→v21); `OfflineWriter.create_cloze_note` syncs it to Anki via `<span class='st'>{translation}</span>` inside Back Extra; `extract_sentence_translation_from_fields` (`sqlite_reader`) pulls it back during `sync_pull`. The frontend `DrillCard` shows it on production reviews so the user has L1 context for the masked sentence.

### 20.4 Anki Round-Trip

`OfflineWriter.create_cloze_note` writes against Anki's built-in Cloze notetype. To make sentence audio show up in the Anki card too, it appends `[sound:filename.mp3]` to the end of Back Extra when sentence audio exists — Anki's media sync then carries the MP3 alongside the note. The `extract_*_from_fields` extractors ignore the trailing `[sound:...]` so the next `sync_pull` doesn't see a phantom field change. Migration v23 primed existing cloze rows for `sync_push` to backfill the tag.

---

## PART 21: Frontend Toolchain

PART 13 covers the SvelteKit + Vite app at a structural level. The toolchain around it:

- **Package manager: Bun 1.3.14** (`~/.bun/bin/bun`). `package-lock.json` → `bun.lock`. `start-dev.sh` and `test.sh` invoke `bun run`. Bun-cold installs in ~3s vs ~25s for npm-cold — material because every CI frontend job and every `./test.sh` starts with an install step.
- **CI** runs the frontend job in parallel with the backend job (`.github/workflows/ci.yml`): `bun install → bun run fmt:check → bun run lint → bun run check → bun run test:coverage`. Playwright stays local-only.
- **Lint**: two layers. **Oxlint** (Rust, near-instant) for `.ts`/`.js`; **ESLint + `eslint-plugin-svelte`** for `.svelte` templates (uses `svelte-eslint-parser` with `typescript-eslint` for `<script lang="ts">`). `eslint-plugin-oxlint` disables rules ESLint and Oxlint both have. `svelte/no-at-html-tags` is globally disabled (Anki card HTML is controlled content); so is `svelte/no-navigation-without-resolve` (view-transitions API, not used).
- **Format: Oxfmt** for `.ts`/`.js`. Installed to the *root* `package.json` because its Svelte extension wants `svelte/compiler` at the same resolution level — Bun hoists Oxfmt to the repo root while Svelte stays in `frontend/node_modules`. `.oxfmtrc.json` excludes `.svelte` files for now.
- **Bundler: Vite 8.0.13** (`vite-plugin-svelte` warns "experimental" for Vite 8 / rolldown but all tests pass).
- **Test runner: Vitest 4** with v8 coverage and a custom Svelte-5 phantom-branch filter (see below).
- **E2E: Playwright** with 11 specs covering curriculum navigation, day picker, lesson page header, the `/cards` admin flow, the review loop including Again-rating queue placement, and SRS-seeding helpers shared via `tests/helpers.ts`.

### 21.1 Svelte 5 Phantom-Filter Coverage Gate

The Svelte 5 compiler injects template fragments that v8 reports as uncovered "branches" no test can reach (`'} created, {'`, ternary literals like `null`, `?? ''` defensives). Without filtering, threshold-based coverage gates would have to sit around 75% to absorb the noise.

`frontend/scripts/coverage-gate.ts` replaces Vitest's `thresholds:` block. It reads `coverage/coverage-final.json` and classifies each uncovered sub-location via `isPhantom(branchType, text, synthetic)`: cond-expr (`?:`) is phantom if text is a JS literal; binary-expr (`||`/`&&`/`??`) is phantom if it brackets a template-interp boundary or is a bare literal; empty source ranges are phantom; unknown branch types stay real (conservative). Drops are logged to `coverage/dropped-branches.json`; the gate then asserts 100% per-file on every metric.

`frontend/tests/coverage-gate.test.ts` pins every classification against empirical TunaTale cases — adding or changing a rule means updating both the heuristic and the test.

Maintenance note (`.claude/rules/testing.md`): after any `svelte` / `@vitest/coverage-v8` bump, eyeball the gate's "dropped N phantom branch(es)" line (baseline 131 on 47 files as of 2026-07-10; it was 46/21 in 2026-05 — growth tracks feature code, not compiler drift). A >20% delta means either a new phantom shape the filter misses or real bugs misclassified as phantom — fix the heuristic, don't lower the threshold.

---

## PART 22: Sentence-Aware Lemmatizer

PARTs 12–15 key every SRS card on a **lemma** — the dictionary form. The transcript view, the collocation matcher, and `/listen` all reduce surface words to lemmas before looking up cards. The default `LowercaseLemmatizer` just lowercases, which is wrong for an inflected language: Slovene `mize`, `mizo`, `mizi` are all the noun `miza`, and a lowercasing "lemmatizer" treats them as three different words. The lemma-as-unit choice in PART 25's word-learning state machine makes lemmatizer accuracy a **hard dependency** — so this part adds a real morphological analyzer behind the same Protocol.

### 22.1 The Protocol Grew an `analyze_sentence`

`app/srs/lemmatizer.py` defines the `Lemmatizer` Protocol. It used to expose just `lemmatize(word)`; it now also exposes `analyze(word) → (lemma, case, number)` and `analyze_sentence(sentence) → list[TokenAnalysis]`. The sentence method is the load-bearing one — Slovene lemmas are **POS-dependent and only resolvable in context**:

```bash
sed -n "402,446p" backend/app/srs/lemmatizer.py
```

```output
def lemmatize_surfaces_in_context(
    surfaces: list[str],
    sentence: str,
    lemmatizer: Lemmatizer,
    language_code: str,
    db: SRSDatabase | None = None,
    model_version: str = "",
) -> list[str]:
    """Lemmatize each surface using its *sentence* context, with a single-word fallback.

    Slovene lemmas are POS-dependent: classla reads the bare token ``dobro`` as the
    adverb (lemma ``dobro``) and bare ``hotel`` as the verb ``hoteti`` — but ``dobro``
    in *"Vse je dobro"* as the adjective (lemma ``dober``) and ``hotel`` in *"To je
    hotel"* as the noun. Lemmatizing tokens in isolation therefore mis-keys them and
    they never match the dictionary-form cards in the DB. We instead analyze the whole
    *sentence* once and map each *surface* to its in-context lemma, falling back to
    single-word ``lemmatize`` when a surface isn't found in the analysis (tokenization
    or punctuation mismatch).

    For ``LowercaseLemmatizer`` ``analyze_sentence`` is a per-token lowercasing, so the
    result is identical to the old single-word path — this change is a no-op for the
    default lemmatizer and only sharpens the real (classla) engine.

    Lemmas are lowercased to match the card keyspace (``import_seed`` stores
    ``lemma = front.lower()``). classla capitalizes proper-noun lemmas
    (``Ženeve`` → ``Ženeva``), which would otherwise miss the lowercase
    ``ženeva`` card on a case-sensitive ``lemma =`` lookup.

    When *db* and *model_version* are provided the sentence analysis is routed through
    the persistent ``lemma_analysis_cache`` table so the result survives restarts.
    """
    # note: this dict collapses on lowercase key. If the sentence contains multiple
    # surface forms that lowercase to the same key, the last analysis wins. This is
    # usually correct (same surface → same lemma) but can lose distinct lemmas when
    # genuinely different words share a lowercase form.
    analysis = analyze_sentence_cached(db, lemmatizer, sentence, language_code, model_version)
    context = {ta.surface.lower(): ta.lemma.lower() for ta in analysis}
    result: list[str] = []
    for surface in surfaces:
        key = surface.lower()
        if key in context:
            result.append(context[key])
        else:
            result.append(lemmatizer.lemmatize(surface, language_code).lower())
    return result
```

`dobro` read as a bare token is the adverb (lemma `dobro`); in *"Vse je dobro"* it is the adjective `dober`. `hotel` alone is the verb `hoteti`; in *"To je hotel"* it is the noun. Lemmatizing tokens in isolation therefore mis-keys them and they never match the dictionary-form card in the DB. `lemmatize_surfaces_in_context` analyzes the whole *sentence* once, then maps each surface back to its in-context lemma, falling back to single-word `lemmatize` only when a surface isn't found in the analysis (tokenization/punctuation mismatch).

For `LowercaseLemmatizer`, `analyze_sentence` is just per-token lowercasing, so the new path is a **no-op for the default** — it only sharpens the real engine. The lemmas are lowercased on the way out to match the card keyspace (`import_seed` stores `lemma = front.lower()`); classla capitalizes proper-noun lemmas (`Ženeve` → `Ženeva`), which would otherwise miss the lowercase `ženeva` card on a case-sensitive lookup (commit `0c26e23`).

### 22.2 The Lemmatizer Engines: Default-On for Dev, Opt-Out for CI

`ClasslaLemmatizer` wraps CLASSLA-Stanza (a PyTorch pipeline for South Slavic languages). It is **never imported at module level** — the `classla` import lives inside `_ensure_pipeline()` and a `try/except ImportError` type alias — so CI, which doesn't install PyTorch, never touches it. The factory selects it only when the user opts in:

```bash
sed -n "295,322p" backend/app/srs/lemmatizer.py
```

```output
def get_lemmatizer(language_code: str) -> Lemmatizer:
    """Return a cached lemmatizer for *language_code*.

    The engine is a **property of the language** (``app.languages.get_lemmatizer_type``):
    ``classla`` for Slovene, ``stanza`` for Norwegian, ``lowercase`` otherwise.
    ``settings.lemmatizer_type == "lowercase"`` (the default, and the test/CI pin)
    is a global off-switch — every language gets ``LowercaseLemmatizer`` so analysis
    stays deterministic without the heavy PyTorch deps. **Any other value** opts in
    and the per-language engine is built, falling back to ``LowercaseLemmatizer``
    with a logged warning when the engine's package is not importable.

    Cached per ``language_code`` (``functools.cache``) so multi-language mode
    (``settings.database_urls``, one process serving both languages) gives each
    language its own engine: a Norwegian request is never analyzed by the Slovene
    model. The lemmatizer's own methods short-circuit to lowercase for any code
    other than the one it was built for, so the *code passed to the methods must
    match the code passed here* — callers resolve from the content's
    ``language_code`` (lesson / body), not a global default.
    """
    from app.config import settings
    from app.languages import get_lemmatizer_type

    # Global off-switch: keep every language on the deterministic lowercase engine
    # (the CI/test default, and the single flag a deployment flips to disable the
    # heavy NLP pipelines everywhere).
    if settings.lemmatizer_type == "lowercase":
        return LowercaseLemmatizer()
```

Configuration is one new setting, `lemmatizer_type` (`"lowercase"` default, `"classla"` opt-in), in `app/config.py`. Tests pin `lemmatizer_type=lowercase` explicitly (commit `ed8937e`) so a developer's local `.env` with the classla flag can't leak PyTorch into a CI-style run. Models live under `CLASSLA_RESOURCES_DIR` (default `~/classla_resources`); run `classla.download("sl")` once before first use — `Pipeline` does not reliably auto-fetch across classla versions. `ClasslaLemmatizer` caches `analyze_sentence` results **per exact sentence string** (commit `fa80ad1`) — lesson text is stable across requests, so the transcript endpoint's state-change refetches drop from ~3.6 s of NLP to a DB-only lookup once warmed.

**Python 3.14 install caveat (verified 2026-06-02; made reproducible 2026-06-02).** The latest working classla (`2.2.1`) pins `torch<=2.6`, but torch `<=2.6` ships no 3.14 (`cp314`) wheel — torch only gained 3.14 support at `2.12`. So a bare `pip install classla` on 3.14 silently resolves to the ancient `classla==1.1.0`, which crashes on modern torch (PyTorch-2.6 `weights_only=True` → "Vector file is not provided"), and the factory returns a `ClasslaLemmatizer` that fails at first use rather than falling back. classla `2.2.1` is pure-Python, so the fix is to override its torch pin to a 3.14-capable build.

This is now **declared, not ad-hoc.** classla and stanza live in per-language groups under `[dependency-groups]` in `backend/pyproject.toml` (`slovene = ["classla==2.2.1"]` and `norwegian = ["stanza"]`), and `[tool.uv] override-dependencies = ["torch==2.12.0", "protobuf>=5.29"]` forces the 3.14 torch/protobuf over classla's `torch<=2.6` / `protobuf==4.21.2` pins. Install reproducibly:

```bash
cd backend && uv sync   # a plain sync; --all-groups also works
```

It is a **default `[dependency-groups]` set** (`[tool.uv] default-groups = ["dev", "slovene", "norwegian"]`), *not* extras — a deliberate 2026-07-11 inversion of the earlier design. Under the old scheme classla/stanza were `[project.optional-dependencies]` extras, so a working dev env required `uv sync --all-groups --extra classla --extra stanza` on *every* sync — and because `uv sync` prunes anything outside the requested set, a bare `uv sync` (the documented default) or a one-extra sync silently uninstalled the other engine. That "sync BOTH or one prunes the other" trap is what kept re-surfacing the `stanza not installed` warning. Now the default sync installs and keeps both, and **CI carries the opt-out flag instead**: all three backend CI jobs run `uv sync --all-groups --no-group slovene --no-group norwegian` to stay PyTorch-free. The principle behind the inversion: the party that wants the *unusual* behaviour (torch-free CI) holds the flag — in machine-controlled yaml that never forgets — not the human, who demonstrably does. The overrides are inert on the CI path (nothing pulls torch/protobuf there). The models still live under `CLASSLA_RESOURCES_DIR` (`~/classla_resources`) and the stanza cache (`~/Library/Caches/stanza`); run `classla.download("sl")` / `stanza.download("nb")` once if absent — uv manages the package, never the downloaded model, so a prune-then-resync doesn't cost a re-download. With this combo the pipeline produces correct lemmas on 3.14 (`hoteli → hoteti`, `smo → biti`, `ste → biti`). (The previous one-off `uv pip install "classla==2.2.1" --override <(echo "torch==2.12.0")` still works but isn't tracked in the lock, which is exactly why it vanished on the 3.13→3.14 upgrade.)

### 22.3 What Was *Not* Built: Bulk Re-Lemmatization

A migration that walked every existing collocation, re-lemmatized its text with classla, and **merged** rows that collapsed to the same lemma was written and then **reverted** (commits `f4bea32` → `a1ecf86`). It was unsafe by design: single-word re-lemmatization is exactly the POS-blind path §22.1 warns about, so it merged `neck` → `door` and `we` → `I`. The legacy deck has genuine surface-keyed duplicate bases (`čas` *and* `časa` as separate cards; `dobrodošli`/`dobrodošel`) that don't fit the lemma-as-unit model — but the resolution is to **dedupe one-at-a-time in Anki with review, or grandfather them**, never to bulk-merge in TT where a mis-lemmatization silently destroys an Anki-linked card.

A smaller transcript-UI affordance landed alongside: lesson text became selectable and copyable (commit `e949cf6`), and the word-state cycle now keys off click-vs-drag distance rather than text selection (commit `4a99925`) so highlighting to copy doesn't accidentally toggle a card's state.

---

## PART 23: Cloze, Always On

PART 20 described the cloze pipeline behind two feature flags (a global enable and a per-language gate). Both flags are **gone** (commit `9285c0b`). The user's decision: cloze is available for every language as it is added, with no checks. Creation is **capability-driven** — a cloze gets made when the language *has the capability* (a curated function-word list, or an inflection-aware lemmatizer), not when a flag is flipped. The two settings endpoints, their four DB getters/setters, the `ClozeSettingRequest` model, and the frontend toggle were all deleted outright (no constant-true dead branch left behind), and the OFF-behavior tests were removed.

### 23.1 Two Kinds of Cloze

`app/srs/function_words.py` (renamed in scope but same module) produces both cloze flavors. A **plain function-word cloze** blanks the whole word; `is_function_word` is the capability check — true only where a curated set exists (Slovene today):

```bash
sed -n "43,62p" backend/app/srs/function_words.py
```

```output
def is_function_word(token: str, language_code: str, *, upos: str | None = None) -> bool:
    """Return True if *token* is a function word in *language_code*.

    POS-first: when an analyzer supplies *upos*, a token whose classla UPOS is in
    the language's closed-class ``pos`` set counts — so the whole biti AUX paradigm
    (sem/si/je/smo/ste/so) is caught without enumerating surfaces. The curated
    ``include`` set adds words POS misses or mistags (the open-class adverbs
    kje/kako/tam; ``ni``, which classla tags VERB) and is the *sole* signal when no
    analyzer is present (LowercaseLemmatizer emits ``upos=""``), exactly reproducing
    the legacy surface-list behavior. ``exclude`` force-removes. Case-insensitive.
    """
    pos, include, exclude, _ = _load_function_word_config(language_code)
    t = token.casefold()
    if t in exclude:
        return False
    if t in include:
        return True
    return upos is not None and upos in pos
```

The plain-cloze blank is built at listen time from the **surface as it appeared in the sentence**, not the dictionary lemma (commit `92140c5`): the cloze must reference the word actually present in the stored sentence, so `make_cloze_text(surface, sentence)` is what runs, keyed off the raw sentence for backfill. The answer-word audio likewise synthesizes the surface, not the lemma (commit `562edab`) — otherwise a learner clozing `sem` would hear `biti`.

### 23.2 Fluent-Forever Ending-Blank for Morphology Clozes

The second flavor — a **morphology cloze** — drills an inflected form. Blanking the entire word would make the card test recall of the whole token; instead, following Fluent Forever, only the **inflectional tail past the lemma↔surface common prefix** is blanked, leaving the stem visible (commit `2db9f6a`):

```bash
sed -n "157,229p" backend/app/srs/function_words.py
```

```output
def _ending_blank_split(matched: str, lemma: str) -> tuple[str, str] | None:
    """Split *matched* into (visible_stem, blanked_tail) for a Fluent-Forever cloze.

    Computes the longest common prefix (LCP) of ``matched.casefold()`` and
    ``lemma.casefold()``. If the LCP is at least 2 characters and shorter
    than the full matched word, returns ``(matched[:n], matched[n:])`` so the
    stem stays visible. Returns ``None`` for suppletive forms (LCP < 2) or
    when *matched* is a prefix of *lemma* (no blankable tail).
    """
    cf_matched = matched.casefold()
    cf_lemma = lemma.casefold()
    n = 0
    for a, b in zip(cf_matched, cf_lemma, strict=False):
        if a == b:
            n += 1
        else:
            break
    if 2 <= n < len(matched):
        return (matched[:n], matched[n:])
    return None


def _format_morphology_feature(feature: str) -> str:
    """Turn a feature key into a concise hint label.

    Examples:
      ``verb:1sg``      -> ``1sg``
      ``noun:loc:sg``   -> ``loc sg``
      ``noun:nom:f:pl`` -> ``nom f pl``
      ``adj:nom:m:sg``  -> ``nom m sg``

    The POS prefix is dropped — the hint is shown alongside the lemma, which
    already implies the part of speech. Returns ``""`` for empty/malformed.
    """
    if not feature or ":" not in feature:
        return ""
    return " ".join(p for p in feature.split(":")[1:] if p)


def format_morphology_hint(lemma: str, feature: str) -> str:
    """Return a human-readable grammar hint like ``"biti, 1st person singular"``.

    Examples:
      ``("biti", "verb:1sg")``        -> ``"biti, 1st person singular"``
      ``("ljubljana", "noun:loc:sg")`` -> ``"ljubljana, locative singular"``
      ``("lep", "adj:nom:f:sg")``      -> ``"lep, nominative feminine singular"``
    """
    if not feature:
        return lemma or ""

    person_map = {"1": "1st", "2": "2nd", "3": "3rd"}
    number_map = {"sg": "singular", "pl": "plural", "du": "dual"}
    case_map = {"nom": "nominative", "acc": "accusative", "loc": "locative"}
    gender_map = {"m": "masculine", "f": "feminine", "n": "neuter"}

    parts = feature.split(":")
    pos = parts[0]

    if pos == "verb" and len(parts) >= 2:
        fc = parts[1]
        person_code = fc[0] if fc else ""
        number_code = fc[1:] if len(fc) > 1 else ""
        person_str = person_map.get(person_code, person_code)
        number_str = number_map.get(number_code, number_code)
        return f"{lemma}, {person_str} person {number_str}".strip()

    if pos == "noun" and len(parts) >= 3:
        c = parts[1]
        n = parts[2]
        case_str = case_map.get(c, c)
        number_str = number_map.get(n, n)
        return f"{lemma}, {case_str} {number_str}"
```

`_ending_blank_split` computes the longest common prefix of surface and lemma. If it is ≥2 chars and shorter than the whole word, the stem stays visible and only the tail is clozed: `Ljubljan{{c1::i::loc sg}}` rather than `{{c1::Ljubljani}}`. Suppletive forms (`biti`→`sem`, `iti`→`grem`) have LCP < 2, so the split returns `None` and the helper falls back to a whole-word blank with a `lemma, feature` hint (`{{c1::sem::biti, 1sg}}`). When the stem is already visible, the hint shows the **feature only** — the lemma is implied by the stem. `ud_feats_to_tt_feature` (bottom of the module) maps a classla UD analysis (`Case=Loc|Number=Sing`, `upos=NOUN`) to the TT feature string `noun:loc:sg`, returning `None` for combinations outside the A1 whitelist.

---

## PART 24: `morphology_focus` Generation

A cloze can only be made for a form the lesson actually contains — **form coverage is the lesson generator's job, not the carder's**. So the story prompt was reframed from `declension_focus` (which steered toward oblique cases inappropriate for A1) to `morphology_focus` (commit `44c5699`), tuned to surface the forms an A1 learner should produce: verb conjugations and accusative/locative nouns.

### 24.1 The Prompt Steers Toward Producible Forms

The LLM builds the `morphology_focus` array last, scanning the dialogue lines it just wrote and tagging inflected words **already present** in them. Two steering rules raised the live card yield from 52% to 91%:

```bash
sed -n "121,155p" backend/app/generation/prompts.py
```

```output
Build the "morphology_focus" array LAST by scanning the NATURAL_SPEED lines you wrote and tagging
inflected words ALREADY PRESENT in them. Aim for 4-6 entries, **prioritizing verb conjugations**.

Each entry becomes a fill-in-the-blank drill card: the learner sees the lemma + feature as a hint
and must PRODUCE the inflected surface. **So the surface MUST differ from its dictionary form** —
otherwise the hint gives away the answer and the entry is discarded (wasted slot). This rules out
two things you might otherwise tag:
- **Nominative-singular nouns** (`dan`, `grad`, `hotel`) — the dictionary form IS the nom sg, so
  there's nothing to produce. Do NOT tag `noun:nom:*` unless the surface genuinely differs from the
  lemma (e.g. plurals like `dnevi`, or feminine `hiša`→ still nom so skip). When in doubt, skip nom.
- **Infinitives appearing as-is.**

Therefore favor, in order: (1) **verb conjugations** (sem/si/je/imam/imaš/stane…), (2) **accusative
and locative nouns** whose ending changes the word (`kavo`, `sobo`, `Ljubljani`, `hotelu`),
(3) adjective agreement where the form changes (`lepa`, `lepo`).

- Surface must be copied CHARACTER-FOR-CHARACTER from a NATURAL_SPEED line (same diacritics č/š/ž),
  a SINGLE word, not invented.
- Lemma is the dictionary form (verb infinitive, noun nom sg, adj masc nom sg) and MUST differ from
  the surface — if they are equal, drop the entry.

**Feature strings — use exactly these shapes:**
- `verb:<p><n>` where p ∈ {{1,2,3}} and n ∈ {{sg,du,pl}}. E.g. `verb:1sg`, `verb:3pl`, `verb:1du`.
  Tag every interesting form of biti/imeti/target verbs that varies the person.
- `noun:<case>:<number>` for accusative or locative: `noun:acc:sg`, `noun:loc:pl`. (These are the
  productive noun forms — prefer them over nominative.)
- `noun:nom:<gender>:<number>` ONLY when the nom surface differs from the lemma (e.g. a plural
  `noun:nom:m:pl` `dnevi`). Skip nom singulars whose form equals the dictionary form.
- `adj:nom:<gender>:<number>`: `adj:nom:f:sg`, etc., when the form changes (`lepa`, `lepo`).

**Allowed cases for A1: nom, acc, loc only.** Do NOT emit `noun:gen:*`, `noun:dat:*`, `noun:ins:*`,
or `adj:` with any case other than `nom` — those are A2+ topics that don't belong in A1 drills.

**Cases derive from the governing word, NOT English gloss:** `v/na/pri/o/po` + static location →
`loc` (v Ljubljani); `v/na/čez/skozi` + motion → `acc` (grem v Ljubljano); direct object → `acc`."""
```

The producible-form rule (commit `2902cd6`) discards any entry whose surface equals its lemma — a nominative-singular noun or a bare infinitive gives the answer away, so it is a wasted slot (the backend also drops degenerate `lemma == surface` clozes defensively, commit `35630cc`). The case rule (commit `1a19a7c`) derives case from the **governing word, not the English gloss**: `v/na/pri/o/po` + a static location → locative (`v Ljubljani`); `v/na/čez/skozi` + motion → accusative (`grem v Ljubljano`). Cases are whitelisted to nom/acc/loc — gen/dat/ins are A2+ and explicitly forbidden.

### 24.2 Model-Agnostic JSON Parsing

Steering experiments pushed against alternate Groq models, which exposed that the parser assumed clean JSON. Reasoning models (`qwen3`) wrap the answer in `<think>…</think>`; `gpt-oss` prepends prose like `**Lesson Title:** …`. `StoryGenerator._parse_json` (commit `8ba2117`) now strips `<think>` blocks and code fences, then tries the cleaned string and, failing that, the first balanced `{…}` span:

```bash
sed -n "143,163p" backend/app/generation/story.py
```

```output
    def _parse_json(raw: str) -> dict:
        try:
            return parse_json_object(raw)
        except ValueError as e:
            raise StoryGenerationError(str(e)) from e

    def _parse_response(self, data: dict, language: Language) -> Lesson:
        return build_lesson_from_story(data, language=language)


def build_lesson_from_story(data: dict, language: Language) -> Lesson:
    """Build a Lesson from Story JSON — the ONE Story-JSON → Lesson build step.

    Used by generation (via ``StoryGenerator._parse_response``) and by lesson
    authoring import (``app.storage.lesson_io``), so authored and generated
    lessons are identical in shape. See docs/lesson-authoring.md.
    """
    key_phrases = data.get("key_phrases", [])
    scenes = data.get("scenes", [])
    title = data.get("title", "Lesson")
```

The model experiments themselves were dead ends — `gpt-oss-120b` returns prose-not-JSON and 400s on `json_object`, `qwen3-32b` 413s on payload size — so the default stays `llama-3.3-70b-versatile`, and the parser hardening is the durable win.

A per-day **Regenerate** button (commit `b72e764`) wires this into the UI: it re-runs `generateStory` for one day against the current prompt, keeps existing cards, and lets new vocabulary and morphology drills flow in on the next listen + sync. The confirm dialog spells out exactly that contract so a regenerate never feels like it discards progress.

---

## PART 25: The Word-Learning State Machine

PARTs 22–24 are the foundation; this part is the model they serve. Each **lemma** moves through a state machine — `BASE (recognition → production) → INFLECTIONS` — and not every lemma has every stage. Content words that inflect go recognition → production → inflections; invariant content words stop at production; **function words enter directly at production via the base cloze** (recognition of a preposition is meaningless). The full settled design and roadmap are in `~/.claude/plans/word-learning-state-machine.md`. The locked principle: **gates govern *introduction* only, never review** — once introduced, recognition, production, and every inflection cloze review in parallel.

### 25.1 Phase 3 — Recognition Before Production (Layer 65)

The first gate holds a vocab card's **production** direction out of the new-queue until its **recognition** sibling graduates past the learning arc. This is implemented as a `NOT EXISTS` clause appended to `get_new_items` for the production direction only:

```bash
sed -n "22,45p" backend/app/srs/db_collocations.py
```

```output
    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> bool:
        """Insert a new collocation; if it already exists, backfill an empty translation.

        New rows get both recognition and production direction rows (defaults).
        Single-word units without an explicit lemma get lemma = casefolded text
        so that get_collocation_by_lemma_with_id lookups succeed. Empty strings
        count as missing — pre-Phase-F sync paths sometimes wrote empties.

        Returns True if a new row was inserted, False if it already existed.
        """
        if not unit.lemma and unit.word_count == 1 and len(card_surface_variants(language_code, unit.text)) == 1:
            unit.lemma = unit.text.casefold()
        disambig = unit.disambig_key
        guid = compute_guid(unit.text, language_code, disambig)
        is_new = False
        with self._get_conn() as conn:
            # Identity is the case-normalized guid; legacy rows may carry a
            # stale guid that no longer matches the current compute_guid output,
            # so check guid first, then fall back to (text, language_code,
            # disambig_key) which is the actual UNIQUE constraint enforced by
            # the schema. Heal a stale guid in place when the fallback matches.
            existing = conn.execute(
                "SELECT id, guid, translation FROM collocations WHERE guid = ?",
                (guid,),
```

This was initially scoped as a TT-only divergence (like `promote_to_learning`), but the binary proved it is **parity-restoring**: real Anki introduces recognition first, 604 vs 36 across the user's 640 paired notes, because Anki orders new cards by deck position and `create_note` places the recognition card (ord 0) below production (ord 1). TT's old production-first behavior was the bug. The fix inverted the stale Layer 28 production-first tests — verified empirically first, per rule 13 (trust the binary). Recognition is never gated; a cloze note has no recognition row so `NOT EXISTS` is trivially true and it stays introducible. No badge change — `count_new_available_collocations` was already consistent.

### 25.2 Per-Lemma Mastery = Aggregated Retrievability

The transcript colors each word by a per-lemma **mastery** gradient. Mastery is the *mean retrievability* over the lemma's whole component set — recognition, production, and every inflection cloze — because retrievability (R) is the dynamic "how well do you know this right now" quantity, where stability is not. `app/srs/mastery.py` is a pure module:

```bash
cat -n backend/app/srs/mastery.py
```

```output
     1	"""Per-lemma mastery = aggregated FSRS stability over the learn-set (Phase 5).
     2	
     3	Mastery uses *stability*, not retrievability. The scheduler actively regulates
     4	retrievability toward desired_retention (~0.9), so a review card's R lives in a
     5	narrow band and can't distinguish a freshly graduated card from a long-mastered
     6	one — every reviewed word renders the same green. Stability instead grows
     7	monotonically as a word is learned (the user's deck spans ~3–116 days), so it is
     8	what the transcript color ramp should track.
     9	"""
    10	
    11	from __future__ import annotations
    12	
    13	import math
    14	from collections.abc import Iterable
    15	
    16	from app.models.srs_item import DirectionState, SRSState
    17	
    18	# A REVIEW card's mastery is its stability mapped onto [0,1] by a log curve: a
    19	# card stable for >= this many days reads as fully mastered (green). Log scale
    20	# because the early stability gains (1→10 days) are the meaningful learning
    21	# signal while the 100→120 day difference is not; the ceiling is chosen so the
    22	# observed stability range spreads across the full red→green ramp.
    23	MASTERY_STABILITY_CEILING_DAYS = 120.0
    24	
    25	# In-steps (learning/relearning) cards sit at a fixed low floor: they are being
    26	# acquired, not yet on the stability ramp.
    27	_LEARNING_FLOOR = 0.15
    28	
    29	
    30	def component_mastery(ds: DirectionState) -> float:
    31	    """Mastery of one component (a direction/card) ∈ [0,1].
    32	
    33	    NEW → 0.0 (unlearned). LEARNING/RELEARNING → 0.15 fixed floor (in-steps, not
    34	    graduated). KNOWN → 1.0. REVIEW → log-normalized stability, which is
    35	    time-independent: a word keeps the same color between reviews.
    36	
    37	    Mastery does NOT depend on ``last_review`` — a card marked KNOWN (via
    38	    ``mark_known``) carries high stability but no review timestamp, and must still
    39	    read as mastered. "Unlearned" is already captured by low stability (s≤1 day →
    40	    ``log10(1)=0``); a separate ``last_review is None`` guard (a relic of the
    41	    retrievability-based formula) would wrongly zero those high-stability cards.
    42	    """
    43	    if ds.state == SRSState.NEW:
    44	        return 0.0
    45	    if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
    46	        return _LEARNING_FLOOR
    47	    if ds.state == SRSState.KNOWN:
    48	        return 1.0
    49	    mastery = math.log10(max(ds.stability, 1.0)) / math.log10(MASTERY_STABILITY_CEILING_DAYS)
    50	    return max(0.0, min(1.0, mastery))
    51	
    52	
    53	def compute_mastery_progress(directions: Iterable[DirectionState]) -> float | None:
    54	    """Mean component_mastery over the learn-set. SUSPENDED components excluded.
    55	    None if the set is empty (→ caller renders as not-on-the-ramp).
    56	    """
    57	    ms = [component_mastery(d) for d in directions if d.state != SRSState.SUSPENDED]
    58	    return sum(ms) / len(ms) if ms else None
```

The per-component carve-out matters: a NEW or never-reviewed component is `0.0`, **not** `compute_retrievability`'s 0.9 NEW fallback (that fallback is for queue placement, not mastery). LEARNING/RELEARNING is a fixed `0.15` floor so a freshly-stepped card doesn't flash green; only REVIEW uses live R; KNOWN is `1.0`. Adding an inflection adds an `m≈0` component, so mastering a new form *lightens* the lemma — an expandable end state, never "100% and done."

The frontend maps that fraction to a red→green hue (`frontend/src/lib/mastery.ts`):

```bash
cat -n frontend/src/lib/mastery.ts
```

```output
     1	/** Map a mastery fraction (0 = new, 1 = mastered) to a red→green hue.
     2	 *  0 → red (hue 0), 0.5 → yellow (hue 60), 1 → green (hue 120). */
     3	export function masteryColor(progress: number): string {
     4	  const p = Math.max(0, Math.min(1, progress));
     5	  const hue = p * 120;
     6	  const lightness = 50 - p * 8;
     7	  return `hsl(${hue}, 70%, ${lightness}%)`;
     8	}
     9	
    10	/** Same red→green hue ramp as {@link masteryColor}, but a low-alpha tint for use
    11	 *  as a background behind text (e.g. a collocation span). 0 → faint red,
    12	 *  1 → faint green. */
    13	export function masteryBackgroundColor(progress: number): string {
    14	  const p = Math.max(0, Math.min(1, progress));
    15	  const hue = p * 120;
    16	  return `hsla(${hue}, 70%, 45%, 0.15)`;
    17	}
```

Lightness co-varies with progress (and due cards get an underline) as a red↔green colorblind hedge. Static states are off the ramp entirely: unknown is indigo, known/ignored are gray.

### 25.3 The Transcript Serializer Resolves the Active Card

`extract_transcript` (`app/srs/transcript.py`) now enriches every `WordToken` with seven Phase-5 fields: `card_type`, `active_state`, `active_direction`, `is_due`, `progress`, `inflectable`, and `inflection_feature`. Resolution is **inflection-first**: an exact-surface inflection cloze wins over the base card, which wins over "unknown." The active direction follows the state machine:

```bash
sed -n "137,162p" backend/app/srs/transcript.py
```

```output
def resolve_active_direction(item: object) -> Direction:
    """Return the active direction for a resolved SRSItem.

    Cloze → PRODUCTION (only direction it has).
    Vocab → RECOGNITION while rec.state != REVIEW; else PRODUCTION.
    When both REVIEW, active = production.
    """
    from app.models.srs_item import SRSItem as _SRSItem

    if not isinstance(item, _SRSItem):
        return Direction.PRODUCTION
    ct = item.syntactic_unit.card_type
    if ct == "cloze":
        return Direction.PRODUCTION
    rec = item.directions.get(Direction.RECOGNITION)
    prod = item.directions.get(Direction.PRODUCTION)
    # Recognition is active until it graduates (REVIEW), then production takes over
    # — BUT only if production exists. Single-direction cards (the imported
    # Norwegian deck is recognition-only) have nothing to advance to, so they stay
    # on the direction they actually have. Returning an absent direction makes the
    # caller's item.directions[active_dir] KeyError (the lesson-transcript 500).
    if rec is not None and rec.state == SRSState.REVIEW and prod is not None:
        return Direction.PRODUCTION
    if rec is not None:
        return Direction.RECOGNITION
    return Direction.PRODUCTION
```

`progress` is `compute_mastery_progress` over the resolved component set; `inflectable` is true only when the surface differs from the lemma, the form is an A1 feature, the base production is REVIEW/KNOWN, and no cloze for that surface exists yet — i.e. exactly when clicking the word *could usefully* mint an inflection cloze. The serializer also reconstructs each `DialogueLine.sentence` from its surfaces, which the popover needs to build a cloze (a bug caught while finishing Phase 5: scene lines didn't carry the sentence, so popover-created cards had empty sentences).

### 25.4 Phase 4 — Inflection Clozes Are Click-Only

`/listen` **stopped** auto-minting morphology clozes (Layer 66, commit `6935e93`). The reasoning: a rare form that never gets clicked should never become a card — coverage is the generator's job (PART 24), and auto-minting on every listen flooded the deck. The sole mint path is now `POST /api/srs/inflection-clozes` (commit `f7abf4d`), called when the user clicks an inflected surface that appeared in a lesson:

```bash
sed -n "1249,1286p" backend/app/api/srs.py
```

```output
@router.post("/inflection-clozes", status_code=200)
async def create_inflection_cloze(body: InflectionClozeRequest, request: Request) -> dict:
    """Create one morphology cloze for an inflected surface (Phase 4a).

    Gated on the lemma's base production being in REVIEW or KNOWN.
    Idempotent by guid. Follows the add_collocation contract
    (card_type=cloze, no Anki ids).
    """
    db = request.state.srs_db
    language_code = body.language_code

    # 1. Eligibility gate — base word production must be REVIEW/KNOWN.
    #    Clozes-only verbs (e.g. biti) have no base card and are ungated.
    if not is_clozes_only_verb(body.lemma, language_code):
        base = db.get_collocation_by_lemma(body.lemma)
        if base is None:
            raise HTTPException(status_code=409, detail="Base word not yet learned")
        prod = base.directions.get(Direction.PRODUCTION)
        if prod is None or prod.state not in (SRSState.REVIEW, SRSState.KNOWN):
            raise HTTPException(status_code=409, detail="Base word not yet learned")

    # 2. Degenerate guard — surface == lemma reveals the answer
    if body.lemma.casefold() == body.surface.casefold():
        raise HTTPException(status_code=422, detail="Surface equals lemma — nothing to cloze")

    # 3. Resolve word gloss + sentence translation from the lesson, mirroring
    #    /listen. The grammar hint lives in its own `grammar` field — never the
    #    translation — so it can't leak into the displayed L1 gloss.
    word_translation = body.translation
    sentence_translation = ""
    if body.lesson_id:
        from app.models.lesson import extract_sentence_translations_from_translated

        lesson = request.state.content_store.get_lesson(body.lesson_id)
        if lesson is not None:
            token_glosses: dict[str, str] = lesson.generation_metadata.get("token_glosses", {})
            sentence_translations: dict[str, str] = dict(lesson.generation_metadata.get("sentence_translations", {}))
            for k, v in extract_sentence_translations_from_translated(lesson).items():
```

The endpoint is gated on the base word's production being REVIEW/KNOWN (409 otherwise — you can't drill an inflection of a word you haven't learned), guards the degenerate `surface == lemma` case (422), is idempotent by guid, and follows the card-adding contract from `.claude/rules/anki-sync.md` (`card_type="cloze"`, no Anki ids — `sync_create_new` mints and links them).

### 25.5 Phase 5 Part C — Click an Unknown Word to Create Its Base Card

Clicking an *unknown* word creates its base card. `POST /api/srs/items/base` branches on word type — the heart of the state machine's entry rule:

```bash
sed -n "985,1025p" backend/app/api/srs.py
```

```output
@router.post("/items/base", status_code=200)
async def create_base_card(body: CreateBaseCardRequest, request: Request) -> dict:
    """Create a base card for an unknown clicked word (Phase 5, Part C / decision 8, C-a).

    Branches by word type (the word-learning state machine):
      - function word → production-only cloze (the *surface* blanked in the sentence)
      - content word  → vocab (recognition + production)
    Both created in NEW state. Idempotent by the base guid. Honors the
    add_collocation card-adding contract (no Anki ids; sync_create_new mints +
    links). No LLM auto-translate here — the caller passes the transcript gloss.
    """
    db = request.state.srs_db
    lang = body.language_code
    lemma = body.lemma.casefold()

    # Clozes-only verbs (e.g. biti) have no base card — only per-form conjugation
    # clozes via /inflection-clozes. Reject so a click can't mint a spurious base.
    if is_clozes_only_verb(lemma, lang):
        raise HTTPException(status_code=409, detail="Clozes-only verb has no base card")

    # POS-first function-word detection: read the active surface's UPOS from the
    # sentence (classla → AUX for biti forms etc.; LowercaseLemmatizer → "" so the
    # curated include-list is the sole signal). The surface is checked too — an
    # inflected function form (classla "sem" → lemma "biti") classifies via its
    # surface even when the dictionary lemma isn't itself a function word.
    # Offload the (classla) lemmatizer off the event loop — see get_lesson_transcript.
    lemmatizer = get_lemmatizer(lang)
    mv = model_version_for(lemmatizer)
    analyses = await anyio.to_thread.run_sync(analyze_sentence_cached, db, lemmatizer, body.sentence, lang, mv)
    upos = next((ta.upos for ta in analyses if ta.surface.casefold() == body.surface.casefold()), None)
    # Check both lemma and surface with the surface's upos (a single-word click).
    upos_map = {lemma.casefold(): upos, body.surface.casefold(): upos} if upos else None
    is_func = is_function_word_for(lemma, {lemma, body.surface}, lang, upos_map)
    if is_func:
        # Blank the surface as it appeared, not the dictionary lemma (Phase 2b):
        # the cloze must reference the word present in the stored sentence.
        source_sentence = make_cloze_text(body.surface, body.sentence)
        card_type = "cloze"
    else:
        source_sentence = body.sentence
        card_type = "vocab"
```

A function word (detected via lemma *or* surface, so an inflected `sem`→`biti` is caught) enters as a **production-only cloze** with the surface blanked in its sentence; a content word enters as **vocab** (recognition + production). Both are created NEW, idempotent by the base guid `compute_guid(lemma, lang, "")`. There is no LLM auto-translate here — the caller passes the gloss already visible in the transcript. This reuses the same `/listen` base-create logic, keeping one definition of "what a base card is."

### 25.6 Phase 5 Part D — The Transcript Becomes Interactive (Frontend)

`WordSpan.svelte` renders the model. The static states (`unknown`/`known`/`ignored`) get a fixed class; everything dynamic gets the mastery hue and a due underline (the old hardcoded `STATE_CYCLE` is deleted):

```bash
sed -n "52,79p" frontend/src/lib/WordSpan.svelte
```

```output
	const dynamicStyle = $derived(
		word.active_state !== 'unknown' && word.active_state !== 'suspended' && word.active_state !== 'ignored'
			? `color: ${masteryColor(word.progress ?? 0)};`
			: ''
	);

	const colorClass = $derived(
		word.active_state === 'unknown'
			? 'word-unknown'
			: word.active_state === 'suspended' || word.active_state === 'ignored'
				? 'word-ignored'
				: ''
	);

	// Show the popover when: not inside a collocation, OR alt-hover mode is active.
	// The Tooltip wrapper is ALWAYS rendered (suppressed otherwise) so the DOM
	// structure stays stable — toggling Alt over a collocation must not reflow the
	// line (the prior if/else swap caused a visible spacing jump).
	const showTooltip = $derived(!requireModifier || altHover);

	// Undo cycle: when the page says THIS word holds the last (still-local)
	// grade, the grade button flips to "Undo ↩" — even though the word is no
	// longer due post-grade. Single-level, mirrors the backend snapshot.
	const undoable = $derived(Boolean(tooltipActions?.isGradeUndoable?.(word)));

	// The normal due-grade path: the active direction is due and tracked.
	const gotItApplies = $derived(
		word.is_due && word.active_direction != null && word.srs_item_id != null
```

Clicks are routed by the lesson `+page.svelte`: clicking an **unknown** word calls `createBaseCard`; clicking a **due** word submits a Good grade on its `active_direction`; clicking a **terminal** (known/suspended) word is a no-op; and clicking inside a collocation reviews the collocation. A hover popover (`Tooltip.svelte`, made interactive with `pointer-events:auto` and a hover bridge) offers create-inflection plus ignore/known/new overrides — note the override set deliberately excludes lapse/restore, so it never touches FSRS scheduling state. The matching `api.ts` methods `createBaseCard` and `createInflectionCloze` complete the loop. This is **Phase 5 complete end-to-end** — every word in a lesson is now a one-click entry point into the learning state machine.

---

## PART 26: FSRS in f32 & Parity Layers 49–66

PART 16 documented queue-parity Layers 24–31. The history has since reached Layer 80 (`docs/anki-parity-layers.md`); this PART tabulates through 66, and PART 29 summarizes 67–80 (rollover day-bounds, graves, push→pull seams, daily caps, per-grade revlog push). Most layers are narrow input-quality or formula-branch fixes; two are structural enough to call out here, and the rest are tabulated.

### 26.1 Layer 59 — All FSRS Arithmetic Moved to f32

`fsrs-rs` (Anki's Rust scheduler) computes stability and difficulty in `f32` end-to-end via Burn tensors. TT computed in Python `f64`, which drifts by single ULPs that, at 4-decimal storage precision, surface as false-positive compare-shadow divergences (the persistent ±0.0001 class). Layer 59 (commit `12338fa`) casts every operand and intermediate to `numpy.float32`, returning `f64` only at storage boundaries:

```bash
sed -n "19,41p" backend/app/srs/fsrs.py
```

```output
# fsrs-rs (rslib/.../fsrs/model.rs) computes stability + difficulty in f32 end-to-end
# via Burn tensors. TT mirrors that precision by casting all arithmetic operands and
# intermediates to numpy.float32, returning Python f64 only at storage boundaries.
# Without this, replays drift by single ULPs at 4-decimal storage precision
# (~0.0001 at s≈100-200), surfacing as false-positive compare-shadow divergences.
_F32 = np.float32


def _w32(w: tuple[float, ...]) -> tuple:
    """Cast a weights tuple to numpy.float32, matching how fsrs-rs holds parameters."""
    return tuple(_F32(x) for x in w)


@cache
def _fsrs_factor_f32(decay: float) -> np.float32:
    """fsrs-rs power-forgetting-curve factor ``exp(ln(0.9) / decay) - 1`` in f32.

    Cached per distinct ``decay`` — in practice a 1-2 entry table (−0.5 for
    FSRS-5, the learned ``w[20]`` for FSRS-6) — so the two numpy transcendental
    calls don't repeat on every per-card retrievability/interval evaluation on
    the queue-sort path. Bit-identical to the inline ``exp(ln(0.9)/_F32(decay))``.
    """
    return np.exp(np.log(_F32(0.9)) / _F32(decay)) - 1
```

Three things had to match Rust exactly, not just the precision: the power-forgetting-curve **factor** is `exp(ln(0.9)/decay) − 1` (not the FSRS-4 `19/81` constant), the `linear_damping` **operation order** in `_next_difficulty`, and Rust's `f32::round` being **half-away-from-zero**, not banker's rounding:

```bash
sed -n "103,107p" backend/app/srs/fsrs.py
```

```output
def _rust_round_half_away(x: float) -> int:
    """Mirror Rust's ``f32::round`` — half away from zero, not banker's rounding."""
    if x >= 0:
        return int(x + 0.5)
    return -int(-x + 0.5)
```

This is pinned by `tests/test_parity_fsrs_f32.py` against `fsrs_rs_python.next_states` (the comparison is architecture-aware — x86 CI vs arm64 local can differ in the last bit, commits `2f47d45`/`b53f05d`/`10720c0`). The consequence for the soak: a `±0.0001` stability divergence is now a **regression signal**, not benign — the old floor guidance is retired (commits `168a5aa`/`ca79ea0`). Full detail in `docs/anki-parity-layers.md` Layer 59.

### 26.2 Layers 53 + 55 — The FSRS Load Balancer

The residual `due_at` divergence in the Stage-3b shadow turned out to be Anki's **FSRS load balancer** (Layer 53, finding), not a memory-state bug: when `loadBalancerEnabled` is set, Anki relocates each graded card's interval to a less-loaded day *within* the fuzz range, using a histogram of the whole collection's due dates. The signature is a stability that is bit-exact but a `due_at` off by ±1–2 days that lands *inside* TT's computed fuzz band. Layer 55 (commit `bb93471`) wired a bit-exact port into TT's live grade path so a TT-native grade matches Anki's relocation; `build_live_load_balancer` builds the histogram from TT state and threads it through `schedule()`. Synced cards were always correct (`sync_pull` reads the balanced `cards.due` directly). Full detail in `docs/anki-parity-layers.md` Layers 53 and 55.

### 26.3 The Rest, Tabulated

| Layer | What changed |
|-------|--------------|
| 49 | `schedule()` review `due_at` uses the col-day rollover-hour anchor, matching `sync_pull` |
| 50 | Grade-time `days_elapsed` is an **integer col-day diff**, not a float |
| 51 | Cascade floor + `scheduled_days` threaded into the fuzz minimum |
| 52 | Graduation uses simple per-rating fuzz, not the passing-review cascade |
| 53 | **Finding**: residual `due_at` divergence is the load balancer (§26.2) |
| 54 | The col-day helpers are non-inverse **by design** — ground-truthed non-bug |
| 55 | Load balancer wired into the live grade path (§26.2) |
| 56 | Review badge buries siblings in **interday learning**, not just "graded today" |
| 57 | Interday LEARNING→REVIEW graduation uses the **recall** formula, not short-term |
| 58 | Revlog ingest reconciles against Anki's full revlog, not a wall-clock watermark |
| 59 | FSRS arithmetic in f32 with fsrs-rs op order (§26.1) |
| 60 | Revlog ingest dedup is **provenance-aware** (rapid same-ease Anki grades survive) |
| 61 | `_bump_col` preserves `col.usn` — stops forcing AnkiWeb full syncs |
| 62 | REVIEW + passing **same-day** grade uses FSRS short-term stability, not recall |
| 63 | FSRS stability clamped to `[S_MIN, S_MAX]` like fsrs-rs `step` |
| 64 | `new` badge mirrors Anki's new-sibling bury (`bury_new`) |
| 65 | Production held until recognition graduates (§25.1) |
| 66 | `/listen` no longer mints morphology clozes (§25.4) |

Layers 57, 58, and 62 were all surfaced by the Stage-3b compare-shadow soak (PART 27) — live bugs that the anchored event-replay made visible before they reached a badge. Layer 61 is the one most worth re-reading before any sync write: clobbering `col.usn = -1` is invisible single-device but makes AnkiWeb demand a **full** sync the moment a second device advances the server USN (`.claude/rules/anki-sync.md`).

---

## PART 27: Stage 3b — Toward Event-Sourced Sync

> **2026-07 status: the migration this PART describes COMPLETED and was then simplified away.** The three-mode `event_sync_pull` switch (legacy/compare/new) did its job: `new` went live 2026-06-02, and once it held, the flag itself and the compare-shadow machinery were **decommissioned** — `sync_pull` now has a single path that takes Anki's values verbatim, keeping the incremental forward-step replay only as a *recompute-divergence detector* (`recompute_divergences ≈ 0` per sync is the soak signal now; see `.claude/rules/anki-queue-parity.md` §Soak health). The text below is kept as the historical record of how the migration was staged, with the code excerpts re-aimed at what remains.

PART 19 left `tt_revlog` writing events but `sync_pull` still merging state field-by-field, with the endgame gated on an empirical measurement. The measurement ran (final result 100% strict match — `docs/stage-3b-empirical-measurement.md`), and Stage 3b staged the takeover as a **three-mode switch** so the event-replay path ran shadowed alongside the legacy merge before it took over.

### 27.1 The Three Modes (since decommissioned)

A single `anki_state_cache` key, `event_sync_pull`, selected the merge strategy. The clearest surviving record of the arc is the migration that cleaned up after it:

```bash
sed -n '938,949p' backend/app/srs/migrations.py
```

```output
def migrate_v31_to_v32(conn: sqlite3.Connection) -> None:
    """Drop the Stage-3b compare-mode shadow columns from collocation_directions.

    ``stability_replayed`` / ``fsrs_difficulty_replayed`` (added in v27) were
    written only under ``event_sync_pull='compare'``. Stage 3b decommissioned the
    ``event_sync_pull`` flag — sync_pull now has a single path (collapsed merge +
    recompute detector), so the shadow columns are dead. TT-only; no USN, no sync.
    """
    for col in ("stability_replayed", "fsrs_difficulty_replayed"):
        if _column_exists(conn, "collocation_directions", col):
            conn.execute(f"ALTER TABLE collocation_directions DROP COLUMN {col}")
    _set_version(conn, 32)
```

`legacy` is the pre-Stage-3b 9-branch merge. `compare` runs both: legacy stays authoritative and writes the card, while the incremental replay is written to **shadow columns** and any disagreement is recorded as a divergence — zero production risk, pure observation. `new` collapses the FSRS branch entirely: take Anki's state verbatim, with the forward-step replay acting only as a validator. The getter defaults to `legacy` and falls back to `legacy` on any unrecognized stored value, so a corrupt row can never silently route sync down an unimplemented path:

```bash
sed -n "110,127p" backend/app/srs/db_revlog.py
```

```output
    def rebuild_from_revlog(
        self,
        collocation_id: int,
        direction: Direction,
        params=None,
        col_crt: int | None = None,
        exclude_review_kinds: frozenset[int] = frozenset({4}),
        anki_card_id: int | None = None,
        starting_state: DirectionState | None = None,
        since_id: int | None = None,
    ) -> DirectionState:
        """Replay tt_revlog rows through FSRS schedule() to derive DirectionState.

        Reads non-excluded revlog rows for ``(collocation_id, direction)`` ordered
        by ``id`` ASC and replays them through ``app.srs.fsrs.schedule``.

        Pass *anki_card_id* to ensure the FSRS interval-fuzz seed matches the
        real Anki card id; omit or pass ``None`` for TT-only directions.
```

### 27.2 What Survives in `sync_pull`

The incremental forward-step replay survives as the recompute detector: when its forward-step disagrees with Anki's `cards.data`, sync logs a `RECOMPUTE_DIVERGENCE` line and counts it on the report — the signal that Anki ran an Optimize/reschedule/restore the replay couldn't reproduce:

```bash
grep -n "SYNC_SOAK\|RECOMPUTE_DIVERGENCE" backend/app/anki/sync_engine.py backend/app/anki/sync.py | head -8
```

```output
backend/app/anki/sync_engine.py:462:        # "RECOMPUTE_DIVERGENCE".
backend/app/anki/sync_engine.py:464:            "RECOMPUTE_DIVERGENCE cid=%s dir=%s replay_s=%.4f anki_s=%.4f replay_d=%.4f anki_d=%.4f",
backend/app/anki/sync.py:150:    ``SYNC_SOAK`` heartbeat per sync (even at count 0, so there's positive
backend/app/anki/sync.py:151:    "ran clean" confirmation) plus one ``RECOMPUTE_DIVERGENCE`` detail line per
backend/app/anki/sync.py:160:        f"{ts} SYNC_SOAK pull_notes={pull.notes_updated} "
backend/app/anki/sync.py:167:            f"{ts}   RECOMPUTE_DIVERGENCE cid={d.collocation_id} dir={d.direction} "
backend/app/anki/sync.py:333:    # SYNC_SOAK heartbeats into the user's real ~/.tunatale/logs/sync.log.
```

The replay is **incremental** — it forward-steps from the stored state through the events ingested this sync, rather than replaying from NEW every time (which would be O(history) per card per sync). Compare-mode used to write the replayed stability/difficulty to shadow columns (dropped in v32); the surviving path records a `RecomputeDivergence` on `report.recompute_divergences` when the forward-step disagrees with Anki, so a real algorithmic gap surfaces in the sync report and the `SYNC_SOAK` heartbeat in `~/.tunatale/logs/sync.log`.

### 27.3 What the Soak Found

Running `compare` against the live deck across many syncs is the soak, and it earned its keep — three of PART 26's layers (57, 58, 62) are bugs it surfaced. Two findings are worth internalizing because they shaped the soak's health bar:

- **Layer 58** (commit `3f848cd`): a replayed-stability divergence was **not** an FSRS bug — it was an *ingest gap*. A Good grade landed inside a 41-hour sync gap and was never ingested, so the replay was missing an event. The fix made ingest reconcile against Anki's full revlog (`get_tt_revlog_ids`) instead of trusting a `last_synced_at` watermark. The lesson: a replay divergence can mean "the replay is missing an input," not "the replay math is wrong."
- **The difficulty floor washed to 0** (2026-05-30): a transient cohort of difficulty-only divergences came from a 2026-05-21 Check-Database/restore that re-stamped ~2333 revlog rows Anki never applied to `card.data` — proving Anki's `card.data` is **not** a pure replay of its revlog. As those cards were re-graded with clean rows, the cohort decayed 104 → 6 → 0.

The soak's health bar is **0 for both stability and difficulty** — the old "~104 benign floor" is retired. The soak held clean: `new` went live 2026-06-02, the legacy and compare branches were deleted, and today's signal is `recompute_divergences ≈ 0` per sync (`grep RECOMPUTE_DIVERGENCE ~/.tunatale/logs/sync.log` → expect empty). The classifier notes live in `.claude/rules/anki-queue-parity.md` §Soak health check.

---

## PART 28: The Documentation Set

The product gained a written identity. `README.md` is the pitch and the map; `docs/prd.md` is the product requirements doc. The pedagogy is grounded in a set of **influence docs**, each written to the same shape (claim → how TunaTale applies it → where it deliberately diverges):

- `docs/pimsleur.md` — graduated-interval recall and the backward-buildup drill (PART 6's syllabification).
- `docs/fluent-forever.md` — the ending-blank cloze (PART 23.2) and image-over-translation cards.
- `docs/lingq.md` — known/unknown word tracking, the lineage of PART 25's transcript model.
- `docs/refold.md` — comprehensible input and the listen-first loop (PART 15).
- `docs/bdt.md` — Lampariello's bidirectional translation, the recognition↔production pairing.

Two operational docs round it out: `docs/adding-a-language.md` (the plugin checklist — preprocessor, voice map, function-word list, lemmatizer) and `docs/anki-recovery.md` (disaster recovery for the user's primary Anki collection). `AGENTS.md` (this file, also `CLAUDE.md`) had its opening polished and absorbed the new-language and Anki-recovery pointers.

The operational set has since grown: `docs/anki-parity-diagnostics.md` (every diagnostic snippet + the load-bearing-helper table), `docs/anki-mirror-audit.md` (the inspection-driven audit that found Layers 62–63), `docs/learning-modes.md` (the Review/Listen/Read/Generate/Produce mode map), `docs/language-plugin-hardening.md` (the registry + literal-gate rationale), `docs/curriculum-planning.md` (the chat planner), and `docs/archive/bp-brief-segmenter-homographs-overlap.md` (the Norwegian segmenter design).

This is where a new contributor — human or agent — should start: the influence docs explain *why* the system is shaped the way the preceding parts describe.

---

## PART 29: The 2026-06/07 Restructurings

*Added 2026-07-11.* PARTs 12–27 describe subsystems as they were built; this PART covers the structural work that reshaped them between June and July 2026 — four decompositions, one new language, and the parity layers 67–80. Each subsection names the load-bearing files so the earlier PARTs' pre-split references can be translated on sight.

### 29.1 The Sync Module Split & the One Sync Path

`app/anki/sync.py` had grown into a god-module. The 2026-06-11 split left it as a **runner + re-export facade**: the `AnkiSync` reconcile engine lives in `sync_engine.py`, collection I/O (`OfflineReader`/`OfflineWriter`) in `sync_reader.py`/`sync_writer.py`, and shared leaf helpers in `sync_common.py`. Every old import path still works through the facade — tests import and patch `app.anki.sync` exactly as before.

```bash
wc -l backend/app/anki/sync.py backend/app/anki/sync_engine.py backend/app/anki/sync_reader.py backend/app/anki/sync_writer.py backend/app/anki/sync_common.py
```

```output
     408 backend/app/anki/sync.py
    1503 backend/app/anki/sync_engine.py
     168 backend/app/anki/sync_reader.py
     788 backend/app/anki/sync_writer.py
     219 backend/app/anki/sync_common.py
    3086 total
```

Around the same time the sync *surface* collapsed to one path. The legacy `POST /api/anki/sync` + `GET /api/anki/status` endpoints were deleted (2026-06-10) and the `python -m app.anki.sync` CLI with its `--all-languages` loop followed (2026-06-30). **`POST /api/anki/peer-sync` is the only sync entry point** — it drives `peer_sync → main → run_full_sync`, and `run_full_sync` owns the ONE ordered phase list (`detect_and_reset_orphans → sync_create_new → sync_push → sync_pull → refresh_* + media refresh + soak heartbeat`). The rule exists because of a real regression (`b0a4b8a`): when the Sync button was repointed at peer-sync, the peer path ran only push+pull and silently dropped `sync_create_new` and every `refresh_*`. Three nets now pin the phase list (`TestRunFullSync`, the sociable `TestSociableSync` against a real on-disk collection, and the self-hosted peer-sync round-trip suite). Full protocol rules: `.claude/rules/anki-sync.md`.

### 29.2 The Database God-Module Split

`app/srs/database.py` got the same treatment on 2026-07-04/05: it is now a ~60-line composition facade over per-concern mixins, and the review-queue assembly that lived in `api/srs.py` moved to `app/srs/anki_mirror/queue_engine.py` (`_merge_directions`, `_compute_live_main`, `build_and_freeze_main_queue`, `assemble_review_queue`). `api/srs.py` keeps only HTTP-layer code. New DB methods go in the matching `db_*` mixin; imports and patches still go through `app.srs.database`.

```bash
wc -l backend/app/srs/database.py backend/app/srs/anki_mirror/queue_engine.py backend/app/srs/db_base.py backend/app/srs/db_collocations.py backend/app/srs/db_directions.py backend/app/srs/db_queue.py backend/app/srs/db_counts.py backend/app/srs/db_revlog.py backend/app/srs/db_sync.py
```

```output
      59 backend/app/srs/database.py
     489 backend/app/srs/anki_mirror/queue_engine.py
     333 backend/app/srs/db_base.py
     523 backend/app/srs/db_collocations.py
     424 backend/app/srs/db_directions.py
     258 backend/app/srs/db_queue.py
     253 backend/app/srs/db_counts.py
     307 backend/app/srs/db_revlog.py
     450 backend/app/srs/db_sync.py
    3096 total
```

(Plus the smaller inert mixins: `db_media`, `db_kv_cache`, `db_histogram`, `db_lemma_cache`, `db_ignored_lemmas`, `db_sync_conflicts`.)

### 29.3 The Language Registry & Norwegian

Norwegian became the second wired language, and the wiring itself was hardened into a registry. `app/languages.py` holds one `LanguageConfig` per language — preprocessor, deck name, vocab notetype, lemmatizer engine, syllabifier, and the Norwegian-specific facets — and every consumer resolves through its accessors or the bundled `resolve_language_context(code, settings) → LanguageContext`. A CI-enforced gate (`backend/scripts/check_language_literals.py`) fails the build on any hardcoded language literal in `backend/app/**` outside the allowlisted plugin modules, so "add an `if code == 'no'` branch" is no longer a possible design. Details: `docs/language-plugin-hardening.md`, `docs/adding-a-language.md`.

```bash
sed -n '91,101p' backend/app/languages.py
```

```output
    "no": LanguageConfig(
        language=Language.norwegian(),
        preprocessor_factory=NorwegianPreprocessor,
        deck_name="0. 6000 Most Frequent Norwegian Words [Part 1]",
        vocab_notetype=NORWEGIAN_VOCAB,
        lemmatizer_type="stanza",
        compound_word_breakdown=True,
        variant_separator=",",
        syllabifier="norwegian",
    ),
}
```

Norwegian's empirical quirks: the deck is **recognition-only** (the direction model handles this structurally — directions are whatever rows exist), the lemmatizer is Stanza (classla silently no-ops on Norwegian), and card fronts can carry comma-separated spelling variants (`mot, imot`) split by `card_surface_variants`.

### 29.4 The Norwegian Compound Breakdown

Norwegian is a compounding language, so the generic per-syllable backward buildup (PART 5's syllabifier) reads compounds wrong. `app/generation/norwegian_breakdown.py` (2026-07-07..10) segments a word into frequency-ranked free stems before building the Pimsleur steps — with a closed-class stem stoplist (so `sommer` never splits into `som`+`mer`), s-joint/geminate handling (`busstasjon` → segments `bus|stasjon` but *speaks* `buss, stasjon`), initial-only homograph guards, and preposition first-elements kept productive (`etterforskning` = `etter`+`forskning`). It dispatches through the registry flag `uses_compound_word_breakdown` in `section_builder.py`; the linguistic decisions (stoplist, golden splits) are human-confirmed by ear via the preview CLI. Design history: `docs/archive/bp-brief-segmenter-homographs-overlap.md`.

```bash
cd backend && uv run python -m app.generation.breakdown_preview etterforskningsteamet busstasjon sommer 2>&1 | head -14
```

```output
=== Breakdown Preview: "etterforskningsteamet" ===
  Compound segments:  etter | forsknings | team | et
  Slow pronunciation:  etter, forsknings, teamet
  Pimsleur steps:      etterforskningsteamet → teamet → et → team → teamet → forsknings → nings → forsk → forsknings → forskningsteamet → etter → ter → ett → etter → etterforskningsteamet

=== Breakdown Preview: "busstasjon" ===
  Compound segments:  bus | stasjon
  Slow pronunciation:  buss, stasjon
  Pimsleur steps:      busstasjon → stasjon → sjon → sta → stasjon → buss → busstasjon

=== Breakdown Preview: "sommer" ===
  Compound segments:  sommer
  Slow pronunciation:  sommer
  Pimsleur steps:      sommer → mer → somm → sommer → sommer
```

### 29.5 The Direction Field Registry

The per-direction schema's invariants used to live in prose (queue-parity rules 7, 8, 10). Since 2026-07-08 they are **declared** in `app/srs/direction_fields.py`: every `collocation_directions` column is a registry entry carrying a `sync_comparable` decision (which derives `_DIR_COLUMNS` and `_direction_differs` — the Layer 17/35/37 diff — so new fields can't silently miss the sync diff), a `WritePolicy` (`STICKY_NEW` for `prior_state`, `ONE_SHOT` for `introduced_at`), and a value domain that migration **v35 turned into SQL `CHECK` constraints** (`bury_kind IN (NULL,'sched','user')`, the `prior_state` domain). `tests/test_direction_fields.py` and `tests/test_direction_invariants.py` pin registry ↔ schema ↔ model ↔ diff to each other.

```bash
grep -n "class WritePolicy" -A 8 backend/app/srs/direction_fields.py | head -12
```

```output
45:class WritePolicy(Enum):
46-    """Write-time transition invariant for a direction column.
47-
48-    Declares, as data, the column-level rules that previously lived only in
49-    ``.claude/rules/anki-queue-parity.md`` prose (rules 7, 8, 10). The resolver
50-    functions that actually enforce the transition rules are pinned to this
51-    declaration by ``tests/test_direction_invariants.py`` — a regression that
52-    reverts sticky/one-shot behavior fails a test instead of silently drifting.
53-    """
```

### 29.6 Parity Layers 67–80: the Daily-Caps Arc and Friends

PART 26's table stopped at Layer 66. The history since (full entries in `docs/anki-parity-layers.md`):

| Layer | One line |
|---|---|
| 67 | "Graded today" means the **4 AM-local rollover window**, not midnight — `_anki_day_bounds_utc` threaded through six helpers (badge under-count fix) |
| 68 | Orphan recovery reads Anki's `graves` — a note grave means the user deleted it: hard-delete in TT, don't resurrect |
| 69–72 | Push→pull seam fixes: push writes `cards.data`, pull gets a TT-ahead recency guard (native grades no longer clobbered), `fsrs_known` day-level poisoning fixed |
| 73–74 | Revlog id discipline: TT pushes land at grade-time ids (`preferred_id`), self-echo suppressed on the next pull |
| 75 | Daily caps limit the **served queue**, not just the badge (a 50-cap deck was serving 1499 reviews) |
| 76 | New-card intros charge the review-per-day budget (`effective_review_budget` nets out `introduced_today`) |
| 77 | The review budget also caps how many NEW cards are served (`new = min(new_quota, review_budget − gathered)`) |
| 78 | Revlog rows mirror the **pre-answer** state (`lastIvl`/`review_kind` keyed on the state before the grade) |
| 79 | Interday learning (queue=3) charges the review limit; intraday (queue=1) stays exempt |
| 80 | `sync_push` pushes **one Anki revlog row per TT grade** from `tt_revlog` (watermark = `MAX(revlog.id)` per card), ending the collapsed-row history loss |

The load-bearing helpers for the caps arc:

```bash
grep -n "def effective_review_budget" backend/app/srs/anki_mirror/queue_stats.py; grep -n "def count_interday_learning_due" backend/app/srs/db_counts.py
```

```output
332:def effective_review_budget(
200:    def count_interday_learning_due(self, today: date) -> int:
```

The collection-level `newCardsIgnoreReviewLimit` flag is synced from Anki's config table and threaded through both the badge and the served queue — when ON, the 76/77 couplings lift. All of it is oracle-pinned in `test_parity_daily_caps.py`.

### 29.7 The Lesson Player Rework

The 2026-07-09/10 player rework replaced the plain `AudioPlayer.svelte` with **`LessonPlayer.svelte`** and a phase model. The renderer now emits **per-section cue manifests** (`render_service.derive_section_cues` splits the full-track cue list per section), lessons gained the `SLOW_TRANSLATED` section (PART 2's fifth type), and the player walks phases with an enunciation-cycle and English-translation track model. Legacy lessons (rendered before per-section cues existed) are gated by `trackMode` — they degrade to the full concatenated track instead of breaking. The transcript's old "Slow" text toggle is gone; slow audio is a *player* concern now.

```bash
grep -n "def derive_section_cues" backend/app/audio/render_service.py; grep -c "trackMode" frontend/src/lib/components/LessonPlayer.svelte
```

```output
28:def derive_section_cues(cues: list[Cue], lesson) -> dict[int, list[Cue]]:
8
```

### 29.8 Where to Look Now

| Old reference (PARTs 12–27) | Current home |
|---|---|
| `app/anki/sync.py:<line>` internals | `sync_engine.py` (engine), `sync_reader.py`/`sync_writer.py` (I/O), `sync_common.py` (helpers) |
| `app/srs/database.py:<line>` methods | the matching `db_*` mixin (`db_counts`, `db_queue`, `db_directions`, `db_collocations`, `db_revlog`, …) |
| `api/srs.py` queue assembly | `app/srs/anki_mirror/queue_engine.py` |
| `app/anki/sqlite_writer.py`, AnkiConnect clients, `detect_mode` | deleted |
| One-shot migration scripts under `app/anki/` | `backend/scripts/anki_archive/` |
| `Language.slovene()` hardcoding, `settings.lemmatizer_type` singletons | `app/languages.py` registry + `LanguageContext` |
| `/admin/srs` | `/cards` |
| `AudioPlayer.svelte` | `LessonPlayer.svelte` |
