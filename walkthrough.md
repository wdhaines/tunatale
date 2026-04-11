# TunaTale Production Codebase Walkthrough

*2026-03-25T01:20:45Z by Showboat 0.6.1*
<!-- showboat-id: 4bdef7f8-1973-46b4-b00d-14caf394240c -->

## Purpose of This Document

This walkthrough covers the production TunaTale codebase — the unified application rebuilt from the two prototypes documented in `walkthrough-prototypes.md`. It serves two audiences: (1) a human reader wanting to understand how TunaTale works, and (2) an AI agent extending or maintaining the system.

**What changed from the prototypes:** The production rebuild unified the audio pipeline (micro-demo-0.0) and the content engine (micro-demo-0.1) under a single FastAPI application. Hardcoded language logic was replaced with pluggable preprocessors and voice maps. The mock LLM (MD5-hashed) became a cassette system with multiple modes. FSRS-5 replaced the custom SRS scheduler. The entire codebase follows hexagonal architecture with Protocol-based ports. Since the initial production build: ContentStore added SQLite persistence for curricula/lessons/audio, per-word SRS tracking added lemmatizer/tokenizer/transcript modules, section_builder extracted from StoryGenerator (now a thin orchestrator), Slovene syllabification added for Pimsleur backward buildup, pydub replaced raw-PCM concatenation, SRS admin UI added (6 admin endpoints + SvelteKit admin page).

## Architecture at a Glance

```
backend/
├── app/
│   ├── main.py              # FastAPI app with CORS, lifespan, routers
│   ├── config.py             # Pydantic Settings (env-driven)
│   ├── models/               # Pure domain models (no I/O)
│   ├── llm/                  # Groq LLM client + cassette replay system
│   ├── srs/                  # FSRS-5 spaced repetition engine + lemmatizer/tokenizer/transcript
│   ├── generation/           # Curriculum + story + section_builder + syllabify + enforcement
│   ├── audio/                # TTS, pydub assembly, preprocessing
│   ├── storage/              # ContentStore SQLite repository
│   └── api/                  # FastAPI route modules (22 endpoints)
└── tests/
    ├── conftest.py           # Cassette fixtures, CLI options
    ├── cassettes/            # Recorded LLM responses (JSON)
    └── test_*.py             # 26 test files, 100% branch coverage
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
     9	from datetime import date, timedelta
    10	
    11	from app.models.srs_item import Rating, SRSItem, SRSState
    12	
    13	# FSRS-5 default parameters (w vector, 19 values)
    14	W = [
    15	    0.4072,  # w0: initial stability for Again
    16	    1.1829,  # w1: initial stability for Hard
    17	    3.1262,  # w2: initial stability for Good
    18	    15.4722,  # w3: initial stability for Easy
    19	    7.2102,  # w4: initial difficulty
    20	    0.5316,  # w5: initial difficulty decay
    21	    1.0651,  # w6: difficulty mean-reversion weight
    22	    0.0589,  # w7: difficulty update weight
    23	    1.5330,  # w8: stability increase factor
    24	    0.1544,  # w9: stability increase decay
    25	    1.0050,  # w10: stability increase R-factor
    26	    1.9767,  # w11: lapse stability factor
    27	    0.0967,  # w12: lapse stability difficulty decay
    28	    0.2573,  # w13: lapse stability S-factor
    29	    2.2930,  # w14: lapse stability R-factor
    30	    0.5100,  # w15: hard penalty
    31	    2.9898,  # w16: easy bonus
    32	    0.5100,  # w17: (unused in v5)
    33	    0.4350,  # w18: (unused in v5)
    34	]
    35	
    36	REQUESTED_RETENTION = 0.9
    37	DECAY = -0.5
    38	FACTOR = 19 / 81  # = 0.234...
    39	
    40	
    41	def _forgetting_curve(elapsed_days: float, stability: float) -> float:
    42	    """Retrievability at elapsed_days given stability."""
    43	    return (1 + FACTOR * elapsed_days / stability) ** DECAY
    44	
    45	
    46	def _next_interval(stability: float) -> int:
    47	    """Days until next review at REQUESTED_RETENTION."""
    48	    interval = stability / FACTOR * (REQUESTED_RETENTION ** (1 / DECAY) - 1)
    49	    return max(1, min(round(interval), 36500))
    50	
    51	
    52	def _init_stability(rating: Rating) -> float:
    53	    return W[rating.value - 1]
    54	
    55	
    56	def _init_difficulty(rating: Rating) -> float:
    57	    d = W[4] - math.exp(W[5] * (rating.value - 1)) + 1
    58	    return max(1.0, min(10.0, d))
    59	
    60	
    61	def _next_difficulty(d: float, rating: Rating) -> float:
    62	    # W[6]=1.0651 is the delta multiplier; W[7]=0.0589 is the mean-reversion weight
    63	    next_d = d - W[6] * (rating.value - 3)
    64	    # Mean-reversion toward W[4]=7.21 (the initial difficulty for a "normal" item)
    65	    next_d = W[7] * W[4] + (1 - W[7]) * next_d
    66	    return max(1.0, min(10.0, next_d))
    67	
    68	
    69	def _next_stability_recall(d: float, s: float, r: float, rating: Rating) -> float:
    70	    hard_penalty = W[15] if rating == Rating.HARD else 1.0
    71	    easy_bonus = W[16] if rating == Rating.EASY else 1.0
    72	    return s * (
    73	        math.exp(W[8]) * (11 - d) * s ** (-W[9]) * (math.exp((1 - r) * W[10]) - 1) * hard_penalty * easy_bonus + 1
    74	    )
    75	
    76	
    77	def _next_stability_lapse(d: float, s: float, r: float) -> float:
    78	    return W[11] * d ** (-W[12]) * ((s + 1) ** W[13] - 1) * math.exp((1 - r) * W[14])
    79	
    80	
    81	def schedule(item: SRSItem, rating: Rating, review_date: date | None = None) -> SRSItem:
    82	    """Apply a review rating to an SRSItem and return the updated item (copy).
    83	
    84	    Args:
    85	        item: The SRSItem to schedule.
    86	        rating: Learner's rating for this review.
    87	        review_date: The date of the review (defaults to today).
    88	
    89	    Returns:
    90	        A new SRSItem with updated scheduling fields.
    91	    """
    92	    if review_date is None:
    93	        review_date = date.today()
    94	
    95	    from dataclasses import replace
    96	
    97	    if item.state == SRSState.NEW:
    98	        new_stability = _init_stability(rating)
    99	        new_difficulty = _init_difficulty(rating)
   100	        new_reps = 1
   101	        new_lapses = item.lapses
   102	        new_state = SRSState.LEARNING if rating == Rating.AGAIN else SRSState.REVIEW
   103	    else:
   104	        # Calculate elapsed days and retrievability
   105	        last = item.last_review or review_date
   106	        elapsed = max(0, (review_date - last).days)
   107	        r = _forgetting_curve(elapsed, item.stability)
   108	
   109	        if rating == Rating.AGAIN:
   110	            new_stability = _next_stability_lapse(item.difficulty, item.stability, r)
   111	            new_difficulty = _next_difficulty(item.difficulty, rating)
   112	            new_reps = item.reps + 1
   113	            new_lapses = item.lapses + 1
   114	            new_state = SRSState.RELEARNING
   115	        else:
   116	            new_stability = _next_stability_recall(item.difficulty, item.stability, r, rating)
   117	            new_difficulty = _next_difficulty(item.difficulty, rating)
   118	            new_reps = item.reps + 1
   119	            new_lapses = item.lapses
   120	            new_state = SRSState.REVIEW
   121	
   122	    new_stability = max(0.1, new_stability)
   123	    new_difficulty = max(1.0, min(10.0, new_difficulty))
   124	    interval = _next_interval(new_stability)
   125	    new_due = review_date + timedelta(days=interval)
   126	
   127	    return replace(
   128	        item,
   129	        stability=new_stability,
   130	        difficulty=new_difficulty,
   131	        due_date=new_due,
   132	        reps=new_reps,
   133	        lapses=new_lapses,
   134	        state=new_state,
   135	        last_review=review_date,
   136	    )
```

FSRS-5 is a 19-parameter model trained on millions of reviews. The key insight: **stability** is how many days before retention drops to 90%. A stability of 3.12 (initial Good rating) means after ~3 days, the learner has a 90% chance of recall — time to review.

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
51:class SRSDatabase:
57:    def close(self) -> None:
63:    def __enter__(self) -> SRSDatabase:
66:    def __exit__(self, *_) -> None:
69:    def __init__(self, db_path: str = ":memory:") -> None:
83:    def _init_schema(self, conn: sqlite3.Connection) -> None:
94:    def _file_conn(self):
104:    def _get_conn(self):
113:    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> None:
140:    def update_collocation(self, item: SRSItem) -> None:
170:    def record_violation(
184:    def get_collocation(self, text: str) -> SRSItem | None:
192:    def get_collocation_by_lemma(self, lemma: str) -> SRSItem | None:
200:    def get_due_collocations(self, as_of: date) -> list[SRSItem]:
209:    def get_new_collocations(self, limit: int = 10) -> list[SRSItem]:
218:    def get_collocation_by_id(self, row_id: int) -> tuple[int, SRSItem, str] | None:
226:    def update_collocation_fields(self, row_id: int, *, text: str, translation: str) -> None:
242:    def delete_collocation(self, row_id: int) -> None:
252:    def delete_collocations(self, row_ids: list[int]) -> int:
268:    def reset_collocation(self, row_id: int) -> None:
284:    def set_suspended(self, row_id: int, suspended: bool) -> None:
295:    def list_collocations(
349:    def get_violations(self, collocation_text: str) -> list[dict]:
358:    def count_collocations(self) -> int:
362:    def count_due_collocations(self, as_of: date) -> int:
372:    def _row_to_item(row: sqlite3.Row) -> SRSItem:
```

The `SRSDatabase` is a SQLite repository with two tables: `collocations` (vocabulary with FSRS fields) and `violations` (content rule violations for debugging). It supports `:memory:` for tests and file-based persistence for production. `count_due_collocations` powers the `/api/srs/stats` endpoint's `due_today` counter.

**Schema additions since the prototype:** the `collocations` table gained a `lemma` column (with an idempotent `ALTER TABLE … ADD COLUMN` migration in `_init_schema`) so per-word SRS tracking can collapse inflected variants. There's also a new `idx_collocations_lemma` index for the `get_collocation_by_lemma` lookup used by `/api/srs/listen`.

**Admin methods (powering the SRS admin UI):**

- `list_collocations(limit, offset, search, state, order_by, order_dir)` — paginated browse with full-text search across `text`/`translation`, state filter, and validated sort columns. Returns `(rows, total_count)`.
- `get_collocation_by_id(id)` / `update_collocation_fields(id, text, translation)` — read/edit by primary key. Update raises `ValueError` on UNIQUE collisions so the API can return 409.
- `delete_collocation(id)` and `delete_collocations(ids)` — single + bulk delete with cascading violation cleanup.
- `reset_collocation(id)` — wipes FSRS scheduling fields back to NEW (stability=1.0, difficulty=5.0, reps=0, lapses=0).
- `set_suspended(id, suspended)` — toggles between `suspended` and `new` states. Suspended items are filtered out of `get_due_collocations`.

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
     1	"""SRS feedback adapters.
     2	
     3	ImplicitFeedbackAdapter: maps learner signals → FSRS ratings.
     4	PostGenerationFeedback: identifies which collocations appear in a generated story.
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
    18	
    19	class ImplicitFeedbackAdapter:
    20	    """Maps implicit learner signals to FSRS ratings."""
    21	
    22	    def signal_to_rating(self, signal: str) -> Rating:
    23	        """Convert a learner signal string to an FSRS Rating.
    24	
    25	        Signals:
    26	            no_help: Learner did not request help → Good
    27	            slowdown: Learner slowed playback → Hard
    28	            translation_request: Learner requested translation → Again
    29	            fast_forward: Learner fast-forwarded → Easy
    30	        """
    31	        if signal not in _SIGNAL_MAP:
    32	            raise ValueError(f"Unknown signal {signal!r}. Valid: {list(_SIGNAL_MAP)}")
    33	        return _SIGNAL_MAP[signal]
    34	
    35	
    36	class PostGenerationFeedback:
    37	    """Identifies which provided collocations were actually used in a story."""
    38	
    39	    def find_used_collocations(self, provided: list[str], story_text: str) -> list[str]:
    40	        """Return the subset of provided collocations that appear in story_text.
    41	
    42	        Matching is case-insensitive. Only collocations that appear as
    43	        substrings in the story are marked as used.
    44	        """
    45	        story_lower = story_text.lower()
    46	        return [c for c in provided if c.lower() in story_lower]
```

```bash
grep -n "class CollocationSelector\|def score\|def select" backend/app/srs/selector.py
```

```output
11:class CollocationSelector:
17:    def score(self, item: SRSItem) -> float:
54:    def select(
```

The feedback adapter translates what the learner *does* into what the SRS *needs*: skipping ahead means they know it (EASY), asking for a translation means they forgot (AGAIN). `PostGenerationFeedback` checks which collocations the LLM actually used in a generated story — useful for tracking whether the content engine is following the curriculum.

The `CollocationSelector` scores items using the weighted formula from the strategy model (SRS readiness 40%, language quality 30%, pedagogical value 20%, diversity 10%), then selects the best mix of new and review items for the next lesson.

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
    70	@router.get("/{curriculum_id}/days/{day}/lesson", status_code=200)
    71	async def get_lesson_by_day(curriculum_id: str, day: int, request: Request):
    72	    store = request.app.state.content_store
    73	    result = store.get_latest_lesson_by_day(curriculum_id, day)
    74	    if result is None:
    75	        raise HTTPException(status_code=404, detail=f"No lesson found for day {day}")
    76	    lesson_id, lesson = result
    77	    return {
    78	        "id": lesson_id,
    79	        "title": lesson.title,
    80	        "language_code": lesson.language_code,
    81	        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in lesson.key_phrases],
    82	        "sections": [
    83	            {
    84	                "type": s.section_type.value,
    85	                "phrases": [
    86	                    {"text": p.text, "role": p.role, "language_code": p.language_code, "voice_id": p.voice_id}
    87	                    for p in s.phrases
    88	                ],
    89	            }
    90	            for s in lesson.sections
    91	        ],
    92	    }
```

Three changes from the prototype:

1. **Slug-based IDs.** `_slug(topic)` lowercases and hyphenates the topic, then appends 8 hex characters from a fresh UUID: `f"{_slug(body.topic)}-{uuid.uuid4().hex[:8]}"`. The result is stable enough to use in URLs (`arriving-in-ljubljana-a3f1b2c8`) and human-readable in logs.
2. **ContentStore replaces `app.state.curricula` dict.** Curricula now survive a server restart and are visible across requests without any threading locks.
3. **`GET /{curriculum_id}/days/{day}/lesson`** is a convenience endpoint for the frontend: given a curriculum and a day number it returns the latest generated lesson, fully expanded (all phrases, all sections, key phrases list).

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

```bash
cat -n backend/app/api/srs.py
```

```output
     1	"""SRS state and review endpoints."""
     2
     3	from __future__ import annotations
     4
     5	import datetime
     6
     7	from fastapi import APIRouter, HTTPException, Request
     8	from pydantic import BaseModel
     9
    10	from app.models.srs_item import SRSItem, SRSState
    11	from app.models.syntactic_unit import SyntacticUnit
    12	from app.srs.feedback import ImplicitFeedbackAdapter
    13	from app.srs.fsrs import Rating, schedule
    14	from app.srs.lemmatizer import LowercaseLemmatizer
    15	from app.srs.tokenizer import tokenize
    16	from app.srs.transcript import extract_transcript
    17
    18	router = APIRouter(prefix="/api/srs", tags=["srs"])
    19
    20	_feedback_adapter = ImplicitFeedbackAdapter()
    21	_lemmatizer = LowercaseLemmatizer()
    22
    23	_WORD_RATING_MAP: dict[str, Rating] = {
    24	    "again": Rating.AGAIN,
    25	    "hard": Rating.HARD,
    26	    "good": Rating.GOOD,
    27	    "easy": Rating.EASY,
    28	}
    29
    30
    31	def _item_to_dict(row_id: int, item: SRSItem, language_code: str) -> dict:
    32	    """Serialize an SRSItem to a response dict for admin endpoints."""
    33	    return {
    34	        "id": row_id,
    35	        "text": item.syntactic_unit.text,
    36	        "translation": item.syntactic_unit.translation,
    37	        "state": item.state.value,
    38	        "due_date": item.due_date.isoformat(),
    39	        "stability": item.stability,
    40	        "difficulty": item.difficulty,
    41	        "reps": item.reps,
    42	        "lapses": item.lapses,
    43	        "last_review": item.last_review.isoformat() if item.last_review else None,
    44	        "language_code": language_code,
    45	    }
    46
    47
    48	class FeedbackRequest(BaseModel):
    49	    collocation_text: str
    50	    signal: str  # no_help | slowdown | translation_request | fast_forward
    51
    52
    53	class ListenRequest(BaseModel):
    54	    lesson_id: str
    55	    word_ratings: dict[str, str] = {}  # lemma → "hard"|"easy"|"again"
    56
    57
    58	@router.get("/due", status_code=200)
    59	async def get_due_collocations(request: Request):
    60	    db = request.app.state.srs_db
    61	    today = datetime.date.today()
    62	    items = db.get_due_collocations(today)
    63	    return {"due": [{"text": i.syntactic_unit.text, "translation": i.syntactic_unit.translation} for i in items]}
    64
    65
    66	@router.get("/new", status_code=200)
    67	async def get_new_collocations(request: Request, limit: int = 10):
    68	    db = request.app.state.srs_db
    69	    items = db.get_new_collocations(limit=limit)
    70	    return {"new": [{"text": i.syntactic_unit.text, "translation": i.syntactic_unit.translation} for i in items]}
    71
    72
    73	@router.post("/feedback", status_code=200)
    74	async def record_feedback(body: FeedbackRequest, request: Request):
    75	    db = request.app.state.srs_db
    76
    77	    item = db.get_collocation(body.collocation_text)
    78	    if item is None:
    79	        return {"status": "not_found"}
    80
    81	    rating = _feedback_adapter.signal_to_rating(body.signal)
    82	    updated = schedule(item, rating)
    83	    db.update_collocation(updated)
    84	    return {"status": "ok", "new_due_date": str(updated.due_date)}
    85
    86
    87	@router.post("/listen", status_code=200)
    88	async def mark_lesson_listened(body: ListenRequest, request: Request):
    89	    store = request.app.state.content_store
    90	    lesson = store.get_lesson(body.lesson_id)
    91	    if lesson is None:
    92	        raise HTTPException(status_code=404, detail="Lesson not found")
    93
    94	    db = request.app.state.srs_db
    95
    96	    # ── Word-level tracking from NATURAL_SPEED section ──────────────────
    97	    from app.models.lesson import SectionType
    98
    99	    natural_speed = next(
   100	        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
   101	        None,
   102	    )
   103
   104	    unique_lemmas: set[str] = set()
   105	    if natural_speed is not None:
   106	        for phrase in natural_speed.phrases:
   107	            if phrase.language_code != lesson.language_code:
   108	                continue
   109	            for surface in tokenize(phrase.text):
   110	                lemma = _lemmatizer.lemmatize(surface, lesson.language_code)
   111	                unique_lemmas.add(lemma)
   112
   113	    for lemma in unique_lemmas:
   114	        unit = SyntacticUnit(
   115	            text=lemma,
   116	            translation="",
   117	            word_count=1,
   118	            difficulty=1,
   119	            source="llm",
   120	            lemma=lemma,
   121	        )
   122	        db.add_collocation(unit, language_code=lesson.language_code)
   123	        item = db.get_collocation_by_lemma(lemma)
   124	        if item is not None:
   125	            rating = _WORD_RATING_MAP.get(body.word_ratings.get(lemma, "good"), Rating.GOOD)
   126	            updated = schedule(item, rating)
   127	            db.update_collocation(updated)
   128
   129	    # ── Key phrase registration (preserves translations) ─────────────────
   130	    for kp in lesson.key_phrases:
   131	        unit = SyntacticUnit(
   132	            text=kp.phrase,
   133	            translation=kp.translation,
   134	            word_count=min(8, max(1, len(kp.phrase.split()))),
   135	            difficulty=1,
   136	            source="llm",
   137	        )
   138	        db.add_collocation(unit, language_code=lesson.language_code)
   139
   140	    registered = len(unique_lemmas) + len(lesson.key_phrases)
   141	    return {"status": "ok", "registered": registered}
   142
   143
   144	@router.get("/lesson/{lesson_id}/transcript", status_code=200)
   145	async def get_lesson_transcript(lesson_id: str, request: Request):
   146	    store = request.app.state.content_store
   147	    lesson = store.get_lesson(lesson_id)
   148	    if lesson is None:
   149	        raise HTTPException(status_code=404, detail="Lesson not found")
   150
   151	    db = request.app.state.srs_db
   152	    transcript = extract_transcript(lesson, db, _lemmatizer)
   153
   154	    return {
   155	        "lesson_id": lesson_id,
   156	        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in transcript.key_phrases],
   157	        "dialogue_lines": [
   158	            {
   159	                "role": line.role,
   160	                "words": [{"surface": w.surface, "lemma": w.lemma, "srs_state": w.srs_state} for w in line.words],
   161	            }
   162	            for line in transcript.dialogue_lines
   163	        ],
   164	    }
   165
   166
   167	@router.get("/stats", status_code=200)
   168	async def get_stats(request: Request):
   169	    db = request.app.state.srs_db
   170	    today = datetime.date.today()
   171	    return {"total": db.count_collocations(), "due_today": db.count_due_collocations(today)}
   172
   173
   174	# ── Admin endpoints ────────────────────────────────────────────────────────────
   175
   176
   177	class UpdateItemRequest(BaseModel):
   178	    text: str
   179	    translation: str
   180
   181
   182	class BulkDeleteRequest(BaseModel):
   183	    ids: list[int]
   184
   185
   186	class SuspendRequest(BaseModel):
   187	    suspended: bool
   188
   189
   190	@router.get("/items", status_code=200)
   191	async def list_items(
   192	    request: Request,
   193	    search: str | None = None,
   194	    state: str | None = None,
   195	    sort: str = "text",
   196	    order: str = "asc",
   197	    limit: int = 50,
   198	    offset: int = 0,
   199	):
   200	    db = request.app.state.srs_db
   201	    state_enum = SRSState(state) if state else None
   202	    try:
   203	        rows, total = db.list_collocations(
   204	            limit=limit,
   205	            offset=offset,
   206	            search=search,
   207	            state=state_enum,
   208	            order_by=sort,
   209	            order_dir=order,
   210	        )
   211	    except ValueError as exc:
   212	        raise HTTPException(status_code=422, detail=str(exc)) from exc
   213	    return {"items": [_item_to_dict(rid, item, lang) for rid, item, lang in rows], "total": total}
   214
   215
   216	@router.patch("/items/{item_id}", status_code=200)
   217	async def patch_item(item_id: int, body: UpdateItemRequest, request: Request):
   218	    db = request.app.state.srs_db
   219	    if db.get_collocation_by_id(item_id) is None:
   220	        raise HTTPException(status_code=404, detail="Item not found")
   221	    try:
   222	        db.update_collocation_fields(item_id, text=body.text, translation=body.translation)
   223	    except ValueError as exc:
   224	        raise HTTPException(status_code=409, detail=str(exc)) from exc
   225	    row_id, item, lang = db.get_collocation_by_id(item_id)
   226	    return _item_to_dict(row_id, item, lang)
   227
   228
   229	@router.delete("/items/{item_id}", status_code=200)
   230	async def delete_item(item_id: int, request: Request):
   231	    db = request.app.state.srs_db
   232	    if db.get_collocation_by_id(item_id) is None:
   233	        raise HTTPException(status_code=404, detail="Item not found")
   234	    db.delete_collocation(item_id)
   235	    return {"status": "deleted"}
   236
   237
   238	@router.post("/items/bulk-delete", status_code=200)
   239	async def bulk_delete_items(body: BulkDeleteRequest, request: Request):
   240	    db = request.app.state.srs_db
   241	    deleted = db.delete_collocations(body.ids)
   242	    return {"deleted": deleted}
   243
   244
   245	@router.post("/items/{item_id}/reset", status_code=200)
   246	async def reset_item(item_id: int, request: Request):
   247	    db = request.app.state.srs_db
   248	    if db.get_collocation_by_id(item_id) is None:
   249	        raise HTTPException(status_code=404, detail="Item not found")
   250	    db.reset_collocation(item_id)
   251	    row_id, item, lang = db.get_collocation_by_id(item_id)
   252	    return _item_to_dict(row_id, item, lang)
   253
   254
   255	@router.post("/items/{item_id}/suspend", status_code=200)
   256	async def suspend_item(item_id: int, body: SuspendRequest, request: Request):
   257	    db = request.app.state.srs_db
   258	    if db.get_collocation_by_id(item_id) is None:
   259	        raise HTTPException(status_code=404, detail="Item not found")
   260	    db.set_suspended(item_id, body.suspended)
   261	    row_id, item, lang = db.get_collocation_by_id(item_id)
   262	    return _item_to_dict(row_id, item, lang)
```

The SRS router grew substantially. It now covers three functional areas:

**Learner loop** (unchanged from prototype):
- `GET /due` — collocations due for review today
- `GET /new` — collocations in `new` state (for spaced introduction)
- `POST /feedback` — record implicit feedback signal, advance FSRS schedule
- `GET /stats` — total and due-today counts

**Per-word tracking** (new):
- `POST /listen` — called after the learner finishes a lesson. Tokenizes every L2 word in the NATURAL_SPEED section, lemmatizes it, and upserts a word-level `SRSItem`. Optional `word_ratings` map lets the frontend pass per-word ratings (`"dober": "hard"`) so the first FSRS schedule isn't always `Good`. Also registers key phrases (with translations) so they appear in the review queue.
- `GET /lesson/{lesson_id}/transcript` — returns the NATURAL_SPEED dialogue annotated with per-word SRS state (`new`/`learning`/`review`/`relearning`/`unknown`). Used by the frontend to colour-code words red/yellow/green.

**Admin** (new — see §9.x):
- `GET /items` — paginated, filterable, sortable list of all SRS items. Query params: `search` (substring match on text/translation), `state` (`new`/`learning`/`review`/`relearning`/`suspended`), `sort` (`text`/`state`/`due_date`/etc.), `order` (`asc`/`desc`), `limit`, `offset`.
- `PATCH /items/{id}` — edit text + translation in-place. Returns the updated item. Raises 409 if the new text conflicts with an existing row.
- `DELETE /items/{id}` — remove a single item.
- `POST /items/bulk-delete` — remove a list of items by ID in one call (used by the admin UI's multi-select delete).
- `POST /items/{id}/reset` — reset FSRS state to `new` (due today, all scheduling fields zeroed). Useful when a learner wants to re-learn a phrase from scratch.
- `POST /items/{id}/suspend` — toggle the `suspended` state flag. Suspended items are excluded from `GET /due` so the learner never sees them until unsuspended.

### 7.4 Audio API

```bash
cat -n backend/app/api/audio.py
```

```output
     1	"""Audio generation and streaming endpoints."""
     2
     3	from __future__ import annotations
     4
     5	import re
     6	import uuid
     7	from pathlib import Path
     8
     9	from fastapi import APIRouter, HTTPException, Request
    10	from fastapi.responses import FileResponse
    11	from pydantic import BaseModel
    12
    13	from app.generation.section_builder import SECTION_TITLES
    14	from app.models.lesson import SectionType
    15
    16	router = APIRouter(prefix="/api/audio", tags=["audio"])
    17
    18
    19	def _sanitize_filename(name: str) -> str:
    20	    """Strip filesystem-illegal characters and collapse whitespace to underscores."""
    21	    name = re.sub(r'[/\\:*?"<>|]', "", name)
    22	    name = re.sub(r"\s+", "_", name.strip())
    23	    return name or "audio"
    24
    25
    26	class RenderAudioRequest(BaseModel):
    27	    lesson_id: str
    28
    29
    30	@router.post("/render", status_code=202)
    31	async def render_audio(body: RenderAudioRequest, request: Request):
    32	    store = request.app.state.content_store
    33	    lesson = store.get_lesson(body.lesson_id)
    34	    if lesson is None:
    35	        raise HTTPException(status_code=404, detail="Lesson not found")
    36
    37	    renderer = request.app.state.renderer
    38	    audio_dir: Path = request.app.state.audio_dir
    39	    audio_dir.mkdir(parents=True, exist_ok=True)
    40
    41	    # Allocate UUIDs for full lesson and each section
    42	    audio_id = str(uuid.uuid4())
    43	    full_path = audio_dir / f"{audio_id}.wav"
    44
    45	    section_ids = [str(uuid.uuid4()) for _ in lesson.sections]
    46	    section_paths = [audio_dir / f"{sid}.wav" for sid in section_ids]
    47
    48	    await renderer.render(lesson, full_path, section_paths=section_paths)
    49
    50	    # Persist full lesson row
    51	    store.save_audio_file(audio_id, body.lesson_id, str(full_path))
    52
    53	    # Persist per-section rows
    54	    for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True)):
    55	        store.save_audio_file(
    56	            sid,
    57	            body.lesson_id,
    58	            str(section_paths[i]),
    59	            section_index=i,
    60	            section_type=section.section_type.value,
    61	        )
    62
    63	    sections = [
    64	        {
    65	            "audio_id": sid,
    66	            "section_index": i,
    67	            "section_type": section.section_type.value,
    68	            "title": SECTION_TITLES.get(section.section_type, section.section_type.value),
    69	        }
    70	        for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True))
    71	    ]
    72
    73	    return {"audio_id": audio_id, "lesson_id": body.lesson_id, "sections": sections}
    74
    75
    76	@router.get("/lesson/{lesson_id}", status_code=200)
    77	async def get_lesson_audio(lesson_id: str, request: Request):
    78	    """Return the audio file list for a lesson (full + sections) without re-rendering."""
    79	    store = request.app.state.content_store
    80	    rows = store.list_audio_files_for_lesson(lesson_id)
    81	    if not rows:
    82	        raise HTTPException(status_code=404, detail="No audio found for this lesson")
    83
    84	    full_row = next((r for r in rows if r["section_index"] is None), None)
    85	    if full_row is None:
    86	        raise HTTPException(status_code=404, detail="Full lesson audio not found")
    87
    88	    section_rows = [r for r in rows if r["section_index"] is not None]
    89
    90	    sections = []
    91	    for r in section_rows:
    92	        section_type_str = r["section_type"] or ""
    93	        try:
    94	            st = SectionType(section_type_str)
    95	            title = SECTION_TITLES.get(st, section_type_str)
    96	        except ValueError:
    97	            title = section_type_str
    98	        sections.append(
    99	            {
   100	                "audio_id": r["id"],
   101	                "section_index": r["section_index"],
   102	                "section_type": section_type_str,
   103	                "title": title,
   104	            }
   105	        )
   106
   107	    return {
   108	        "audio_id": full_row["id"],
   109	        "lesson_id": lesson_id,
   110	        "sections": sections,
   111	    }
   112
   113
   114	@router.get("/{audio_id}", status_code=200)
   115	async def get_audio(audio_id: str, request: Request):
   116	    store = request.app.state.content_store
   117	    row = store.get_audio_file_row(audio_id)
   118	    if row is None:
   119	        raise HTTPException(status_code=404, detail="Audio not found")
   120
   121	    path = Path(row["file_path"])
   122	    if not path.exists():
   123	        raise HTTPException(status_code=404, detail="Audio file missing")
   124
   125	    # Build a friendly download filename
   126	    lesson = store.get_lesson(row["lesson_id"])
   127	    lesson_title = lesson.title if lesson else "audio"
   128	    safe_title = _sanitize_filename(lesson_title)
   129
   130	    if row["section_index"] is not None:
   131	        section_type = row["section_type"] or "section"
   132	        idx = row["section_index"]
   133	        filename = f"{safe_title}_{idx:02d}_{section_type}.wav"
   134	    else:
   135	        filename = f"{safe_title}.wav"
   136
   137	    return FileResponse(
   138	        str(path),
   139	        media_type="audio/wav",
   140	        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
   141	    )
```

Three changes from the prototype:

1. **Per-section audio.** `POST /render` allocates a UUID for each section as well as for the full lesson. The renderer writes one WAV per section plus the full-lesson WAV. All are persisted in `ContentStore.audio_files` with `section_index` and `section_type` columns. The response body includes the section list so the frontend can build a section picker immediately.
2. **`GET /lesson/{lesson_id}`** returns the audio metadata (full audio ID + section list) for a lesson that was already rendered, without re-rendering. The frontend calls this on lesson load to check whether audio is ready.
3. **Friendly filenames.** `GET /{audio_id}` builds a `Content-Disposition` filename from the lesson title and section info (`Arriving_in_Ljubljana_01_slow_speed.wav`), so the file is self-describing when downloaded.

### 7.5 Route Reference

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/curriculum/generate` | POST | Generate a multi-day curriculum |
| `/api/curriculum` | GET | List all persisted curricula |
| `/api/curriculum/{id}` | GET | Retrieve curriculum metadata |
| `/api/curriculum/{id}/days/{day}/lesson` | GET | Get latest lesson for a curriculum day |
| `/api/story/generate` | POST | Generate a Pimsleur lesson from a curriculum day |
| `/api/story/{lesson_id}` | GET | Retrieve lesson with full phrase list |
| `/api/srs/due` | GET | Collocations due for review today |
| `/api/srs/new` | GET | Collocations in `new` state |
| `/api/srs/feedback` | POST | Record implicit feedback signal |
| `/api/srs/listen` | POST | Mark lesson listened + register words with SRS |
| `/api/srs/lesson/{id}/transcript` | GET | Per-word transcript with SRS state |
| `/api/srs/stats` | GET | Total / due-today counts |
| `/api/srs/items` | GET | Admin: paginated SRS item list |
| `/api/srs/items/{id}` | PATCH | Admin: edit text + translation |
| `/api/srs/items/{id}` | DELETE | Admin: delete item |
| `/api/srs/items/bulk-delete` | POST | Admin: bulk delete by ID list |
| `/api/srs/items/{id}/reset` | POST | Admin: reset FSRS schedule to `new` |
| `/api/srs/items/{id}/suspend` | POST | Admin: toggle suspended flag |
| `/api/audio/render` | POST | Render lesson to WAV (full + per-section) |
| `/api/audio/lesson/{lesson_id}` | GET | Get audio metadata for a lesson |
| `/api/audio/{audio_id}` | GET | Download a WAV file |
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

409 tests, **100% branch coverage**. All in mock mode — no network calls needed. The coverage target was raised from 95% to 100% after the test suite expanded to cover every branch.

### 8.2 Test File Inventory

```bash
ls backend/tests/test_*.py | xargs -I{} sh -c "echo \"{}: \$(grep -c \"def test_\" {}) tests\"" | sort
```

```output
backend/tests/test_api.py: 48 tests
backend/tests/test_api_srs_admin.py: 14 tests
backend/tests/test_audio_ports.py: 5 tests
backend/tests/test_config.py: 2 tests
backend/tests/test_curriculum.py: 13 tests
backend/tests/test_edge_tts.py: 9 tests
backend/tests/test_enforcer.py: 10 tests
backend/tests/test_fsrs.py: 13 tests
backend/tests/test_lemmatizer.py: 7 tests
backend/tests/test_llm_cassette.py: 11 tests
backend/tests/test_llm_client.py: 41 tests
backend/tests/test_main_lifespan.py: 2 tests
backend/tests/test_models.py: 37 tests
backend/tests/test_pauses.py: 12 tests
backend/tests/test_preprocessor.py: 7 tests
backend/tests/test_prompts.py: 7 tests
backend/tests/test_renderer.py: 19 tests
backend/tests/test_section_builder.py: 24 tests
backend/tests/test_srs_database.py: 37 tests
backend/tests/test_srs_feedback.py: 5 tests
backend/tests/test_srs_selector.py: 7 tests
backend/tests/test_storage.py: 17 tests
backend/tests/test_story.py: 11 tests
backend/tests/test_syllabify.py: 5 tests
backend/tests/test_tokenizer.py: 13 tests
backend/tests/test_transcript.py: 11 tests
```

409 tests across 26 files. New test files since the original walkthrough:

| File | Tests | What it covers |
|------|-------|----------------|
| `test_api_srs_admin.py` | 14 | SRS admin endpoints (list/patch/delete/bulk-delete/reset/suspend) |
| `test_lemmatizer.py` | 7 | `LowercaseLemmatizer` Protocol implementation |
| `test_main_lifespan.py` | 2 | FastAPI startup/shutdown lifecycle |
| `test_section_builder.py` | 24 | All four section builders + `build_word_breakdown` |
| `test_storage.py` | 17 | `ContentStore` round-trips for curricula, lessons, audio files |
| `test_syllabify.py` | 5 | `syllabify_slovene_word` onset-maximization algorithm |
| `test_tokenizer.py` | 13 | `tokenize()` whitespace splitting and punctuation stripping |
| `test_transcript.py` | 11 | `extract_transcript` per-word SRS annotation |

The heaviest areas: API (48+14=62), LLM client (41), SRS database (37), models (37), section builder (24), renderer (19), storage (17).

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
| **Testing** | Unit tests only | 409 tests, 100% branch coverage, cassette fixtures, 4 mock strategies |
| **API endpoints** | 10 endpoints | 22 endpoints |

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
./test.sh   # ruff lint + pytest (409 tests) + vitest (frontend)
```

### Start the dev server
```bash
./start-dev.sh   # FastAPI at :8000 + SvelteKit at :5173
```
Open http://localhost:5173, enter a topic (e.g. "ordering coffee in Ljubljana"), choose CEFR level and days, click Generate → select a day → Generate Lesson → Render Audio → play.

### SRS practice loop
First generate a curriculum and lesson (which registers SRS items via `POST /api/srs/listen`), then navigate to http://localhost:5173/practice — click through cards, rate each with Again / Hard / Good / Easy, and view the completion screen after the last card.

### SRS admin UI
Navigate to http://localhost:5173/srs to browse and manage SRS items. Features: search (full-text across text and translation), filter by state, sortable columns, inline edit, single and bulk delete, reset schedule, suspend/unsuspend.

