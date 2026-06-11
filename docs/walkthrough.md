# TunaTale Production Codebase Walkthrough

*2026-03-25T01:20:45Z by Showboat 0.6.1*
<!-- showboat-id: 4bdef7f8-1973-46b4-b00d-14caf394240c -->

## Purpose of This Document

This walkthrough covers the production TunaTale codebase — the unified application rebuilt from the two prototypes documented in `walkthrough-prototypes.md`. It serves two audiences: (1) a human reader wanting to understand how TunaTale works, and (2) an AI agent extending or maintaining the system.

**What changed from the prototypes:** The production rebuild unified the audio pipeline (micro-demo-0.0) and the content engine (micro-demo-0.1) under a single FastAPI application. Hardcoded language logic was replaced with pluggable preprocessors and voice maps. The mock LLM (MD5-hashed) became a cassette system with multiple modes. FSRS-5 replaced the custom SRS scheduler. The entire codebase follows hexagonal architecture with Protocol-based ports. Since the initial production build: ContentStore added SQLite persistence for curricula/lessons/audio, per-word SRS tracking added lemmatizer/tokenizer/transcript modules, section_builder extracted from StoryGenerator (now a thin orchestrator), Slovene syllabification added for Pimsleur backward buildup, pydub replaced raw-PCM concatenation, SRS admin UI added (6 admin endpoints + SvelteKit admin page).

**Stage-3 Anki integration (PART 12 onward):** SRS items track two directions independently (RECOGNITION L2→L1 and PRODUCTION L1→L2), mirroring Anki's note/card model. The `app/anki/` package handles direct SQLite access to `collection.anki2` with a backup-and-lock safety envelope (`safe_open`), an offline-first sync engine (push → drain pending revlog → pull) that doesn't depend on AnkiConnect, and a media pipeline (Forvo + EdgeTTS fallback + Pixabay + ffmpeg LUFS normalization). Queue stats read FSRS-5 parameters from Anki's deck_config protobuf, cached in `anki_state_cache`. Frontend has a unified review queue, Anki-running status gating, a single Sync button, and an `/admin/srs` page. PARTs 18–21 cover the parity testing harness, the `tt_revlog` event log, the cloze pipeline, and the frontend toolchain that all support this.

**The word-learning state machine (PART 22 onward):** the model shifted from a flat per-card list to a per-**lemma** state machine — `BASE (recognition → production) → INFLECTIONS` — built on a sentence-aware classla lemmatizer (PART 22), always-on cloze cards with Fluent-Forever ending-blanks (PART 23), and an A1-tuned `morphology_focus` generator (PART 24). PART 25 ties these together: introduction gates, per-lemma mastery coloring, and a fully interactive transcript where any word is a one-click entry into the learning loop. PART 26 covers the f32 FSRS migration and parity Layers 49–66; PART 27 the move toward event-sourced sync; PART 28 the documentation set.

## Architecture at a Glance

```
backend/
├── app/
│   ├── main.py              # FastAPI app with CORS, lifespan, routers
│   ├── config.py             # Pydantic Settings (env-driven, +Anki/Forvo/Pixabay)
│   ├── common/               # Cross-cutting helpers (guid generation)
│   ├── models/               # Pure domain models (no I/O)
│   ├── llm/                  # Groq LLM client + cassette replay system
│   ├── srs/                  # FSRS-5 + queue_stats + lemmatizer/tokenizer/transcript/migrations
│   ├── generation/           # Curriculum + story + section_builder + syllabify + enforcement
│   ├── audio/                # TTS, pydub assembly, preprocessing
│   ├── storage/              # ContentStore SQLite repository
│   ├── media/                # In-app media import (refresh Anki media into TT cache)
│   ├── anki/                 # Direct sqlite access to collection.anki2 (safety/sync/media)
│   │   └── media/            # Forvo + EdgeTTS fallback + Pixabay + ffmpeg normalize
│   └── api/                  # FastAPI route modules (36 endpoints)
└── tests/
    ├── conftest.py           # Cassette + DB + ASGI fixtures
    ├── cassettes/            # Recorded LLM responses (JSON)
    └── test_*.py             # 124 test files, ~2650 tests, 100% branch coverage
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
     3	from pydantic_settings import BaseSettings, SettingsConfigDict
     4	
     5	
     6	class Settings(BaseSettings):
     7	    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
     8	
     9	    groq_api_key: str = ""
    10	    database_url: str = "sqlite:///./tunatale.db"
    11	    llm_mode: str = "mock"  # mock | live | record | patch
    12	    llm_model: str = "llama-3.3-70b-versatile"
    13	
    14	
    15	settings = Settings()
```

Four settings drive the system: `groq_api_key` for the LLM provider, `database_url` for SRS persistence, `llm_mode` controlling cassette behavior (mock/live/record/patch), and `llm_model` selecting the Groq model. The `extra="ignore"` setting means stray env vars won't crash startup. CI runs with defaults — no API key needed because `llm_mode` defaults to `mock`.

### 1.2 FastAPI Application

```bash
cat -n backend/app/main.py
```

```output
     1	"""FastAPI application for TunaTale language learning."""
     2
     3	import logging
     4	from contextlib import asynccontextmanager
     5	from pathlib import Path
     6
     7	from dotenv import load_dotenv
     8
     9	load_dotenv()
    10
    11	from fastapi import FastAPI  # noqa: E402
    12	from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
    13
    14	from app.audio.edge_tts import EdgeTTSService  # noqa: E402
    15	from app.audio.pause_calculator import NaturalPauseCalculator  # noqa: E402
    16	from app.audio.preprocessing.slovene import SlovenePreprocessor  # noqa: E402
    17	from app.audio.renderer import LessonRenderer  # noqa: E402
    18	from app.config import settings  # noqa: E402
    19	from app.generation.curriculum import CurriculumGenerator  # noqa: E402
    20	from app.generation.story import StoryGenerator  # noqa: E402
    21	from app.llm.cassette import CassetteLLMClient  # noqa: E402
    22	from app.llm.client import LLMClient  # noqa: E402
    23	from app.models.language import Language  # noqa: E402
    24	from app.srs.database import SRSDatabase  # noqa: E402
    25	from app.storage.store import ContentStore  # noqa: E402
    26
    27	logging.basicConfig(level=logging.INFO)
    28	logging.getLogger("app.audio.renderer").setLevel(logging.DEBUG)
    29
    30	logger = logging.getLogger(__name__)
    31
    32
    33	@asynccontextmanager
    34	async def lifespan(app: FastAPI):
    35	    real_client = LLMClient(groq_api_key=settings.groq_api_key, groq_model=settings.llm_model)
    36	    cassette_path = Path("tests/cassettes/e2e.json")
    37
    38	    # Wrap with cassettes unless explicitly in live mode
    39	    if settings.llm_mode != "live":
    40	        llm = CassetteLLMClient(mode=settings.llm_mode, cassette_path=cassette_path, real_client=real_client)
    41	    else:
    42	        llm = real_client
    43
    44	    db_path = settings.database_url.removeprefix("sqlite:///")
    45	    srs_db = SRSDatabase(db_path)
    46	    content_store = ContentStore(db_path)
    47
    48	    language = Language.slovene()
    49
    50	    app.state.srs_db = srs_db
    51	    app.state.content_store = content_store
    52	    app.state.language = language
    53	    app.state.curriculum_generator = CurriculumGenerator(llm)
    54	    app.state.story_generator = StoryGenerator(llm)
    55	    app.state.renderer = LessonRenderer(
    56	        tts=EdgeTTSService(),
    57	        preprocessor=SlovenePreprocessor(),
    58	        pause_calculator=NaturalPauseCalculator(),
    59	    )
    60	    app.state.audio_dir = Path("output/audio")
    61
    62	    logger.info("TunaTale backend starting up")
    63	    yield
    64
    65	    srs_db.close()
    66	    content_store.close()
    67	    logger.info("TunaTale backend shutting down")
    68
    69
    70	app = FastAPI(title="TunaTale", version="0.1.0", lifespan=lifespan)
    71
    72	app.add_middleware(
    73	    CORSMiddleware,
    74	    allow_origins=["*"],
    75	    allow_credentials=True,
    76	    allow_methods=["*"],
    77	    allow_headers=["*"],
    78	)
    79
    80	from app.api import audio, curriculum, generation, srs  # noqa: E402
    81
    82	app.include_router(curriculum.router)
    83	app.include_router(generation.router)
    84	app.include_router(srs.router)
    85	app.include_router(audio.router)
    86
    87
    88	@app.get("/api/health")
    89	async def health():
    90	    return {"status": "ok"}
```

The lifespan context manager wires every dependency the API needs. Three production refinements since the prototype phase stand out:

1. **Cassette wrapping is automatic.** Unless `llm_mode == "live"`, the real `LLMClient` is wrapped in a `CassetteLLMClient` that records or replays from `tests/cassettes/e2e.json`. This keeps the dev server (and CI) from hitting the real Groq API by accident.
2. **`ContentStore` joined the wiring.** Curricula, lessons, and rendered audio files are persisted to SQLite alongside the SRS database (same `db_path`). The store is closed on shutdown.
3. **`StoryGenerator` no longer takes `srs_db`.** The LLM produces creative content and the new `section_builder` transforms it into structured `Section`s deterministically (see Part 5.3).

`LessonRenderer` is constructed with three collaborators (TTS, preprocessor, pause calculator) — the old `AudioAssembler` port is gone, replaced by pydub-based assembly inside the renderer itself (Part 6.4). Logging is configured at INFO globally with the renderer at DEBUG so per-section synthesis steps show up in dev. Four routers partition the API: curriculum, generation, SRS, audio. The health check at `/api/health` is the smoke test.

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
     7
     8	@dataclass
     9	class Language:
    10	    """Language configuration including ISO code, display names, script, and TTS voice map."""
    11
    12	    code: str  # ISO 639-1 code, e.g. "sl"
    13	    name: str  # English name, e.g. "Slovene"
    14	    native_name: str  # Native name, e.g. "slovenščina"
    15	    script: str  # Writing system, e.g. "latin"
    16	    tts_voice_map: dict[str, str] = field(default_factory=dict)  # role → EdgeTTS voice name
    17
    18	    @classmethod
    19	    def slovene(cls) -> Language:
    20	        return cls(
    21	            code="sl",
    22	            name="Slovene",
    23	            native_name="slovenščina",
    24	            script="latin",
    25	            tts_voice_map={
    26	                "narrator": "en-US-GuyNeural",
    27	                "female-1": "sl-SI-PetraNeural",
    28	                "female-2": "sl-SI-PetraNeural",
    29	                "male-1": "sl-SI-RokNeural",
    30	                "male-2": "sl-SI-RokNeural",
    31	                "female": "sl-SI-PetraNeural",  # legacy
    32	                "male": "sl-SI-RokNeural",  # legacy
    33	            },
    34	        )
    35
    36	    @classmethod
    37	    def english(cls) -> Language:
    38	        return cls(
    39	            code="en",
    40	            name="English",
    41	            native_name="English",
    42	            script="latin",
    43	            tts_voice_map={
    44	                "narrator": "en-US-GuyNeural",
    45	                "female-1": "en-US-AriaNeural",
    46	                "female-2": "en-US-AriaNeural",
    47	                "male-1": "en-US-GuyNeural",
    48	                "male-2": "en-US-GuyNeural",
    49	                "female": "en-US-AriaNeural",  # legacy
    50	                "male": "en-US-GuyNeural",  # legacy
    51	            },
    52	        )
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

The lesson model implements the Pimsleur 4-section format — the same structure from the prototypes, now as clean dataclasses:

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
    12
    13	@dataclass
    14	class KeyPhraseInfo:
    15	    """A key phrase with its L1 translation, stored on the Lesson for deferred SRS registration."""
    16
    17	    phrase: str
    18	    translation: str
    19
    20
    21	class SectionType(Enum):
    22	    """Four Pimsleur section types for each lesson."""
    23
    24	    KEY_PHRASES = "key_phrases"
    25	    NATURAL_SPEED = "natural_speed"
    26	    SLOW_SPEED = "slow_speed"
    27	    TRANSLATED = "translated"
    28
    29
    30	@dataclass
    31	class Phrase:
    32	    """A single phrase with TTS voice settings."""
    33
    34	    text: str
    35	    voice_id: str
    36	    language_code: str
    37	    rate: str = "+0%"
    38	    pitch: str = "+0Hz"
    39	    volume: str = "+0%"
    40	    role: str = ""
    41
    42
    43	@dataclass
    44	class Section:
    45	    """A section within a lesson, grouping phrases of the same Pimsleur type."""
    46
    47	    section_type: SectionType
    48	    phrases: list[Phrase] = field(default_factory=list)
    49
    50	    def __post_init__(self) -> None:
    51	        if not isinstance(self.section_type, SectionType):
    52	            raise ValueError(f"section_type must be a SectionType enum, got {type(self.section_type)}")
    53
    54
    55	@dataclass
    56	class Lesson:
    57	    """A complete TunaTale audio lesson."""
    58
    59	    title: str
    60	    language_code: str
    61	    sections: list[Section] = field(default_factory=list)
    62	    narrator_voice: str = "en-US-GuyNeural"
    63	    key_phrases: list[KeyPhraseInfo] = field(default_factory=list)
    64
    65	    def to_json(self) -> str:
    66	        data = {
    67	            "title": self.title,
    68	            "language_code": self.language_code,
    69	            "narrator_voice": self.narrator_voice,
    70	            "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in self.key_phrases],
    71	            "sections": [
    72	                {
    73	                    "section_type": s.section_type.value,
    74	                    "phrases": [
    75	                        {
    76	                            "text": p.text,
    77	                            "voice_id": p.voice_id,
    78	                            "language_code": p.language_code,
    79	                            "rate": p.rate,
    80	                            "pitch": p.pitch,
    81	                            "volume": p.volume,
    82	                            "role": p.role,
    83	                        }
    84	                        for p in s.phrases
    85	                    ],
    86	                }
    87	                for s in self.sections
    88	            ],
    89	        }
    90	        return json.dumps(data, ensure_ascii=False)
    91
    92	    @classmethod
    93	    def from_json(cls, json_str: str) -> Lesson:
    94	        data = json.loads(json_str)
    95	        sections = [
    96	            Section(
    97	                section_type=SectionType(s["section_type"]),
    98	                phrases=[Phrase(**p) for p in s["phrases"]],
    99	            )
   100	            for s in data.get("sections", [])
   101	        ]
   102	        key_phrases = [KeyPhraseInfo(**kp) for kp in data.get("key_phrases", [])]
   103	        return cls(
   104	            title=data["title"],
   105	            language_code=data["language_code"],
   106	            sections=sections,
   107	            narrator_voice=data.get("narrator_voice", "en-US-GuyNeural"),
   108	            key_phrases=key_phrases,
   109	        )
```

The four section types encode the Pimsleur method: (1) **KEY_PHRASES** — individual vocabulary, (2) **NATURAL_SPEED** — full dialogue at native speed, (3) **SLOW_SPEED** — same dialogue with pauses between words, (4) **TRANSLATED** — L2 followed by L1 translation. Each `Phrase` carries its own TTS settings (rate, pitch, volume) plus a `role` field (`narrator`, `female-1`, `male-1`, …) that the audio pipeline uses for voice routing.

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
     5	from dataclasses import dataclass
     6
     7
     8	@dataclass
     9	class SyntacticUnit:
    10	    """A collocation in the target language (L2) with its L1 translation.
    11
    12	    word_count must be 1-8. difficulty must be 1-5.
    13	    source is either "corpus" (frequency-derived) or "llm" (generated).
    14	    """
    15
    16	    text: str  # L2 text
    17	    translation: str  # L1 translation
    18	    word_count: int
    19	    difficulty: int  # 1–5
    20	    source: str  # "corpus" | "llm"
    21	    frequency: int = 0
    22	    lemma: str | None = None
    23
    24	    def __post_init__(self) -> None:
    25	        if not 1 <= self.word_count <= 8:
    26	            raise ValueError(f"word_count must be 1–8, got {self.word_count}")
    27	        if not 1 <= self.difficulty <= 5:
    28	            raise ValueError(f"difficulty must be 1–5, got {self.difficulty}")
```

```bash
cat -n backend/app/models/srs_item.py
```

```output
     1	"""SRS item domain model (FSRS-based)."""
     2
     3	from __future__ import annotations
     4
     5	from dataclasses import dataclass, field
     6	from datetime import date
     7	from enum import Enum
     8
     9	from .syntactic_unit import SyntacticUnit
    10
    11
    12	class SRSState(Enum):
    13	    """Learning state of an SRS item."""
    14
    15	    NEW = "new"
    16	    LEARNING = "learning"
    17	    REVIEW = "review"
    18	    RELEARNING = "relearning"
    19	    SUSPENDED = "suspended"
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
    31	@dataclass
    32	class SRSItem:
    33	    """An SRS-tracked syntactic unit with FSRS scheduling fields."""
    34
    35	    syntactic_unit: SyntacticUnit
    36	    due_date: date
    37	    stability: float = 1.0  # FSRS stability (days before 90% retention)
    38	    difficulty: float = 5.0  # FSRS difficulty (1–10)
    39	    reps: int = 0
    40	    lapses: int = 0
    41	    state: SRSState = field(default=SRSState.NEW)
    42	    last_review: date | None = None
```

The `SyntacticUnit` is a collocation (multi-word phrase or single word) with bounds validation — `word_count` 1–8, `difficulty` 1–5. The optional `lemma` field stores the canonical form (currently the lowercased word) so per-word SRS tracking can collapse inflected variants — see Part 4.4 for the lemmatizer. The `SRSItem` wraps a SyntacticUnit with FSRS-5 scheduling fields: stability (days before 90% retention drops), difficulty (1–10 scale), reps, lapses, and state.

The state machine is: `NEW → LEARNING → REVIEW ↔ RELEARNING`, with `SUSPENDED` as a terminal state the admin UI can toggle. Suspended items are excluded from due-card queries until unsuspended, at which point they reset to `NEW`.

### 2.5 Content Strategy

The strategy model controls how new vs. review content is balanced:

```bash
cat -n backend/app/models/strategy.py
```

```output
     1	"""Content strategy, difficulty level, and pedagogical scoring configuration.
     2	
     3	Ported from micro-demo-0.1/content_strategy.py — exact scoring weights preserved.
     4	"""
     5	
     6	from __future__ import annotations
     7	
     8	from dataclasses import dataclass
     9	from enum import Enum
    10	
    11	
    12	class ContentStrategy(Enum):
    13	    """Content generation strategy.
    14	
    15	    WIDER: Generate new scenarios using familiar vocabulary (breadth).
    16	    DEEPER: Enhance existing scenarios with more advanced L2 expressions (depth).
    17	    """
    18	
    19	    WIDER = "wider"
    20	    DEEPER = "deeper"
    21	
    22	
    23	class DifficultyLevel(Enum):
    24	    """L2 language complexity level."""
    25	
    26	    BASIC = "basic"
    27	    INTERMEDIATE = "intermediate"
    28	    ADVANCED = "advanced"
    29	
    30	
    31	@dataclass
    32	class PedagogicalScoringConfig:
    33	    """Scoring weights for collocation selection.
    34	
    35	    The four primary weights must sum to 1.0.
    36	    """
    37	
    38	    # Primary weights (must sum to 1.0)
    39	    srs_readiness_weight: float = 0.4
    40	    language_quality_weight: float = 0.3
    41	    pedagogical_value_weight: float = 0.2
    42	    diversity_weight: float = 0.1
    43	
    44	    # Language quality scoring
    45	    english_word_penalty: float = -0.5
    46	    digit_penalty: float = -0.3
    47	    target_word_bonus: float = 0.1
    48	    pure_target_bonus: float = 0.3
    49	
    50	    # Pedagogical value
    51	    min_frequency_threshold: int = 2
    52	    frequency_bonus_multiplier: float = 0.1
    53	    completeness_bonus: float = 0.2
    54	
    55	    # Diversity
    56	    similarity_penalty: float = -0.15
    57	    category_diversity_bonus: float = 0.1
    58	
    59	    # SRS readiness
    60	    low_stability_bonus: float = 0.3
    61	    review_overdue_bonus: float = 0.2
    62	
    63	    def weights_sum_to_one(self) -> bool:
    64	        total = (
    65	            self.srs_readiness_weight
    66	            + self.language_quality_weight
    67	            + self.pedagogical_value_weight
    68	            + self.diversity_weight
    69	        )
    70	        return abs(total - 1.0) < 0.01
    71	
    72	
    73	@dataclass
    74	class StrategyConfig:
    75	    """Parameters controlling SRS behavior and content generation for a strategy."""
    76	
    77	    strategy: ContentStrategy
    78	    difficulty_level: DifficultyLevel
    79	    max_new_collocations: int
    80	    min_review_collocations: int
    81	    review_interval_multiplier: float
    82	
    83	
    84	DEFAULT_STRATEGY_CONFIGS: dict[ContentStrategy, StrategyConfig] = {
    85	    ContentStrategy.WIDER: StrategyConfig(
    86	        strategy=ContentStrategy.WIDER,
    87	        difficulty_level=DifficultyLevel.BASIC,
    88	        max_new_collocations=8,
    89	        min_review_collocations=2,
    90	        review_interval_multiplier=1.5,
    91	    ),
    92	    ContentStrategy.DEEPER: StrategyConfig(
    93	        strategy=ContentStrategy.DEEPER,
    94	        difficulty_level=DifficultyLevel.INTERMEDIATE,
    95	        max_new_collocations=3,
    96	        min_review_collocations=7,
    97	        review_interval_multiplier=0.8,
    98	    ),
    99	}
```

Two strategies: **WIDER** introduces 8 new collocations with 2 reviews (breadth-first for beginners), **DEEPER** introduces only 3 new with 7 reviews (depth-first for reinforcement). The `PedagogicalScoringConfig` carries tuned weights for the collocation selector: SRS readiness 40%, language quality 30%, pedagogical value 20%, diversity 10%. These weights were ported directly from the prototype.

---

## PART 3: LLM Client & Cassette System

The LLM layer wraps Groq's API with retry logic and a VCR-style cassette system for deterministic testing.

### 3.1 HTTP Client

```bash
cat -n backend/app/llm/client.py
```

```output
     1	"""Async LLM client — Groq primary, fallback_client secondary, Ollama offline fallback."""
     2
     3	from __future__ import annotations
     4
     5	import asyncio
     6	import logging
     7	import re
     8	import shutil
     9	import subprocess
    10	import time
    11	from collections.abc import Callable
    12
    13	import httpx
    14
    15	logger = logging.getLogger(__name__)
    16
    17	GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
    18	GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
    19	OLLAMA_DEFAULT_URL = "http://localhost:11434"
    20	OLLAMA_DEFAULT_MODEL = "llama3.2"
    21
    22
    23	def _parse_reset_duration(s: str) -> float:
    24	    """Parse Groq's x-ratelimit-reset-requests header, e.g. '2s', '500ms', '1m30s' → seconds."""
    25	    total = 0.0
    26	    m = re.fullmatch(r"(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?", s.strip())
    27	    if m and any(m.groups()):
    28	        if m.group(1):
    29	            total += int(m.group(1)) * 60
    30	        if m.group(2):
    31	            total += float(m.group(2))
    32	        if m.group(3):
    33	            total += int(m.group(3)) / 1000
    34	    return total
    35
    36
    37	class LLMError(Exception):
    38	    """Raised when all LLM backends fail."""
    39
    40	    def __init__(self, message: str, attempts: list[dict] | None = None) -> None:
    41	        super().__init__(message)
    42	        self.attempts = attempts or []
    43
    44
    45	class LLMClient:
    46	    def __init__(
    47	        self,
    48	        groq_api_key: str | None = None,
    49	        groq_model: str = GROQ_DEFAULT_MODEL,
    50	        timeout: float = 30.0,
    51	        max_retries_429: int = 3,
    52	        max_retry_after_s: float = 10.0,
    53	        ollama_url: str = OLLAMA_DEFAULT_URL,
    54	        ollama_model: str = OLLAMA_DEFAULT_MODEL,
    55	        groq_extra_body_params: dict | None = None,
    56	        on_call: Callable[[dict], None] | None = None,
    57	        fallback_client: LLMClient | None = None,
    58	    ) -> None:
    59	        self.groq_api_key = groq_api_key
    60	        self.groq_model = groq_model
    61	        self.timeout = timeout
    62	        self.max_retries_429 = max_retries_429
    63	        self.max_retry_after_s = max_retry_after_s
    64	        self.ollama_url = ollama_url
    65	        self.ollama_model = ollama_model
    66	        self.groq_extra_body_params = groq_extra_body_params
    67	        self.on_call = on_call
    68	        self.fallback_client = fallback_client
    69	        self.last_provider: str | None = None
    70	        self._next_call_at: float = 0.0
    71	        self._groq_call_delay: float = 0.0
    72	        self._last_429_at: float = 0.0
    73
    74	    def pacing_info(self) -> dict:
    75	        """Return current pacing state for diagnostics."""
    76	        now = time.monotonic()
    77	        return {
    78	            "call_delay_s": self._groq_call_delay,
    79	            "next_call_in_s": max(0.0, self._next_call_at - now),
    80	            "last_429_ago_s": (now - self._last_429_at) if self._last_429_at > 0 else None,
    81	        }
    82
    83	    def _fire_callback(
    84	        self,
    85	        *,
    86	        provider: str,
    87	        model: str,
    88	        latency_ms: int,
    89	        status: str | int,
    90	        prompt: str = "",
    91	        response_text: str | None = None,
    92	        error: str | None = None,
    93	        rate_limits: dict | None = None,
    94	        is_fallback: bool = False,
    95	    ) -> None:
    96	        if self.on_call is None:
    97	            return
    98	        info: dict = {
    99	            "timestamp": time.time(),
   100	            "provider": provider,
   101	            "model": model,
   102	            "latency_ms": latency_ms,
   103	            "status": status,
   104	            "is_fallback": is_fallback,
   105	        }
   106	        if prompt:
   107	            info["prompt_preview"] = prompt[:80]
   108	        if response_text is not None:
   109	            info["response_preview"] = response_text[:200]
   110	        if error is not None:
   111	            info["error"] = error
   112	        if rate_limits is not None:
   113	            info["rate_limits"] = rate_limits
   114	        if self.groq_extra_body_params and "reasoning_effort" in self.groq_extra_body_params:
   115	            info["reasoning_effort"] = self.groq_extra_body_params["reasoning_effort"]
   116	        self.on_call(info)
   117
   118	    @staticmethod
   119	    def _make_attempt(provider: str, model: str, status: str | int, error: str, latency_ms: int) -> dict:
   120	        return {"provider": provider, "model": model, "status": status, "error": error, "latency_ms": latency_ms}
   121
   122	    async def complete(
   123	        self,
   124	        prompt: str,
   125	        system_prompt: str | None = None,
   126	        temperature: float = 0.7,
   127	        max_tokens: int = 2048,
   128	    ) -> str:
   129	        """Try Groq, then fallback_client, then Ollama; raise LLMError if all fail."""
   130	        if not self.groq_api_key:
   131	            raise LLMError("No GROQ_API_KEY configured")
   132
   133	        attempts: list[dict] = []
   134
   135	        try:
   136	            return await self._call_groq(
   137	                prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
   138	            )
   139	        except LLMError as e:
   140	            attempts.extend(e.attempts)
   141	            logger.warning("Groq failed, trying fallback: %s", e)
   142	            if self.fallback_client is not None:
   143	                try:
   144	                    return await self.fallback_client.complete(
   145	                        prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
   146	                    )
   147	                except LLMError as fe:
   148	                    attempts.extend(fe.attempts)
   149	                    logger.warning("Fallback client also failed: %s", fe)
   150
   151	        try:
   152	            return await self._call_ollama(
   153	                prompt,
   154	                max_tokens,
   155	                system_prompt=system_prompt,
   156	                temperature=temperature,
   157	                is_fallback=True,
   158	            )
   159	        except LLMError as e:
   160	            attempts.extend(e.attempts)
   161	            logger.warning("Ollama also failed: %s", e)
   162
   163	        msgs = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts)
   164	        raise LLMError(f"All LLM backends failed: {msgs}", attempts)
   165
   166	    async def _call_groq(
   167	        self,
   168	        prompt: str,
   169	        system_prompt: str | None,
   170	        temperature: float,
   171	        max_tokens: int,
   172	    ) -> str:
   173	        headers = {
   174	            "Authorization": f"Bearer {self.groq_api_key}",
   175	            "Content-Type": "application/json",
   176	        }
   177	        messages: list[dict] = []
   178	        if system_prompt:
   179	            messages.append({"role": "system", "content": system_prompt})
   180	        messages.append({"role": "user", "content": prompt})
   181
   182	        body: dict = {
   183	            "model": self.groq_model,
   184	            "messages": messages,
   185	            "temperature": temperature,
   186	        }
   187	        if self.groq_extra_body_params:
   188	            body.update(self.groq_extra_body_params)
   189	            if "max_completion_tokens" not in body:
   190	                body["max_completion_tokens"] = max_tokens
   191	        else:
   192	            body["max_tokens"] = max_tokens
   193
   194	        async with httpx.AsyncClient(timeout=self.timeout) as http:
   195	            for attempt in range(self.max_retries_429 + 1):
   196	                if self._groq_call_delay > 0 and time.monotonic() - self._last_429_at > 60:
   197	                    self._groq_call_delay = 0.0
   198	                wait = self._next_call_at - time.monotonic()
   199	                if wait > 0:
   200	                    logger.info("Groq RPM pacing: waiting %.1fs", wait)
   201	                    await asyncio.sleep(wait)
   202
   203	                start = time.monotonic()
   204	                try:
   205	                    response = await http.post(GROQ_API_URL, headers=headers, json=body)
   206	                except httpx.TimeoutException as err:
   207	                    latency_ms = int((time.monotonic() - start) * 1000)
   208	                    msg = f"Groq timed out after {self.timeout}s"
   209	                    self._fire_callback(
   210	                        provider="groq",
   211	                        model=self.groq_model,
   212	                        latency_ms=latency_ms,
   213	                        status="timeout",
   214	                        prompt=prompt,
   215	                        error=msg,
   216	                    )
   217	                    raise LLMError(
   218	                        msg, [self._make_attempt("groq", self.groq_model, "timeout", msg, latency_ms)]
   219	                    ) from err
   220	                latency_ms = int((time.monotonic() - start) * 1000)
   221
   222	                # Log rate-limit headers
   223	                rl_tokens_remaining = response.headers.get("x-ratelimit-remaining-tokens", "?")
   224	                rl_tokens_limit = response.headers.get("x-ratelimit-limit-tokens", "?")
   225	                rl_requests_remaining = response.headers.get("x-ratelimit-remaining-requests", "?")
   226	                rl_requests_limit = response.headers.get("x-ratelimit-limit-requests", "?")
   227	                logger.info(
   228	                    "Groq rate-limit: tokens=%s/%s requests=%s/%s",
   229	                    rl_tokens_remaining,
   230	                    rl_tokens_limit,
   231	                    rl_requests_remaining,
   232	                    rl_requests_limit,
   233	                )
   234
   235	                if response.status_code == 429:
   236	                    retry_after_raw = response.headers.get("retry-after", "2")
   237	                    try:
   238	                        retry_after = float(retry_after_raw)
   239	                    except ValueError:
   240	                        retry_after = 2.0
   241	                    msg = f"Groq returned 429 Too Many Requests (retry after {retry_after_raw}s)"
   242	                    if retry_after <= self.max_retry_after_s:
   243	                        self._last_429_at = time.monotonic()
   244	                        self._groq_call_delay = retry_after
   245	                    if attempt < self.max_retries_429 and retry_after <= self.max_retry_after_s:
   246	                        logger.warning(
   247	                            "Groq 429, retry %d/%d after %.1fs", attempt + 1, self.max_retries_429, retry_after
   248	                        )
   249	                        self._fire_callback(
   250	                            provider="groq",
   251	                            model=self.groq_model,
   252	                            latency_ms=latency_ms,
   253	                            status=429,
   254	                            prompt=prompt,
   255	                            error=msg,
   256	                        )
   257	                        await asyncio.sleep(retry_after)
   258	                        continue
   259	                    self._fire_callback(
   260	                        provider="groq",
   261	                        model=self.groq_model,
   262	                        latency_ms=latency_ms,
   263	                        status=429,
   264	                        prompt=prompt,
   265	                        error=msg,
   266	                    )
   267	                    raise LLMError(msg, [self._make_attempt("groq", self.groq_model, 429, msg, latency_ms)])
   268
   269	                if not response.is_success:
   270	                    msg = f"Groq returned HTTP {response.status_code}"
   271	                    self._fire_callback(
   272	                        provider="groq",
   273	                        model=self.groq_model,
   274	                        latency_ms=latency_ms,
   275	                        status=response.status_code,
   276	                        prompt=prompt,
   277	                        error=msg,
   278	                    )
   279	                    raise LLMError(
   280	                        msg, [self._make_attempt("groq", self.groq_model, response.status_code, msg, latency_ms)]
   281	                    )
   282
   283	                data = response.json()
   284	                content = data["choices"][0]["message"]["content"]
   285	                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
   286	                self.last_provider = "groq"
   287	                logger.info("Groq success: model=%s latency=%dms", self.groq_model, latency_ms)
   288
   289	                if self.on_call:
   290	                    _rl: dict = {}
   291	                    if rl_tokens_remaining.isdigit():
   292	                        _rl["tokens_remaining"] = int(rl_tokens_remaining)
   293	                    if rl_tokens_limit.isdigit():
   294	                        _rl["tokens_limit"] = int(rl_tokens_limit)
   295	                    if rl_requests_remaining.isdigit():
   296	                        _rl["requests_remaining"] = int(rl_requests_remaining)
   297	                    if rl_requests_limit.isdigit():
   298	                        _rl["requests_limit"] = int(rl_requests_limit)
   299	                    self._fire_callback(
   300	                        provider="groq",
   301	                        model=self.groq_model,
   302	                        latency_ms=latency_ms,
   303	                        status="success",
   304	                        prompt=prompt,
   305	                        response_text=content,
   306	                        rate_limits=_rl if _rl else None,
   307	                    )
   308
   309	                # Proactive pacing: RPM + TPM
   310	                proactive_delay = 0.0
   311	                rem_req_raw = response.headers.get("x-ratelimit-remaining-requests", "")
   312	                rst_req_raw = response.headers.get("x-ratelimit-reset-requests", "")
   313	                if rem_req_raw.isdigit() and rst_req_raw:
   314	                    rem_req = int(rem_req_raw)
   315	                    rst_req_s = _parse_reset_duration(rst_req_raw)
   316	                    if rem_req == 0 and rst_req_s > 0:
   317	                        proactive_delay = rst_req_s
   318	                    elif rem_req > 0 and rst_req_s > 0:
   319	                        proactive_delay = rst_req_s / rem_req
   320
   321	                rem_tok_raw = response.headers.get("x-ratelimit-remaining-tokens", "")
   322	                rst_tok_raw = response.headers.get("x-ratelimit-reset-tokens", "")
   323	                lim_tok_raw = response.headers.get("x-ratelimit-limit-tokens", "")
   324	                if rem_tok_raw.isdigit() and rst_tok_raw and lim_tok_raw.isdigit():
   325	                    rem_tok = int(rem_tok_raw)
   326	                    rst_tok_s = _parse_reset_duration(rst_tok_raw)
   327	                    lim_tok = int(lim_tok_raw)
   328	                    if rem_tok == 0 and rst_tok_s > 0:
   329	                        proactive_delay = max(proactive_delay, rst_tok_s)
   330	                    elif rem_tok > 0 and rst_tok_s > 0 and lim_tok > 0 and rem_tok < lim_tok * 0.20:
   331	                        tokens_per_call = body.get("max_completion_tokens") or body.get("max_tokens") or max_tokens
   332	                        calls_left = max(rem_tok / max(tokens_per_call, 1), 1.0)
   333	                        tok_delay = rst_tok_s / calls_left
   334	                        proactive_delay = max(proactive_delay, tok_delay)
   335
   336	                if proactive_delay > 0.5:
   337	                    logger.info(
   338	                        "Groq proactive pacing: req=%s/%s tok=%s/%s → %.2fs delay",
   339	                        rem_req_raw,
   340	                        rst_req_raw,
   341	                        rem_tok_raw,
   342	                        rst_tok_raw,
   343	                        proactive_delay,
   344	                    )
   345	                delay = max(self._groq_call_delay, proactive_delay)
   346	                self._next_call_at = time.monotonic() + delay
   347	                return content
   348
   349	        raise LLMError("Groq call loop exhausted", [])  # pragma: no cover
   350
   351	    async def _start_ollama(self) -> bool:  # pragma: no cover
   352	        """Try to start 'ollama serve' in the background. Returns True if started."""
   353	        if shutil.which("ollama") is None:
   354	            logger.warning("ollama binary not found in PATH")
   355	            return False
   356	        try:
   357	            subprocess.Popen(
   358	                ["ollama", "serve"],
   359	                stdout=subprocess.DEVNULL,
   360	                stderr=subprocess.DEVNULL,
   361	            )
   362	            logger.info("Started 'ollama serve', waiting for it to be ready...")
   363	            for _ in range(20):  # up to 10s
   364	                await asyncio.sleep(0.5)
   365	                try:
   366	                    async with httpx.AsyncClient(timeout=2.0) as http:
   367	                        resp = await http.get(f"{self.ollama_url}/api/tags")
   368	                        if resp.is_success:
   369	                            logger.info("Ollama is ready")
   370	                            return True
   371	                except Exception:
   372	                    pass
   373	            logger.warning("Ollama started but not ready after 10s")
   374	            return False
   375	        except OSError as e:
   376	            logger.warning("Failed to start ollama: %s", e)
   377	            return False
   378
   379	    async def _call_ollama(
   380	        self,
   381	        prompt: str,
   382	        max_tokens: int,
   383	        system_prompt: str | None = None,
   384	        temperature: float = 0.7,
   385	        is_fallback: bool = False,
   386	    ) -> str:
   387	        body: dict = {
   388	            "model": self.ollama_model,
   389	            "prompt": prompt,
   390	            "stream": False,
   391	            "options": {"temperature": temperature, "num_predict": max_tokens},
   392	        }
   393	        if system_prompt:
   394	            body["system"] = system_prompt
   395
   396	        start = time.monotonic()
   397	        try:
   398	            async with httpx.AsyncClient(timeout=self.timeout) as http:
   399	                response = await http.post(f"{self.ollama_url}/api/generate", json=body)
   400	        except httpx.ConnectError:
   401	            if await self._start_ollama():  # pragma: no cover
   402	                start = time.monotonic()  # pragma: no cover
   403	                try:  # pragma: no cover
   404	                    async with httpx.AsyncClient(timeout=self.timeout) as http:  # pragma: no cover
   405	                        response = await http.post(f"{self.ollama_url}/api/generate", json=body)  # pragma: no cover
   406	                except (httpx.ConnectError, httpx.TimeoutException) as err:  # pragma: no cover
   407	                    latency_ms = int((time.monotonic() - start) * 1000)  # pragma: no cover
   408	                    status = (
   409	                        "timeout" if isinstance(err, httpx.TimeoutException) else "connect_error"
   410	                    )  # pragma: no cover
   411	                    msg = f"Ollama {status} at {self.ollama_url} (after auto-start)"  # pragma: no cover
   412	                    self._fire_callback(
   413	                        provider="ollama",
   414	                        model=self.ollama_model,
   415	                        latency_ms=latency_ms,  # pragma: no cover
   416	                        status=status,
   417	                        prompt=prompt,
   418	                        error=msg,
   419	                        is_fallback=is_fallback,
   420	                    )
   421	                    raise LLMError(
   422	                        msg, [self._make_attempt("ollama", self.ollama_model, status, msg, latency_ms)]
   423	                    ) from err  # pragma: no cover
   424	            else:
   425	                latency_ms = int((time.monotonic() - start) * 1000)
   426	                msg = f"Ollama connection refused at {self.ollama_url} (auto-start failed)"
   427	                self._fire_callback(
   428	                    provider="ollama",
   429	                    model=self.ollama_model,
   430	                    latency_ms=latency_ms,
   431	                    status="connect_error",
   432	                    prompt=prompt,
   433	                    error=msg,
   434	                    is_fallback=is_fallback,
   435	                )
   436	                raise LLMError(
   437	                    msg, [self._make_attempt("ollama", self.ollama_model, "connect_error", msg, latency_ms)]
   438	                ) from None
   439	        except httpx.TimeoutException as err:
   440	            latency_ms = int((time.monotonic() - start) * 1000)
   441	            msg = f"Ollama timed out after {self.timeout}s"
   442	            self._fire_callback(
   443	                provider="ollama",
   444	                model=self.ollama_model,
   445	                latency_ms=latency_ms,
   446	                status="timeout",
   447	                prompt=prompt,
   448	                error=msg,
   449	                is_fallback=is_fallback,
   450	            )
   451	            raise LLMError(msg, [self._make_attempt("ollama", self.ollama_model, "timeout", msg, latency_ms)]) from err
   452
   453	        latency_ms = int((time.monotonic() - start) * 1000)
   454
   455	        if not response.is_success:
   456	            msg = f"Ollama returned HTTP {response.status_code}"
   457	            self._fire_callback(
   458	                provider="ollama",
   459	                model=self.ollama_model,
   460	                latency_ms=latency_ms,
   461	                status=response.status_code,
   462	                prompt=prompt,
   463	                error=msg,
   464	                is_fallback=is_fallback,
   465	            )
   466	            raise LLMError(
   467	                msg, [self._make_attempt("ollama", self.ollama_model, response.status_code, msg, latency_ms)]
   468	            )
   469
   470	        data = response.json()
   471	        result = data["response"].strip()
   472	        logger.info("Ollama success: model=%s latency=%dms", self.ollama_model, latency_ms)
   473	        self.last_provider = "ollama"
   474	        self._fire_callback(
   475	            provider="ollama",
   476	            model=self.ollama_model,
   477	            latency_ms=latency_ms,
   478	            status="success",
   479	            prompt=prompt,
   480	            response_text=result,
   481	            is_fallback=is_fallback,
   482	        )
   483	        return result
   484
   485	    async def health(self) -> dict:
   486	        """Return which backends are available."""
   487	        result = {"groq": bool(self.groq_api_key), "ollama": False}
   488	        try:
   489	            async with httpx.AsyncClient(timeout=3.0) as http:
   490	                response = await http.get(f"{self.ollama_url}/api/tags")
   491	                result["ollama"] = response.is_success
   492	        except Exception:
   493	            pass
   494	        return result
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
cd backend && uv run pytest tests/test_llm_client.py::test_rate_limit_retry_succeeds -v --no-header --no-cov 2>&1
```

```output
============================= test session starts ==============================
collecting ... collected 1 item

tests/test_llm_client.py::test_rate_limit_retry_succeeds PASSED          [100%]

============================== 1 passed in 0.02s ===============================
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
    24	
    25	def _hash_prompt(prompt: str) -> str:
    26	    return "sha256:" + hashlib.sha256(prompt.encode()).hexdigest()[:16]
    27	
    28	
    29	class CassetteLLMClient:
    30	    """LLMClient wrapper with cassette-based mock/live/record/patch modes."""
    31	
    32	    def __init__(
    33	        self,
    34	        mode: str,  # "mock" | "live" | "record" | "patch"
    35	        cassette_path: Path,
    36	        real_client: LLMClient | None = None,
    37	    ) -> None:
    38	        self._mode = mode
    39	        self._cassette_path = cassette_path
    40	        self._real_client = real_client
    41	        self.last_provider: str | None = None
    42	
    43	        self._calls: list[dict] = []
    44	        self._playback_by_hash: dict[str, list[dict]] = {}
    45	        self._playback_used: dict[str, int] = {}
    46	
    47	        if mode in ("mock", "patch"):
    48	            data = json.loads(cassette_path.read_text())
    49	            for entry in data["calls"]:
    50	                h = entry["prompt_hash"]
    51	                self._playback_by_hash.setdefault(h, []).append(entry)
    52	            if mode == "patch":
    53	                self._calls = list(data["calls"])
    54	
    55	    async def complete(
    56	        self,
    57	        prompt: str,
    58	        system_prompt: str | None = None,
    59	        temperature: float = 0.7,
    60	        max_tokens: int = 256,
    61	    ) -> str:
    62	        if self._mode == "mock":
    63	            return self._replay(prompt)
    64	        if self._mode == "patch":
    65	            return await self._patch(
    66	                prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
    67	            )
    68	        assert self._real_client is not None, "real_client required for live/record mode"
    69	        response = await self._real_client.complete(
    70	            prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
    71	        )
    72	        self.last_provider = self._real_client.last_provider
    73	        if self._mode == "record":
    74	            self._calls.append(
    75	                {
    76	                    "prompt_hash": _hash_prompt(prompt),
    77	                    "prompt_preview": prompt[:80].replace("\n", " "),
    78	                    "max_tokens": max_tokens,
    79	                    "response": response,
    80	                    "provider": self.last_provider,
    81	                }
    82	            )
    83	            self.save()
    84	        return response
    85	
    86	    def _replay(self, prompt: str) -> str:
    87	        h = _hash_prompt(prompt)
    88	        entries = self._playback_by_hash.get(h)
    89	        if not entries:
    90	            raise RuntimeError(
    91	                f"Cassette has no entry for prompt hash {h}.\n  Preview: {prompt[:80]!r}\nRe-record with --llm-mode=record."
    92	            )
    93	        idx = self._playback_used.get(h, 0)
    94	        if idx >= len(entries):
    95	            raise RuntimeError(
    96	                f"Cassette entry {h!r} used {idx} times but only {len(entries)} recorded.\n  Preview: {prompt[:80]!r}"
    97	            )
    98	        entry = entries[idx]
    99	        self._playback_used[h] = idx + 1
   100	        self.last_provider = entry.get("provider", "groq")
   101	        return entry["response"]
   102	
   103	    async def _patch(self, prompt: str, **kwargs) -> str:
   104	        h = _hash_prompt(prompt)
   105	        entries = self._playback_by_hash.get(h)
   106	        if entries:
   107	            idx = self._playback_used.get(h, 0)
   108	            if idx < len(entries):
   109	                entry = entries[idx]
   110	                self._playback_used[h] = idx + 1
   111	                self.last_provider = entry.get("provider", "groq")
   112	                return entry["response"]
   113	
   114	        assert self._real_client is not None, "real_client required for patch mode"
   115	        response = await self._real_client.complete(prompt, **kwargs)
   116	        self.last_provider = self._real_client.last_provider
   117	        new_entry = {
   118	            "prompt_hash": h,
   119	            "prompt_preview": prompt[:80].replace("\n", " "),
   120	            "max_tokens": kwargs.get("max_tokens", 256),
   121	            "response": response,
   122	            "provider": self.last_provider,
   123	        }
   124	        self._calls.append(new_entry)
   125	        self._playback_by_hash.setdefault(h, []).append(new_entry)
   126	        self.save()
   127	        return response
   128	
   129	    def save(self) -> None:
   130	        if self._mode not in ("record", "patch"):
   131	            return
   132	        self._cassette_path.parent.mkdir(parents=True, exist_ok=True)
   133	        data = {
   134	            "recorded_at": datetime.datetime.now(datetime.UTC).isoformat(),
   135	            "calls": self._calls,
   136	        }
   137	        self._cassette_path.write_text(json.dumps(data, indent=2) + "\n")
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
  "recorded_at": "2026-03-25T11:55:09.002705+00:00",
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
cat -n backend/tests/conftest.py
```

```output
     1	"""Pytest configuration for TunaTale test suite."""
     2	
     3	import os
     4	from pathlib import Path
     5	
     6	import pytest
     7	
     8	_CASSETTES_DIR = Path(__file__).parent / "cassettes"
     9	
    10	
    11	def pytest_addoption(parser: pytest.Parser) -> None:
    12	    parser.addoption(
    13	        "--llm-mode",
    14	        choices=["mock", "live", "record", "patch"],
    15	        default="mock",
    16	        help="LLM mode for cassette fixtures: mock (replay), live, record, or patch.",
    17	    )
    18	
    19	
    20	@pytest.fixture
    21	def llm_mode(request: pytest.FixtureRequest) -> str:
    22	    return request.config.getoption("--llm-mode")  # type: ignore[return-value]
    23	
    24	
    25	@pytest.fixture
    26	async def cassette_llm(request: pytest.FixtureRequest, llm_mode: str):
    27	    """Yield a CassetteLLMClient configured for the current --llm-mode."""
    28	    from app.llm.cassette import CassetteLLMClient
    29	
    30	    cls_name = request.node.cls.__name__ if request.node.cls else "_noclass"
    31	    test_name = request.node.name
    32	    cassette_path = _CASSETTES_DIR / f"{cls_name}__{test_name}.json"
    33	
    34	    if llm_mode == "mock":
    35	        if not cassette_path.exists():
    36	            pytest.skip(f"No cassette at {cassette_path} — run with --llm-mode=record first.")
    37	        client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
    38	        yield client
    39	        return
    40	
    41	    if llm_mode == "patch" and not cassette_path.exists():
    42	        pytest.skip(f"No cassette at {cassette_path} — run with --llm-mode=record first.")
    43	
    44	    api_key = os.environ.get("GROQ_API_KEY")
    45	    if not api_key:
    46	        pytest.skip("GROQ_API_KEY not set — cannot run in live/record/patch mode.")
    47	
    48	    from app.llm.client import LLMClient
    49	
    50	    real_client = LLMClient(groq_api_key=api_key)
    51	    client = CassetteLLMClient(mode=llm_mode, cassette_path=cassette_path, real_client=real_client)
    52	    yield client
    53	    client.save()
```

Cassette naming convention: `{ClassName}__{test_name}.json`. In mock mode (CI), missing cassettes cause a `pytest.skip` — tests degrade gracefully rather than failing. For record/patch, `GROQ_API_KEY` must be set.

---

## PART 4: SRS Engine (FSRS-5)

The spaced repetition system tracks what vocabulary the learner knows and when to review it. Production uses FSRS-5, a modern algorithm that replaced the prototype's custom scheduler.

### 4.1 FSRS-5 Scheduling Algorithm

```bash
cat -n backend/app/srs/fsrs.py
```

```output
     1	"""FSRS-5 spaced repetition scheduling algorithm.
     2	
     3	Reference: https://github.com/open-spaced-repetition/fsrs5
     4	"""
     5	
     6	from __future__ import annotations
     7	
     8	import math
     9	from dataclasses import dataclass
    10	from datetime import date, timedelta
    11	
    12	from app.models.srs_item import Direction, Rating, SRSItem, SRSState
    13	
    14	# FSRS-5 default parameters (w vector, 19 values)
    15	_DEFAULT_WEIGHTS: tuple[float, ...] = (
    16	    0.4072,  # w0: initial stability for Again
    17	    1.1829,  # w1: initial stability for Hard
    18	    3.1262,  # w2: initial stability for Good
    19	    15.4722,  # w3: initial stability for Easy
    20	    7.2102,  # w4: initial difficulty
    21	    0.5316,  # w5: initial difficulty decay
    22	    1.0651,  # w6: difficulty mean-reversion weight
    23	    0.0589,  # w7: difficulty update weight
    24	    1.5330,  # w8: stability increase factor
    25	    0.1544,  # w9: stability increase decay
    26	    1.0050,  # w10: stability increase R-factor
    27	    1.9767,  # w11: lapse stability factor
    28	    0.0967,  # w12: lapse stability difficulty decay
    29	    0.2573,  # w13: lapse stability S-factor
    30	    2.2930,  # w14: lapse stability R-factor
    31	    0.5100,  # w15: hard penalty
    32	    2.9898,  # w16: easy bonus
    33	    0.5100,  # w17: (unused in v5)
    34	    0.4350,  # w18: (unused in v5)
    35	)
    36	
    37	DECAY = -0.5
    38	FACTOR = 19 / 81  # = 0.234...
    39	
    40	
    41	@dataclass(frozen=True)
    42	class FSRSParams:
    43	    """FSRS scheduling parameters (weights + desired retention)."""
    44	
    45	    weights: tuple[float, ...]  # 19 floats for FSRS-5
    46	    desired_retention: float = 0.9
    47	
    48	    def __post_init__(self) -> None:
    49	        if len(self.weights) != 19:
    50	            raise ValueError(f"FSRSParams requires exactly 19 weights, got {len(self.weights)}")
    51	
    52	
    53	DEFAULT_FSRS5_PARAMS = FSRSParams(weights=_DEFAULT_WEIGHTS)
    54	
    55	
    56	def _forgetting_curve(elapsed_days: float, stability: float) -> float:
    57	    """Retrievability at elapsed_days given stability."""
    58	    return (1 + FACTOR * elapsed_days / stability) ** DECAY
    59	
    60	
    61	def _next_interval(stability: float, desired_retention: float) -> int:
    62	    """Days until next review at the given desired_retention."""
    63	    interval = stability / FACTOR * (desired_retention ** (1 / DECAY) - 1)
    64	    return max(1, min(round(interval), 36500))
    65	
    66	
    67	def _init_stability(rating: Rating, w: tuple[float, ...]) -> float:
    68	    return w[rating.value - 1]
    69	
    70	
    71	def _init_difficulty(rating: Rating, w: tuple[float, ...]) -> float:
    72	    d = w[4] - math.exp(w[5] * (rating.value - 1)) + 1
    73	    return max(1.0, min(10.0, d))
    74	
    75	
    76	def _next_difficulty(d: float, rating: Rating, w: tuple[float, ...]) -> float:
    77	    next_d = d - w[6] * (rating.value - 3)
    78	    # Mean-reversion toward w[4] (the initial difficulty for a "normal" item)
    79	    next_d = w[7] * w[4] + (1 - w[7]) * next_d
    80	    return max(1.0, min(10.0, next_d))
    81	
    82	
    83	def _next_stability_recall(d: float, s: float, r: float, rating: Rating, w: tuple[float, ...]) -> float:
    84	    hard_penalty = w[15] if rating == Rating.HARD else 1.0
    85	    easy_bonus = w[16] if rating == Rating.EASY else 1.0
    86	    return s * (
    87	        math.exp(w[8]) * (11 - d) * s ** (-w[9]) * (math.exp((1 - r) * w[10]) - 1) * hard_penalty * easy_bonus + 1
    88	    )
    89	
    90	
    91	def _next_stability_lapse(d: float, s: float, r: float, w: tuple[float, ...]) -> float:
    92	    return w[11] * d ** (-w[12]) * ((s + 1) ** w[13] - 1) * math.exp((1 - r) * w[14])
    93	
    94	
    95	def schedule(
    96	    item: SRSItem,
    97	    rating: Rating,
    98	    review_date: date | None = None,
    99	    direction: Direction = Direction.RECOGNITION,
   100	    params: FSRSParams = DEFAULT_FSRS5_PARAMS,
   101	) -> SRSItem:
   102	    """Apply a review rating to the given direction of an SRSItem.
   103	
   104	    Updates only the specified direction; the other is left untouched.
   105	    Marks `dirty_fsrs=True` on the updated direction so the Anki-sync layer
   106	    can later push the change.
   107	    """
   108	    if review_date is None:
   109	        review_date = date.today()
   110	
   111	    from dataclasses import replace
   112	
   113	    w = params.weights
   114	    prev = item.directions[direction]
   115	
   116	    if prev.state == SRSState.NEW:
   117	        new_stability = _init_stability(rating, w)
   118	        new_difficulty = _init_difficulty(rating, w)
   119	        new_reps = 1
   120	        new_lapses = prev.lapses
   121	        new_state = SRSState.LEARNING if rating == Rating.AGAIN else SRSState.REVIEW
   122	    else:
   123	        last = prev.last_review or review_date
   124	        elapsed = max(0, (review_date - last).days)
   125	        r = _forgetting_curve(elapsed, prev.stability)
   126	
   127	        if rating == Rating.AGAIN:
   128	            new_stability = _next_stability_lapse(prev.difficulty, prev.stability, r, w)
   129	            new_difficulty = _next_difficulty(prev.difficulty, rating, w)
   130	            new_reps = prev.reps + 1
   131	            new_lapses = prev.lapses + 1
   132	            new_state = SRSState.RELEARNING
   133	        else:
   134	            new_stability = _next_stability_recall(prev.difficulty, prev.stability, r, rating, w)
   135	            new_difficulty = _next_difficulty(prev.difficulty, rating, w)
   136	            new_reps = prev.reps + 1
   137	            new_lapses = prev.lapses
   138	            new_state = SRSState.REVIEW
   139	
   140	    new_stability = max(0.1, new_stability)
   141	    new_difficulty = max(1.0, min(10.0, new_difficulty))
   142	    interval = _next_interval(new_stability, params.desired_retention)
   143	    new_due = review_date + timedelta(days=interval)
   144	
   145	    new_dir = replace(
   146	        prev,
   147	        stability=new_stability,
   148	        difficulty=new_difficulty,
   149	        due_date=new_due,
   150	        reps=new_reps,
   151	        lapses=new_lapses,
   152	        state=new_state,
   153	        last_review=review_date,
   154	        dirty_fsrs=True,
   155	        last_rating=rating.value,
   156	    )
   157	    new_directions = dict(item.directions)
   158	    new_directions[direction] = new_dir
   159	    return SRSItem(
   160	        syntactic_unit=item.syntactic_unit,
   161	        directions=new_directions,
   162	        guid=item.guid,
   163	        anki_note_id=item.anki_note_id,
   164	    )

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
State: review, Stability: 3.13, Due: 2026-03-28

After EASY rating:
State: review, Stability: 24.16, Due: 2026-04-21

After AGAIN rating (forgot):
State: relearning, Stability: 0.92, Lapses: 1, Due: 2026-03-29
```

Notice the progression: GOOD → stability 3.13 (review in 3 days), EASY → stability 24.16 (review in 24 days), but AGAIN → stability drops to 0.92 with a lapse recorded and the item enters RELEARNING state.

### 4.2 SRS Database

```bash
grep -n "def \|class " backend/app/srs/database.py
```

```output
122:class SRSDatabase:
128:    def close(self) -> None:
134:    def __enter__(self) -> SRSDatabase:
137:    def __exit__(self, *_) -> None:
140:    def __init__(self, db_path: str = ":memory:") -> None:
155:    def _init_schema(self, conn: sqlite3.Connection) -> None:
167:    def _file_conn(self):
178:    def _get_conn(self):
188:    def _commit(self, conn: sqlite3.Connection) -> None:
196:    def begin_transaction(self, dry_run: bool = False):
232:    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> None:
289:    def get_untranslated_collocations(self) -> list[tuple[str, str]]:
297:    def backfill_translations(self, glosses: dict[str, str]) -> int:
315:    def update_direction(
362:    def update_collocation(self, item: SRSItem) -> None:
382:    def record_violation(
394:    def _load_directions(self, conn: sqlite3.Connection, collocation_id: int) -> dict[Direction, DirectionState]:
419:    def _row_to_item(self, conn: sqlite3.Connection, row: sqlite3.Row) -> SRSItem:
444:    def get_collocation(self, text: str) -> SRSItem | None:
451:    def get_collocation_by_guid(self, guid: str) -> SRSItem | None:
458:    def get_collocation_by_anki_note_id(self, anki_note_id: int) -> SRSItem | None:
465:    def get_collocation_by_lemma(self, lemma: str) -> SRSItem | None:
472:    def get_collocation_by_lemma_with_id(self, lemma: str) -> tuple[int, SRSItem] | None:
479:    def get_collocations_for_language(
491:    def get_due_collocations(
511:    def get_new_collocations(
529:    def get_due_items(
550:    def get_new_items(
569:    def update_direction_by_id(self, row_id: int, direction: Direction, state: DirectionState) -> None:
577:    def list_collocations_reviewed_today(self, today: date) -> set[int]:
586:    def get_image_filename(self, collocation_id: int) -> str | None:
595:    def get_audio_filename(self, collocation_id: int) -> str | None:
605:    def get_collocation_by_id(self, row_id: int) -> tuple[int, SRSItem, str] | None:
612:    def update_collocation_fields(self, row_id: int, *, text: str, translation: str) -> None:
644:    def delete_collocation(self, row_id: int) -> None:
652:    def delete_collocations(self, row_ids: list[int]) -> int:
666:    def reset_collocation(self, row_id: int, direction: Direction | None = None) -> None:
696:    def set_state_by_id(
720:    def set_suspended(
756:    def list_collocations(
811:    def get_violations(self, collocation_text: str) -> list[dict]:
819:    def count_collocations(self) -> int:
825:    def upsert_by_guid(
973:    def set_anki_ids(
996:    def add_media(
1018:    def find_media_by_anki_filename(self, anki_filename: str) -> dict[str, Any] | None:
1027:    def update_media_file(self, row_id: int, sha256: str, size_bytes: int) -> None:
1036:    def list_dirty(
1085:    def mark_direction_clean(self, guid: str, direction: Direction) -> None:
1102:    def count_new_available(self) -> int:
1107:    def count_due_today_total(self, today: date) -> int:
1120:    def count_due_collocations(
1138:    def record_sync_conflict(
1159:    def list_sync_conflicts(self) -> list[dict]:
1164:    def enqueue_pending_revlog(
1186:    def drain_pending_revlog(self) -> list[dict]:
1194:    def set_anki_state_cache(self, key: str, value: str) -> None:
1206:    def set_anki_state_cache_raw(self, key: str, value: str, updated_at: str) -> None:
1220:    def get_anki_state_cache(self, key: str) -> tuple[str, str] | None:
1231:    def set_dirty_fields(self, guid: str, fields_str: str) -> None:
1240:    def get_dirty_fields(self, guid: str) -> str:
1249:    def update_collocation_for_sync(
1265:    def list_items_without_anki_note(self) -> list[tuple[str, SRSItem]]:
1271:    def list_dirty_field_edits(self) -> list[tuple[str, int | None, str, SRSItem]]:

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

**Admin methods (powering `/admin/srs`):**

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

After reviewing Dober dan: state=review, next due=2026-03-28
Remaining new: 2
Due for review on 2026-03-28: 1
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
     3	PostGenerationFeedback: identifies which collocations appear in a generated story.
     4	rating_from_input: maps explicit rating strings or implicit signal strings to FSRS ratings.
     5	"""
     6	
     7	from __future__ import annotations
     8	
     9	from app.models.srs_item import Rating
    10	
    11	_SIGNAL_MAP: dict[str, Rating] = {
    12	    "no_help": Rating.GOOD,
    13	    "slowdown": Rating.HARD,
    14	    "translation_request": Rating.AGAIN,
    15	    "fast_forward": Rating.EASY,
    16	}
    17	
    18	_RATING_MAP: dict[str, Rating] = {
    19	    "again": Rating.AGAIN,
    20	    "hard": Rating.HARD,
    21	    "good": Rating.GOOD,
    22	    "easy": Rating.EASY,
    23	}
    24	
    25	
    26	def rating_from_input(rating: str | None = None, signal: str | None = None) -> Rating:
    27	    """Convert explicit rating string or implicit signal string to a Rating enum.
    28	
    29	    Exactly one of rating/signal must be provided; raises ValueError otherwise.
    30	    rating accepts 'again'|'hard'|'good'|'easy' (case-insensitive).
    31	    signal delegates to the existing _SIGNAL_MAP.
    32	    """
    33	    if (rating is None) == (signal is None):
    34	        raise ValueError("Provide exactly one of rating or signal, not both (or neither).")
    35	    if rating is not None:
    36	        key = rating.lower()
    37	        if key not in _RATING_MAP:
    38	            raise ValueError(f"Unknown rating {rating!r}. Valid: {list(_RATING_MAP)}")
    39	        return _RATING_MAP[key]
    40	    if signal not in _SIGNAL_MAP:
    41	        raise ValueError(f"Unknown signal {signal!r}. Valid: {list(_SIGNAL_MAP)}")
    42	    return _SIGNAL_MAP[signal]
    43	
    44	
    45	class PostGenerationFeedback:
    46	    """Identifies which provided collocations were actually used in a story."""
    47	
    48	    def find_used_collocations(self, provided: list[str], story_text: str) -> list[str]:
    49	        """Return the subset of provided collocations that appear in story_text.
    50	
    51	        Matching is case-insensitive. Only collocations that appear as
    52	        substrings in the story are marked as used.
    53	        """
    54	        story_lower = story_text.lower()
    55	        return [c for c in provided if c.lower() in story_lower]

```

```bash
grep -n "class CollocationSelector\|def score\|def select" backend/app/srs/selector.py
```

```output
11:class CollocationSelector:
17:    def score(self, item: SRSItem) -> float:
54:    def select(
```

`rating_from_input(rating=..., signal=...)` is the unified entry point. Pass `rating="good"` for explicit four-button feedback (the `/review` UI's path) or `signal="translation_request"` for implicit signals from the player. Skipping ahead means they know it (EASY), asking for a translation means they forgot (AGAIN). `PostGenerationFeedback` is unchanged: it checks which collocations the LLM actually used in a generated story — useful for tracking whether the content engine is following the curriculum.

The `CollocationSelector` scores items using the weighted formula from the strategy model (SRS readiness 40%, language quality 30%, pedagogical value 20%, diversity 10%), then selects the best mix of new and review items for the next lesson. Note: it is currently **direction-agnostic** — it scores using the recognition-direction shim fields on `SRSItem` and treats each row as a single unit. The unified review queue at `/api/srs/review-queue` (PART 13) is where direction-aware ordering actually happens; the selector is preserved for the older curriculum-driven path.

---

### 4.4 Per-Word SRS Tracking

Production added per-word SRS tracking on top of the per-collocation tracking. The pipeline lemmatizes every L2 word in a generated lesson, looks each lemma up in the SRS database, and exposes the state to the frontend so the UI can highlight unknown words. Three small modules wire this together.

**Lemmatizer** — a thin Protocol with a `LowercaseLemmatizer` default. Real Slovene lemmatization (e.g. via `stanza`) can be plugged in by satisfying the Protocol.

```bash
cat -n backend/app/srs/lemmatizer.py
```

```output
     1	"""Lemmatizer protocol and default implementation."""
     2
     3	from __future__ import annotations
     4
     5	from typing import Protocol, runtime_checkable
     6
     7
     8	@runtime_checkable
     9	class Lemmatizer(Protocol):
    10	    """Reduces a word to its canonical base form."""
    11
    12	    def lemmatize(self, word: str, language_code: str) -> str: ...
    13
    14
    15	class LowercaseLemmatizer:
    16	    """Simple lemmatizer that lowercases the word.
    17
    18	    Language-agnostic default. Replace with a language-specific lemmatizer
    19	    (e.g. stanza for Slovene) for proper conjugation/declension collapsing.
    20	    """
    21
    22	    def lemmatize(self, word: str, language_code: str) -> str:
    23	        return word.lower()
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
cat -n backend/app/srs/transcript.py
```

```output
     1	"""Transcript extraction service for SRS word-level tracking."""
     2
     3	from __future__ import annotations
     4
     5	from dataclasses import dataclass, field
     6
     7	from app.models.lesson import KeyPhraseInfo, Lesson, SectionType
     8	from app.srs.database import SRSDatabase
     9	from app.srs.lemmatizer import Lemmatizer
    10	from app.srs.tokenizer import tokenize
    11
    12
    13	@dataclass
    14	class WordToken:
    15	    """A single word in the transcript with its SRS state."""
    16
    17	    surface: str  # original word as it appears in text (punctuation stripped)
    18	    lemma: str  # canonical base form (lowercased)
    19	    srs_state: str  # "unknown"|"new"|"learning"|"review"|"relearning"
    20
    21
    22	@dataclass
    23	class DialogueLine:
    24	    """A single speaker line in the dialogue."""
    25
    26	    role: str
    27	    words: list[WordToken] = field(default_factory=list)
    28
    29
    30	@dataclass
    31	class TranscriptData:
    32	    """Full lesson transcript with per-word SRS state snapshot."""
    33
    34	    key_phrases: list[KeyPhraseInfo] = field(default_factory=list)
    35	    dialogue_lines: list[DialogueLine] = field(default_factory=list)
    36
    37
    38	def extract_transcript(
    39	    lesson: Lesson,
    40	    db: SRSDatabase,
    41	    lemmatizer: Lemmatizer,
    42	) -> TranscriptData:
    43	    """Extract transcript data from a lesson with current SRS states.
    44
    45	    Only processes the NATURAL_SPEED section, filtering to L2 phrases only.
    46	    """
    47	    natural_speed = next(
    48	        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
    49	        None,
    50	    )
    51
    52	    dialogue_lines: list[DialogueLine] = []
    53
    54	    if natural_speed is not None:
    55	        for phrase in natural_speed.phrases:
    56	            if phrase.language_code != lesson.language_code:
    57	                continue  # skip narrator/English lines
    58
    59	            tokens = tokenize(phrase.text)
    60	            words: list[WordToken] = []
    61	            for surface in tokens:
    62	                lemma = lemmatizer.lemmatize(surface, lesson.language_code)
    63	                item = db.get_collocation_by_lemma(lemma)
    64	                srs_state = item.state.value if item is not None else "unknown"
    65	                words.append(WordToken(surface=surface, lemma=lemma, srs_state=srs_state))
    66
    67	            dialogue_lines.append(DialogueLine(role=phrase.role, words=words))
    68
    69	    return TranscriptData(
    70	        key_phrases=list(lesson.key_phrases),
    71	        dialogue_lines=dialogue_lines,
    72	    )
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
cat -n backend/app/generation/prompts.py
```

```output
     1	"""Prompt builder for curriculum and story generation.
     2	
     3	Language-aware: instructions adjust based on the target language.
     4	All prompts request JSON responses for deterministic parsing.
     5	"""
     6	
     7	from __future__ import annotations
     8	
     9	from app.models.language import Language
    10	
    11	_CURRICULUM_PROMPT_TEMPLATE = """\
    12	You are generating a {num_days}-day language learning curriculum.
    13	
    14	Topic: {topic}
    15	Target language: {language_name} ({language_code})
    16	CEFR level: {cefr_level}
    17	
    18	Respond with a JSON object matching this schema exactly:
    19	{{
    20	  "days": [
    21	    {{
    22	      "day": 1,
    23	      "title": "Short lesson title",
    24	      "focus": "Main focus area for this day",
    25	      "collocations": ["phrase one", "phrase two", "phrase three"],
    26	      "learning_objective": "Specific skill the learner will practice",
    27	      "story_guidance": "Brief setting/scenario hint for audio story generation"
    28	    }}
    29	  ]
    30	}}
    31	
    32	Requirements:
    33	- Respond with ONLY the JSON object, no markdown fences, no preamble
    34	- All collocations must be in {language_name} ({language_code}) using {script} script
    35	- 3–8 collocations per day (natural 1–5 word phrases)
    36	- Days should progress from simpler to more complex vocabulary
    37	- Make collocations practical for real-world use of the topic
    38	"""
    39	
    40	_SYSTEM_PROMPT_TEMPLATE = """\
    41	You are an expert language curriculum designer specializing in {language_name}.
    42	You create structured, practical curricula for learners studying {language_name} ({language_code}).
    43	
    44	Language details:
    45	- ISO code: {language_code}
    46	- Script: {script}
    47	- Native name: {native_name}
    48	
    49	When generating collocations, use authentic {language_name} as a native speaker would.
    50	Focus on practical, conversational phrases appropriate for the learner's CEFR level.
    51	"""
    52	
    53	
    54	class PromptBuilder:
    55	    """Builds prompts for LLM-powered curriculum and story generation."""
    56	
    57	    def build_system_prompt(self, language: Language) -> str:
    58	        """Build the system prompt for a given target language."""
    59	        return _SYSTEM_PROMPT_TEMPLATE.format(
    60	            language_name=language.name,
    61	            language_code=language.code,
    62	            script=language.script,
    63	            native_name=language.native_name,
    64	        )
    65	
    66	    def build_curriculum_prompt(
    67	        self,
    68	        topic: str,
    69	        language: Language,
    70	        cefr_level: str,
    71	        num_days: int,
    72	    ) -> str:
    73	        """Build the user prompt for curriculum generation."""
    74	        return _CURRICULUM_PROMPT_TEMPLATE.format(
    75	            topic=topic,
    76	            language_name=language.name,
    77	            language_code=language.code,
    78	            script=language.script,
    79	            cefr_level=cefr_level,
    80	            num_days=num_days,
    81	        )
```

Prompts are language-aware templates that inject the `Language` model fields. The curriculum prompt requests strict JSON output — no markdown fences, no preamble — so the response can be parsed directly. The system prompt establishes the LLM as a language curriculum expert who knows the target language natively.

In addition to the `PromptBuilder` class, `prompts.py` now owns all story-generation prompt content: `SYSTEM_PROMPT` (shared system prompt for all story generations), `STORY_PROMPT_WIDER_TEMPLATE` / `STORY_PROMPT_DEEPER_TEMPLATE` (strategy-specific user prompts), and `get_strategy_prompt(strategy)` — returns the correct template or raises `ValueError` on unknown strategy.

Here is what the actual prompt looks like for a Slovene curriculum:

```bash
cd backend && uv run python -c "
from app.generation.prompts import PromptBuilder
from app.models.language import Language

pb = PromptBuilder()
sl = Language.slovene()
print(\"=== System Prompt ===\")
print(pb.build_system_prompt(sl))
print(\"=== Curriculum Prompt ===\")
print(pb.build_curriculum_prompt(\"Travel in Slovenia\", sl, \"A1\", 3))
"
```

```output
=== System Prompt ===
You are an expert language curriculum designer specializing in Slovene.
You create structured, practical curricula for learners studying Slovene (sl).

Language details:
- ISO code: sl
- Script: latin
- Native name: slovenščina

When generating collocations, use authentic Slovene as a native speaker would.
Focus on practical, conversational phrases appropriate for the learner's CEFR level.

=== Curriculum Prompt ===
You are generating a 3-day language learning curriculum.

Topic: Travel in Slovenia
Target language: Slovene (sl)
CEFR level: A1

Respond with a JSON object matching this schema exactly:
{
  "days": [
    {
      "day": 1,
      "title": "Short lesson title",
      "focus": "Main focus area for this day",
      "collocations": ["phrase one", "phrase two", "phrase three"],
      "learning_objective": "Specific skill the learner will practice",
      "story_guidance": "Brief setting/scenario hint for audio story generation"
    }
  ]
}

Requirements:
- Respond with ONLY the JSON object, no markdown fences, no preamble
- All collocations must be in Slovene (sl) using latin script
- 3–8 collocations per day (natural 1–5 word phrases)
- Days should progress from simpler to more complex vocabulary
- Make collocations practical for real-world use of the topic

```

### 5.2 Curriculum Generator

```bash
cat -n backend/app/generation/curriculum.py
```

```output
     1	"""Curriculum generator: LLM + PromptBuilder → Curriculum model."""
     2	
     3	from __future__ import annotations
     4	
     5	import json
     6	import logging
     7	import uuid
     8	
     9	from app.generation.prompts import PromptBuilder
    10	from app.models.curriculum import Curriculum, CurriculumDay
    11	from app.models.language import Language
    12	
    13	logger = logging.getLogger(__name__)
    14	
    15	
    16	class CurriculumGenerationError(Exception):
    17	    pass
    18	
    19	
    20	class CurriculumGenerator:
    21	    """Generates a Curriculum from a topic using the LLM client."""
    22	
    23	    def __init__(self, llm_client) -> None:
    24	        self._llm = llm_client
    25	        self._prompt_builder = PromptBuilder()
    26	
    27	    async def generate(
    28	        self,
    29	        topic: str,
    30	        language: Language,
    31	        cefr_level: str,
    32	        num_days: int = 5,
    33	    ) -> Curriculum:
    34	        """Generate a curriculum for the given topic.
    35	
    36	        Args:
    37	            topic: Learning topic (e.g., "ordering coffee in Ljubljana")
    38	            language: Target language configuration
    39	            cefr_level: CEFR level string (e.g., "A2", "B1")
    40	            num_days: Number of curriculum days to generate
    41	
    42	        Returns:
    43	            Parsed Curriculum model
    44	        """
    45	        system_prompt = self._prompt_builder.build_system_prompt(language)
    46	        user_prompt = self._prompt_builder.build_curriculum_prompt(
    47	            topic=topic,
    48	            language=language,
    49	            cefr_level=cefr_level,
    50	            num_days=num_days,
    51	        )
    52	
    53	        logger.info("Generating %d-day curriculum for topic %r (%s)", num_days, topic, language.code)
    54	        raw = await self._llm.complete(user_prompt, system_prompt=system_prompt, temperature=0.7, max_tokens=4096)
    55	
    56	        return self._parse_response(raw, topic=topic, language=language, cefr_level=cefr_level)
    57	
    58	    def _parse_response(self, raw: str, *, topic: str, language: Language, cefr_level: str) -> Curriculum:
    59	        """Parse the LLM JSON response into a Curriculum."""
    60	        try:
    61	            data = json.loads(raw)
    62	        except json.JSONDecodeError as e:
    63	            raise CurriculumGenerationError(f"LLM returned invalid JSON: {e}\nRaw: {raw[:200]}") from e
    64	
    65	        days_data = data.get("days", [])
    66	        if not days_data:
    67	            raise CurriculumGenerationError(f"LLM response missing 'days' key: {raw[:200]}")
    68	
    69	        days = []
    70	        for d in days_data:
    71	            days.append(
    72	                CurriculumDay(
    73	                    day=d["day"],
    74	                    title=d.get("title", f"Day {d['day']}"),
    75	                    focus=d.get("focus", ""),
    76	                    collocations=d.get("collocations", []),
    77	                    learning_objective=d.get("learning_objective", ""),
    78	                    story_guidance=d.get("story_guidance", ""),
    79	                )
    80	            )
    81	
    82	        return Curriculum(
    83	            id=str(uuid.uuid4()),
    84	            topic=topic,
    85	            language_code=language.code,
    86	            cefr_level=cefr_level,
    87	            days=days,
    88	        )
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
     5	import json
     6	import logging
     7
     8	from app.generation.prompts import SYSTEM_PROMPT, get_strategy_prompt
     9	from app.generation.section_builder import (
    10	    build_key_phrases_section,
    11	    build_natural_speed_section,
    12	    build_slow_speed_section,
    13	    build_translated_section,
    14	)
    15	from app.models.curriculum import CurriculumDay
    16	from app.models.language import Language
    17	from app.models.lesson import KeyPhraseInfo, Lesson
    18	from app.models.strategy import ContentStrategy
    19
    20	logger = logging.getLogger(__name__)
    21
    22
    23	class StoryGenerationError(Exception):
    24	    pass
    25
    26
    27	class StoryGenerator:
    28	    """Generates a Lesson from a CurriculumDay using the LLM client."""
    29
    30	    def __init__(self, llm_client) -> None:
    31	        self._llm = llm_client
    32
    33	    async def generate(
    34	        self,
    35	        curriculum_day: CurriculumDay,
    36	        language: Language,
    37	        strategy: ContentStrategy,
    38	    ) -> Lesson:
    39	        """Generate a Lesson for the given curriculum day.
    40
    41	        Args:
    42	            curriculum_day: Day specification including collocations and objectives.
    43	            language: Target language configuration.
    44	            strategy: WIDER or DEEPER content strategy.
    45
    46	        Returns:
    47	            Parsed Lesson with 4 Pimsleur sections built mechanically from LLM JSON.
    48	        """
    49	        system_prompt = SYSTEM_PROMPT.format(
    50	            language_name=language.name,
    51	            language_code=language.code,
    52	        )
    53
    54	        new_collocations = "\n".join(f"- {c}" for c in curriculum_day.collocations)
    55	        user_prompt_template = get_strategy_prompt(strategy)
    56	        user_prompt = user_prompt_template.format(
    57	            language_name=language.name,
    58	            language_code=language.code,
    59	            learning_objective=curriculum_day.learning_objective,
    60	            focus=curriculum_day.focus,
    61	            story_guidance=curriculum_day.story_guidance,
    62	            new_collocations=new_collocations,
    63	            review_collocations="(none yet)",
    64	            source_day_transcript="(not available)",
    65	        )
    66
    67	        logger.info("Generating story for day %d (%s)", curriculum_day.day, strategy.value)
    68	        raw = await self._llm.complete(user_prompt, system_prompt=system_prompt, temperature=0.7, max_tokens=8192)
    69	        return self._parse_response(raw, language=language)
    70
    71	    def _parse_response(self, raw: str, language: Language) -> Lesson:
    72	        try:
    73	            data = json.loads(raw)
    74	        except json.JSONDecodeError as e:
    75	            raise StoryGenerationError(f"LLM returned invalid JSON: {e}") from e
    76
    77	        key_phrases = data.get("key_phrases", [])
    78	        scenes = data.get("scenes", [])
    79	        title = data.get("title", "Lesson")
    80
    81	        if not key_phrases and not scenes:
    82	            raise StoryGenerationError("LLM response missing 'key_phrases' and 'scenes'")
    83
    84	        narrator_voice = language.tts_voice_map.get("narrator", "en-US-GuyNeural")
    85
    86	        sections = [
    87	            build_key_phrases_section(key_phrases, language.tts_voice_map, narrator_voice, language.code),
    88	            build_natural_speed_section(scenes, language.tts_voice_map, narrator_voice, language.code),
    89	            build_slow_speed_section(scenes, language.tts_voice_map, narrator_voice, language.code),
    90	            build_translated_section(scenes, language.tts_voice_map, narrator_voice, language.code),
    91	        ]
    92
    93	        kp_infos = [KeyPhraseInfo(phrase=kp["phrase"], translation=kp["translation"]) for kp in key_phrases]
    94
    95	        return Lesson(
    96	            title=title,
    97	            language_code=language.code,
    98	            sections=sections,
    99	            narrator_voice=narrator_voice,
   100	            key_phrases=kp_infos,
   101	        )
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
     9	from app.generation.syllabify import syllabify_slovene_word
    10	from app.models.lesson import Phrase, Section, SectionType
    11
    12	# Type aliases for plain-dict inputs from parsed LLM JSON
    13	KeyPhrase = dict  # {"phrase": str, "translation": str}
    14	DialogueLine = dict  # {"speaker": str, "text": str, "translation": str}
    15	Scene = dict  # {"label": str, "lines": list[DialogueLine]}
    16
    17	# Narrator-spoken section titles matching the demo format
    18	SECTION_TITLES: dict[SectionType, str] = {
    19	    SectionType.KEY_PHRASES: "Key Phrases",
    20	    SectionType.NATURAL_SPEED: "Natural Speed",
    21	    SectionType.SLOW_SPEED: "Slow Speed",
    22	    SectionType.TRANSLATED: "Translated",
    23	}
    24
    25
    26	def _resolve_voice(speaker: str, l2_voice_map: dict[str, str], narrator_voice: str) -> str:
    27	    return l2_voice_map.get(speaker, l2_voice_map.get("female-1", narrator_voice))
    28
    29
    30	def build_word_breakdown(phrase_text: str) -> list[str]:
    31	    """Build a Pimsleur-style syllable-level backward buildup sequence.
    32
    33	    Processes words right-to-left. For each multi-syllable word the syllables
    34	    are presented backward then progressively rebuilt before moving to the
    35	    preceding word. Single-syllable words are presented as-is.
    36
    37	    The sequence always starts with the full phrase and ends with the full
    38	    phrase repeated twice.
    39
    40	    Examples:
    41	        "dan"     → ["dan", "dan"]
    42	        "prosim"  → ["prosim", "sim", "pro", "prosim", "prosim"]
    43	        "dober dan" → ["dober dan", "dan", "ber", "do", "dober",
    44	                        "dober dan", "dober dan"]
    45	    """
    46	    phrase = " ".join(phrase_text.strip().split())
    47	    words = phrase.split()
    48	    if not words:
    49	        return []
    50
    51	    breakdown: list[str] = [phrase]
    52
    53	    if len(words) == 1:
    54	        syllables = syllabify_slovene_word(words[0])
    55	        if len(syllables) <= 1:
    56	            breakdown.append(phrase)
    57	            return breakdown
    58	        for i in range(len(syllables) - 1, -1, -1):
    59	            breakdown.append(syllables[i])
    60	            if i < len(syllables) - 1:
    61	                breakdown.append("".join(syllables[i:]))
    62	        breakdown.append(phrase)
    63	        return breakdown
    64
    65	    for word_index in range(len(words) - 1, -1, -1):
    66	        word = words[word_index]
    67	        syllables = syllabify_slovene_word(word)
    68
    69	        if len(syllables) > 1:
    70	            for i in range(len(syllables) - 1, -1, -1):
    71	                breakdown.append(syllables[i])
    72	                if i < len(syllables) - 1:
    73	                    breakdown.append("".join(syllables[i:]))
    74	                elif (
    75	                    i == 0
    76	                ):  # pragma: no cover — unreachable: when len(syllables)>1 and i==0, the `i < len-1` branch always fires
    77	                    breakdown.append("".join(syllables))
    78	        else:
    79	            breakdown.append(word)
    80
    81	        if word_index < len(words) - 1:
    82	            partial = " ".join(words[word_index:])
    83	            if partial != phrase:
    84	                breakdown.append(partial)
    85
    86	        if word_index == 0:
    87	            breakdown.append(phrase)
    88
    89	    breakdown.append(phrase)
    90	    return breakdown
    91
    92
    93	def build_key_phrases_section(
    94	    key_phrases: list[KeyPhrase],
    95	    l2_voice_map: dict[str, str],
    96	    narrator_voice: str,
    97	    l2_code: str,
    98	) -> Section:
    99	    """Build the KEY_PHRASES section.
   100
   101	    For each phrase:
   102	    1. L2 phrase (female-1)
   103	    2. Narrator translation
   104	    3. L2 phrase repeat (female-1)
   105	    4. Word breakdown steps (female-1)
   106	    """
   107	    female_1_voice = l2_voice_map.get("female-1", narrator_voice)
   108	    phrases: list[Phrase] = [
   109	        Phrase(
   110	            text=SECTION_TITLES[SectionType.KEY_PHRASES], voice_id=narrator_voice, language_code="en", role="narrator"
   111	        )
   112	    ]
   113
   114	    for kp in key_phrases:
   115	        phrase_text = kp["phrase"]
   116	        translation = kp["translation"]
   117
   118	        phrases.append(Phrase(text=phrase_text, voice_id=female_1_voice, language_code=l2_code))
   119	        phrases.append(Phrase(text=translation, voice_id=narrator_voice, language_code="en", role="narrator"))
   120	        for step in build_word_breakdown(phrase_text):
   121	            phrases.append(Phrase(text=step, voice_id=female_1_voice, language_code=l2_code))
   122
   123	    return Section(section_type=SectionType.KEY_PHRASES, phrases=phrases)
   124
   125
   126	def build_natural_speed_section(
   127	    scenes: list[Scene],
   128	    l2_voice_map: dict[str, str],
   129	    narrator_voice: str,
   130	    l2_code: str,
   131	) -> Section:
   132	    """Build the NATURAL_SPEED section with scene labels and multi-speaker dialogue."""
   133	    phrases: list[Phrase] = [
   134	        Phrase(
   135	            text=SECTION_TITLES[SectionType.NATURAL_SPEED], voice_id=narrator_voice, language_code="en", role="narrator"
   136	        )
   137	    ]
   138
   139	    for scene in scenes:
   140	        phrases.append(Phrase(text=scene["label"], voice_id=narrator_voice, language_code="en", role="narrator"))
   141	        for line in scene.get("lines", []):
   142	            speaker = line["speaker"].lower()
   143	            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
   144	            phrases.append(Phrase(text=line["text"], voice_id=voice_id, language_code=l2_code, role=speaker))
   145
   146	    return Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)
   147
   148
   149	def build_slow_speed_section(
   150	    scenes: list[Scene],
   151	    l2_voice_map: dict[str, str],
   152	    narrator_voice: str,
   153	    l2_code: str,
   154	) -> Section:
   155	    """Build the SLOW_SPEED section — mirrors NATURAL_SPEED with '...' between words."""
   156	    phrases: list[Phrase] = [
   157	        Phrase(
   158	            text=SECTION_TITLES[SectionType.SLOW_SPEED], voice_id=narrator_voice, language_code="en", role="narrator"
   159	        )
   160	    ]
   161
   162	    for scene in scenes:
   163	        phrases.append(Phrase(text=scene["label"], voice_id=narrator_voice, language_code="en", role="narrator"))
   164	        for line in scene.get("lines", []):
   165	            speaker = line["speaker"].lower()
   166	            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
   167	            slowed = " ... ".join(line["text"].split())
   168	            phrases.append(Phrase(text=slowed, voice_id=voice_id, language_code=l2_code, role=speaker))
   169
   170	    return Section(section_type=SectionType.SLOW_SPEED, phrases=phrases)
   171
   172
   173	def build_translated_section(
   174	    scenes: list[Scene],
   175	    l2_voice_map: dict[str, str],
   176	    narrator_voice: str,
   177	    l2_code: str,
   178	) -> Section:
   179	    """Build the TRANSLATED section — every L2 line followed by narrator translation."""
   180	    phrases: list[Phrase] = [
   181	        Phrase(
   182	            text=SECTION_TITLES[SectionType.TRANSLATED], voice_id=narrator_voice, language_code="en", role="narrator"
   183	        )
   184	    ]
   185
   186	    for scene in scenes:
   187	        phrases.append(Phrase(text=scene["label"], voice_id=narrator_voice, language_code="en", role="narrator"))
   188	        for line in scene.get("lines", []):
   189	            speaker = line["speaker"].lower()
   190	            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
   191	            phrases.append(Phrase(text=line["text"], voice_id=voice_id, language_code=l2_code, role=speaker))
   192	            phrases.append(
   193	                Phrase(text=line["translation"], voice_id=narrator_voice, language_code="en", role="narrator")
   194	            )
   195
   196	    return Section(section_type=SectionType.TRANSLATED, phrases=phrases)
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
     1	"""Slovene syllabification for Pimsleur breakdown generation."""
     2
     3	from __future__ import annotations
     4
     5	_VOWELS = frozenset("aeiou")
     6
     7	# Valid consonant clusters that can begin a Slovene syllable.
     8	# Onset maximization: the longest matching suffix of a consonant cluster
     9	# that appears here goes with the following vowel.
    10	_VALID_ONSETS = frozenset(
    11	    [
    12	        # Three-consonant onsets
    13	        "str",
    14	        "spr",
    15	        "skl",
    16	        "štr",
    17	        "škl",
    18	        # Two-consonant onsets — stop + liquid
    19	        "pr",
    20	        "pl",
    21	        "br",
    22	        "bl",
    23	        "tr",
    24	        "dr",
    25	        "kr",
    26	        "kl",
    27	        "gr",
    28	        "gl",
    29	        "fr",
    30	        "fl",
    31	        # Two-consonant onsets — fricative + liquid / nasal
    32	        "vr",
    33	        "vl",
    34	        "sr",
    35	        "sl",
    36	        "zr",
    37	        "zl",
    38	        "šr",
    39	        "šl",
    40	        "žr",
    41	        "žl",
    42	        "čr",
    43	        "čl",
    44	        # Two-consonant onsets — obstruent sequences
    45	        "hv",
    46	        "st",
    47	        "sk",
    48	        "sp",
    49	        "šk",
    50	        "šp",
    51	        "št",
    52	        "šč",
    53	        "zg",
    54	        "zd",
    55	        "zm",
    56	        "zn",
    57	        "mn",
    58	        "gn",
    59	        "ps",
    60	        "pn",
    61	    ]
    62	)
    63
    64
    65	def syllabify_slovene_word(word: str) -> list[str]:
    66	    """Split a Slovene word into syllables.
    67
    68	    Uses onset-maximization: for a consonant cluster between two vowels the
    69	    longest suffix that is a recognised Slovene onset goes with the following
    70	    vowel; the remainder closes the preceding syllable.
    71
    72	    Single-vowel and no-vowel words (including syllabic-r words like "prst")
    73	    are returned as a single syllable.
    74
    75	    Args:
    76	        word: Word to syllabify (case-insensitive; returned lowercased).
    77
    78	    Returns:
    79	        List of syllables, lowercased.
    80	    """
    81	    word = word.lower().strip()
    82	    if not word:
    83	        return []
    84
    85	    vowel_positions = [i for i, ch in enumerate(word) if ch in _VOWELS]
    86
    87	    if len(vowel_positions) <= 1:
    88	        return [word]
    89
    90	    syllables: list[str] = []
    91	    start = 0
    92
    93	    for vi in range(len(vowel_positions) - 1):
    94	        curr_v = vowel_positions[vi]
    95	        next_v = vowel_positions[vi + 1]
    96	        cluster = word[curr_v + 1 : next_v]
    97
    98	        if len(cluster) == 0:
    99	            # Hiatus — split between adjacent vowels
   100	            syllables.append(word[start : curr_v + 1])
   101	            start = curr_v + 1
   102	        elif len(cluster) == 1:
   103	            # Single consonant → V-CV, consonant goes with following vowel
   104	            syllables.append(word[start : curr_v + 1])
   105	            start = curr_v + 1
   106	        else:
   107	            # Multiple consonants — find longest valid onset suffix
   108	            split = _onset_split(cluster, curr_v + 1)
   109	            syllables.append(word[start:split])
   110	            start = split
   111
   112	    syllables.append(word[start:])
   113	    return syllables
   114
   115
   116	def _onset_split(cluster: str, cluster_start: int) -> int:
   117	    """Return the index in the word where the onset begins.
   118
   119	    Tries progressively shorter suffixes of *cluster* (longest first) until a
   120	    valid onset is found or only one consonant remains.
   121	    """
   122	    for onset_start in range(len(cluster)):
   123	        candidate = cluster[onset_start:]
   124	        if len(candidate) == 1 or candidate in _VALID_ONSETS:
   125	            return cluster_start + onset_start
   126	    # Fallback (should not be reached): first consonant closes preceding syllable
   127	    return cluster_start + 1  # pragma: no cover
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
cat -n backend/app/generation/enforcer.py
```

```output
     1	"""Content enforcer: two-pass L1 → L2 replacement using SRS database.
     2	
     3	The replacement dictionary is fully dynamic — built from SRS collocations'
     4	translation fields. No hardcoded vocabulary.
     5	"""
     6	
     7	from __future__ import annotations
     8	
     9	import logging
    10	import re
    11	
    12	from app.srs.database import SRSDatabase
    13	
    14	logger = logging.getLogger(__name__)
    15	
    16	
    17	class ContentEnforcer:
    18	    """Replaces L1 words/phrases in generated text with their L2 equivalents.
    19	
    20	    Uses the SRS database to build the replacement dictionary dynamically.
    21	    Matches are word-boundary-aware and case-insensitive.
    22	    """
    23	
    24	    def __init__(self, srs_db: SRSDatabase) -> None:
    25	        self._db = srs_db
    26	        self._cached_patterns: list[tuple[re.Pattern, str]] | None = None
    27	
    28	    def get_replacement_dict(self) -> dict[str, str]:
    29	        """Build {L1_translation → L2_text} mapping from the SRS database."""
    30	        items = self._db.get_new_collocations(limit=10000)
    31	        due_items = self._db.get_due_collocations(__import__("datetime").date.today())
    32	        all_items = {i.syntactic_unit.text: i for i in items + due_items}
    33	
    34	        replacements: dict[str, str] = {}
    35	        for item in all_items.values():
    36	            translation = item.syntactic_unit.translation.strip().lower()
    37	            l2_text = item.syntactic_unit.text
    38	            if translation:
    39	                replacements[translation] = l2_text
    40	        return replacements
    41	
    42	    def enforce(self, text: str, day_number: int | None = None) -> str:
    43	        """Replace known L1 phrases in text with their L2 equivalents.
    44	
    45	        Args:
    46	            text: Input text (story dialogue) that may contain L1 words.
    47	            day_number: Optional day number for violation recording.
    48	
    49	        Returns:
    50	            Text with known L1 phrases replaced by their L2 equivalents.
    51	        """
    52	        if not text:
    53	            return text
    54	
    55	        if self._cached_patterns is None:
    56	            replacements = self.get_replacement_dict()
    57	            if not replacements:
    58	                return text
    59	            self._cached_patterns = [
    60	                (re.compile(r"(?<!\w)" + re.escape(l1) + r"(?!\w)", re.IGNORECASE), l2)
    61	                for l1, l2 in sorted(replacements.items(), key=lambda x: -len(x[0]))
    62	            ]
    63	
    64	        if not self._cached_patterns:
    65	            return text
    66	
    67	        result = text
    68	        for pattern, l2_phrase in self._cached_patterns:
    69	            new_result = pattern.sub(l2_phrase, result)
    70	            if new_result != result:
    71	                logger.debug("Enforcer replaced → %r", l2_phrase)
    72	                result = new_result
    73	
    74	        return result
```

This is one of the key design decisions from the prototypes: **no hardcoded vocabulary**. The replacement dictionary is built dynamically from whatever the SRS database currently contains. Patterns are sorted longest-first so "thank you very much" matches before "thank you". Word-boundary regex prevents partial matches (e.g., "the" inside "other").

Real example — feeding English text through the enforcer with some Slovene vocabulary loaded:

```bash
cd backend && uv run python -c "
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.generation.enforcer import ContentEnforcer

with SRSDatabase(\":memory:\") as db:
    # Load vocabulary the learner has studied
    db.add_collocation(SyntacticUnit(text=\"Dober dan\", translation=\"Good day\", word_count=2, difficulty=1, source=\"llm\"))
    db.add_collocation(SyntacticUnit(text=\"Hvala\", translation=\"Thank you\", word_count=1, difficulty=1, source=\"llm\"))
    db.add_collocation(SyntacticUnit(text=\"prosim\", translation=\"please\", word_count=1, difficulty=1, source=\"llm\"))

    enforcer = ContentEnforcer(db)

    text = \"Good day! Could you help me, please? Thank you for your help.\"
    enforced = enforcer.enforce(text)
    print(f\"Before: {text}\")
    print(f\"After:  {enforced}\")
"
```

```output
Before: Good day! Could you help me, please? Thank you for your help.
After:  Dober dan! Could you help me, prosim? Hvala for your help.
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
    48	]
    49
    50
    51	class ContentStore:
    52	    """SQLite-backed store for curricula, lessons, and audio files.
    53
    54	    Use `:memory:` as db_path for in-memory test databases.
    55	    """
    56
    57	    def __init__(self, db_path: str = ":memory:") -> None:
    58	        self._in_memory = db_path == ":memory:"
    59	        if self._in_memory:
    60	            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
    61	            self._conn.row_factory = sqlite3.Row
    62	            self._init_schema(self._conn)
    63	        else:
    64	            path = Path(db_path)
    65	            path.parent.mkdir(parents=True, exist_ok=True)
    66	            self._path = str(path)
    67	            self._conn = None
    68	            with self._file_conn() as conn:
    69	                self._init_schema(conn)
    70
    71	    def _init_schema(self, conn: sqlite3.Connection) -> None:
    72	        conn.execute(_CREATE_CURRICULA)
    73	        conn.execute(_CREATE_LESSONS)
    74	        conn.execute(_CREATE_AUDIO_FILES)
    75	        conn.execute("CREATE INDEX IF NOT EXISTS idx_lessons_curriculum_id ON lessons(curriculum_id)")
    76	        self._migrate_audio_files(conn)
    77	        conn.commit()
    78
    79	    def _migrate_audio_files(self, conn: sqlite3.Connection) -> None:
    80	        """Add any missing columns to audio_files (idempotent)."""
    81	        existing = {row[1] for row in conn.execute("PRAGMA table_info(audio_files)").fetchall()}
    82	        for col_name, col_type in _AUDIO_FILES_MIGRATION_COLUMNS:
    83	            if col_name not in existing:
    84	                conn.execute(f"ALTER TABLE audio_files ADD COLUMN {col_name} {col_type}")
    85
    86	    @contextmanager
    87	    def _file_conn(self):
    88	        conn = sqlite3.connect(self._path, check_same_thread=False)
    89	        conn.row_factory = sqlite3.Row
    90	        try:
    91	            yield conn
    92	            conn.commit()
    93	        finally:
    94	            conn.close()
    95
    96	    @contextmanager
    97	    def _get_conn(self):
    98	        if self._in_memory:
    99	            yield self._conn
   100	        else:
   101	            with self._file_conn() as conn:
   102	                yield conn
   103
   104	    def close(self) -> None:
   105	        if self._in_memory and self._conn is not None:
   106	            self._conn.close()
   107	            self._conn = None
   108
   109	    def __enter__(self) -> ContentStore:
   110	        return self
   111
   112	    def __exit__(self, *_) -> None:
   113	        self.close()
   114
   115	    # ── Curricula ─────────────────────────────────────────────────────────
   116
   117	    def save_curriculum(self, curriculum_id: str, curriculum: Curriculum) -> None:
   118	        with self._get_conn() as conn:
   119	            conn.execute(
   120	                "INSERT OR REPLACE INTO curricula (id, data_json) VALUES (?, ?)",
   121	                (curriculum_id, curriculum.to_json()),
   122	            )
   123	            if self._in_memory:
   124	                conn.commit()
   125
   126	    def get_curriculum(self, curriculum_id: str) -> Curriculum | None:
   127	        with self._get_conn() as conn:
   128	            row = conn.execute("SELECT data_json FROM curricula WHERE id = ?", (curriculum_id,)).fetchone()
   129	        if row is None:
   130	            return None
   131	        return Curriculum.from_json(row["data_json"])
   132
   133	    def list_curricula(self) -> list[dict]:
   134	        with self._get_conn() as conn:
   135	            rows = conn.execute("SELECT id, data_json FROM curricula ORDER BY created_at DESC").fetchall()
   136	        result = []
   137	        for row in rows:
   138	            c = Curriculum.from_json(row["data_json"])
   139	            result.append({"id": row["id"], "topic": c.topic})
   140	        return result
   141
   142	    # ── Lessons ───────────────────────────────────────────────────────────
   143
   144	    def save_lesson(self, lesson_id: str, curriculum_id: str, day: int, lesson: Lesson) -> None:
   145	        with self._get_conn() as conn:
   146	            conn.execute(
   147	                "INSERT OR REPLACE INTO lessons (id, curriculum_id, day, data_json) VALUES (?, ?, ?, ?)",
   148	                (lesson_id, curriculum_id, day, lesson.to_json()),
   149	            )
   150	            if self._in_memory:
   151	                conn.commit()
   152
   153	    def get_lesson(self, lesson_id: str) -> Lesson | None:
   154	        with self._get_conn() as conn:
   155	            row = conn.execute("SELECT data_json FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
   156	        if row is None:
   157	            return None
   158	        return Lesson.from_json(row["data_json"])
   159
   160	    def get_latest_lesson_by_day(self, curriculum_id: str, day: int) -> tuple[str, Lesson] | None:
   161	        """Return the most recent (lesson_id, Lesson) for a given curriculum day, or None."""
   162	        with self._get_conn() as conn:
   163	            row = conn.execute(
   164	                "SELECT id, data_json FROM lessons"
   165	                " WHERE curriculum_id = ? AND day = ?"
   166	                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
   167	                (curriculum_id, day),
   168	            ).fetchone()
   169	        if row is None:
   170	            return None
   171	        return row["id"], Lesson.from_json(row["data_json"])
   172
   173	    # ── Audio files ───────────────────────────────────────────────────────
   174
   175	    def save_audio_file(
   176	        self,
   177	        audio_id: str,
   178	        lesson_id: str,
   179	        file_path: str,
   180	        *,
   181	        section_index: int | None = None,
   182	        section_type: str | None = None,
   183	    ) -> None:
   184	        with self._get_conn() as conn:
   185	            conn.execute(
   186	                "INSERT OR REPLACE INTO audio_files (id, lesson_id, file_path, section_index, section_type)"
   187	                " VALUES (?, ?, ?, ?, ?)",
   188	                (audio_id, lesson_id, file_path, section_index, section_type),
   189	            )
   190	            if self._in_memory:
   191	                conn.commit()
   192
   193	    def get_audio_file(self, audio_id: str) -> str | None:
   194	        with self._get_conn() as conn:
   195	            row = conn.execute("SELECT file_path FROM audio_files WHERE id = ?", (audio_id,)).fetchone()
   196	        if row is None:
   197	            return None
   198	        return row["file_path"]
   199
   200	    def get_audio_file_row(self, audio_id: str) -> dict | None:
   201	        """Return all fields for an audio_files row, or None if not found."""
   202	        with self._get_conn() as conn:
   203	            row = conn.execute(
   204	                "SELECT id, lesson_id, file_path, section_index, section_type FROM audio_files WHERE id = ?",
   205	                (audio_id,),
   206	            ).fetchone()
   207	        if row is None:
   208	            return None
   209	        return dict(row)
   210
   211	    def list_audio_files_for_lesson(self, lesson_id: str) -> list[dict]:
   212	        """Return all audio file rows for a lesson.
   213
   214	        Ordering: full-lesson row first (section_index IS NULL), then sections
   215	        in ascending section_index order.
   216	        """
   217	        with self._get_conn() as conn:
   218	            rows = conn.execute(
   219	                "SELECT id, lesson_id, file_path, section_index, section_type FROM audio_files"
   220	                " WHERE lesson_id = ?"
   221	                " ORDER BY section_index IS NOT NULL, section_index ASC",
   222	                (lesson_id,),
   223	            ).fetchall()
   224	        return [dict(r) for r in rows]
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
curricula: [{'id': 'greetings-abc12345', 'topic': 'greetings'}]
lesson by day: day1-abc12345 -> Day 1
audio rows: [{'id': 'audio-abc12345', 'lesson_id': 'day1-abc12345', 'file_path': '/tmp/full.wav', 'section_index': None, 'section_type': None}]
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
    16	
    17	
    18	@runtime_checkable
    19	class AudioProcessor(Protocol):
    20	    """Protocol for audio processing operations."""
    21	
    22	    def concatenate(self, audio_bytes_list: list[bytes], silence_ms: int = 300) -> bytes: ...
    23	
    24	    def normalize(self, audio_bytes: bytes) -> bytes: ...
    25	
    26	    def add_silence(self, duration_ms: int) -> bytes: ...
    27	
    28	    def trim_silence(self, audio_bytes: bytes, threshold_db: float = -40.0) -> bytes: ...
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
    11	import edge_tts
    12
    13	logger = logging.getLogger(__name__)
    14
    15	# Rate limiting constants (ported from prototype)
    16	MIN_REQUEST_DELAY_S = 0.2
    17	MAX_CONCURRENT_REQUESTS = 10
    18	MAX_RETRIES = 3
    19
    20
    21	class EdgeTTSService:
    22	    """Microsoft Edge TTS adapter.
    23
    24	    Implements the TTSService Protocol with:
    25	    - Rate limiting (200 ms between requests, max 3 concurrent)
    26	    - Optional file-based caching (keyed on text + voice + rate)
    27	    - Retry on transient errors
    28	    """
    29
    30	    def __init__(self, cache_dir: Path | None = None) -> None:
    31	        self._cache_dir = cache_dir
    32	        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    33
    34	    # ------------------------------------------------------------------
    35	    # TTSService Protocol implementation
    36	    # ------------------------------------------------------------------
    37
    38	    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None:
    39	        """Synthesize *text* to *output_path* using Edge TTS.
    40
    41	        Args:
    42	            text: Text to synthesize.
    43	            voice_id: Edge TTS voice short name (e.g. "sl-SI-PetraNeural").
    44	            output_path: Destination file path for the synthesized audio.
    45	            rate: Speech rate adjustment (e.g. "+0%", "-20%").
    46	        """
    47	        if self._cache_dir is not None:
    48	            cached = self._cache_path(text, voice_id, rate)
    49	            if cached.exists():
    50	                shutil.copy2(cached, output_path)
    51	                logger.debug("EdgeTTS cache hit for %r", text[:40])
    52	                return
    53
    54	        await self._synthesize_with_retry(text, voice_id, output_path, rate)
    55
    56	        if self._cache_dir is not None:
    57	            cached = self._cache_path(text, voice_id, rate)
    58	            cached.parent.mkdir(parents=True, exist_ok=True)
    59	            shutil.copy2(output_path, cached)
    60
    61	    async def list_voices(self, language_code: str | None = None) -> list[dict]:
    62	        """Return available Edge TTS voices, optionally filtered by language."""
    63	        voices = await edge_tts.list_voices()
    64	        if language_code:
    65	            voices = [v for v in voices if language_code in v.get("Locale", "")]
    66	        return voices
    67
    68	    # ------------------------------------------------------------------
    69	    # Private helpers
    70	    # ------------------------------------------------------------------
    71
    72	    def _cache_path(self, text: str, voice_id: str, rate: str) -> Path:
    73	        key = f"{voice_id}|{rate}|{text}"
    74	        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    75	        return self._cache_dir / f"{digest}.mp3"  # type: ignore[operator]
    76
    77	    async def _synthesize_with_retry(self, text: str, voice_id: str, output_path: Path, rate: str) -> None:
    78	        last_error: Exception | None = None
    79	        for attempt in range(MAX_RETRIES):
    80	            try:
    81	                await self._do_synthesize(text, voice_id, output_path, rate)
    82	                return
    83	            except (ConnectionResetError, ConnectionError, OSError) as exc:
    84	                last_error = exc
    85	                logger.warning("EdgeTTS transient error (attempt %d): %s", attempt + 1, exc)
    86	                await asyncio.sleep(0.5 * (2**attempt))
    87	        raise RuntimeError(f"EdgeTTS synthesis failed after {MAX_RETRIES} attempts") from last_error
    88
    89	    async def _do_synthesize(self, text: str, voice_id: str, output_path: Path, rate: str) -> None:
    90	        async with self._semaphore:
    91	            communicate = edge_tts.Communicate(text, voice_id, rate=rate)
    92	            output_path.parent.mkdir(parents=True, exist_ok=True)
    93	            await communicate.save(str(output_path))
    94	            await asyncio.sleep(MIN_REQUEST_DELAY_S)
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
    25	    def get_boundary_pause(self, boundary_type: str) -> int:
    26	        """Return a fixed pause (ms) for the given boundary type."""
    27	        return _BOUNDARY_PAUSES[boundary_type]
    28
    29	    def get_phrase_pause(
    30	        self,
    31	        audio_duration_s: float,
    32	        word_count: int,
    33	        section_type: SectionType,
    34	        language_code: str = _ENGLISH_LANG,
    35	    ) -> int:
    36	        """Pause in ms to insert after a phrase.
    37
    38	        - Key Phrases + L2: audio-duration-based (1:1), floor 500 ms.
    39	        - Key Phrases + English narrator: base 500 ms.
    40	        - Slow Speed + L2: base 500 ms × 1.2.
    41	        - Slow Speed + English narrator: base 500 ms (no slow factor).
    42	        - Natural Speed / Translated (any language): base 500 ms.
    43
    44	        `word_count` is retained for backward compatibility with the renderer
    45	        call site and is currently unused.
    46	        """
    47	        del word_count  # unused; kept for API stability
    48
    49	        is_l2 = language_code != _ENGLISH_LANG
    50
    51	        if section_type == SectionType.KEY_PHRASES and is_l2:
    52	            return max(_BASE_PHRASE_PAUSE_MS, int(audio_duration_s * 1000))
    53
    54	        if section_type == SectionType.SLOW_SPEED and is_l2:
    55	            return int(_BASE_PHRASE_PAUSE_MS * _SLOW_SPEED_FACTOR)
    56
    57	        return _BASE_PHRASE_PAUSE_MS
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
     9	from pathlib import Path
    10
    11	from pydub import AudioSegment
    12
    13	from app.audio.pause_calculator import NaturalPauseCalculator
    14	from app.audio.ports import TTSService
    15	from app.audio.preprocessing.base import TextPreprocessor
    16	from app.models.lesson import Lesson, Section
    17
    18	logger = logging.getLogger(__name__)
    19
    20
    21	class LessonRenderer:
    22	    """Renders a Lesson to a WAV audio file using pydub for assembly.
    23
    24	    Pipeline per phrase:
    25	      1. Preprocess text (language-specific)
    26	      2. Synthesize via TTS → temp file
    27	      3. Load as AudioSegment, measure actual duration
    28	      4. Calculate post-phrase pause from real duration
    29	      5. Concatenate all segments with boundary gaps
    30	    Then export the combined AudioSegment as WAV.
    31	    """
    32
    33	    def __init__(
    34	        self,
    35	        tts: TTSService,
    36	        preprocessor: TextPreprocessor,
    37	        pause_calculator: NaturalPauseCalculator,
    38	    ) -> None:
    39	        self._tts = tts
    40	        self._preprocessor = preprocessor
    41	        self._calc = pause_calculator
    42
    43	    async def _render_section(self, section: Section, tmp: Path, section_idx: int) -> AudioSegment:
    44	        """Render a single section to an AudioSegment (no boundary silence).
    45
    46	        Args:
    47	            section: The Section to render.
    48	            tmp: Temp directory for intermediate TTS files.
    49	            section_idx: Index used for temp file naming.
    50
    51	        Returns:
    52	            AudioSegment containing all phrases with inter-phrase pauses.
    53	        """
    54	        phrase_files = [tmp / f"s{section_idx}_p{i}.mp3" for i in range(len(section.phrases))]
    55	        processed_texts = [
    56	            self._preprocessor.preprocess(phrase.text, section.section_type) for phrase in section.phrases
    57	        ]
    58
    59	        # Synthesize all phrases in this section concurrently.
    60	        # EdgeTTSService._semaphore limits total concurrent requests globally.
    61	        await asyncio.gather(
    62	            *[
    63	                self._tts.synthesize(text, phrase.voice_id, phrase_files[i], rate=phrase.rate)
    64	                for i, (text, phrase) in enumerate(zip(processed_texts, section.phrases, strict=True))
    65	            ]
    66	        )
    67
    68	        # Assemble in phrase order (order is preserved by the pre-allocated paths)
    69	        seg = AudioSegment.empty()
    70	        for i, phrase in enumerate(section.phrases):
    71	            phrase_seg = AudioSegment.from_file(str(phrase_files[i]))
    72	            audio_duration_s = len(phrase_seg) / 1000.0
    73	            seg += phrase_seg
    74
    75	            pause_ms = self._calc.get_phrase_pause(
    76	                audio_duration_s=audio_duration_s,
    77	                word_count=len(phrase.text.split()),
    78	                section_type=section.section_type,
    79	                language_code=phrase.language_code,
    80	            )
    81	            if pause_ms > 0:
    82	                seg += AudioSegment.silent(duration=pause_ms)
    83
    84	        return seg
    85
    86	    async def render(
    87	        self,
    88	        lesson: Lesson,
    89	        output_path: Path,
    90	        section_paths: list[Path] | None = None,
    91	    ) -> None:
    92	        """Render *lesson* to *output_path* as a valid WAV file.
    93
    94	        Optionally writes per-section WAV files to *section_paths* (one per
    95	        section, in lesson order). Each section file contains only the section
    96	        content with no leading/trailing boundary silence.
    97
    98	        Args:
    99	            lesson: Lesson with sections and phrases.
   100	            output_path: Destination file path for the full lesson (written as WAV).
   101	            section_paths: Optional list of paths for per-section output WAVs.
   102	                           Must have same length as lesson.sections if provided.
   103	        """
   104	        t_start = time.perf_counter()
   105	        boundary_silence = AudioSegment.silent(duration=self._calc.get_section_boundary_pause())
   106
   107	        with tempfile.TemporaryDirectory() as tmp_dir:
   108	            tmp = Path(tmp_dir)
   109
   110	            # Render lesson title (full WAV only — not in section files)
   111	            t0 = time.perf_counter()
   112	            title_file = tmp / "title.mp3"
   113	            await self._tts.synthesize(lesson.title, lesson.narrator_voice, title_file, rate="+0%")
   114	            logger.debug("TTS title → %.0f ms", (time.perf_counter() - t0) * 1000)
   115	            title_seg = AudioSegment.from_file(str(title_file))
   116
   117	            # Render all sections concurrently — phrases within each section are
   118	            # also parallelised; EdgeTTSService._semaphore caps total concurrency.
   119	            t0 = time.perf_counter()
   120	            section_segs: list[AudioSegment] = list(
   121	                await asyncio.gather(
   122	                    *[self._render_section(section, tmp, i) for i, section in enumerate(lesson.sections)]
   123	                )
   124	            )
   125	            logger.debug("All sections TTS → %.0f ms", (time.perf_counter() - t0) * 1000)
   126
   127	            if section_paths is not None:
   128	                for section_idx, sec_seg in enumerate(section_segs):
   129	                    sp = section_paths[section_idx]
   130	                    sp.parent.mkdir(parents=True, exist_ok=True)
   131	                    t0 = time.perf_counter()
   132	                    sec_seg.export(str(sp), format="wav")
   133	                    logger.debug("Section %d export → %.0f ms", section_idx, (time.perf_counter() - t0) * 1000)
   134
   135	            # Assemble full lesson: title + bs + sec0 + bs + sec1 + ...
   136	            combined = title_seg + boundary_silence
   137	            for i, sec_seg in enumerate(section_segs):
   138	                if i > 0:
   139	                    combined += boundary_silence
   140	                combined += sec_seg
   141
   142	        output_path.parent.mkdir(parents=True, exist_ok=True)
   143	        t0 = time.perf_counter()
   144	        combined.export(str(output_path), format="wav")
   145	        logger.debug("Full lesson export → %.0f ms", (time.perf_counter() - t0) * 1000)
   146	        total_ms = (time.perf_counter() - t_start) * 1000
   147	        logger.info(
   148	            "Rendered lesson to %s (audio: %d ms, wall: %.0f ms)",
   149	            output_path,
   150	            len(combined),
   151	            total_ms,
   152	        )
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
     5	import re
     6	import uuid
     7	
     8	from fastapi import APIRouter, HTTPException, Request
     9	from pydantic import BaseModel
    10	
    11	router = APIRouter(prefix="/api/curriculum", tags=["curriculum"])
    12	
    13	
    14	def _slug(text: str) -> str:
    15	    text = text.lower()
    16	    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    17	    return text[:50]
    18	
    19	
    20	class GenerateCurriculumRequest(BaseModel):
    21	    topic: str
    22	    cefr_level: str = "A2"
    23	    num_days: int = 7
    24	
    25	
    26	@router.post("/generate", status_code=201)
    27	async def generate_curriculum(body: GenerateCurriculumRequest, request: Request):
    28	    generator = request.app.state.curriculum_generator
    29	    language = request.app.state.language
    30	    store = request.app.state.content_store
    31	
    32	    curriculum = await generator.generate(
    33	        topic=body.topic,
    34	        language=language,
    35	        cefr_level=body.cefr_level,
    36	        num_days=body.num_days,
    37	    )
    38	
    39	    curriculum_id = f"{_slug(body.topic)}-{uuid.uuid4().hex[:8]}"
    40	    store.save_curriculum(curriculum_id, curriculum)
    41	
    42	    return {
    43	        "id": curriculum_id,
    44	        "topic": curriculum.topic,
    45	        "language_code": curriculum.language_code,
    46	        "days": len(curriculum.days),
    47	    }
    48	
    49	
    50	@router.get("", status_code=200)
    51	async def list_curricula(request: Request):
    52	    store = request.app.state.content_store
    53	    return store.list_curricula()
    54	
    55	
    56	@router.get("/{curriculum_id}", status_code=200)
    57	async def get_curriculum(curriculum_id: str, request: Request):
    58	    store = request.app.state.content_store
    59	    curriculum = store.get_curriculum(curriculum_id)
    60	    if curriculum is None:
    61	        raise HTTPException(status_code=404, detail="Curriculum not found")
    62	    return {
    63	        "id": curriculum_id,
    64	        "topic": curriculum.topic,
    65	        "language_code": curriculum.language_code,
    66	        "days": len(curriculum.days),
    67	    }
    68	
    69	
    70	@router.get("/{curriculum_id}/progress")
    71	async def get_curriculum_progress(curriculum_id: str, request: Request):
    72	    store = request.app.state.content_store
    73	    if store.get_curriculum(curriculum_id) is None:
    74	        raise HTTPException(status_code=404, detail="Curriculum not found")
    75	    return store.get_lesson_days(curriculum_id)
    76	
    77	
    78	@router.get("/{curriculum_id}/days/{day}/lesson", status_code=200)
    79	async def get_lesson_by_day(curriculum_id: str, day: int, request: Request):
    80	    store = request.app.state.content_store
    81	    result = store.get_latest_lesson_by_day(curriculum_id, day)
    82	    if result is None:
    83	        raise HTTPException(status_code=404, detail=f"No lesson found for day {day}")
    84	    lesson_id, lesson = result
    85	    return {
    86	        "id": lesson_id,
    87	        "title": lesson.title,
    88	        "language_code": lesson.language_code,
    89	        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in lesson.key_phrases],
    90	        "sections": [
    91	            {
    92	                "type": s.section_type.value,
    93	                "phrases": [
    94	                    {"text": p.text, "role": p.role, "language_code": p.language_code, "voice_id": p.voice_id}
    95	                    for p in s.phrases
    96	                ],
    97	            }
    98	            for s in lesson.sections
    99	        ],
   100	    }

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
     5	import re
     6	import uuid
     7
     8	from fastapi import APIRouter, HTTPException, Request
     9	from pydantic import BaseModel
    10
    11	from app.models.strategy import ContentStrategy
    12
    13	router = APIRouter(prefix="/api/story", tags=["generation"])
    14
    15
    16	def _slug(text: str) -> str:
    17	    text = text.lower()
    18	    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    19	    return text[:50]
    20
    21
    22	class GenerateStoryRequest(BaseModel):
    23	    curriculum_id: str
    24	    day: int = 1
    25	    strategy: str = "WIDER"
    26
    27
    28	@router.post("/generate", status_code=201)
    29	async def generate_story(body: GenerateStoryRequest, request: Request):
    30	    store = request.app.state.content_store
    31	    curriculum = store.get_curriculum(body.curriculum_id)
    32	    if curriculum is None:
    33	        raise HTTPException(status_code=404, detail="Curriculum not found")
    34
    35	    days = [d for d in curriculum.days if d.day == body.day]
    36	    if not days:
    37	        raise HTTPException(status_code=404, detail=f"Day {body.day} not found in curriculum")
    38
    39	    curriculum_day = days[0]
    40	    strategy = ContentStrategy[body.strategy]
    41	    language = request.app.state.language
    42	    generator = request.app.state.story_generator
    43
    44	    lesson = await generator.generate(
    45	        curriculum_day=curriculum_day,
    46	        language=language,
    47	        strategy=strategy,
    48	    )
    49
    50	    lesson_id = f"{_slug(lesson.title)}-{uuid.uuid4().hex[:8]}"
    51	    store.save_lesson(lesson_id, body.curriculum_id, body.day, lesson)
    52
    53	    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    54	    return {"id": lesson_id, "title": lesson.title, "sections": sections}
    55
    56
    57	@router.get("/{lesson_id}", status_code=200)
    58	async def get_lesson(lesson_id: str, request: Request):
    59	    store = request.app.state.content_store
    60	    lesson = store.get_lesson(lesson_id)
    61	    if lesson is None:
    62	        raise HTTPException(status_code=404, detail="Lesson not found")
    63	    return {
    64	        "id": lesson_id,
    65	        "title": lesson.title,
    66	        "language_code": lesson.language_code,
    67	        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in lesson.key_phrases],
    68	        "sections": [
    69	            {
    70	                "type": s.section_type.value,
    71	                "phrases": [
    72	                    {"text": p.text, "role": p.role, "language_code": p.language_code, "voice_id": p.voice_id}
    73	                    for p in s.phrases
    74	                ],
    75	            }
    76	            for s in lesson.sections
    77	        ],
    78	    }
```

The generation router is similarly slug-based. Lesson IDs are derived from the lesson title (which the LLM sets), so `arriving-in-ljubljana-a3f1b2c8` is the lesson ID you see in the player URL. The `GET /{lesson_id}` endpoint returns a fully-expanded lesson for the frontend to render the transcript view.

**Key phrases are no longer registered with the SRS database during generation.** In the prototype, `StoryGenerator` took an `srs_db` and called `db.add_collocation` for each key phrase. That coupling made the generator hard to test in isolation. Now generation only produces a `Lesson` with `key_phrases: list[KeyPhraseInfo]`; SRS registration happens in `POST /api/srs/listen` when the learner first listens to the lesson (see §7.3).

### 7.3 SRS API

The SRS router is now the largest module in `app/api/` (~700 lines, 19 routes). The full surface:

```bash
grep -nE "^@router\." backend/app/api/srs.py
```

```output
111:@router.get("/due", status_code=200)
128:@router.get("/new", status_code=200)
144:@router.post("/items/{item_id}/direction/{direction}/feedback", status_code=200)
175:@router.get("/media/{filename}", status_code=200)
186:@router.post("/listen", status_code=200)
245:@router.get("/lesson/{lesson_id}/transcript", status_code=200)
296:@router.post("/translate-missing", status_code=200)
328:@router.post("/backfill-translations", status_code=200)
338:@router.get("/stats", status_code=200)
345:@router.get("/queue-stats", status_code=200)
400:@router.post("/items", status_code=201)
440:@router.get("/items", status_code=200)
466:@router.patch("/items/{item_id}", status_code=200)
479:@router.delete("/items/{item_id}", status_code=200)
488:@router.post("/items/bulk-delete", status_code=200)
495:@router.post("/items/{item_id}/reset", status_code=200)
505:@router.post("/items/{item_id}/state", status_code=200)
519:@router.post("/items/{item_id}/suspend", status_code=200)
646:@router.get("/review-queue", status_code=200)
```

These cover four functional areas: **learner loop** (due/new/feedback), **per-word capture and transcript** (listen/transcript/translate-missing/backfill-translations/queue-stats/stats), **review queue and media** (review-queue, media/{filename}), and **admin CRUD** (items POST/GET/PATCH/DELETE/state/suspend/reset, items/bulk-delete).

#### Response shape — `_item_to_dict`

Every list and detail endpoint serialises through one helper. Response payload includes both flat (legacy) FSRS fields and a per-direction breakdown — plus Anki identity, media URLs, and grammar/note context:

```bash
sed -n '56,88p' backend/app/api/srs.py
```

```output
def _item_to_dict(
    row_id: int,
    item: SRSItem,
    language_code: str,
    image_url: str | None = None,
    audio_url: str | None = None,
) -> dict:
    """Serialize an SRSItem to a response dict."""
    return {
        "id": row_id,
        "text": item.syntactic_unit.text,
        "translation": item.syntactic_unit.translation,
        "word_count": item.syntactic_unit.word_count,
        # Flat recognition shims (back-compat)
        "state": item.state.value,
        "due_date": item.due_date.isoformat(),
        "stability": item.stability,
        "difficulty": item.difficulty,
        "reps": item.reps,
        "lapses": item.lapses,
        "last_review": item.last_review.isoformat() if item.last_review else None,
        "language_code": language_code,
        "guid": item.guid,
        "anki_note_id": item.anki_note_id,
        "directions": {
            "recognition": _direction_to_dict(item.directions[Direction.RECOGNITION]),
            "production": _direction_to_dict(item.directions[Direction.PRODUCTION]),
        },
        "image_url": image_url,
        "audio_url": audio_url,
        "grammar": item.syntactic_unit.grammar,
        "note": item.syntactic_unit.note,
    }
```

The two `directions` entries each contain `{state, due_date, stability, difficulty, reps, lapses, last_review, anki_card_id, anki_due, dirty_fsrs, last_synced_at, last_rating}` — the full `DirectionState` (PART 4.2). `image_url` and `audio_url` point at `/api/srs/media/{filename}`, populated only when the row has stored media (post-sync).

#### Per-direction feedback (`POST /items/{id}/direction/{direction}/feedback`)

This replaces the old single-direction `/api/srs/feedback`. The body accepts either an explicit `rating` (`"again"`/`"hard"`/`"good"`/`"easy"`) or an implicit `signal` (`"no_help"`/`"slowdown"`/`"translation_request"`/`"fast_forward"`); `rating_from_input` enforces exactly-one-of:

```bash
sed -n '144,165p' backend/app/api/srs.py
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

    db = request.app.state.srs_db
    result = db.get_collocation_by_id(item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found")
    _, item, _ = result

    fsrs_params, _ = resolve_fsrs_params(db)
    updated = schedule(item, rating, direction=dir_enum, params=fsrs_params)
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

**Admin (powering `/admin/srs`):**
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
     6	import re
     7	import uuid
     8	import zipfile
     9	from pathlib import Path
    10	
    11	from fastapi import APIRouter, HTTPException, Request
    12	from fastapi.responses import FileResponse, Response
    13	from pydantic import BaseModel
    14	
    15	from app.generation.section_builder import SECTION_TITLES
    16	from app.models.lesson import SectionType
    17	
    18	router = APIRouter(prefix="/api/audio", tags=["audio"])
    19	
    20	
    21	def _sanitize_filename(name: str) -> str:
    22	    """Strip filesystem-illegal characters and collapse whitespace to underscores."""
    23	    name = re.sub(r'[/\\:*?"<>|]', "", name)
    24	    name = re.sub(r"\s+", "_", name.strip())
    25	    return name or "audio"
    26	
    27	
    28	def _build_section_filename(topic: str, day: int, section_index: int, section_type: str) -> str:
    29	    """Build a context-rich section WAV filename: {Topic}_Day{DD}_{NN}_{Title}.wav."""
    30	    safe_topic = _sanitize_filename(topic)
    31	    try:
    32	        st = SectionType(section_type)
    33	        title = SECTION_TITLES.get(st, section_type)
    34	    except ValueError:
    35	        title = section_type
    36	    safe_title = _sanitize_filename(title)
    37	    return f"{safe_topic}_Day{day:02d}_{section_index + 1:02d}_{safe_title}.wav"
    38	
    39	
    40	class RenderAudioRequest(BaseModel):
    41	    lesson_id: str
    42	
    43	
    44	@router.post("/render", status_code=202)
    45	async def render_audio(body: RenderAudioRequest, request: Request):
    46	    store = request.app.state.content_store
    47	    lesson = store.get_lesson(body.lesson_id)
    48	    if lesson is None:
    49	        raise HTTPException(status_code=404, detail="Lesson not found")
    50	
    51	    renderer = request.app.state.renderer
    52	    audio_dir: Path = request.app.state.audio_dir
    53	    audio_dir.mkdir(parents=True, exist_ok=True)
    54	
    55	    # Allocate UUIDs for full lesson and each section
    56	    audio_id = str(uuid.uuid4())
    57	    full_path = audio_dir / f"{audio_id}.wav"
    58	
    59	    section_ids = [str(uuid.uuid4()) for _ in lesson.sections]
    60	    section_paths = [audio_dir / f"{sid}.wav" for sid in section_ids]
    61	
    62	    await renderer.render(lesson, full_path, section_paths=section_paths)
    63	
    64	    # Persist full lesson row
    65	    store.save_audio_file(audio_id, body.lesson_id, str(full_path))
    66	
    67	    # Persist per-section rows
    68	    for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True)):
    69	        store.save_audio_file(
    70	            sid,
    71	            body.lesson_id,
    72	            str(section_paths[i]),
    73	            section_index=i,
    74	            section_type=section.section_type.value,
    75	        )
    76	
    77	    sections = [
    78	        {
    79	            "audio_id": sid,
    80	            "section_index": i,
    81	            "section_type": section.section_type.value,
    82	            "title": SECTION_TITLES.get(section.section_type, section.section_type.value),
    83	        }
    84	        for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True))
    85	    ]
    86	
    87	    return {"audio_id": audio_id, "lesson_id": body.lesson_id, "sections": sections}
    88	
    89	
    90	@router.get("/lesson/{lesson_id}", status_code=200)
    91	async def get_lesson_audio(lesson_id: str, request: Request):
    92	    """Return the audio file list for a lesson (full + sections) without re-rendering."""
    93	    store = request.app.state.content_store
    94	    rows = store.list_audio_files_for_lesson(lesson_id)
    95	    if not rows:
    96	        raise HTTPException(status_code=404, detail="No audio found for this lesson")
    97	
    98	    full_row = next((r for r in rows if r["section_index"] is None), None)
    99	    if full_row is None:
   100	        raise HTTPException(status_code=404, detail="Full lesson audio not found")
   101	
   102	    section_rows = [r for r in rows if r["section_index"] is not None]
   103	
   104	    sections = []
   105	    for r in section_rows:
   106	        section_type_str = r["section_type"] or ""
   107	        try:
   108	            st = SectionType(section_type_str)
   109	            title = SECTION_TITLES.get(st, section_type_str)
   110	        except ValueError:
   111	            title = section_type_str
   112	        sections.append(
   113	            {
   114	                "audio_id": r["id"],
   115	                "section_index": r["section_index"],
   116	                "section_type": section_type_str,
   117	                "title": title,
   118	            }
   119	        )
   120	
   121	    return {
   122	        "audio_id": full_row["id"],
   123	        "lesson_id": lesson_id,
   124	        "sections": sections,
   125	    }
   126	
   127	
   128	@router.get("/lesson/{lesson_id}/zip", status_code=200)
   129	async def download_lesson_zip(lesson_id: str, request: Request):
   130	    """Return a ZIP of all section WAVs for a lesson with context-rich filenames."""
   131	    store = request.app.state.content_store
   132	    rows = store.list_audio_files_for_lesson(lesson_id)
   133	    full_row = next((r for r in rows if r["section_index"] is None), None)
   134	    section_rows = [r for r in rows if r["section_index"] is not None]
   135	
   136	    if not section_rows:
   137	        raise HTTPException(status_code=404, detail="No section audio files found for this lesson")
   138	
   139	    # Validate all files exist before building the ZIP
   140	    all_rows = ([full_row] if full_row else []) + section_rows
   141	    for r in all_rows:
   142	        if not Path(r["file_path"]).exists():
   143	            raise HTTPException(status_code=404, detail=f"Audio file missing: {r['file_path']}")
   144	
   145	    # Resolve topic and day for naming
   146	    topic = "audio"
   147	    day = 1
   148	    lesson_row = store.get_lesson_row(lesson_id)
   149	    if lesson_row is not None:
   150	        day = lesson_row["day"]
   151	        curriculum = store.get_curriculum(lesson_row["curriculum_id"])
   152	        if curriculum is not None:
   153	            topic = curriculum.topic
   154	        else:
   155	            lesson = store.get_lesson(lesson_id)
   156	            topic = lesson.title  # lesson_row exists → lesson exists
   157	
   158	    safe_topic = _sanitize_filename(topic)
   159	
   160	    # Build ZIP in memory: full lesson file first (sorts as _00_), then sections
   161	    buf = io.BytesIO()
   162	    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
   163	        if full_row:
   164	            full_filename = f"{safe_topic}_Day{day:02d}_00_Full.wav"
   165	            zf.write(full_row["file_path"], arcname=full_filename)
   166	        for r in sorted(section_rows, key=lambda x: x["section_index"]):
   167	            filename = _build_section_filename(topic, day, r["section_index"], r["section_type"] or "")
   168	            zf.write(r["file_path"], arcname=filename)
   169	
   170	    zip_name = f"{_sanitize_filename(topic)}_Day{day:02d}.zip"
   171	    return Response(
   172	        content=buf.getvalue(),
   173	        media_type="application/zip",
   174	        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
   175	    )
   176	
   177	
   178	@router.get("/{audio_id}", status_code=200)
   179	async def get_audio(audio_id: str, request: Request):
   180	    store = request.app.state.content_store
   181	    row = store.get_audio_file_row(audio_id)
   182	    if row is None:
   183	        raise HTTPException(status_code=404, detail="Audio not found")
   184	
   185	    path = Path(row["file_path"])
   186	    if not path.exists():
   187	        raise HTTPException(status_code=404, detail="Audio file missing")
   188	
   189	    # Build a friendly download filename with curriculum context
   190	    lesson_id = row["lesson_id"]
   191	    topic = "audio"
   192	    day = 1
   193	    lesson_row = store.get_lesson_row(lesson_id)
   194	    if lesson_row is not None:
   195	        day = lesson_row["day"]
   196	        curriculum = store.get_curriculum(lesson_row["curriculum_id"])
   197	        if curriculum is not None:
   198	            topic = curriculum.topic
   199	        else:
   200	            lesson = store.get_lesson(lesson_id)
   201	            topic = lesson.title  # lesson_row exists → lesson exists
   202	
   203	    if row["section_index"] is not None:
   204	        filename = _build_section_filename(topic, day, row["section_index"], row["section_type"] or "")
   205	    else:
   206	        filename = f"{_sanitize_filename(topic)}_Day{day:02d}_full.wav"
   207	
   208	    return FileResponse(
   209	        str(path),
   210	        media_type="audio/wav",
   211	        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
   212	    )

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
........................................................................ [ 17%]
........................................................................ [ 35%]
........................................................................ [ 52%]
........................................................................ [ 70%]
........................................................................ [ 88%]
.................................................                        [100%]
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.13.7-final-0 _______________

Name                                  Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------
app/__init__.py                           0      0      0      0   100%
app/api/__init__.py                       0      0      0      0   100%
app/api/audio.py                         73      0     16      0   100%
app/api/curriculum.py                    42      0      4      0   100%
app/api/generation.py                    40      0      6      0   100%
app/api/srs.py                          143      0     28      0   100%
app/audio/__init__.py                     0      0      0      0   100%
app/audio/assembler.py                   22      0      4      0   100%
app/audio/edge_tts.py                    53      0     10      0   100%
app/audio/pause_calculator.py            20      0      4      0   100%
app/audio/ports.py                        7      0      0      0   100%
app/audio/preprocessing/__init__.py       0      0      0      0   100%
app/audio/preprocessing/base.py           5      0      0      0   100%
app/audio/preprocessing/slovene.py        5      0      0      0   100%
app/audio/renderer.py                    61      0     12      0   100%
app/config.py                             8      0      0      0   100%
app/generation/__init__.py                0      0      0      0   100%
app/generation/curriculum.py             32      0      4      0   100%
app/generation/enforcer.py               35      0     12      0   100%
app/generation/prompts.py                20      0      4      0   100%
app/generation/section_builder.py        83      0     38      0   100%
app/generation/story.py                  37      0      2      0   100%
app/generation/syllabify.py              32      0     12      0   100%
app/llm/__init__.py                       0      0      0      0   100%
app/llm/cassette.py                      72      0     22      0   100%
app/llm/client.py                       226      0     70      0   100%
app/main.py                              55      0      2      0   100%
app/models/__init__.py                    0      0      0      0   100%
app/models/curriculum.py                 30      0      2      0   100%
app/models/language.py                   15      0      0      0   100%
app/models/lesson.py                     45      0      2      0   100%
app/models/srs_item.py                   26      0      0      0   100%
app/models/strategy.py                   38      0      0      0   100%
app/models/syntactic_unit.py             16      0      4      0   100%
app/srs/__init__.py                       0      0      0      0   100%
app/srs/database.py                     176      0     44      0   100%
app/srs/feedback.py                      12      0      2      0   100%
app/srs/fsrs.py                          57      0      6      0   100%
app/srs/lemmatizer.py                     7      0      0      0   100%
app/srs/selector.py                      40      0      8      0   100%
app/srs/tokenizer.py                      5      0      0      0   100%
app/srs/transcript.py                    35      0      8      0   100%
app/storage/__init__.py                   0      0      0      0   100%
app/storage/store.py                    114      0     28      0   100%
---------------------------------------------------------------------------------
TOTAL                                  1687      0    354      0   100%
Required test coverage of 100.0% reached. Total coverage: 100.00%
409 passed in 6.85s
```

409 tests, **100% branch coverage** *(at the time of the original walkthrough revision)*. As of the Anki-sync work the suite has grown to **1460 tests across 73 files** at **~99.95% branch coverage** — the only remaining uncovered lines are a handful of conditional ALTER guards in `srs/migrations.py` that only fire when re-running a partially applied migration. All in mock mode — no network calls needed. PART 8 below shows the original test snapshot; for an up-to-date breakdown of the new Anki-sync test files see PART 12.

### 8.2 Test File Inventory

```bash
ls backend/tests/test_*.py | xargs -I{} sh -c "echo \"{}: \$(grep -c \"def test_\" {}) tests\"" | sort
```

```output
backend/tests/test_anki_audit_guids.py: 12 tests
backend/tests/test_anki_backfill_guids.py: 16 tests
backend/tests/test_anki_bootstrap_e2e.py: 8 tests
backend/tests/test_anki_connect_client.py: 24 tests
backend/tests/test_anki_fallback_log.py: 4 tests
backend/tests/test_anki_guid.py: 4 tests
backend/tests/test_anki_import_seed_readonly.py: 27 tests
backend/tests/test_anki_media_forvo.py: 13 tests
backend/tests/test_anki_media_normalize.py: 9 tests
backend/tests/test_anki_media_pipeline.py: 15 tests
backend/tests/test_anki_media_pixabay.py: 24 tests
backend/tests/test_anki_media_tts.py: 5 tests
backend/tests/test_anki_merge_dupes_apply.py: 26 tests
backend/tests/test_anki_merge_dupes_cli.py: 9 tests
backend/tests/test_anki_merge_dupes_plan.py: 20 tests
backend/tests/test_anki_migrate_homonyms.py: 20 tests
backend/tests/test_anki_model_discovery.py: 16 tests
backend/tests/test_anki_normalize_usns.py: 5 tests
backend/tests/test_anki_notetype.py: 16 tests
backend/tests/test_anki_offline_writer_create_note.py: 24 tests
backend/tests/test_anki_repair_nested_homonyms.py: 14 tests
backend/tests/test_anki_safety.py: 17 tests
backend/tests/test_anki_safety_rw.py: 11 tests
backend/tests/test_anki_sqlite_reader.py: 43 tests
backend/tests/test_anki_sqlite_writer.py: 22 tests
backend/tests/test_anki_syncKey_preflight.py: 8 tests
backend/tests/test_anki_sync_create_new.py: 43 tests
backend/tests/test_anki_sync_force_fsrs.py: 22 tests
backend/tests/test_anki_sync_mode_detection.py: 19 tests
backend/tests/test_anki_sync_pull.py: 60 tests
backend/tests/test_anki_sync_push.py: 43 tests
backend/tests/test_anki_sync_round_trip.py: 2 tests
backend/tests/test_api.py: 66 tests
backend/tests/test_api_admin.py: 3 tests
backend/tests/test_api_anki.py: 10 tests
backend/tests/test_api_srs.py: 46 tests
backend/tests/test_api_srs_admin.py: 39 tests
backend/tests/test_api_srs_directions.py: 25 tests
backend/tests/test_audio_ports.py: 5 tests
backend/tests/test_collocation_matcher.py: 11 tests
backend/tests/test_config.py: 5 tests
backend/tests/test_curriculum.py: 13 tests
backend/tests/test_dirty_fields.py: 11 tests
backend/tests/test_edge_tts.py: 9 tests
backend/tests/test_enforcer.py: 10 tests
backend/tests/test_feedback_rating_input.py: 13 tests
backend/tests/test_fsrs.py: 18 tests
backend/tests/test_lemmatizer.py: 7 tests
backend/tests/test_llm_cassette.py: 11 tests
backend/tests/test_llm_client.py: 41 tests
backend/tests/test_llm_translate.py: 6 tests
backend/tests/test_main_lifespan.py: 2 tests
backend/tests/test_media_importer.py: 12 tests
backend/tests/test_models.py: 37 tests
backend/tests/test_pauses.py: 12 tests
backend/tests/test_preprocessor.py: 7 tests
backend/tests/test_prompts.py: 22 tests
backend/tests/test_queue_stats.py: 35 tests
backend/tests/test_queue_stats_cache.py: 53 tests
backend/tests/test_renderer.py: 19 tests
backend/tests/test_section_builder.py: 24 tests
backend/tests/test_srs_database.py: 88 tests
backend/tests/test_srs_database_anki_surface.py: 15 tests
backend/tests/test_srs_direction_state.py: 20 tests
backend/tests/test_srs_feedback.py: 3 tests
backend/tests/test_srs_guid.py: 7 tests
backend/tests/test_srs_migrations.py: 51 tests
backend/tests/test_srs_selector.py: 7 tests
backend/tests/test_srs_sync_scratch.py: 8 tests
backend/tests/test_storage.py: 25 tests
backend/tests/test_story.py: 18 tests
backend/tests/test_syllabify.py: 5 tests
backend/tests/test_tokenizer.py: 13 tests
backend/tests/test_transcript.py: 25 tests

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
head -40 backend/tests/test_curriculum.py | cat -n
```

```output
     1	"""Curriculum generation tests."""
     2
     3	import json
     4	from unittest.mock import AsyncMock, MagicMock
     5
     6	import pytest
     7
     8	from app.generation.curriculum import CurriculumGenerator
     9	from app.generation.prompts import PromptBuilder
    10	from app.models.curriculum import Curriculum
    11	from app.models.language import Language
    12
    13
    14	@pytest.fixture
    15	def language():
    16	    return Language.slovene()
    17
    18
    19	@pytest.fixture
    20	def prompt_builder(language):
    21	    return PromptBuilder()
    22
    23
    24	# -- PromptBuilder ----------------------------------------------------------
    25
    26
    27	def test_prompt_includes_topic(prompt_builder, language):
    28	    prompt = prompt_builder.build_curriculum_prompt(
    29	        topic="ordering coffee in Ljubljana",
    30	        language=language,
    31	        cefr_level="A2",
    32	        num_days=3,
    33	    )
    34	    assert "ordering coffee in Ljubljana" in prompt
    35
    36
    37	def test_prompt_includes_language_name(prompt_builder, language):
    38	    prompt = prompt_builder.build_curriculum_prompt(
    39	        topic="coffee",
    40	        language=language,
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
| **Testing** | Unit tests only | 1460 tests, ~99.95% branch coverage, cassette fixtures, 4 mock strategies, Playwright e2e |
| **API endpoints** | 10 endpoints | 28 endpoints |
| **SRS directions** | Single direction (recognition only) | Two directions per item (RECOGNITION L2→L1 + PRODUCTION L1→L2) with independent FSRS state |
| **SRS states** | new/learning/review/relearning + suspended | + `BURIED` (Anki bury), `KNOWN` (graduated), full Anki queue mapping |
| **Anki integration** | None | Bidirectional offline sync over `collection.anki2` SQLite (push → drain revlog → pull → create-new) |
| **Anki safety** | n/a | `safe_open` lock-probe + SHA-256 backup + integrity validation; USN normalization protocol |
| **Media** | EdgeTTS only | Forvo audio → EdgeTTS fallback + Pixabay images (token-overlap scoring) + ffmpeg LUFS normalize, deduped per-card |
| **Queue stats** | Live count from SRS DB | Cached daily-new-cap + FSRS-5 params parsed from Anki `deck_config` protobuf |
| **Frontend** | Generate / lesson / practice routes | + unified `/review`, `/admin/srs`, single Sync button, Anki-running gating |

**What was preserved from the prototypes:**
- Pimsleur 4-section format (KEY_PHRASES, NATURAL_SPEED, SLOW_SPEED, TRANSLATED)
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
./test.sh   # ruff lint + pytest (~1460 tests) + vitest (frontend) + playwright e2e
```

### Start the dev server

```bash
./start-dev.sh   # FastAPI at :8000 + SvelteKit at :5173
```

Open http://localhost:5173, enter a topic (e.g. "ordering coffee in Ljubljana"), choose CEFR level and days, click Generate → select a day → Generate Lesson → Render Audio → play.

### SRS review loop
First generate a curriculum and lesson (which registers SRS items via `POST /api/srs/listen`), then navigate to http://localhost:5173/review — the unified queue blends due cards and a daily-capped slice of new ones, alternating directions (L2→L1 and L1→L2). Rate each with Again / Hard / Good / Easy.

### SRS admin UI
Navigate to http://localhost:5173/admin/srs to browse and manage SRS items. Features: search (full-text across text and translation), filter by state, sortable columns, inline edit, single and bulk delete, reset schedule, suspend/unsuspend, force state, create new item.

### Anki sync
Click **Sync** in the UI (or `POST /api/anki/peer-sync`). The backend runs the peer-sync sequence against TT's own ``tt_collection``, which works with Anki open.

### Developer reference
For day-to-day developer commands, testing quirks (cassette modes, the offline-Anki test fixtures), and architectural conventions, see `AGENTS.md` at the repo root and `.claude/rules/anki-sync.md` for the USN/sync protocol details. CLAUDE.md is the project-level companion that points at the rules directory.

---

## PART 12: Anki Integration (Stage 3)

The biggest change since the original walkthrough is **bidirectional Anki sync**. TunaTale's SRS database now mirrors a user's Anki collection: items have stable Anki-compatible GUIDs, two review directions (recognition + production matching Anki ord 0/1), and a sync engine that reads and writes `collection.anki2` directly via SQLite. AnkiConnect (the HTTP plugin) is supported for compatibility but is no longer the primary path — offline sync is faster and works while Anki is closed.

This part explains the design from the inside out: domain shape (12.1), safety envelope (12.2), readers/writers (12.3), the four-phase sync flow (12.4), media pipeline (12.5), queue stats from Anki's protobuf deck config (12.6), and the API surface (12.7).

### 12.1 Two-Direction SRS Items

Each `SRSItem` now has independent FSRS state for two directions: **RECOGNITION** (L2→L1, shown the Slovene word and asked for the English) and **PRODUCTION** (L1→L2, the reverse). Anki models the same shape with `cards.ord = 0/1`. The model lives in `app/models/srs_item.py`.

```bash
sed -n '14,75p' backend/app/models/srs_item.py | cat -n
```

```output
     1	
     2	from dataclasses import dataclass, field
     3	from datetime import date
     4	from enum import Enum
     5	
     6	from .syntactic_unit import SyntacticUnit
     7	
     8	
     9	class SRSState(Enum):
    10	    """Learning state of an SRS item."""
    11	
    12	    NEW = "new"
    13	    LEARNING = "learning"
    14	    REVIEW = "review"
    15	    RELEARNING = "relearning"
    16	    SUSPENDED = "suspended"
    17	    BURIED = "buried"
    18	    KNOWN = "known"
    19	
    20	
    21	class Rating(Enum):
    22	    """Learner rating for an SRS review."""
    23	
    24	    AGAIN = 1  # Complete blackout / forgot
    25	    HARD = 2  # Significant difficulty
    26	    GOOD = 3  # Correct with some effort
    27	    EASY = 4  # Perfect recall
    28	
    29	
    30	class Direction(Enum):
    31	    """Review direction for an SRS item."""
    32	
    33	    RECOGNITION = "recognition"  # L2 → L1 (Anki ord=0)
    34	    PRODUCTION = "production"  # L1 → L2 (Anki ord=1)
    35	
    36	
    37	@dataclass
    38	class DirectionState:
    39	    """FSRS scheduling state for one direction of a collocation."""
    40	
    41	    direction: Direction
    42	    due_date: date
    43	    stability: float = 1.0
    44	    difficulty: float = 5.0
    45	    reps: int = 0
    46	    lapses: int = 0
    47	    state: SRSState = field(default=SRSState.NEW)
    48	    last_review: date | None = None
    49	    anki_card_id: int | None = None
    50	    anki_due: int | None = None
    51	    dirty_fsrs: bool = False
    52	    last_synced_at: str | None = None
    53	    last_rating: int | None = None
    54	
    55	
    56	class SRSItem:
    57	    """An SRS-tracked syntactic unit with per-direction FSRS scheduling.
    58	
    59	    Accepts two construction styles:
    60	
    61	    1. Two-direction (new): `SRSItem(syntactic_unit=..., directions={...}, guid=..., anki_note_id=...)`.
    62	    2. Flat legacy:         `SRSItem(syntactic_unit=..., due_date=..., stability=..., state=..., ...)`.
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
sed -n '135,205p' backend/app/anki/safety.py | cat -n
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
    61	    backup_dir.mkdir(parents=True, exist_ok=True)
    62	    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    63	    backup_path = backup_dir / f"collection.anki2.bak_{timestamp}"
    64	
    65	    src_conn = sqlite3.connect(str(collection_path))
    66	    dst_conn = sqlite3.connect(str(backup_path))
    67	    try:
    68	        src_conn.backup(dst_conn)
    69	    finally:
    70	        dst_conn.close()
    71	        src_conn.close()
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

The sync engine talks to the underlying store through four ports defined in `app/anki/sync.py`:

| Port | When used | Backend |
|------|-----------|---------|
| `OfflineReader` | Default sync; Anki must be closed | Direct SQLite `SELECT` against `collection.anki2` |
| `OfflineWriter` | Default sync | Direct SQLite `INSERT`/`UPDATE` with USN bookkeeping |
| `OnlineReader` | Compatibility / legacy paths | AnkiConnect JSON-RPC (`findNotes`, `notesInfo`) |
| `OnlineWriter` | Compatibility / legacy paths | AnkiConnect (`addNote`, `updateNoteFields`, `storeMediaFile`) |

Both *Reader* ports return the same in-memory shapes — `AnkiNote` and `AnkiCard` from `app/anki/sqlite_reader.py`. The card record carries the FSRS state parsed out of Anki's per-card data blob (queue, due, ivl, factor, lapses, reps), plus the `fsrs_data` payload (stability, difficulty, last review).

```bash
sed -n '38,80p' backend/app/anki/sqlite_reader.py | cat -n
```

```output
     1	def compute_due_date(queue: int, due_raw: int, col_crt: int) -> date:
     2	    """Convert Anki's queue-dependent due field to a Python date.
     3	
     4	    queue 2/3 (review/day-learn): due_raw is days since col.crt epoch.
     5	    queue 1 (learning): due_raw is an absolute unix timestamp (seconds).
     6	    queue 0 (new) or -1 (suspended): due_raw is a queue position — fall back to today.
     7	    """
     8	    if queue in (2, 3):
     9	        return date.fromtimestamp(col_crt) + timedelta(days=due_raw)
    10	    if queue == 1:
    11	        return datetime.fromtimestamp(due_raw).date()
    12	    return date.today()
    13	
    14	
    15	def find_deck_id(conn: sqlite3.Connection, deck_name: str) -> int | None:
    16	    """Find deck id by name. Tries col.decks JSON (legacy) then decks table (modern)."""
    17	    row = conn.execute("SELECT decks FROM col").fetchone()
    18	    if row:
    19	        try:
    20	            deck_data = json.loads(row[0])
    21	            for did, info in deck_data.items():
    22	                if isinstance(info, dict) and info.get("name") == deck_name:
    23	                    return int(did)
    24	        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
    25	            pass
    26	
    27	    try:
    28	        rows = conn.execute("SELECT id, name FROM decks").fetchall()
    29	        for r in rows:
    30	            if r[1] == deck_name:
    31	                return r[0]
    32	    except sqlite3.OperationalError:
    33	        pass
    34	
    35	    return None
    36	
    37	
    38	def fetch_notes_for_deck(conn: sqlite3.Connection, deck_id: int) -> list[AnkiNote]:
    39	    """Fetch all notes that have at least one card in the given deck."""
    40	    rows = conn.execute(
    41	        """
    42	        SELECT DISTINCT n.id, n.guid, n.mid, n.mod, n.tags, n.flds
    43	        FROM notes n
```

Two details from the reader are worth highlighting because they're easy to get wrong:

- **Dual deck lookup.** Modern Anki stores decks in a `decks` *table*, but legacy collections still keep the deck list as JSON in `col.decks`. `find_deck_id` reads JSON first then falls back to the table — neither is canonical, and which exists depends on the user's Anki version.
- **Queue-dependent due decoding.** Anki's `cards.due` field is overloaded: it's days-since-collection-epoch for queues 2/3 (review / day-learn), an absolute Unix timestamp for queue 1 (intra-day learning), and a positional integer for queues 0 / -1 (new / suspended). `compute_due_date` unifies these into a Python `date` and the offline reader propagates them through `AnkiCard.due_date`.

`OfflineWriter` is the first place where the safety rules from 12.2 turn into code. Every `INSERT`/`UPDATE` sets `usn = -1` and `mod = now()` on touched rows; every batch ends with `UPDATE col SET mod = ?, usn = -1`; revlog rows additionally bump `col.scm` only when the schema actually changed. `OfflineWriter.create_note` (a Stage 3.9 addition) hashes new media bytes, dedupes against existing files in the media collection, and stores both the binary and a row in `media` with the right `csum`.

### 12.4 The Four-Phase Sync Flow

The sync flow (``run_full_sync``) runs four phases in a single transaction. The order matters — getting it wrong loses revlog entries or creates duplicate notes.

```bash
grep -nE '    def sync_|    def _direction_differs|class AnkiSync' backend/app/anki/sync.py | head -20
```

```output
605:class AnkiSync:
637:    def sync_pull(self, dry_run: bool = False) -> PullReport:
773:    def sync_push(self, dry_run: bool = False, force_fsrs: bool = False) -> PushReport:
```

1. **`sync_create_new`** — for every TunaTale collocation that has no `anki_note_id`, fetch media (12.5), call `writer.create_note` to add it to Anki, and stash the new note id back on the SRSItem. New notes are filtered against existing GUIDs/L2-text-with-disambiguation to avoid duplicate-note errors (the B11/B16/B17/B19 fixes from session 2 of S3.11 — `detect_and_link_duplicates` does an id-first lookup before falling back to GUID).
2. **`sync_push`** — for every direction with `dirty_fsrs=True` or pending field edits, write the FSRS state and field changes to Anki via `writer.update_*`. Push uses `setSpecificValueOfCard` (preflighted in `preflight_set_specific_value_of_card`) since stock AnkiConnect doesn't expose FSRS-state edits. Suspends, due dates, and field text round-trip here.
3. **Drain pending revlog.** TunaTale records every review locally in a scratch `pending_revlog` table — direction id, rating, ease/factor, time taken — independently of whether Anki was reachable. `drain_pending_revlog_to_writer` flushes those rows to Anki's `revlog` table (this is what populates Anki's review history graph). The drain happens *after* push so the rated card already has its updated FSRS state on the Anki side; running it before push could lose entries if push fails partway. (See commit `67e9a57` — B14 swap.)
4. **`sync_pull`** — read every note in the deck, diff against TunaTale's local copy, and update SRSItems whose Anki side changed. The diff function `_direction_differs` compares state, due, stability, difficulty, lapses, reps, last_rating, and `anki_due` — anything else (e.g. internal review counts) is treated as noise. **Local FSRS state with `dirty_fsrs=True` is preserved** even if Anki has different values, since the next push will overwrite Anki anyway (the b9bbcb4 fix). Conflicts on field text are recorded in the `sync_conflicts` scratch table for later resolution.

Each phase returns a typed report (`CreateNewReport`, `PushReport`, `PullReport`) and the API combines them into a single response shape.

Two helper concepts appear repeatedly:

- **`force_fsrs` gating** (`ensure_force_fsrs_ack`). Pushing FSRS-state changes to Anki is irreversible from Anki's perspective. The first time a user runs sync the writer prompts for explicit ack and writes a marker file; subsequent runs read the marker and proceed silently.
- **Mode auto-detection** (`detect_mode`). The CLI wrapper sniffs whether AnkiConnect is reachable on `anki_connect_url`; if yes, it uses Online ports; if no, it falls back to Offline. The HTTP API always uses Offline.

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
     7	from typing import Any
     8	
     9	from .forvo import fetch_forvo_audio
    10	from .normalize import normalize_audio
    11	from .pixabay import fetch_pixabay_image
    12	from .tts import DEFAULT_VOICE, generate_tts_audio
    13	
    14	
    15	@dataclass
    16	class MediaResult:
    17	    audio_bytes: bytes | None = None
    18	    audio_source: str | None = None
    19	    image_bytes: bytes | None = None
    20	    image_ext: str | None = None
    21	    image_url: str | None = None
    22	
    23	
    24	async def fetch_card_media(
    25	    word: str,
    26	    english: str,
    27	    *,
    28	    pixabay_key: str,
    29	    http_client: Any = None,
    30	    tts_voice: str = DEFAULT_VOICE,
    31	    normalize: bool = True,
    32	    used_image_urls: set[str] | None = None,
    33	    _forvo_fn: Callable[..., bytes | None] | None = None,
    34	    _tts_fn: Callable[..., Awaitable[bytes | None]] | None = None,
    35	    _pixabay_fn: Callable[..., Any] | None = None,
    36	    _normalize_fn: Callable[..., bytes] | None = None,
    37	) -> MediaResult:
    38	    """Fetch audio and image for a vocabulary card.
    39	
    40	    Tries Forvo first, falls back to edge-tts. Image from Pixabay.
    41	    Pass used_image_urls (a shared set) across cards to prevent duplicate images.
    42	    """
    43	    forvo_fn = _forvo_fn or fetch_forvo_audio
    44	    tts_fn = _tts_fn or generate_tts_audio
    45	    pixabay_fn = _pixabay_fn or fetch_pixabay_image
    46	    norm_fn = _normalize_fn or normalize_audio
    47	
    48	    result = MediaResult()
    49	
    50	    audio = forvo_fn(word, http_client=http_client)
    51	    if audio is not None:
    52	        result.audio_source = "forvo"
    53	        result.audio_bytes = audio
    54	    else:
    55	        audio = await tts_fn(word, voice=tts_voice)
    56	        if audio is not None:
    57	            result.audio_source = "tts"
    58	            result.audio_bytes = audio
    59	
    60	    if result.audio_bytes is not None and normalize:
    61	        result.audio_bytes = norm_fn(result.audio_bytes)
    62	
    63	    img = pixabay_fn(
    64	        english,
    65	        api_key=pixabay_key,
    66	        http_client=http_client,
    67	        used_urls=frozenset(used_image_urls) if used_image_urls is not None else frozenset(),
    68	    )
    69	    if img is not None:
    70	        result.image_bytes, result.image_ext, result.image_url = img
    71	        if used_image_urls is not None:
    72	            used_image_urls.add(result.image_url)
    73	
    74	    return result
```

Audio path: **Forvo → EdgeTTS fallback → ffmpeg LUFS normalize**. Forvo is a community pronunciation database — `forvo.py` scrapes the public word page (no API key needed) and returns the first MP3 link. If no Forvo audio exists for the word, EdgeTTS synthesizes a fallback. Either way the resulting bytes go through `normalize.py`, which uses ffmpeg's `loudnorm` filter to clamp output to a target LUFS so cards in the same deck have consistent volume.

Image path: **Pixabay with token-overlap scoring**. `build_query(english)` strips function words; `fetch_pixabay_image` scores candidate hits against the query tokens (`_tag_overlap`) and picks the best match not already in `used_image_urls`. The shared `used_image_urls` set threads through every call in the same sync run so two cards that would otherwise pick the same image get distinct images instead — this was the dedup feature added in commit `85279f6`.

The `/api/admin/refresh-media` endpoint and the `app/media/importer.py` module handle a separate task: copying Anki's `collection.media/` files into TunaTale's local `media_dir` so the review UI can serve them. Since commit `83c4c9e` this is invoked as a side effect of every sync, not via a manual button.

### 12.6 Queue Stats from Anki's Protobuf Deck Config

A subtle but important detail: modern Anki stores deck configuration (daily new cap, FSRS parameters, bury settings) as **protobuf-encoded blobs** in the `deck_config` table — not JSON in `col.dconf` like older versions. `app/srs/queue_stats.py` includes a hand-rolled minimal protobuf decoder (`_pb_read_varint`, `_pb_find_varint_field`, `_pb_find_packed_float_field`, etc.) so TunaTale can read those values without a protoc-generated stub.

```bash
sed -n '154,235p' backend/app/srs/queue_stats.py | cat -n
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
    11	    deck_row = conn.execute("SELECT kind FROM decks WHERE name = ?", (deck_name,)).fetchone()
    12	    if deck_row is None or not deck_row[0]:
    13	        return None
    14	
    15	    kind_blob = deck_row[0]
    16	    normal_kind_bytes = _pb_find_len_field(kind_blob if isinstance(kind_blob, bytes) else bytes(kind_blob), 1)
    17	    if normal_kind_bytes is None:
    18	        return None
    19	
    20	    conf_id = _pb_find_varint_field(normal_kind_bytes, 1)
    21	    if conf_id is None:
    22	        return None
    23	
    24	    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    25	    if config_row is None or not config_row[0]:
    26	        return None
    27	
    28	    config_blob = config_row[0]
    29	    config_blob = bytes(config_blob) if isinstance(config_blob, memoryview) else config_blob
    30	
    31	    weights = _pb_find_packed_float_field(config_blob, _FSRS5_WEIGHTS_FIELD)
    32	    if weights is None or len(weights) != 19:
    33	        return None
    34	
    35	    retention_raw = _pb_find_fixed32_float_field(config_blob, _DESIRED_RETENTION_FIELD)
    36	    retention = float(retention_raw) if retention_raw is not None else 0.9
    37	
    38	    try:
    39	        return FSRSParams(weights=tuple(weights), desired_retention=retention)
    40	    except (ValueError, TypeError):  # pragma: no cover
    41	        return None  # pragma: no cover
    42	
    43	
    44	def _read_new_per_day_from_deck_config_table(conn: sqlite3.Connection, deck_name: str) -> int | None:
    45	    """Read new-per-day from modern Anki's deck_config table (Anki ≥2.1.55).
    46	
    47	    Modern Anki stores deck configs as protobuf BLOBs in the deck_config table.
    48	    The deck's conf_id is found via decks.kind (protobuf: field 1 LEN → field 1 VARINT).
    49	    The cap is at field 9 (VARINT) in deck_config.config.
    50	    """
    51	    try:
    52	        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    53	    except sqlite3.Error:  # pragma: no cover
    54	        return None  # pragma: no cover
    55	
    56	    if "deck_config" not in tables or "decks" not in tables:
    57	        return None
    58	
    59	    deck_row = conn.execute("SELECT kind FROM decks WHERE name = ?", (deck_name,)).fetchone()
    60	    if deck_row is None or not deck_row[0]:
    61	        return None
    62	
    63	    kind_blob = deck_row[0]
    64	    # NormalDeckKind: field 1 (LEN) contains the config sub-message
    65	    normal_kind_bytes = _pb_find_len_field(kind_blob if isinstance(kind_blob, bytes) else bytes(kind_blob), 1)
    66	    if normal_kind_bytes is None:
    67	        return None
    68	
    69	    # Within NormalDeckKind, field 1 (VARINT) = conf_id
    70	    conf_id = _pb_find_varint_field(normal_kind_bytes, 1)
    71	    if conf_id is None:
    72	        return None
    73	
    74	    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    75	    if config_row is None or not config_row[0]:
    76	        return None
    77	
    78	    config_blob = config_row[0]
    79	    # DeckConfig.Config: field 9 (VARINT) = new_per_day
    80	    return _pb_find_varint_field(config_blob if isinstance(config_blob, bytes) else bytes(config_blob), 9)
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
| `test_anki_sqlite_reader.py` | `fetch_notes_for_deck`, `compute_due_date`, dual deck lookup |
| `test_anki_sqlite_writer.py` | GUID backfill plan + apply, USN bookkeeping |
| `test_anki_offline_writer_create_note.py` | Stage 3.9 — offline note creation with media dedup |
| `test_anki_sync_pull.py`, `test_anki_sync_push.py` | Per-direction diffs, conflict recording, dirty-FSRS preservation |
| `test_anki_sync_create_new.py` | Duplicate detection (id-first then GUID), media linking |
| `test_anki_sync_round_trip.py` | Full push → drain → pull cycle |
| `test_anki_sync_force_fsrs.py`, `test_anki_syncKey_preflight.py` | force-FSRS ack flow, setSpecificValueOfCard preflight |
| `test_anki_sync_mode_detection.py` | Online vs offline auto-detection |
| `test_anki_connect_client.py` | JSON-RPC client over a mock transport |
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
| **H4** | `app.anki.backfill_guids` | Rewrites every Anki note GUID to TunaTale's deterministic formula (sha256 of language + visible text + DisambigKey). After this, sync's GUID-based reconciliation works. `app.anki.sqlite_writer.check_anki_web_sync_active` warns the user to force-upload after running. |
| **H5** | `app.anki.normalize_usns` | Post-full-upload USN clamp (already covered in 12.2). Resets `cards.usn`, `notes.usn`, `revlog.usn` back to `col.usn` after the user has done a forced full upload. |

Each step has a `__main__` entry point (`uv run python -m app.anki.<module>`), goes through `safe_open` for backup + lock probe, and emits a dry-run plan before mutating. All five test files in PART 12.8 cover these CLIs.

After this pipeline, ongoing sync uses only the peer-sync endpoint (PART 12.4) — no further bootstrap is needed unless the user adds a third notetype or imports a substantially new deck.

`app.anki.model_discovery` is a small support utility: given a deck and an open Anki connection (or just the offline collection), it figures out which notetype's notes to sync. Called by the sync handler whenever `settings.anki_model_name` is unset.

---

## PART 13: Frontend Updates

The SvelteKit app in `frontend/` got significant new UI work alongside the Anki integration.

### 13.1 Routes

```
frontend/src/routes/
├── +page.svelte                # Home: curriculum form + list
├── +layout.svelte              # Header, Sync button, Anki status badge
├── c/[curriculumId]/           # Curriculum overview + day picker
│   └── l/[lessonId]/           # Lesson view: transcript, audio player, render
├── review/                     # Unified review queue (replaces the old /practice)
└── admin/srs/                  # SRS item admin: search/edit/bulk delete/reset/suspend
```

The notable changes:

- **`/review`** replaces the per-lesson `/practice` flow. It pulls from `/api/srs/review-queue`, which serves a unified queue blending due cards with a daily-capped slice of new ones (capped by the `new_per_day` value cached from Anki — see 12.6) and alternates direction per card. Each card shows L2 audio, image, English gloss, and optional grammar/note metadata; the user rates Again / Hard / Good / Easy. Media URLs come pre-populated in the queue payload (commit `52003c2`); `DrillCard` resets its revealed state between cards (commit `472b845`).

- **`/admin/srs`** provides full CRUD over the SRS database: paginated table, search across text and translation, state filter, sortable columns, inline edit, single + bulk delete, reset schedule, suspend/unsuspend, force state, create new item.

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
    12	    database_url: str = "sqlite:///./tunatale.db"
    13	    llm_mode: str = "mock"  # mock | live | record | patch
    14	    llm_model: str = "llama-3.3-70b-versatile"
    15	
    16	    anki_collection_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.anki2").expanduser()
    17	    anki_media_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.media").expanduser()
    18	    anki_deck_name: str = "0. Slovene"
    19	    anki_backup_dir: Path = Path("~/.tunatale/anki-backups").expanduser()
    20	    media_dir: Path = Path("./media")
    21	    anki_fallback_log: Path = Path("~/.tunatale/logs/anki-fallback.log").expanduser()
    22	
    23	    anki_connect_url: str = "http://127.0.0.1:8765"
    24	    anki_model_name: str = ""
    25	    forvo_api_key: str = ""
    26	    pixabay_api_key: str = ""
    27	    anki_new_per_day_default: int = 20
    28	
    29	
    30	settings = Settings()
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

`POST /api/srs/items/{id}/state` lets the UI flip a card directly to a non-FSRS state. The valid transitions are `new`, `learning`, `known`, `ignored` — the frontend cycles through them in this order:

```bash
sed -n "20,29p" frontend/src/routes/c/\[curriculumId\]/l/\[lessonId\]/+page.svelte
```

```output
	const STATE_CYCLE: Record<string, string> = {
		unknown: 'learning',
		new: 'learning',
		learning: 'known',
		review: 'known',
		relearning: 'known',
		known: 'ignored',
		ignored: 'new',
		suspended: 'new'
	};
```

A click on a word advances it one step around the cycle (`unknown → learning → known → ignored → new → …`). Stepping into `ignored` no longer calls `set_state_by_id(SUSPENDED)`; it routes through a dedicated endpoint that knows whether the row was ever synced to Anki.

`POST /api/srs/items/{id}/untrack` lives in `backend/app/api/srs.py:624` and delegates to `SRSDatabase.untrack_collocation`:

```bash
sed -n "785,815p" backend/app/srs/database.py
```

```output
    def untrack_collocation(self, row_id: int) -> dict[str, str]:
        """Remove a collocation from the user's learning queue.

        If the row was never pushed to Anki (anki_note_id IS NULL), delete it
        outright (cascade deletes both direction rows). Otherwise suspend both
        directions and mark dirty_fsrs=1 so the next Anki push suspends the card.

        Returns {"action": "deleted"} or {"action": "suspended"}.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT anki_note_id FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return {"action": "deleted"}
            if row["anki_note_id"] is None:
                conn.execute(
                    "DELETE FROM violations WHERE collocation_text = (SELECT text FROM collocations WHERE id = ?)",
                    (row_id,),
                )
                conn.execute("DELETE FROM collocations WHERE id = ?", (row_id,))
                self._commit(conn)
                return {"action": "deleted"}
            conn.execute(
                "UPDATE collocation_directions SET state = 'suspended', dirty_fsrs = 1 WHERE collocation_id = ?",
                (row_id,),
            )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)
            return {"action": "suspended"}
```

Two-path semantics:

- **Never-synced rows** (`anki_note_id IS NULL`) — e.g. words auto-added by `/listen` that the user immediately marks "ignored" before any sync — get hard-deleted, taking their `violations` rows with them. Cascade FK delete handles the direction rows.
- **Synced rows** — both directions flip to `state='suspended', dirty_fsrs=1`. The next `sync_push` translates that into Anki's `queue=-1` (suspended) via the existing dirty-FSRS branch, so the card disappears from Anki's review pool too.

The matching state-set endpoint (`/state`) special-cases `"learning"` to call `db.promote_to_learning` instead of `set_state_by_id` — `promote_to_learning` writes a fresh `last_review = now`, `due_date = today`, and `dirty_fsrs = 1`, but leaves `left`/`due_at` as NULL. That asymmetry is intentional but documented in the docstring at `backend/app/srs/database.py:874`: TT shows the card as LEARNING immediately, Anki receives it as a same-day-due card without learning-step metadata, and the user re-grades it normally on next session.

### 15.2 The `/api/srs/listen` Endpoint

`POST /api/srs/listen` is the entry point: the user clicks "I listened to this lesson" and the lesson's words are tokenized, lemmatized, and registered as SRS items with a Rating.GOOD grade. It now also branches on `card_type`:

```bash
sed -n "220,289p" backend/app/api/srs.py
```

```output
@router.post("/listen", status_code=200)
async def mark_lesson_listened(body: ListenRequest, request: Request):
    store = request.app.state.content_store
    lesson = store.get_lesson(body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.app.state.srs_db

    # ── Word-level tracking from NATURAL_SPEED section ──────────────────
    from app.models.lesson import SectionType

    token_glosses: dict[str, str] = lesson.generation_metadata.get("token_glosses", {})

    natural_speed = next(
        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
        None,
    )

    unique_lemmas: set[str] = set()
    lemma_to_sentence: dict[str, str] = {}
    if natural_speed is not None:
        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue
            for surface in tokenize(phrase.text):
                lemma = _lemmatizer.lemmatize(surface, lesson.language_code)
                unique_lemmas.add(lemma)
                if lemma not in lemma_to_sentence:
                    lemma_to_sentence[lemma] = phrase.text

    cloze_enabled = lesson.language_code == "sl" and db.get_enable_cloze_cards()

    for lemma in unique_lemmas:
        is_cloze = cloze_enabled and is_function_word(lemma, lesson.language_code)
        unit = SyntacticUnit(
            text=lemma,
            translation=token_glosses.get(lemma, ""),
            word_count=1,
            difficulty=1,
            source="llm",
            lemma=lemma,
            card_type="cloze" if is_cloze else "vocab",
            source_sentence=lemma_to_sentence.get(lemma, "") if is_cloze else "",
        )
        db.add_collocation(unit, language_code=lesson.language_code)
        item = db.get_collocation_by_lemma(lemma)
        if item is None:
            continue  # pragma: no cover — lemma is always filled for single-word units
        rating = _WORD_RATING_MAP.get(body.word_ratings.get(lemma, "good"), Rating.GOOD)
        now = datetime.datetime.now(datetime.UTC)
        updated = schedule(item, rating, params=resolve_fsrs_params(db)[0], now=now)
        db.update_collocation(updated)

    # ── Key phrase registration (preserves translations) ─────────────────
    for kp in lesson.key_phrases:
        if db.get_collocation(kp.phrase) is not None:
            continue  # idempotent — already registered from a prior listen
        unit = SyntacticUnit(
            text=kp.phrase,
            translation=kp.translation,
            word_count=min(8, max(1, len(kp.phrase.split()))),
            difficulty=1,
            source="llm",
        )
        db.add_collocation(unit, language_code=lesson.language_code)

    registered = len(unique_lemmas) + len(lesson.key_phrases)
    return {"status": "ok", "registered": registered}

```

Notable details:

- **Lemma-keyed registration.** The natural-speed phrases are tokenized via `app.srs.tokenizer.tokenize` then lemmatized through `app.srs.lemmatizer.Lemmatizer.lemmatize` (a thin wrapper over a hand-curated dictionary). The lemma is what gets stored as `collocations.text`, so subsequent listens of the same lesson hit the existing row (`unique_lemmas` dedup is per-call; `db.add_collocation` ON CONFLICT DO NOTHING dedups across calls).
- **Cloze branching.** When `enable_cloze_cards` is on (DB-backed flag, default OFF) and the language is Slovene, function words go through the cloze path: `card_type="cloze"`, `source_sentence` captured from the first natural-speed phrase containing the surface. Everything else gets `card_type="vocab"`. See PART 15.5 below for the cloze pipeline.
- **Auto-grade.** Every registered lemma gets a `Rating.GOOD` grade immediately — the user already heard it, so the FSRS state advances on first listen rather than waiting for a manual review.
- **Key phrases are preserved verbatim** (`kp.phrase` is the original surface form, not lemmatized). Their `translation` is already known from the curriculum, so it survives the `idempotent` guard at line 276 even on re-listen.

### 15.3 The Transcript Component (Phase D)

`frontend/src/lib/components/Transcript.svelte` is a 175-line Svelte 5 component (with a 261-line test file) that renders the lesson dialogue with per-word color coding, click-to-cycle state, drag-to-select phrase capture, and an "Add phrase…" affordance for phrases that don't appear verbatim. The data shape comes from `GET /api/srs/lesson/{lesson_id}/transcript` (`backend/app/api/srs.py:291`):

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

Click a word and the parent page calls `handleStateChange(lemma, srs_item_id)` which (1) reads `currentState` off the most recent transcript snapshot, (2) computes `nextState = STATE_CYCLE[currentState]`, (3) creates an SRS row if needed (`srs_item_id === null` for cards never registered), and (4) calls the appropriate endpoint (`/untrack` for `ignored`, `/state` otherwise). The transcript is then re-fetched so the next click reads the updated state.

### 15.4 Translate Button + Off-Transcript Phrase Entry (Phase E)

When the user drags to select a phrase ("dober dan" → "good day") that isn't pre-translated, the popover shows a ✨ button. Clicking it calls a new endpoint:

```bash
sed -n "347,363p" backend/app/api/srs.py
```

```output
_VALID_LANGUAGE_CODES = frozenset({"sl", "en"})


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
2. **Storage.** Migration v18→v19 adds `collocations.card_type TEXT DEFAULT 'vocab'`. Cloze cards get `card_type='cloze'` and `source_sentence=<the natural-speed phrase>`. `add_collocation` (`backend/app/srs/database.py:238`) only creates a RECOGNITION direction for cloze cards — there's no L1→L2 production side for a function-word fill-in.
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

Stage 3 (PART 12) introduced bidirectional sync. Between syncs, both apps schedule independently, and TT must mirror Anki's algorithms closely enough that switching apps doesn't feel discontinuous. The "layers" history lives in `docs/anki-parity-layers.md` and the principles plus a divergence decision tree live in `.claude/rules/anki-queue-parity.md` — read those before editing `app/api/srs.py`, `app/srs/fsrs.py`, `app/srs/queue_stats.py`, or `app/anki/sync.py`.

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

`count_new_introduced_today` (`backend/app/srs/database.py:1589`) just filters distinct `collocation_id` with `introduced_at` in today's UTC window:

```bash
sed -n "1602,1615p" backend/app/srs/database.py
```

```output
        local_tz = datetime.now().astimezone().tzinfo
        start_utc = datetime.combine(today, time(0), tzinfo=local_tz).astimezone(UTC)
        end_utc = datetime.combine(today + timedelta(days=1), time(0), tzinfo=local_tz).astimezone(UTC)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT collocation_id) FROM collocation_directions
                WHERE introduced_at IS NOT NULL
                  AND introduced_at >= ?
                  AND introduced_at < ?
                """,
                (start_utc.isoformat(), end_utc.isoformat()),
            ).fetchone()
            return row[0] if row else 0
```

Pre-Layer-26 rows have NULL `introduced_at` and naturally fall out of the count. Going forward, every new grade populates the column. The local-timezone-to-UTC math handles the daily rollover the same way `count_review_due_collocations` does.

The Layer 22 distinction (`introduced_at` is a one-shot stamp, NOT a sticky marker) matters: don't conflate it with `prior_state='new'`. `prior_state` lives for the entire intro arc and applies to revlog correctness; `introduced_at` is a fixed timestamp that anchors Anki's `newToday` parity.

### 16.3 Layer 27 — Daily Unbury Sweep

Anki resets `queue=-2` (sibling-buried) and `queue=-3` (scheduler-buried) cards back to their original queues once per day, on the first queue rebuild after rollover. TT must mirror this — stale `state='buried'` rows from a prior day under-count `count_review_due_collocations` and silently drop cards from the review pool.

`SRSDatabase.unbury_if_needed(today)` (`backend/app/srs/database.py:1483`) runs at the top of three call sites: `/queue-stats`, `/review-queue` (via `_compute_live_main`), and `sync_pull`. It's tracked via `anki_state_cache['last_unbury_day']`:

```bash
sed -n "1497,1515p" backend/app/srs/database.py
```

```output
        Returns the number of rows unburied.
        """
        cached = self.get_anki_state_cache("last_unbury_day")
        today_iso = today.isoformat()
        if cached and cached[0] == today_iso:
            return 0
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE collocation_directions
                SET state = CASE WHEN reps > 0 THEN 'review' ELSE 'new' END
                WHERE state = 'buried'
                """
            )
            rowcount = cursor.rowcount
            self._commit(conn)
        self.set_anki_state_cache("last_unbury_day", today_iso)
        return rowcount

```

Idempotency matters: `sync_pull` within the same day may land *new* `state='buried'` rows (today's sibling-buries that must stick). The `last_unbury_day` cache guards against re-sweeping them.

### 16.4 Layer 25 + Layer 28 — Cross-Direction Gather, Bury, Template Sort

Per-direction ordering in `get_new_items` is necessary but not sufficient. Anki's `add_new_card` (rslib `queue/builder/gathering.rs:63-169`) gathers BOTH ords in one pass and proactively buries the LATER sibling per note — so the higher-due sibling wins. Then `sort_new` (`sorting.rs:14-36`) stably re-sorts by `ord` (the Template step) so ord=0 (recognition) comes before ord=1 (production) within each note's surviving direction.

TT's `_merge_directions` (`backend/app/api/srs.py:707`) mirrors the gather sort key exactly:

```bash
sed -n "707,746p" backend/app/api/srs.py
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
        ds = item.directions[direction]
        ord_value = 0 if direction == Direction.RECOGNITION else 1
        primary = (0, 0) if ds.anki_due is None else (1, -ds.anki_due)
        return (*primary, ord_value, ds.anki_card_id or (1 << 62), row_id)

    combined.sort(key=_gather_key)
    return combined
```

After `_merge_directions`, `_compute_live_main` runs `_bury` (`backend/app/api/srs.py:850`) to keep only the first-seen survivor per `collocation_id`. Then a final stable sort by `ord` (`nonlearning_new.sort(key=lambda t: 0 if t[3] == Direction.RECOGNITION else 1)`) reproduces Anki's Template step.

Layer 28's fix was the `časa`/`sekira` head-of-queue divergence: per-direction sorts let recognition-bucket order disagree with Anki because the gather/bury order on the production side was selecting a different survivor. The interleaved merge fixed it.

### 16.5 Layer 29 — Eager `session_main_queue` Rebuild on Sync

`session_main_queue` is the DB-backed frozen queue order — Anki rebuilds it once at session open / sync; TT mirrors the freeze moment. Before Layer 29, `sync_pull` only **cleared** the cache and deferred rebuild to the next `/review-queue` request. Hours could pass before that request, letting the underlying pool shift — the two apps froze their queues at different moments, causing off-by-slot drift on the first-new-card position.

Layer 29 added `build_and_freeze_main_queue(db)` in `backend/app/api/srs.py:871` and called it immediately after the clear in `sync_pull`:

```bash
sed -n "871,882p" backend/app/api/srs.py
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

`_compute_live_main` (`backend/app/api/srs.py:801`) was extracted out of `get_review_queue` for this: the live-pool build logic up through the spread step is shared between the route handler and the eager-rebuild call. The route handler still owns cache reconciliation, learning-card assembly, and the collapse hack — those depend on the request-scoped `now`/`cutoff`.

Deploy-time pitfall to remember: the cache lives in `anki_state_cache` (DB-backed), so it survives backend restarts. After changing queue-assembly logic, an existing cache row will replay the OLD order until the next sync — restart alone does NOT invalidate it. When debugging a "fix doesn't seem to be working" report, run `clear_session_main_queue` first (see the diagnostic in `.claude/rules/anki-queue-parity.md`) before concluding the fix is broken.

### 16.6 Layer 30 — `_queue_to_state` Must Trust `queue`, Not `reps`

The previous mapper had a fallback `if reps == 0: return SRSState.NEW`. That broke when an Anki user hit "Forget" on a graduated card — `cards.queue` stays at 2 (review) but `cards.reps` resets to 0. The fallback wrongly mapped these to NEW, surfacing them as fresh new cards in TT.

`_queue_to_state` (`backend/app/anki/sync.py:696`) now treats `queue` as authoritative:

```bash
sed -n "696,719p" backend/app/anki/sync.py
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

`import_seed` and the sync_pull `get_note_records` path both use the updated extractor, so new imports come in clean. For the 39 already-mangled rows in the live DB, a one-shot script under `app/anki/fix_html_concat_imports.py` walks the TT DB, cross-checks the linked Anki note, and either renames the row (`text=X, translation=Y`) or deletes it when a clean-X twin collocation already exists. The script is read-only on `collection.anki2`, mutates only `tunatale.db`, supports `--dry-run`, and is invoked as:

```
uv run python -m app.anki.fix_html_concat_imports [--dry-run]
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

collapsed to `self._record_conflict(report, guid=..., direction=..., field=..., local=..., remote=..., resolution=..., dry_run=dry_run)` (`backend/app/anki/sync.py:874`).

**`_resolve_prior_state` closure** (commit `38d2804`). The call-site signature was passing `first_review_ms`, `today_start_ms`, and the local direction state through repeated kwargs. The refactor introduces a per-iteration `_prior` closure that captures `card_rec.first_review_ms` and `today_start_ms` once, leaving the call site as `_prior(local_dir, new_state)`. Same idea applied to `_intro_at = _resolve_introduced_at`. Visual noise dropped, behavior identical.

### 17.2 Three Dead Pipelines Deleted

**`_factor_to_fsrs_difficulty` helper** (commit `55d57b2`). The push path used to compute an FSRS difficulty from the Anki ease factor before writing revlog. Layer 17+ obsoleted it (we now persist `prior_state` and use `_derive_revlog_shape`), but the helper plus its 12-test suite hung on. Removed both.

**`_spread_mix.ratio_override`** (commit `916e0bf`). Layer 9 added a parameter to override the intersperser ratio at session-start; Layer 14 reverted that approach but left the parameter in place. The parameter and its tests are gone.

**Review-count pipeline** (commit `b4e6fd7`). An entire `count_review_*` family inside `queue_stats.py` plus a 512-line test file (`tests/test_queue_stats_review.py`) and a 193-line cache test file (`tests/test_queue_stats_cache.py`) — all driving a badge logic path that hadn't been wired to the API since the Phase A refactor. The `count_review_due_collocations` method (the path the UI actually reads) was left in `database.py`. Deletes:

- 251 lines from `app/srs/queue_stats.py`
- `tests/test_queue_stats_cache.py` (193 lines)
- `tests/test_queue_stats_review.py` (512 lines)

### 17.3 Why It's Worth Reading

When debugging a queue divergence, dead code is a trap: the divergence playbook in `.claude/rules/anki-queue-parity.md` walks specific helpers, and if a stale one is still in the tree, it can look like the active implementation. The Cleanup pass made the file harder to misread. Future cleanups should follow the same shape: prove the path is dead with `git grep` + test removal, delete in one commit, leave the rule file untouched.

---

## PART 18: Parity Testing Harness

TT mirrors Anki's scheduling algorithms, and the divergence history (`docs/anki-parity-layers.md`, 48 layers) reflects how many subtle branches that touches. The parity harness lets TT pin its parallel functions against Anki's actual scheduler at test time, before divergences reach a user-visible badge.

### 18.1 Subprocess Boundary

`backend/tests/anki_oracle/` holds the three-file harness: `synthetic_collection.py` builds a minimal modern-schema `collection.anki2` on disk (with the `config` table modern Anki actually reads, not just legacy `col.conf` JSON); `oracle.py` is the subprocess that opens the collection, enables V3, and runs JSON-in/JSON-out ops; `harness_fixtures.py` exposes the pytest fixtures + `run_oracle()` helper.

**Backend production code must never `import anki`** (queue-parity rule 1 — TT cannot have a runtime dependency on Anki being installed). The harness spawns a separate process via `uv run --with anki python oracle.py`. Backend tests don't import anki either; they call `run_oracle(collection_path, operations)`. CI runs without `--run-oracle` so it doesn't need anki installable in the image; `./test.sh` passes the flag locally.

### 18.2 What's Pinned

Five parity-test files under `backend/tests/test_parity_*.py` each cover a cluster:

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

Writes are live. Reads aren't yet — `sync_pull` still uses field-merge, and the replay script is diagnostic-only. The endgame (collapse field-merge into invariant-check, drop `prior_state` / `introduced_at`) waits on an empirical measurement comparing replay output to Anki's `cards.data` directly. Procedure and decision gate (≥95% / 50–95% / <50%) at `docs/stage-3b-empirical-measurement.md`; measurement script at `backend/app/anki/measure_stage3b_premise.py`.

---

## PART 20: Cloze Pipeline

Cloze cards (introduced in PART 15.5) target Anki's built-in Cloze notetype with `card_type='cloze'` set on the `SyntacticUnit`. Only the PRODUCTION direction exists — the user supplies the missing word given the surrounding sentence. The pipeline produces the cloze text, sentence and word audio, an L1 sentence translation, and syncs all of it bidirectionally with Anki.

### 20.1 Cloze Text And Function-Word Detection

`make_cloze_text(sentence, target_word)` in `app/cloze/` wraps the target with Anki's `{{c1::word}}` syntax. The frontend rendering uses Unicode-aware lookarounds to mask the word — ASCII-only `\b` doesn't match around š/č/ž. `is_function_word(word, language)` keys off a per-language list (Slovene lives at `app/cloze/function_words/sl.py`); the `/listen` endpoint creates a cloze row only for function-word matches when the cloze feature flag is enabled, no-op otherwise.

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
- **E2E: Playwright** with 11 specs covering curriculum navigation, day picker, lesson page header, the `/admin/srs` flow, the review loop including Again-rating queue placement, and SRS-seeding helpers shared via `tests/helpers.ts`.

### 21.1 Svelte 5 Phantom-Filter Coverage Gate

The Svelte 5 compiler injects template fragments that v8 reports as uncovered "branches" no test can reach (`'} created, {'`, ternary literals like `null`, `?? ''` defensives). Without filtering, threshold-based coverage gates would have to sit around 75% to absorb the noise.

`frontend/scripts/coverage-gate.ts` replaces Vitest's `thresholds:` block. It reads `coverage/coverage-final.json` and classifies each uncovered sub-location via `isPhantom(branchType, text, synthetic)`: cond-expr (`?:`) is phantom if text is a JS literal; binary-expr (`||`/`&&`/`??`) is phantom if it brackets a template-interp boundary or is a bare literal; empty source ranges are phantom; unknown branch types stay real (conservative). Drops are logged to `coverage/dropped-branches.json`; the gate then asserts 100% per-file on every metric.

`frontend/tests/coverage-gate.test.ts` pins every classification against empirical TunaTale cases — adding or changing a rule means updating both the heuristic and the test.

Maintenance note (`.claude/rules/testing.md`): after any `svelte` / `@vitest/coverage-v8` bump, eyeball the gate's "dropped N phantom branch(es)" line (baseline 46 on 21 files). A >20% delta means either a new phantom shape the filter misses or real bugs misclassified as phantom — fix the heuristic, don't lower the threshold.

---

## PART 22: Sentence-Aware Lemmatizer

PARTs 12–15 key every SRS card on a **lemma** — the dictionary form. The transcript view, the collocation matcher, and `/listen` all reduce surface words to lemmas before looking up cards. The default `LowercaseLemmatizer` just lowercases, which is wrong for an inflected language: Slovene `mize`, `mizo`, `mizi` are all the noun `miza`, and a lowercasing "lemmatizer" treats them as three different words. The lemma-as-unit choice in PART 25's word-learning state machine makes lemmatizer accuracy a **hard dependency** — so this part adds a real morphological analyzer behind the same Protocol.

### 22.1 The Protocol Grew an `analyze_sentence`

`app/srs/lemmatizer.py` defines the `Lemmatizer` Protocol. It used to expose just `lemmatize(word)`; it now also exposes `analyze(word) → (lemma, case, number)` and `analyze_sentence(sentence) → list[TokenAnalysis]`. The sentence method is the load-bearing one — Slovene lemmas are **POS-dependent and only resolvable in context**:

```bash
sed -n "230,264p" backend/app/srs/lemmatizer.py
```

```output
def lemmatize_surfaces_in_context(
    surfaces: list[str],
    sentence: str,
    lemmatizer: Lemmatizer,
    language_code: str,
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
    """
    context = {ta.surface.lower(): ta.lemma.lower() for ta in lemmatizer.analyze_sentence(sentence, language_code)}
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

### 22.2 The classla Engine Is Opt-In and CI-Invisible

`ClasslaLemmatizer` wraps CLASSLA-Stanza (a PyTorch pipeline for South Slavic languages). It is **never imported at module level** — the `classla` import lives inside `_ensure_pipeline()` and a `try/except ImportError` type alias — so CI, which doesn't install PyTorch, never touches it. The factory selects it only when the user opts in:

```bash
sed -n "206,227p" backend/app/srs/lemmatizer.py
```

```output
@lru_cache(maxsize=1)
def get_lemmatizer() -> Lemmatizer:
    """Return a cached lemmatizer based on ``settings.lemmatizer_type``.

    * ``"lowercase"`` (default) — ``LowercaseLemmatizer``
    * ``"classla"`` — ``ClasslaLemmatizer``, falling back to ``LowercaseLemmatizer``
      with a logged warning if classla is not importable.
    """
    from app.config import settings

    lemmatizer_type = settings.lemmatizer_type
    if lemmatizer_type == "classla":
        try:
            import classla  # noqa: F401 — check importability at factory time

            return ClasslaLemmatizer()
        except ImportError:
            _logger.warning(
                "classla not installed; falling back to LowercaseLemmatizer. "
                "Install the opt-in extra: `uv sync --all-groups --extra classla` "
                "(pins classla==2.2.1; the torch==2.12.0 override for Python 3.14 is "
                "baked into pyproject.toml). Then set lemmatizer_type=classla. "
                "See docs/walkthrough.md §22.2."
            )
    return LowercaseLemmatizer()
```

Configuration is one new setting, `lemmatizer_type` (`"lowercase"` default, `"classla"` opt-in), in `app/config.py`. Tests pin `lemmatizer_type=lowercase` explicitly (commit `ed8937e`) so a developer's local `.env` with the classla flag can't leak PyTorch into a CI-style run. Models live under `CLASSLA_RESOURCES_DIR` (default `~/classla_resources`); run `classla.download("sl")` once before first use — `Pipeline` does not reliably auto-fetch across classla versions. `ClasslaLemmatizer` caches `analyze_sentence` results **per exact sentence string** (commit `fa80ad1`) — lesson text is stable across requests, so the transcript endpoint's state-change refetches drop from ~3.6 s of NLP to a DB-only lookup once warmed.

**Python 3.14 install caveat (verified 2026-06-02; made reproducible 2026-06-02).** The latest working classla (`2.2.1`) pins `torch<=2.6`, but torch `<=2.6` ships no 3.14 (`cp314`) wheel — torch only gained 3.14 support at `2.12`. So a bare `pip install classla` on 3.14 silently resolves to the ancient `classla==1.1.0`, which crashes on modern torch (PyTorch-2.6 `weights_only=True` → "Vector file is not provided"), and the factory returns a `ClasslaLemmatizer` that fails at first use rather than falling back. classla `2.2.1` is pure-Python, so the fix is to override its torch pin to a 3.14-capable build.

This is now **declared, not ad-hoc.** classla is a `[project.optional-dependencies]` *extra* in `backend/pyproject.toml` (`classla = ["classla==2.2.1"]`), and `[tool.uv] override-dependencies = ["torch==2.12.0"]` forces the 3.14 torch over classla's `torch<=2.6` pin. Install it reproducibly:

```bash
cd backend && uv sync --all-groups --extra classla
```

It is an *extra*, not a `[dependency-groups]` group, on purpose: CI and the standard dev setup both run `uv sync --all-groups`, which does **not** pull extras — so PyTorch stays out of CI and the lemmatizer falls back to lowercase there. The override is inert unless the extra is synced (nothing else pulls torch). The model still lives under `CLASSLA_RESOURCES_DIR` (`~/classla_resources`); run `classla.download("sl")` once if it's absent. With this combo the pipeline produces correct lemmas on 3.14 (`hoteli → hoteti`, `smo → biti`, `ste → biti`). (The previous one-off `uv pip install "classla==2.2.1" --override <(echo "torch==2.12.0")` still works but isn't tracked in the lock, which is exactly why it vanished on the 3.13→3.14 upgrade.)

### 22.3 What Was *Not* Built: Bulk Re-Lemmatization

A migration that walked every existing collocation, re-lemmatized its text with classla, and **merged** rows that collapsed to the same lemma was written and then **reverted** (commits `f4bea32` → `a1ecf86`). It was unsafe by design: single-word re-lemmatization is exactly the POS-blind path §22.1 warns about, so it merged `neck` → `door` and `we` → `I`. The legacy deck has genuine surface-keyed duplicate bases (`čas` *and* `časa` as separate cards; `dobrodošli`/`dobrodošel`) that don't fit the lemma-as-unit model — but the resolution is to **dedupe one-at-a-time in Anki with review, or grandfather them**, never to bulk-merge in TT where a mis-lemmatization silently destroys an Anki-linked card.

A smaller transcript-UI affordance landed alongside: lesson text became selectable and copyable (commit `e949cf6`), and the word-state cycle now keys off click-vs-drag distance rather than text selection (commit `4a99925`) so highlighting to copy doesn't accidentally toggle a card's state.

---

## PART 23: Cloze, Always On

PART 20 described the cloze pipeline behind two feature flags (a global enable and a per-language gate). Both flags are **gone** (commit `9285c0b`). The user's decision: cloze is available for every language as it is added, with no checks. Creation is **capability-driven** — a cloze gets made when the language *has the capability* (a curated function-word list, or an inflection-aware lemmatizer), not when a flag is flipped. The two settings endpoints, their four DB getters/setters, the `ClozeSettingRequest` model, and the frontend toggle were all deleted outright (no constant-true dead branch left behind), and the OFF-behavior tests were removed.

### 23.1 Two Kinds of Cloze

`app/srs/function_words.py` (renamed in scope but same module) produces both cloze flavors. A **plain function-word cloze** blanks the whole word; `is_function_word` is the capability check — true only where a curated set exists (Slovene today):

```bash
sed -n "45,53p" backend/app/srs/function_words.py
```

```output
def is_function_word(lemma: str, language_code: str) -> bool:
    """Return True if *lemma* is a known function word in *language_code*.

    Phase F scope: Slovene only (language_code == "sl").
    Case-insensitive (casefold) lookup against the curated set.
    """
    if language_code == "sl":
        return lemma.casefold() in SLOVENE_FUNCTION_WORDS
    return False
```

The plain-cloze blank is built at listen time from the **surface as it appeared in the sentence**, not the dictionary lemma (commit `92140c5`): the cloze must reference the word actually present in the stored sentence, so `make_cloze_text(surface, sentence)` is what runs, keyed off the raw sentence for backfill. The answer-word audio likewise synthesizes the surface, not the lemma (commit `562edab`) — otherwise a learner clozing `sem` would hear `biti`.

### 23.2 Fluent-Forever Ending-Blank for Morphology Clozes

The second flavor — a **morphology cloze** — drills an inflected form. Blanking the entire word would make the card test recall of the whole token; instead, following Fluent Forever, only the **inflectional tail past the lemma↔surface common prefix** is blanked, leaving the stem visible (commit `2db9f6a`):

```bash
sed -n "85,157p" backend/app/srs/function_words.py
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


def make_morphology_cloze_text(
    surface: str,
    lemma: str,
    feature: str,
    source_sentence: str,
) -> str:
    """Wrap ``surface`` with a hinted cloze: ``{{c1::sem::biti, 1sg}}``.

    The hint (``::hint``) tells the learner which lemma + morphology to
    produce. Anki renders the blank as ``[biti, 1sg]``.

    Idempotent: already-clozed text passes through unchanged.
    Returns empty string when ``source_sentence`` is empty.
    """
    if not source_sentence:
        return ""
    if not surface:
        return source_sentence
    if _CLOZE_RE.search(source_sentence):
        return source_sentence
    label = _format_morphology_feature(feature)
    pattern = re.compile(rf"\b{re.escape(surface)}\b", re.IGNORECASE)

    def _replacer(m: re.Match) -> str:
        matched = m.group(0)
        split = _ending_blank_split(matched, lemma)
        if split is None:
            hint = f"{lemma}, {label}" if label else lemma
            return f"{{{{c1::{matched}::{hint}}}}}"
        visible, tail = split
        hint = label or lemma
        return f"{visible}{{{{c1::{tail}::{hint}}}}}"

    return pattern.sub(_replacer, source_sentence)
```

`_ending_blank_split` computes the longest common prefix of surface and lemma. If it is ≥2 chars and shorter than the whole word, the stem stays visible and only the tail is clozed: `Ljubljan{{c1::i::loc sg}}` rather than `{{c1::Ljubljani}}`. Suppletive forms (`biti`→`sem`, `iti`→`grem`) have LCP < 2, so the split returns `None` and the helper falls back to a whole-word blank with a `lemma, feature` hint (`{{c1::sem::biti, 1sg}}`). When the stem is already visible, the hint shows the **feature only** — the lemma is implied by the stem. `ud_feats_to_tt_feature` (bottom of the module) maps a classla UD analysis (`Case=Loc|Number=Sing`, `upos=NOUN`) to the TT feature string `noun:loc:sg`, returning `None` for combinations outside the A1 whitelist.

---

## PART 24: `morphology_focus` Generation

A cloze can only be made for a form the lesson actually contains — **form coverage is the lesson generator's job, not the carder's**. So the story prompt was reframed from `declension_focus` (which steered toward oblique cases inappropriate for A1) to `morphology_focus` (commit `44c5699`), tuned to surface the forms an A1 learner should produce: verb conjugations and accusative/locative nouns.

### 24.1 The Prompt Steers Toward Producible Forms

The LLM builds the `morphology_focus` array last, scanning the dialogue lines it just wrote and tagging inflected words **already present** in them. Two steering rules raised the live card yield from 52% to 91%:

```bash
sed -n "134,168p" backend/app/generation/prompts.py
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
`loc` (v Ljubljani); `v/na/čez/skozi` + motion → `acc` (grem v Ljubljano); direct object → `acc`.
```

The producible-form rule (commit `2902cd6`) discards any entry whose surface equals its lemma — a nominative-singular noun or a bare infinitive gives the answer away, so it is a wasted slot (the backend also drops degenerate `lemma == surface` clozes defensively, commit `35630cc`). The case rule (commit `1a19a7c`) derives case from the **governing word, not the English gloss**: `v/na/pri/o/po` + a static location → locative (`v Ljubljani`); `v/na/čez/skozi` + motion → accusative (`grem v Ljubljano`). Cases are whitelisted to nom/acc/loc — gen/dat/ins are A2+ and explicitly forbidden.

### 24.2 Model-Agnostic JSON Parsing

Steering experiments pushed against alternate Groq models, which exposed that the parser assumed clean JSON. Reasoning models (`qwen3`) wrap the answer in `<think>…</think>`; `gpt-oss` prepends prose like `**Lesson Title:** …`. `StoryGenerator._parse_json` (commit `8ba2117`) now strips `<think>` blocks and code fences, then tries the cleaned string and, failing that, the first balanced `{…}` span:

```bash
sed -n "137,157p" backend/app/generation/story.py
```

```output
    def _parse_json(raw: str) -> dict:
        # Model-agnostic: drop <think> reasoning, code fences, and any prose the model
        # wraps around the JSON (gpt-oss prepends "**Lesson Title:** …"; others append
        # commentary). Try the cleaned string, then the first balanced {…} span.
        cleaned = _strip_fences(_THINK_RE.sub("", raw).strip())
        candidates = [cleaned]
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            candidates.append(cleaned[start : end + 1])
        last_error: json.JSONDecodeError | None = None
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                last_error = e
        logger.error(
            "LLM returned unparseable response (len=%d): %r",
            len(cleaned),
            cleaned[:500],
        )
        raise StoryGenerationError(f"LLM returned invalid JSON: {last_error}") from last_error
```

The model experiments themselves were dead ends — `gpt-oss-120b` returns prose-not-JSON and 400s on `json_object`, `qwen3-32b` 413s on payload size — so the default stays `llama-3.3-70b-versatile`, and the parser hardening is the durable win.

A per-day **Regenerate** button (commit `b72e764`) wires this into the UI: it re-runs `generateStory` for one day against the current prompt, keeps existing cards, and lets new vocabulary and morphology drills flow in on the next listen + sync. The confirm dialog spells out exactly that contract so a regenerate never feels like it discards progress.

---

## PART 25: The Word-Learning State Machine

PARTs 22–24 are the foundation; this part is the model they serve. Each **lemma** moves through a state machine — `BASE (recognition → production) → INFLECTIONS` — and not every lemma has every stage. Content words that inflect go recognition → production → inflections; invariant content words stop at production; **function words enter directly at production via the base cloze** (recognition of a preposition is meaningless). The full settled design and roadmap are in `~/.claude/plans/word-learning-state-machine.md`. The locked principle: **gates govern *introduction* only, never review** — once introduced, recognition, production, and every inflection cloze review in parallel.

### 25.1 Phase 3 — Recognition Before Production (Layer 65)

The first gate holds a vocab card's **production** direction out of the new-queue until its **recognition** sibling graduates past the learning arc. This is implemented as a `NOT EXISTS` clause appended to `get_new_items` for the production direction only:

```bash
sed -n "733,756p" backend/app/srs/database.py
```

```output
        # Phase 3 introduction gate (TT-only): a PRODUCTION new card is not
        # introducible until its recognition sibling has graduated past the
        # learning arc (recognition state not in new/learning/relearning). This
        # makes TT introduce recognition before production — which is what Anki
        # does too: Anki is direction-agnostic and orders new cards by deck
        # position, and `create_note` places the recognition card (ord 0) at a
        # lower position than production (ord 1), so recognition surfaces first
        # (empirically 604/36 across the user's paired notes — the prior
        # "production-first" parity assumption was wrong). A cloze note has no
        # recognition direction, so NOT EXISTS is true and it stays introducible.
        # The recognition direction is never gated. See
        # ~/.claude/plans/word-learning-state-machine.md Phase 3 and
        # docs/anki-parity-layers.md.
        gate = (
            """
                  AND NOT EXISTS (
                    SELECT 1 FROM collocation_directions r
                    WHERE r.collocation_id = c.id
                      AND r.direction = 'recognition'
                      AND r.state IN ('new', 'learning', 'relearning')
                  )"""
            if direction == Direction.PRODUCTION
            else ""
        )
```

This was initially scoped as a TT-only divergence (like `promote_to_learning`), but the binary proved it is **parity-restoring**: real Anki introduces recognition first, 604 vs 36 across the user's 640 paired notes, because Anki orders new cards by deck position and `create_note` places the recognition card (ord 0) below production (ord 1). TT's old production-first behavior was the bug. The fix inverted the stale Layer 28 production-first tests — verified empirically first, per rule 13 (trust the binary). Recognition is never gated; a cloze note has no recognition row so `NOT EXISTS` is trivially true and it stays introducible. No badge change — `count_new_available_collocations` was already consistent.

### 25.2 Per-Lemma Mastery = Aggregated Retrievability

The transcript colors each word by a per-lemma **mastery** gradient. Mastery is the *mean retrievability* over the lemma's whole component set — recognition, production, and every inflection cloze — because retrievability (R) is the dynamic "how well do you know this right now" quantity, where stability is not. `app/srs/mastery.py` is a pure module:

```bash
cat -n backend/app/srs/mastery.py
```

```output
     1	"""Per-lemma mastery = aggregated retrievability over the learn-set (Phase 5)."""
     2	
     3	from __future__ import annotations
     4	
     5	from collections.abc import Iterable
     6	from datetime import date, datetime
     7	
     8	from app.models.srs_item import DirectionState, SRSState
     9	from app.srs.fsrs import compute_retrievability
    10	
    11	
    12	def component_mastery(
    13	    ds: DirectionState,
    14	    today: date,
    15	    now: datetime | None,
    16	    col_crt: int | None,
    17	    desired_retention: float = 0.9,
    18	) -> float:
    19	    """Mastery of one component (a direction/card) ∈ [0,1].
    20	
    21	    NEW/never-reviewed → 0.0 (unlearned). LEARNING/RELEARNING → 0.15 fixed floor
    22	    (in-steps, not graduated). REVIEW → aggregated retrievability. KNOWN → 1.0.
    23	    """
    24	    if ds.state == SRSState.NEW or ds.last_review is None:
    25	        return 0.0
    26	    if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
    27	        return 0.15
    28	    if ds.state == SRSState.KNOWN:
    29	        return 1.0
    30	    return compute_retrievability(ds, today, now=now, desired_retention=desired_retention, col_crt=col_crt)
    31	
    32	
    33	def compute_mastery_progress(
    34	    directions: Iterable[DirectionState],
    35	    today: date,
    36	    now: datetime | None,
    37	    col_crt: int | None,
    38	    desired_retention: float = 0.9,
    39	) -> float | None:
    40	    """Mean component_mastery over the learn-set. SUSPENDED components excluded.
    41	    None if the set is empty (→ caller renders as not-on-the-ramp).
    42	    """
    43	    ms = [
    44	        component_mastery(d, today, now, col_crt, desired_retention)
    45	        for d in directions
    46	        if d.state != SRSState.SUSPENDED
    47	    ]
    48	    return sum(ms) / len(ms) if ms else None
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
```

Lightness co-varies with progress (and due cards get an underline) as a red↔green colorblind hedge. Static states are off the ramp entirely: unknown is indigo, known/ignored are gray.

### 25.3 The Transcript Serializer Resolves the Active Card

`extract_transcript` (`app/srs/transcript.py`) now enriches every `WordToken` with seven Phase-5 fields: `card_type`, `active_state`, `active_direction`, `is_due`, `progress`, `inflectable`, and `inflection_feature`. Resolution is **inflection-first**: an exact-surface inflection cloze wins over the base card, which wins over "unknown." The active direction follows the state machine:

```bash
sed -n "77,94p" backend/app/srs/transcript.py
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
    if rec is None or rec.state != SRSState.REVIEW:
        return Direction.RECOGNITION
    return Direction.PRODUCTION
```

`progress` is `compute_mastery_progress` over the resolved component set; `inflectable` is true only when the surface differs from the lemma, the form is an A1 feature, the base production is REVIEW/KNOWN, and no cloze for that surface exists yet — i.e. exactly when clicking the word *could usefully* mint an inflection cloze. The serializer also reconstructs each `DialogueLine.sentence` from its surfaces, which the popover needs to build a cloze (a bug caught while finishing Phase 5: scene lines didn't carry the sentence, so popover-created cards had empty sentences).

### 25.4 Phase 4 — Inflection Clozes Are Click-Only

`/listen` **stopped** auto-minting morphology clozes (Layer 66, commit `6935e93`). The reasoning: a rare form that never gets clicked should never become a card — coverage is the generator's job (PART 24), and auto-minting on every listen flooded the deck. The sole mint path is now `POST /api/srs/inflection-clozes` (commit `f7abf4d`), called when the user clicks an inflected surface that appeared in a lesson:

```bash
sed -n "1182,1219p" backend/app/api/srs.py
```

```output
@router.post("/inflection-clozes", status_code=200)
async def create_inflection_cloze(body: InflectionClozeRequest, request: Request) -> dict:
    """Create one morphology cloze for an inflected surface (Phase 4a).

    Gated on the lemma's base production being in REVIEW or KNOWN.
    Idempotent by guid. Follows the add_collocation contract
    (card_type=cloze, no Anki ids).
    """
    db = request.app.state.srs_db
    language_code = body.language_code

    # 1. Eligibility gate — base word production must be REVIEW/KNOWN
    base = db.get_collocation_by_lemma(body.lemma)
    if base is None:
        raise HTTPException(status_code=409, detail="Base word not yet learned")
    prod = base.directions.get(Direction.PRODUCTION)
    if prod is None or prod.state not in (SRSState.REVIEW, SRSState.KNOWN):
        raise HTTPException(status_code=409, detail="Base word not yet learned")

    # 2. Degenerate guard — surface == lemma reveals the answer
    if body.lemma.casefold() == body.surface.casefold():
        raise HTTPException(status_code=422, detail="Surface equals lemma — nothing to cloze")

    # 3. Build + create (mirrors /listen morphology-cloze block)
    disambig = f"morph:{body.feature.replace(':', '-')}"
    cloze_sent = make_morphology_cloze_text(body.surface, body.lemma, body.feature, body.sentence)
    unit = SyntacticUnit(
        text=body.surface,
        translation="",
        word_count=1,
        difficulty=1,
        source="llm",
        lemma=body.lemma,
        disambig_key=disambig,
        card_type="cloze",
        source_sentence=cloze_sent,
    )
    was_created = db.add_collocation(unit, language_code=language_code)
```

The endpoint is gated on the base word's production being REVIEW/KNOWN (409 otherwise — you can't drill an inflection of a word you haven't learned), guards the degenerate `surface == lemma` case (422), is idempotent by guid, and follows the card-adding contract from `.claude/rules/anki-sync.md` (`card_type="cloze"`, no Anki ids — `sync_create_new` mints and links them).

### 25.5 Phase 5 Part C — Click an Unknown Word to Create Its Base Card

Clicking an *unknown* word creates its base card. `POST /api/srs/items/base` branches on word type — the heart of the state machine's entry rule:

```bash
sed -n "731,758p" backend/app/api/srs.py
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
    db = request.app.state.srs_db
    lang = body.language_code
    lemma = body.lemma.casefold()

    # Function-word detection is capability-driven: is_function_word is only true
    # where a curated list exists (Slovene today). The surface is checked too — an
    # inflected function form (classla "sem" → lemma "biti") is detected via the
    # surface even when the dictionary lemma isn't itself a function word.
    is_func = is_function_word(lemma, lang) or is_function_word(body.surface, lang)
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
sed -n "66,93p" frontend/src/lib/WordSpan.svelte
```

```output

	const dynamicStyle = $derived(
		word.active_state !== 'unknown' && word.active_state !== 'known' && word.active_state !== 'suspended'
			? `color: ${masteryColor(word.progress ?? 0)};`
			: ''
	);

	const colorClass = $derived(
		word.active_state === 'unknown'
			? 'word-unknown'
			: word.active_state === 'known'
				? 'word-known'
				: word.active_state === 'suspended'
					? 'word-ignored'
					: ''
	);

	// Show tooltip when: not inside a collocation, OR alt-hover mode is active
	const showTooltip = $derived(!requireModifier || altHover);
</script>

{#if showTooltip}
	<Tooltip translation={word.translation} state={word.srs_state} {word} {sentence} actions={tooltipActions}>
		<span
			class="word {colorClass}"
			class:word-selected={selected}
			class:word-due={word.is_due}
			style={dynamicStyle}
```

Clicks are routed by the lesson `+page.svelte`: clicking an **unknown** word calls `createBaseCard`; clicking a **due** word submits a Good grade on its `active_direction`; clicking a **terminal** (known/suspended) word is a no-op; and clicking inside a collocation reviews the collocation. A hover popover (`Tooltip.svelte`, made interactive with `pointer-events:auto` and a hover bridge) offers create-inflection plus ignore/known/new overrides — note the override set deliberately excludes lapse/restore, so it never touches FSRS scheduling state. The matching `api.ts` methods `createBaseCard` and `createInflectionCloze` complete the loop. This is **Phase 5 complete end-to-end** — every word in a lesson is now a one-click entry point into the learning state machine.

---

## PART 26: FSRS in f32 & Parity Layers 49–66

PART 16 documented queue-parity Layers 24–31. The history has since reached Layer 66 (`docs/anki-parity-layers.md`). Most layers are narrow input-quality or formula-branch fixes; two are structural enough to call out here, and the rest are tabulated.

### 26.1 Layer 59 — All FSRS Arithmetic Moved to f32

`fsrs-rs` (Anki's Rust scheduler) computes stability and difficulty in `f32` end-to-end via Burn tensors. TT computed in Python `f64`, which drifts by single ULPs that, at 4-decimal storage precision, surface as false-positive compare-shadow divergences (the persistent ±0.0001 class). Layer 59 (commit `12338fa`) casts every operand and intermediate to `numpy.float32`, returning `f64` only at storage boundaries:

```bash
sed -n "18,40p" backend/app/srs/fsrs.py
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
sed -n "102,106p" backend/app/srs/fsrs.py
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

PART 19 left `tt_revlog` writing events but `sync_pull` still merging state field-by-field, with the endgame ("collapse the 9-branch field-merge into an event-replay") gated on an empirical measurement. That measurement ran (`docs/stage-3b-empirical-measurement.md`: ~87.6% practical match), and Stage 3b is now a **three-mode switch** that lets the new event-replay path run shadowed alongside the legacy merge before it ever takes over.

### 27.1 The Three Modes

A single `anki_state_cache` key, `event_sync_pull`, selects the merge strategy:

```bash
sed -n "128,132p" backend/app/srs/database.py
```

```output
# Stage 3b sync_pull merge modes (anki_state_cache['event_sync_pull']):
#   legacy  — the pre-Stage-3b 9-branch _pull_merge_direction (default)
#   compare — run legacy + replay, write legacy authoritative + replay to shadow cols
#   new     — collapsed FSRS branch: take Anki verbatim, forward-step as validator
_EVENT_SYNC_PULL_MODES = frozenset({"legacy", "compare", "new"})
```

`legacy` is the pre-Stage-3b 9-branch merge. `compare` runs both: legacy stays authoritative and writes the card, while the incremental replay is written to **shadow columns** and any disagreement is recorded as a divergence — zero production risk, pure observation. `new` collapses the FSRS branch entirely: take Anki's state verbatim, with the forward-step replay acting only as a validator. The getter defaults to `legacy` and falls back to `legacy` on any unrecognized stored value, so a corrupt row can never silently route sync down an unimplemented path:

```bash
sed -n "2024,2041p" backend/app/srs/database.py
```

```output
    def get_event_sync_pull_mode(self) -> str:
        """Return the sync_pull merge mode (Stage 3b): ``legacy`` / ``compare`` / ``new``.

        Defaults to ``legacy`` (the pre-Stage-3b 9-branch merge tree) when unset.
        A corrupt/unrecognised stored value also falls back to ``legacy`` so a
        bad row can never silently take sync_pull down an unimplemented path.
        """
        row = self.get_anki_state_cache("event_sync_pull")
        if row is None or row[0] not in _EVENT_SYNC_PULL_MODES:
            return "legacy"
        return row[0]

    def set_event_sync_pull_mode(self, mode: str) -> None:
        """Persist the sync_pull merge mode; rejects anything but the 3 known modes."""
        if mode not in _EVENT_SYNC_PULL_MODES:
            raise ValueError(f"event_sync_pull mode must be one of {sorted(_EVENT_SYNC_PULL_MODES)}, got {mode!r}")
        self.set_anki_state_cache("event_sync_pull", mode)

```

### 27.2 The Dispatch in `sync_pull`

`compare` and `new` share one incremental forward-step replay; only the write target differs. `sync_pull` resolves the FSRS params and `col_crt` once at the top when either mode is active:

```bash
sed -n "1846,1858p" backend/app/anki/sync.py
```

```output
        # Stage 3b: compare/new modes both run incremental replay alongside the
        # legacy merge. Compare writes replay to shadow columns (legacy stays
        # authoritative); new replaces authoritative FSRS state with replay-derived
        # values (or Anki's on divergence). Resolve params/col_crt once.
        event_mode = self._db.get_event_sync_pull_mode()
        compare_params = None
        compare_col_crt = None
        if event_mode in ("compare", "new"):
            from app.srs.queue_stats import resolve_fsrs_params

            compare_params = resolve_fsrs_params(self._db)[0]
            compare_col_crt = self._anki_col_crt

```

The replay is **incremental** — it forward-steps from the stored state through the events ingested this sync, rather than replaying from NEW every time (which would be O(history) per card per sync). Compare-mode writes the replayed stability/difficulty to shadow columns; `new`-mode records a `RecomputeDivergence` on `report.recompute_divergences` when its forward-step disagrees with Anki, so a real algorithmic gap surfaces in the sync report.

### 27.3 What the Soak Found

Running `compare` against the live deck across many syncs is the soak, and it earned its keep — three of PART 26's layers (57, 58, 62) are bugs it surfaced. Two findings are worth internalizing because they shaped the soak's health bar:

- **Layer 58** (commit `3f848cd`): a replayed-stability divergence was **not** an FSRS bug — it was an *ingest gap*. A Good grade landed inside a 41-hour sync gap and was never ingested, so the replay was missing an event. The fix made ingest reconcile against Anki's full revlog (`get_tt_revlog_ids`) instead of trusting a `last_synced_at` watermark. The lesson: a replay divergence can mean "the replay is missing an input," not "the replay math is wrong."
- **The difficulty floor washed to 0** (2026-05-30): a transient cohort of difficulty-only divergences came from a 2026-05-21 Check-Database/restore that re-stamped ~2333 revlog rows Anki never applied to `card.data` — proving Anki's `card.data` is **not** a pure replay of its revlog. As those cards were re-graded with clean rows, the cohort decayed 104 → 6 → 0.

The soak's health bar is now **0 for both stability and difficulty** — the old "~104 benign floor" is retired. The classifier lives in `.claude/rules/anki-queue-parity.md`; the measurement procedure and decision gate are in `docs/stage-3b-empirical-measurement.md`. Live mode is still `compare`; the flip to `new` and the deletion of the legacy branch follow once the soak holds clean.

---

## PART 28: The Documentation Set

The product gained a written identity. `README.md` is the pitch and the map; `docs/prd.md` is the product requirements doc. The pedagogy is grounded in a set of **influence docs**, each written to the same shape (claim → how TunaTale applies it → where it deliberately diverges):

- `docs/pimsleur.md` — graduated-interval recall and the backward-buildup drill (PART 6's syllabification).
- `docs/fluent-forever.md` — the ending-blank cloze (PART 23.2) and image-over-translation cards.
- `docs/lingq.md` — known/unknown word tracking, the lineage of PART 25's transcript model.
- `docs/refold.md` — comprehensible input and the listen-first loop (PART 15).
- `docs/bdt.md` — Lampariello's bidirectional translation, the recognition↔production pairing.

Two operational docs round it out: `docs/adding-a-language.md` (the plugin checklist — preprocessor, voice map, function-word list, lemmatizer) and `docs/anki-recovery.md` (disaster recovery for the user's primary Anki collection). `AGENTS.md` (this file, also `CLAUDE.md`) had its opening polished and absorbed the new-language and Anki-recovery pointers.

This is where a new contributor — human or agent — should start: the influence docs explain *why* the system is shaped the way the preceding 27 parts describe.

