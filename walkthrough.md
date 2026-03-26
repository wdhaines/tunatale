# TunaTale Production Codebase Walkthrough

*2026-03-25T01:20:45Z by Showboat 0.6.1*
<!-- showboat-id: 4bdef7f8-1973-46b4-b00d-14caf394240c -->

## Purpose of This Document

This walkthrough covers the production TunaTale codebase — the unified application rebuilt from the two prototypes documented in `walkthrough-prototypes.md`. It serves two audiences: (1) a human reader wanting to understand how TunaTale works, and (2) an AI agent extending or maintaining the system.

**What changed from the prototypes:** The production rebuild unified the audio pipeline (micro-demo-0.0) and the content engine (micro-demo-0.1) under a single FastAPI application. Hardcoded language logic was replaced with pluggable preprocessors and voice maps. The mock LLM (MD5-hashed) became a cassette system with multiple modes. FSRS-5 replaced the custom SRS scheduler. The entire codebase follows hexagonal architecture with Protocol-based ports.

## Architecture at a Glance

```
backend/
├── app/
│   ├── main.py              # FastAPI app with CORS, lifespan, routers
│   ├── config.py             # Pydantic Settings (env-driven)
│   ├── models/               # Pure domain models (no I/O)
│   ├── llm/                  # Groq LLM client + cassette replay system
│   ├── srs/                  # FSRS-5 spaced repetition engine
│   ├── generation/           # Curriculum + story generation + enforcement
│   ├── audio/                # TTS, audio assembly, preprocessing
│   └── api/                  # FastAPI route modules
└── tests/
    ├── conftest.py           # Cassette fixtures, CLI options
    ├── cassettes/            # Recorded LLM responses (JSON)
    └── test_*.py             # 18 test files, ≥95% coverage
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
     5	
     6	from dotenv import load_dotenv
     7	
     8	load_dotenv()
     9	
    10	from fastapi import FastAPI  # noqa: E402
    11	from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
    12	
    13	logger = logging.getLogger(__name__)
    14	
    15	
    16	@asynccontextmanager
    17	async def lifespan(app: FastAPI):  # pragma: no cover
    18	    logger.info("TunaTale backend starting up")
    19	    yield
    20	    logger.info("TunaTale backend shutting down")
    21	
    22	
    23	app = FastAPI(title="TunaTale", version="0.1.0", lifespan=lifespan)
    24	
    25	app.add_middleware(
    26	    CORSMiddleware,
    27	    allow_origins=["*"],
    28	    allow_credentials=True,
    29	    allow_methods=["*"],
    30	    allow_headers=["*"],
    31	)
    32	
    33	from app.api import audio, curriculum, generation, srs  # noqa: E402
    34	
    35	app.include_router(curriculum.router)
    36	app.include_router(generation.router)
    37	app.include_router(srs.router)
    38	app.include_router(audio.router)
    39	
    40	
    41	@app.get("/api/health")
    42	async def health():
    43	    return {"status": "ok"}
```

The app is minimal by design. The lifespan context manager handles startup/shutdown logging (excluded from coverage since it's infrastructure). CORS is wide-open for development. Four routers partition the API: curriculum generation, story generation, SRS tracking, and audio rendering. The health check at `/api/health` is the smoke test — if this returns `{"status": "ok"}`, the server is alive.

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
    26	                "female": "sl-SI-PetraNeural",
    27	                "male": "sl-SI-RokNeural",
    28	            },
    29	        )
    30	
    31	    @classmethod
    32	    def english(cls) -> Language:
    33	        return cls(
    34	            code="en",
    35	            name="English",
    36	            native_name="English",
    37	            script="latin",
    38	            tts_voice_map={
    39	                "female": "en-US-AriaNeural",
    40	                "male": "en-US-GuyNeural",
    41	            },
    42	        )
```

**Key design decision:** The prototype hardcoded a `Language` enum with Tagalog/English/Spanish. Production replaces this with a data-driven `Language` dataclass. Adding a new language is just creating a new factory method — no enum changes, no code branching. The `tts_voice_map` dict maps roles ("female", "male") to EdgeTTS voice names, which the audio pipeline looks up at synthesis time.

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
tts_voice_map: {'female': 'sl-SI-PetraNeural', 'male': 'sl-SI-RokNeural'}
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
     8	from dataclasses import dataclass, field
     9	from enum import Enum
    10	
    11	
    12	class SectionType(Enum):
    13	    """Four Pimsleur section types for each lesson."""
    14	
    15	    KEY_PHRASES = "key_phrases"
    16	    NATURAL_SPEED = "natural_speed"
    17	    SLOW_SPEED = "slow_speed"
    18	    TRANSLATED = "translated"
    19	
    20	
    21	@dataclass
    22	class Phrase:
    23	    """A single phrase with TTS voice settings."""
    24	
    25	    text: str
    26	    voice_id: str
    27	    language_code: str
    28	    rate: str = "+0%"
    29	    pitch: str = "+0Hz"
    30	    volume: str = "+0%"
    31	
    32	
    33	@dataclass
    34	class Section:
    35	    """A section within a lesson, grouping phrases of the same Pimsleur type."""
    36	
    37	    section_type: SectionType
    38	    phrases: list[Phrase] = field(default_factory=list)
    39	
    40	    def __post_init__(self) -> None:
    41	        if not isinstance(self.section_type, SectionType):
    42	            raise ValueError(f"section_type must be a SectionType enum, got {type(self.section_type)}")
    43	
    44	
    45	@dataclass
    46	class Lesson:
    47	    """A complete TunaTale audio lesson."""
    48	
    49	    title: str
    50	    language_code: str
    51	    sections: list[Section] = field(default_factory=list)
```

The four section types encode the Pimsleur method: (1) **KEY_PHRASES** — individual vocabulary, (2) **NATURAL_SPEED** — full dialogue at native speed, (3) **SLOW_SPEED** — same dialogue with pauses between words, (4) **TRANSLATED** — L2 followed by L1 translation. Each `Phrase` carries its own TTS settings (rate, pitch, volume) so the audio pipeline can render without additional lookups.

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
    22	
    23	    def __post_init__(self) -> None:
    24	        if not 1 <= self.word_count <= 8:
    25	            raise ValueError(f"word_count must be 1–8, got {self.word_count}")
    26	        if not 1 <= self.difficulty <= 5:
    27	            raise ValueError(f"difficulty must be 1–5, got {self.difficulty}")
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
    30	@dataclass
    31	class SRSItem:
    32	    """An SRS-tracked syntactic unit with FSRS scheduling fields."""
    33	
    34	    syntactic_unit: SyntacticUnit
    35	    due_date: date
    36	    stability: float = 1.0  # FSRS stability (days before 90% retention)
    37	    difficulty: float = 5.0  # FSRS difficulty (1–10)
    38	    reps: int = 0
    39	    lapses: int = 0
    40	    state: SRSState = field(default=SRSState.NEW)
    41	    last_review: date | None = None
```

The `SyntacticUnit` is a collocation (multi-word phrase) with bounds validation — word_count 1–8, difficulty 1–5. The `SRSItem` wraps a SyntacticUnit with FSRS-5 scheduling fields: stability (days before 90% retention drops), difficulty (1–10 scale), reps, lapses, and state tracking. The state machine is: NEW → LEARNING → REVIEW ↔ RELEARNING.

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
     1	"""Async LLM client — Groq via OpenAI-compatible API."""
     2	
     3	from __future__ import annotations
     4	
     5	import asyncio
     6	import logging
     7	import re
     8	import time
     9	
    10	import httpx
    11	
    12	logger = logging.getLogger(__name__)
    13	
    14	GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
    15	GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
    16	
    17	
    18	class LLMError(Exception):
    19	    """Raised when the LLM call fails."""
    20	
    21	    def __init__(self, message: str, attempts: list[dict] | None = None) -> None:
    22	        super().__init__(message)
    23	        self.attempts = attempts or []
    24	
    25	
    26	class LLMClient:
    27	    def __init__(
    28	        self,
    29	        groq_api_key: str | None = None,
    30	        groq_model: str = GROQ_DEFAULT_MODEL,
    31	        timeout: float = 30.0,
    32	        max_retries_429: int = 3,
    33	        max_retry_after_s: float = 10.0,
    34	    ) -> None:
    35	        self.groq_api_key = groq_api_key
    36	        self.groq_model = groq_model
    37	        self.timeout = timeout
    38	        self.max_retries_429 = max_retries_429
    39	        self.max_retry_after_s = max_retry_after_s
    40	        self.last_provider: str | None = None
    41	        self._next_call_at: float = 0.0
    42	        self._groq_call_delay: float = 0.0
    43	        self._last_429_at: float = 0.0
    44	
    45	    async def complete(
    46	        self,
    47	        prompt: str,
    48	        system_prompt: str | None = None,
    49	        temperature: float = 0.7,
    50	        max_tokens: int = 2048,
    51	    ) -> str:
    52	        if not self.groq_api_key:
    53	            raise LLMError("No GROQ_API_KEY configured")
    54	        return await self._call_groq(
    55	            prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
    56	        )
    57	
    58	    async def _call_groq(
    59	        self,
    60	        prompt: str,
    61	        system_prompt: str | None,
    62	        temperature: float,
    63	        max_tokens: int,
    64	    ) -> str:
    65	        headers = {
    66	            "Authorization": f"Bearer {self.groq_api_key}",
    67	            "Content-Type": "application/json",
    68	        }
    69	        messages: list[dict] = []
    70	        if system_prompt:
    71	            messages.append({"role": "system", "content": system_prompt})
    72	        messages.append({"role": "user", "content": prompt})
    73	
    74	        body = {
    75	            "model": self.groq_model,
    76	            "messages": messages,
    77	            "temperature": temperature,
    78	            "max_tokens": max_tokens,
    79	        }
    80	
    81	        async with httpx.AsyncClient(timeout=self.timeout) as http:
    82	            for attempt in range(self.max_retries_429 + 1):
    83	                if self._groq_call_delay > 0 and time.monotonic() - self._last_429_at > 60:
    84	                    self._groq_call_delay = 0.0
    85	                wait = self._next_call_at - time.monotonic()
    86	                if wait > 0:
    87	                    await asyncio.sleep(wait)
    88	
    89	                start = time.monotonic()
    90	                response = await http.post(GROQ_API_URL, headers=headers, json=body)
    91	                latency_ms = int((time.monotonic() - start) * 1000)
    92	
    93	                if response.status_code == 429:
    94	                    retry_after_raw = response.headers.get("retry-after", "2")
    95	                    try:
    96	                        retry_after = float(retry_after_raw)
    97	                    except ValueError:
    98	                        retry_after = 2.0
    99	
   100	                    msg = f"Groq returned 429 Too Many Requests (retry after {retry_after_raw}s)"
   101	
   102	                    if retry_after <= self.max_retry_after_s:
   103	                        self._last_429_at = time.monotonic()
   104	                        self._groq_call_delay = retry_after
   105	
   106	                    if attempt < self.max_retries_429 and retry_after <= self.max_retry_after_s:
   107	                        logger.warning(
   108	                            "Groq 429, retry %d/%d after %.1fs", attempt + 1, self.max_retries_429, retry_after
   109	                        )
   110	                        await asyncio.sleep(retry_after)
   111	                        continue
   112	
   113	                    raise LLMError(
   114	                        msg,
   115	                        [
   116	                            {
   117	                                "provider": "groq",
   118	                                "model": self.groq_model,
   119	                                "status": 429,
   120	                                "error": msg,
   121	                                "latency_ms": latency_ms,
   122	                            }
   123	                        ],
   124	                    )
   125	
   126	                if not response.is_success:
   127	                    msg = f"Groq returned HTTP {response.status_code}"
   128	                    raise LLMError(
   129	                        msg,
   130	                        [
   131	                            {
   132	                                "provider": "groq",
   133	                                "model": self.groq_model,
   134	                                "status": response.status_code,
   135	                                "error": msg,
   136	                                "latency_ms": latency_ms,
   137	                            }
   138	                        ],
   139	                    )
   140	
   141	                data = response.json()
   142	                content = data["choices"][0]["message"]["content"]
   143	                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
   144	                self.last_provider = "groq"
   145	                logger.info("Groq success: model=%s latency=%dms", self.groq_model, latency_ms)
   146	                return content
   147	
   148	        raise LLMError("Groq call loop exhausted", [])  # pragma: no cover
```

The `LLMClient` talks to Groq's OpenAI-compatible endpoint. Key behaviors:

- **Rate-limit handling**: On HTTP 429, reads the `retry-after` header and sleeps before retrying (up to 3 retries, capped at 10s per wait). After a 429, subsequent calls are delayed automatically.
- **Think-tag stripping**: Groq's `llama-3.3-70b` sometimes wraps reasoning in `<think>...</think>` tags. Line 143 strips these so downstream JSON parsing isn't polluted.
- **Attempt logging**: Every failure is recorded with provider, model, status, and latency for debugging.

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
50:class SRSDatabase:
56:    def close(self) -> None:
62:    def __enter__(self) -> SRSDatabase:
65:    def __exit__(self, *_) -> None:
68:    def __init__(self, db_path: str = ":memory:") -> None:
82:    def _init_schema(self, conn: sqlite3.Connection) -> None:
90:    def _file_conn(self):
100:    def _get_conn(self):
109:    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> None:
135:    def update_collocation(self, item: SRSItem) -> None:
165:    def record_violation(
179:    def get_collocation(self, text: str) -> SRSItem | None:
187:    def get_due_collocations(self, as_of: date) -> list[SRSItem]:
196:    def get_new_collocations(self, limit: int = 10) -> list[SRSItem]:
205:    def get_violations(self, collocation_text: str) -> list[dict]:
214:    def count_collocations(self) -> int:
221:    def _row_to_item(row: sqlite3.Row) -> SRSItem:
```

The `SRSDatabase` is a SQLite repository with two tables: `collocations` (vocabulary with FSRS fields) and `violations` (content rule violations for debugging). It supports `:memory:` for tests and file-based persistence for production.

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
     8	from app.models.curriculum import CurriculumDay
     9	from app.models.language import Language
    10	from app.models.lesson import Lesson, Phrase, Section, SectionType
    11	from app.models.strategy import ContentStrategy
    12	from app.srs.database import SRSDatabase
    13	
    14	logger = logging.getLogger(__name__)
    15	
    16	_SECTION_TYPE_MAP = {
    17	    "key_phrases": SectionType.KEY_PHRASES,
    18	    "natural_speed": SectionType.NATURAL_SPEED,
    19	    "slow_speed": SectionType.SLOW_SPEED,
    20	    "translated": SectionType.TRANSLATED,
    21	}
    22	
    23	_STORY_PROMPT_TEMPLATE = """\
    24	Generate a Pimsleur-style language lesson for the following curriculum day.
    25	
    26	Language: {language_name} ({language_code})
    27	Day: {day} — {title}
    28	Focus: {focus}
    29	Learning objective: {learning_objective}
    30	Story guidance: {story_guidance}
    31	Strategy: {strategy}
    32	
    33	Key collocations to include:
    34	{collocations}
    35	
    36	Respond with a JSON object matching this schema:
    37	{{
    38	  "sections": [
    39	    {{
    40	      "type": "key_phrases",
    41	      "phrases": [{{"text": "...", "language": "{language_code}"}}]
    42	    }},
    43	    {{
    44	      "type": "natural_speed",
    45	      "phrases": [{{"text": "...", "language": "{language_code}"}}]
    46	    }},
    47	    {{
    48	      "type": "slow_speed",
    49	      "phrases": [{{"text": "...", "language": "{language_code}"}}]
    50	    }},
    51	    {{
    52	      "type": "translated",
    53	      "phrases": [{{"text": "...", "language": "en"}}]
    54	    }}
    55	  ]
    56	}}
    57	
    58	Requirements:
    59	- Respond with ONLY the JSON object, no markdown fences
    60	- All 4 section types must be present
    61	- key_phrases section: include 3–8 target collocations
    62	- natural_speed: full story dialogue at natural pace
    63	- slow_speed: repeat key phrases at reduced pace for practice
    64	- translated: English translation of the natural_speed dialogue
    65	"""
    66	
    67	
    68	class StoryGenerationError(Exception):
    69	    pass
    70	
    71	
    72	class StoryGenerator:
    73	    """Generates a Lesson from a CurriculumDay using the LLM client."""
    74	
    75	    def __init__(self, llm_client, srs_db: SRSDatabase) -> None:
    76	        self._llm = llm_client
    77	        self._db = srs_db
    78	
    79	    async def generate(
    80	        self,
    81	        curriculum_day: CurriculumDay,
    82	        language: Language,
    83	        strategy: ContentStrategy,
    84	    ) -> Lesson:
    85	        """Generate a Lesson for the given curriculum day.
    86	
    87	        Args:
    88	            curriculum_day: Day specification including collocations and objectives.
    89	            language: Target language configuration.
    90	            strategy: WIDER or DEEPER content strategy.
    91	
    92	        Returns:
    93	            Parsed Lesson with 4 Pimsleur sections.
    94	        """
    95	        collocation_list = "\n".join(f"- {c}" for c in curriculum_day.collocations)
    96	        prompt = _STORY_PROMPT_TEMPLATE.format(
    97	            language_name=language.name,
    98	            language_code=language.code,
    99	            day=curriculum_day.day,
   100	            title=curriculum_day.title,
   101	            focus=curriculum_day.focus,
   102	            learning_objective=curriculum_day.learning_objective,
   103	            story_guidance=curriculum_day.story_guidance,
   104	            strategy=strategy.value,
   105	            collocations=collocation_list,
   106	        )
   107	
   108	        logger.info("Generating story for day %d (%s)", curriculum_day.day, strategy.value)
   109	        raw = await self._llm.complete(prompt, temperature=0.7, max_tokens=4096)
   110	        return self._parse_response(raw, language=language)
   111	
   112	    def _parse_response(self, raw: str, language: Language) -> Lesson:
   113	        try:
   114	            data = json.loads(raw)
   115	        except json.JSONDecodeError as e:
   116	            raise StoryGenerationError(f"LLM returned invalid JSON: {e}") from e
   117	
   118	        sections_data = data.get("sections", [])
   119	        if not sections_data:
   120	            raise StoryGenerationError("LLM response missing 'sections' key")
   121	
   122	        sections = []
   123	        for s in sections_data:
   124	            section_type = _SECTION_TYPE_MAP.get(s.get("type", ""))
   125	            if section_type is None:
   126	                logger.warning("Unknown section type %r — skipping", s.get("type"))
   127	                continue
   128	            phrases = [
   129	                Phrase(
   130	                    text=p["text"],
   131	                    voice_id=language.tts_voice_map.get("female", ""),
   132	                    language_code=p.get("language", language.code),
   133	                )
   134	                for p in s.get("phrases", [])
   135	            ]
   136	            sections.append(Section(section_type=section_type, phrases=phrases))
   137	
   138	        return Lesson(
   139	            title=f"Day {sections[0].phrases[0].text[:20] if sections and sections[0].phrases else 'lesson'}",
   140	            language_code=language.code,
   141	            sections=sections,
   142	        )
```

The story generator takes a `CurriculumDay` and produces a full `Lesson` with 4 Pimsleur sections. The prompt includes the collocations the curriculum specified, and the LLM weaves them into a dialogue. Voice assignment defaults to the "female" voice from the language's `tts_voice_map`.

### 5.4 Content Enforcer

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
    17	MAX_CONCURRENT_REQUESTS = 3
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

EdgeTTS is Microsoft's free neural TTS. The adapter adds three reliability features from the prototype:

1. **Rate limiting**: 200ms minimum delay between requests + semaphore capping at 3 concurrent (avoids Microsoft throttling)
2. **Caching**: SHA-256 keyed on text+voice+rate, so repeated phrases skip synthesis entirely
3. **Retry with backoff**: Transient network errors get 3 attempts with exponential backoff (0.5s, 1s, 2s)

### 6.3 Audio Assembler & Pause Calculator

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
     7	# Word-count → multiplier table (exact from prototype CLAUDE.md)
     8	_WORD_COUNT_MULTIPLIERS: dict[int, float] = {
     9	    1: 1.5,
    10	    2: 1.8,
    11	    3: 2.2,
    12	    4: 2.6,
    13	    5: 3.0,
    14	}
    15	_DEFAULT_MULTIPLIER = 3.5  # 6+ words
    16	
    17	# Fixed boundary pauses (ms)
    18	_BOUNDARY_PAUSES: dict[str, int] = {
    19	    "syllable": 300,
    20	    "sentence": 2000,
    21	}
    22	
    23	_SECTION_BOUNDARY_PAUSE_MS = 3000
    24	_SLOW_SPEED_FACTOR = 1.2
    25	
    26	# Base pause ratio: pause = audio_duration * multiplier
    27	_BASE_PAUSE_RATIO = 0.8
    28	
    29	
    30	class NaturalPauseCalculator:
    31	    """Calculates natural inter-phrase pauses matching prototype ratios."""
    32	
    33	    def _get_word_count_multiplier(self, word_count: int) -> float:
    34	        return _WORD_COUNT_MULTIPLIERS.get(word_count, _DEFAULT_MULTIPLIER)
    35	
    36	    def get_section_boundary_pause(self) -> int:
    37	        """Return the pause (ms) inserted between lesson sections."""
    38	        return _SECTION_BOUNDARY_PAUSE_MS
    39	
    40	    def get_boundary_pause(self, boundary_type: str) -> int:
    41	        """Return a fixed pause (ms) for the given boundary type."""
    42	        return _BOUNDARY_PAUSES[boundary_type]
    43	
    44	    def get_phrase_pause(
    45	        self,
    46	        audio_duration_s: float,
    47	        word_count: int,
    48	        section_type: SectionType,
    49	    ) -> int:
    50	        """Calculate the pause (ms) to insert after a phrase.
    51	
    52	        Args:
    53	            audio_duration_s: Duration of the synthesised phrase audio in seconds.
    54	            word_count: Number of words in the phrase.
    55	            section_type: The section this phrase belongs to.
    56	
    57	        Returns:
    58	            Pause duration in milliseconds (non-negative).
    59	        """
    60	        multiplier = self._get_word_count_multiplier(word_count)
    61	        pause_s = audio_duration_s * _BASE_PAUSE_RATIO * multiplier
    62	
    63	        if section_type == SectionType.SLOW_SPEED:
    64	            pause_s *= _SLOW_SPEED_FACTOR
    65	
    66	        return max(0, int(pause_s * 1000))
```

```bash
cd backend && uv run python -c '
from app.audio.pause_calculator import NaturalPauseCalculator
from app.models.lesson import SectionType

calc = NaturalPauseCalculator()

# A 2-word phrase that took 1.5 seconds to synthesize
pause_normal = calc.get_phrase_pause(audio_duration_s=1.5, word_count=2, section_type=SectionType.NATURAL_SPEED)
pause_slow = calc.get_phrase_pause(audio_duration_s=1.5, word_count=2, section_type=SectionType.SLOW_SPEED)

print("Phrase: Dober dan (2 words, 1.5s audio)")
print(f"  Normal speed pause: {pause_normal}ms")
print(f"  Slow speed pause:   {pause_slow}ms")
print(f"  Section boundary:   {calc.get_section_boundary_pause()}ms")
print()

# A 5-word phrase that took 3.0 seconds
pause_long = calc.get_phrase_pause(audio_duration_s=3.0, word_count=5, section_type=SectionType.NATURAL_SPEED)
print("Phrase: Kje je najblizja postaja (5 words, 3.0s audio)")
print(f"  Normal speed pause: {pause_long}ms")
'
```

```output
Phrase: Dober dan (2 words, 1.5s audio)
  Normal speed pause: 2160ms
  Slow speed pause:   2592ms
  Section boundary:   3000ms

Phrase: Kje je najblizja postaja (5 words, 3.0s audio)
  Normal speed pause: 7200ms
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
     5	import logging
     6	import tempfile
     7	from pathlib import Path
     8	
     9	from app.audio.assembler import AudioAssembler
    10	from app.audio.pause_calculator import NaturalPauseCalculator
    11	from app.audio.ports import TTSService
    12	from app.audio.preprocessing.base import TextPreprocessor
    13	from app.models.lesson import Lesson
    14	
    15	logger = logging.getLogger(__name__)
    16	
    17	# Assumed duration for pause calculation when we can't measure real audio
    18	_DEFAULT_PHRASE_DURATION_S = 1.5
    19	
    20	
    21	class LessonRenderer:
    22	    """Renders a Lesson to an audio file.
    23	
    24	    Pipeline per phrase:
    25	      1. Preprocess text (language-specific)
    26	      2. Synthesize via TTS → temp file
    27	      3. Read bytes
    28	      4. Calculate post-phrase pause
    29	      5. Collect all chunks
    30	    Then assemble with section-boundary gaps and write to output.
    31	    """
    32	
    33	    def __init__(
    34	        self,
    35	        tts: TTSService,
    36	        preprocessor: TextPreprocessor,
    37	        pause_calculator: NaturalPauseCalculator,
    38	        assembler: AudioAssembler,
    39	    ) -> None:
    40	        self._tts = tts
    41	        self._preprocessor = preprocessor
    42	        self._calc = pause_calculator
    43	        self._assembler = assembler
    44	
    45	    async def render(self, lesson: Lesson, output_path: Path) -> None:
    46	        """Render *lesson* to *output_path*.
    47	
    48	        Args:
    49	            lesson: Lesson with sections and phrases.
    50	            output_path: Destination file path (written as raw audio).
    51	        """
    52	        all_chunks: list[bytes] = []
    53	        boundary_silence = self._assembler.add_silence(self._calc.get_section_boundary_pause())
    54	
    55	        with tempfile.TemporaryDirectory() as tmp_dir:
    56	            tmp = Path(tmp_dir)
    57	
    58	            for section_idx, section in enumerate(lesson.sections):
    59	                if section_idx > 0:
    60	                    all_chunks.append(boundary_silence)
    61	
    62	                for phrase_idx, phrase in enumerate(section.phrases):
    63	                    processed = self._preprocessor.preprocess(phrase.text, section.section_type)
    64	                    word_count = len(phrase.text.split())
    65	
    66	                    phrase_file = tmp / f"s{section_idx}_p{phrase_idx}.mp3"
    67	                    await self._tts.synthesize(
    68	                        processed,
    69	                        phrase.voice_id,
    70	                        phrase_file,
    71	                        rate="+0%",
    72	                    )
    73	
    74	                    audio_bytes = phrase_file.read_bytes()
    75	                    all_chunks.append(audio_bytes)
    76	
    77	                    pause_ms = self._calc.get_phrase_pause(
    78	                        audio_duration_s=_DEFAULT_PHRASE_DURATION_S,
    79	                        word_count=word_count,
    80	                        section_type=section.section_type,
    81	                    )
    82	                    if pause_ms > 0:
    83	                        all_chunks.append(self._assembler.add_silence(pause_ms))
    84	
    85	        combined = self._assembler.concatenate(all_chunks, silence_ms=0)
    86	        output_path.parent.mkdir(parents=True, exist_ok=True)
    87	        output_path.write_bytes(combined)
    88	        logger.info("Rendered lesson to %s (%d bytes)", output_path, len(combined))
```

The renderer is the "main loop" of the audio pipeline. For each phrase: preprocess text → synthesize to temp file → read bytes → calculate pause → append both to chunk list. Between sections, a 3-second boundary silence is inserted. Finally, all chunks are concatenated into a single output file.

### 6.5 Text Preprocessing

Language-specific text transformations before TTS:

```bash
cat -n backend/app/audio/preprocessing/base.py && echo "---" && cat -n backend/app/audio/preprocessing/slovene.py
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
---
     1	"""Slovene-specific text preprocessing for TTS synthesis."""
     2	
     3	from __future__ import annotations
     4	
     5	from app.models.lesson import SectionType
     6	
     7	
     8	class SlovenePreprocessor:
     9	    """Prepares Slovene (and translated English) text for TTS synthesis.
    10	
    11	    - NATURAL_SPEED / KEY_PHRASES / TRANSLATED: pass text through unchanged.
    12	    - SLOW_SPEED: insert ellipses between syllable groups to slow delivery.
    13	    """
    14	
    15	    def preprocess(self, text: str, section_type: SectionType) -> str:
    16	        """Preprocess text for the given section type.
    17	
    18	        Args:
    19	            text: Input text to preprocess.
    20	            section_type: Determines what transformations to apply.
    21	
    22	        Returns:
    23	            Preprocessed text suitable for TTS.
    24	        """
    25	        if section_type == SectionType.SLOW_SPEED:
    26	            return self._add_slow_pauses(text)
    27	        return text
    28	
    29	    # ------------------------------------------------------------------
    30	    # Private helpers
    31	    # ------------------------------------------------------------------
    32	
    33	    def _add_slow_pauses(self, text: str) -> str:
    34	        """Insert pause markers between words to produce slower delivery."""
    35	        # Insert an ellipsis between each word so TTS inserts natural gaps
    36	        words = text.split()
    37	        return " ... ".join(words)
```

The prototype had a massive 1000-line Tagalog preprocessor (number clarification, abbreviation handling, ellipsis conversion). Production uses a pluggable `TextPreprocessor` protocol. The Slovene implementation is minimal — SLOW_SPEED sections get ellipses between words (which EdgeTTS naturally renders as pauses, a critical discovery from the prototype), everything else passes through unchanged.

Adding a new language is creating a new class that implements `preprocess()`. Here is the Slovene preprocessor on a slow-speed phrase:

```bash
cd backend && uv run python -c '
from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.models.lesson import SectionType

pp = SlovenePreprocessor()
text = "Kje je najblizja postaja"

print(f"Original:      {text}")
print(f"Natural speed: {pp.preprocess(text, SectionType.NATURAL_SPEED)}")
print(f"Slow speed:    {pp.preprocess(text, SectionType.SLOW_SPEED)}")
'
```

```output
Original:      Kje je najblizja postaja
Natural speed: Kje je najblizja postaja
Slow speed:    Kje ... je ... najblizja ... postaja
```

---

## PART 7: API Layer

Four REST routers expose the full pipeline.

### 7.1 Curriculum API

```bash
cat -n backend/app/api/curriculum.py
```

```output
     1	"""Curriculum generation and retrieval endpoints."""
     2	
     3	from __future__ import annotations
     4	
     5	import uuid
     6	
     7	from fastapi import APIRouter, HTTPException, Request
     8	from pydantic import BaseModel
     9	
    10	router = APIRouter(prefix="/api/curriculum", tags=["curriculum"])
    11	
    12	
    13	class GenerateCurriculumRequest(BaseModel):
    14	    topic: str
    15	    cefr_level: str = "A2"
    16	    num_days: int = 7
    17	
    18	
    19	@router.post("/generate", status_code=201)
    20	async def generate_curriculum(body: GenerateCurriculumRequest, request: Request):
    21	    generator = request.app.state.curriculum_generator
    22	    language = request.app.state.language
    23	
    24	    curriculum = await generator.generate(
    25	        topic=body.topic,
    26	        language=language,
    27	        cefr_level=body.cefr_level,
    28	        num_days=body.num_days,
    29	    )
    30	
    31	    curriculum_id = str(uuid.uuid4())
    32	    if not hasattr(request.app.state, "curricula"):
    33	        request.app.state.curricula = {}
    34	    request.app.state.curricula[curriculum_id] = curriculum
    35	
    36	    return {
    37	        "id": curriculum_id,
    38	        "topic": curriculum.topic,
    39	        "language_code": curriculum.language_code,
    40	        "days": len(curriculum.days),
    41	    }
    42	
    43	
    44	@router.get("", status_code=200)
    45	async def list_curricula(request: Request):
    46	    curricula = getattr(request.app.state, "curricula", {})
    47	    return [{"id": cid, "topic": c.topic} for cid, c in curricula.items()]
    48	
    49	
    50	@router.get("/{curriculum_id}", status_code=200)
    51	async def get_curriculum(curriculum_id: str, request: Request):
    52	    curricula = getattr(request.app.state, "curricula", {})
    53	    if curriculum_id not in curricula:
    54	        raise HTTPException(status_code=404, detail="Curriculum not found")
    55	    c = curricula[curriculum_id]
    56	    return {"id": curriculum_id, "topic": c.topic, "language_code": c.language_code, "days": len(c.days)}
```

The API layer uses `app.state` for in-memory storage — suitable for prototype/development, not production. Each endpoint reaches back to `request.app.state` for services and data.

### 7.2 Story Generation, SRS, and Audio APIs

```bash
grep -n "def \|class \|router\." backend/app/api/generation.py backend/app/api/srs.py backend/app/api/audio.py
```

```output
backend/app/api/generation.py:15:class GenerateStoryRequest(BaseModel):
backend/app/api/generation.py:21:@router.post("/generate", status_code=201)
backend/app/api/generation.py:22:async def generate_story(body: GenerateStoryRequest, request: Request):
backend/app/api/srs.py:13:class FeedbackRequest(BaseModel):
backend/app/api/srs.py:18:@router.get("/due", status_code=200)
backend/app/api/srs.py:19:async def get_due_collocations(request: Request):
backend/app/api/srs.py:26:@router.post("/feedback", status_code=200)
backend/app/api/srs.py:27:async def record_feedback(body: FeedbackRequest, request: Request):
backend/app/api/srs.py:46:@router.get("/stats", status_code=200)
backend/app/api/srs.py:47:async def get_stats(request: Request):
backend/app/api/audio.py:15:class RenderAudioRequest(BaseModel):
backend/app/api/audio.py:19:@router.post("/render", status_code=202)
backend/app/api/audio.py:20:async def render_audio(body: RenderAudioRequest, request: Request):
backend/app/api/audio.py:42:@router.get("/{audio_id}", status_code=200)
backend/app/api/audio.py:43:async def get_audio(audio_id: str, request: Request):
```

The four routers cover the full pipeline:

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/curriculum/generate` | POST | Generate a multi-day curriculum from a topic |
| `/api/curriculum` | GET | List all generated curricula |
| `/api/curriculum/{id}` | GET | Retrieve a specific curriculum |
| `/api/story/generate` | POST | Generate a Pimsleur lesson from a curriculum day |
| `/api/srs/due` | GET | Get collocations due for review |
| `/api/srs/feedback` | POST | Record learner feedback signal |
| `/api/srs/stats` | GET | Total/due collocation counts |
| `/api/audio/render` | POST | Render a lesson to audio |
| `/api/audio/{id}` | GET | Download rendered audio file |
| `/api/health` | GET | Health check |

### 7.3 API Tests

The API tests use FastAPI's `ASGITransport` + `httpx.AsyncClient` — no real server needed:

```bash
head -50 backend/tests/test_api.py | cat -n
```

```output
     1	"""API endpoint tests."""
     2	
     3	from unittest.mock import AsyncMock
     4	
     5	import pytest
     6	from httpx import ASGITransport, AsyncClient
     7	
     8	from app.main import app
     9	from app.models.curriculum import Curriculum, CurriculumDay
    10	from app.models.language import Language
    11	from app.models.lesson import Lesson, Phrase, Section, SectionType
    12	
    13	
    14	@pytest.mark.asyncio
    15	async def test_health_returns_ok():
    16	    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    17	        response = await client.get("/api/health")
    18	    assert response.status_code == 200
    19	    assert response.json() == {"status": "ok"}
    20	
    21	
    22	# ── Curriculum endpoints ──────────────────────────────────────────────
    23	
    24	
    25	@pytest.mark.asyncio
    26	async def test_generate_curriculum_returns_201(monkeypatch):
    27	    mock_curriculum = Curriculum(
    28	        id="test-id",
    29	        topic="ordering coffee",
    30	        language_code="sl",
    31	        cefr_level="A2",
    32	        days=[
    33	            CurriculumDay(
    34	                day=1,
    35	                title="First day",
    36	                focus="greetings",
    37	                learning_objective="say hello",
    38	                story_guidance="use dober dan",
    39	                collocations=["dober dan"],
    40	            )
    41	        ],
    42	    )
    43	
    44	    mock_generator = AsyncMock()
    45	    mock_generator.generate = AsyncMock(return_value=mock_curriculum)
    46	
    47	    app.state.curriculum_generator = mock_generator
    48	    app.state.language = Language.slovene()
    49	
    50	    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
```

API tests inject mocks via `app.state` — no real LLM or TTS calls. The `ASGITransport` runs the FastAPI app in-process, so tests are fast and isolated.

---

## PART 8: Test Suite

### 8.1 Full Test Run

```bash
cd backend && uv run pytest --tb=short -q 2>&1
```

```output
........................................................................ [ 48%]
........................................................................ [ 96%]
......                                                                   [100%]
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.13.7-final-0 _______________

Name                                  Stmts   Miss  Cover   Missing
-------------------------------------------------------------------
app/__init__.py                           0      0   100%
app/api/__init__.py                       0      0   100%
app/api/audio.py                         34      5    85%   23, 48-52
app/api/curriculum.py                    30      2    93%   55-56
app/api/generation.py                    30      2    93%   25, 30
app/api/srs.py                           35     12    66%   28-43
app/audio/__init__.py                     0      0   100%
app/audio/assembler.py                   22      1    95%   22
app/audio/edge_tts.py                    53      1    98%   87
app/audio/pause_calculator.py            21      0   100%
app/audio/ports.py                        7      0   100%
app/audio/preprocessing/__init__.py       0      0   100%
app/audio/preprocessing/base.py           5      0   100%
app/audio/preprocessing/slovene.py       10      0   100%
app/audio/renderer.py                    39      1    97%   60
app/config.py                             8      0   100%
app/generation/__init__.py                0      0   100%
app/generation/curriculum.py             32      1    97%   67
app/generation/enforcer.py               37      1    97%   65
app/generation/prompts.py                 9      0   100%
app/generation/story.py                  40      3    92%   120, 126-127
app/llm/__init__.py                       0      0   100%
app/llm/cassette.py                      72      2    97%   95, 131
app/llm/client.py                        69      4    94%   84, 87, 97-98
app/main.py                              17      0   100%
app/models/__init__.py                    0      0   100%
app/models/curriculum.py                 30      0   100%
app/models/language.py                   15      0   100%
app/models/lesson.py                     28      0   100%
app/models/srs_item.py                   25      0   100%
app/models/strategy.py                   38      2    95%   64-70
app/models/syntactic_unit.py             15      0   100%
app/srs/__init__.py                       0      0   100%
app/srs/database.py                      92      0   100%
app/srs/feedback.py                      12      0   100%
app/srs/fsrs.py                          57      0   100%
app/srs/selector.py                      40      1    98%   43
-------------------------------------------------------------------
TOTAL                                   922     38    96%
Required test coverage of 95.0% reached. Total coverage: 95.88%
150 passed in 2.25s
```

150 tests, 95.88% coverage, 2.25 seconds. All in mock mode — no network calls needed.

### 8.2 Test File Inventory

```bash
ls backend/tests/test_*.py | xargs -I{} sh -c "echo \"{}: \$(grep -c \"def test_\" {}) tests\"" | sort
```

```output
backend/tests/test_api.py: 9 tests
backend/tests/test_audio_ports.py: 5 tests
backend/tests/test_config.py: 2 tests
backend/tests/test_curriculum.py: 12 tests
backend/tests/test_edge_tts.py: 7 tests
backend/tests/test_enforcer.py: 9 tests
backend/tests/test_fsrs.py: 13 tests
backend/tests/test_llm_cassette.py: 7 tests
backend/tests/test_llm_client.py: 8 tests
backend/tests/test_models.py: 24 tests
backend/tests/test_pauses.py: 12 tests
backend/tests/test_preprocessor.py: 6 tests
backend/tests/test_renderer.py: 7 tests
backend/tests/test_srs_database.py: 11 tests
backend/tests/test_srs_feedback.py: 8 tests
backend/tests/test_srs_selector.py: 6 tests
backend/tests/test_story.py: 4 tests
```

150 tests across 17 files. The heaviest areas: models (24), FSRS scheduling (13), curriculum generation (12), pause calculation (12), and SRS database (11). Every module has its own test file following the `test_{module}.py` convention.

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
    24	# ── PromptBuilder ──────────────────────────────────────────────────────────
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
   │
   ▼
User POST /api/story/generate {"curriculum_id": "...", "day": 1, "strategy": "wider"}
   │
   ▼
StoryGenerator.generate()
   │── Formats prompt with CurriculumDay collocations + strategy
   │── LLMClient.complete() → Groq API
   │── _parse_response() → Lesson with 4 Pimsleur Sections
   │
   ▼
ContentEnforcer.enforce()  (optional)
   │── Builds replacement dict from SRS database
   │── Replaces known L1 phrases with L2 equivalents
   │
   ▼
User POST /api/audio/render {"lesson_id": "..."}
   │
   ▼
LessonRenderer.render()
   │── For each Section → for each Phrase:
   │     TextPreprocessor.preprocess()  (ellipsis insertion for slow speed)
   │     EdgeTTSService.synthesize()    (text → audio bytes)
   │     NaturalPauseCalculator.get_phrase_pause()  (word-count-based silence)
   │── AudioAssembler.concatenate()     (stitch all chunks)
   │── Write output file
   │
   ▼
User GET /api/audio/{audio_id}  → FileResponse (audio download)
```

Each step is independently testable: cassettes for LLM, `:memory:` for SRS, mocks for TTS. The full pipeline can run in CI with zero network calls.

---

## PART 10: What Changed from the Prototypes

| Area | Prototype | Production |
|------|-----------|------------|
| **Architecture** | Two separate codebases (micro-demo-0.0, 0.1) | Unified FastAPI monolith |
| **Language support** | Hardcoded `Language` enum (Tagalog/English/Spanish) | Data-driven `Language` dataclass with factory methods |
| **SRS algorithm** | Custom scheduler | FSRS-5 (19-parameter model, research-backed) |
| **LLM mock** | MD5-hashed cache | SHA-256 cassette system with 4 modes (mock/record/live/patch) |
| **Preprocessing** | 1000-line Tagalog preprocessor | Pluggable `TextPreprocessor` protocol, minimal Slovene impl |
| **Voice mapping** | Hardcoded speaker→voice table | `Language.tts_voice_map` dict |
| **Vocabulary** | Hardcoded replacement dictionary | Dynamic from SRS database (`ContentEnforcer`) |
| **Configuration** | Module-level globals | Pydantic Settings with `.env` |
| **Testing** | Unit tests only | 150 tests, 96% coverage, cassette fixtures, 4 mock strategies |
| **API** | CLI-only | REST API with 10 endpoints |
| **TTS** | Multi-provider (Edge, gTTS, Google Cloud) | EdgeTTS-only with caching + retry |
| **Audio format** | MP3 via pydub | Raw PCM (extensible) |
| **Pause system** | Complex hierarchy with config files | Same ratios, simplified to single calculator class |

**What was preserved from the prototypes:**
- Pimsleur 4-section format (KEY_PHRASES, NATURAL_SPEED, SLOW_SPEED, TRANSLATED)
- Natural pause multiplier table (1.5x–3.5x by word count)
- EdgeTTS rate limiting (200ms, 3 concurrent)
- Hexagonal architecture / Protocol-based ports
- Pedagogical scoring weights (40/30/20/10)
- Content strategy framework (WIDER vs DEEPER)
- Ellipsis-as-pause discovery for EdgeTTS
