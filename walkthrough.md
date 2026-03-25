# TunaTale Codebase Walkthrough: From Prototype to Production

*2026-02-27T18:25:10Z by Showboat 0.6.1*
<!-- showboat-id: ee2c74d0-0f0c-442a-856e-1524a47e4ed3 -->

## Purpose of This Document

This walkthrough serves two audiences: (1) a human reader wanting to understand the TunaTale codebase, and (2) an AI agent (Claude) tasked with rebuilding TunaTale as a production application using TDD.

**For the AI agent rebuilding this:** This document is your primary reference. It explains what exists, what works, what's prototype-quality, and how to map the existing code to the PRD's vision. Every section ends with production notes telling you what to keep, what to redesign, and what to build from scratch.

## Project Overview

TunaTale is an AI-powered language learning system that generates personalized audio curricula. The user provides scripts or topics, and the system creates adaptive, audio-first immersion content using comprehensible input principles. The prototype exists as two separate codebases:

- **micro-demo-0.0** — The audio pipeline: parses lesson files, runs TTS, assembles audio with natural pauses
- **micro-demo-0.1** — The content engine: generates curricula, stories, tracks vocabulary with SRS, enforces constraints

These two halves have never been integrated. The production app must unify them under one architecture.

---

## PART 1: The Audio Pipeline (micro-demo-0.0)

This is the more architecturally mature half. It follows hexagonal architecture with clear separation between domain logic and infrastructure.

### 1.1 Architecture Overview

The audio pipeline uses ports-and-adapters (hexagonal) architecture:

```bash
cat -n micro-demo-0.0/tunatale/core/ports/tts_service.py | head -55
```

```output
     1	"""Interface for Text-to-Speech services."""
     2	from abc import ABC, abstractmethod
     3	from typing import Optional, Dict, Any, List, Protocol, runtime_checkable
     4	from pathlib import Path
     5	
     6	from ..models.voice import Voice
     7	
     8	
     9	class TTSException(Exception):
    10	    """Base exception for TTS service errors."""
    11	    pass
    12	
    13	
    14	class TTSValidationError(TTSException):
    15	    """Raised when input validation fails."""
    16	    pass
    17	
    18	
    19	class TTSRateLimitError(TTSException):
    20	    """Raised when rate limits are exceeded."""
    21	    def __init__(self, message, **kwargs):
    22	        super().__init__(message)
    23	        self.retry_after = kwargs.get('retry_after')
    24	        self.status_code = kwargs.get('status_code')
    25	        self.headers = kwargs.get('headers', {})
    26	
    27	
    28	class TTSAuthenticationError(TTSException):
    29	    """Raised when authentication fails."""
    30	    pass
    31	
    32	
    33	class TTSTransientError(TTSException):
    34	    """Raised for transient errors that might succeed on retry."""
    35	    pass
    36	
    37	
    38	@runtime_checkable
    39	class TTSService(Protocol):
    40	    """Protocol defining the interface for TTS services.
    41	    
    42	    This protocol defines the required methods that any TTS service implementation
    43	    must provide to be compatible with the TunaTale application.
    44	    """
    45	    
    46	    @property
    47	    @abstractmethod
    48	    def name(self) -> str:
    49	        """Get the name of the TTS service.
    50	        
    51	        Returns:
    52	            str: The name of the service (e.g., 'edge_tts', 'google_tts')
    53	        """
    54	        ...
    55	    
```

The `TTSService` Protocol is the key abstraction. It defines `get_voices()`, `synthesize_speech()`, `get_voice()`, and `validate_credentials()`. Any TTS provider must implement this protocol. The prototype ships with three implementations:

- **EdgeTTS** (`edge_tts_service.py`) — Microsoft's free Edge TTS, the primary provider. Has rate limiting (200ms delay, 3 concurrent), caching with SHA-256 hashing, and async support.
- **gTTS** (`gtts_service.py`) — Google Translate TTS as a free fallback.
- **Google Cloud TTS** (`google_tts_service.py`) — Premium option (not heavily used in prototype).
- **MultiProviderTTS** (`multi_provider_tts_service.py`) — Routes between providers.

**Production note:** This port/adapter pattern is excellent and should be preserved. The `TTSService` Protocol should become the foundation for production TTS. Edge TTS is adequate for prototype but the PRD notes it's 4x cheaper than neural TTS — start here, upgrade later.

### 1.2 Domain Models

The core domain models live in `tunatale/core/models/` and represent the lesson structure:

```bash
cat -n micro-demo-0.0/tunatale/core/models/enums.py
```

```output
     1	"""Enums for the TunaTale domain model."""
     2	from enum import Enum, auto
     3	from typing import Dict, Optional, Type, TypeVar
     4	
     5	T = TypeVar('T', bound='AutoName')
     6	
     7	class AutoName(Enum):
     8	    """Enum that automatically generates values as lowercase names."""
     9	    def _generate_next_value_(name: str, start: int, count: int, last_values: list) -> str:
    10	        return name.lower()
    11	    
    12	    @classmethod
    13	    def from_string(cls: Type[T], value: str) -> Optional[T]:
    14	        """Get enum member from string value (case-insensitive)."""
    15	        try:
    16	            return cls[value.upper()]
    17	        except KeyError:
    18	            # Try to find by value if not found by name
    19	            value_lower = value.lower()
    20	            for member in cls:
    21	                if member.value.lower() == value_lower:
    22	                    return member
    23	            return None
    24	
    25	class Environment(AutoName):
    26	    """Application environment types."""
    27	    DEVELOPMENT = auto()
    28	    TESTING = auto()
    29	    STAGING = auto()
    30	    PRODUCTION = auto()
    31	
    32	
    33	class SectionType(AutoName):
    34	    """Types of sections in a language lesson."""
    35	    KEY_PHRASES = auto()
    36	    NATURAL_SPEED = auto()
    37	    SLOW_SPEED = auto()
    38	    TRANSLATED = auto()
    39	
    40	class VoiceGender(AutoName):
    41	    """Gender of a voice."""
    42	    MALE = auto()
    43	    FEMALE = auto()
    44	    NEUTRAL = auto()
    45	
    46	class VoiceAge(AutoName):
    47	    """Age group of a voice."""
    48	    CHILD = auto()
    49	    YOUNG_ADULT = auto()
    50	    ADULT = auto()
    51	    SENIOR = auto()
    52	
    53	class Language(AutoName):
    54	    """Supported languages."""
    55	    TAGALOG = auto()
    56	    ENGLISH = auto()
    57	    SPANISH = auto()
    58	    
    59	    @property
    60	    def code(self) -> str:
    61	        """Get the language code (using 'fil' for Tagalog instead of 'tl')."""
    62	        return {
    63	            'tagalog': 'fil',
    64	            'english': 'en',
    65	            'spanish': 'es',
    66	        }.get(self.value, 'en')
    67	        
    68	    @classmethod
    69	    def from_string(cls: Type[T], value: str) -> Optional[T]:
    70	        """Get enum member from string value (case-insensitive).
    71	        
    72	        Also accepts language codes 'en', 'fil', and 'es'.
    73	        """
    74	        # First try the standard lookup
    75	        result = super().from_string(value)
    76	        if result is not None:
    77	            return result
    78	            
    79	        # Then try matching by language code
    80	        code_map = {
    81	            'en': 'english',
    82	            'fil': 'tagalog',
    83	            'es': 'spanish'
    84	        }
    85	        
    86	        normalized_value = value.lower()
    87	        if normalized_value in code_map:
    88	            return super().from_string(code_map[normalized_value])
    89	            
    90	        return None
```

The model hierarchy is: `Lesson` contains `Section`s (KEY_PHRASES, NATURAL_SPEED, SLOW_SPEED, TRANSLATED), which contain `Phrase`s. Each Phrase has voice assignment, rate/pitch/volume settings, and language tracking. There's also a `Voice` model with provider-specific IDs and a `BaseEntity` with UUID, timestamps, and serialization.

**The four section types are the Pimsleur method in action:**
1. **Key Phrases** — Individual vocabulary items with syllable breakdowns
2. **Natural Speed** — Full dialogue at native speed
3. **Slow Speed** — Same dialogue with ellipsis-based pauses between words
4. **Translated** — Filipino dialogue followed by English narrator translation

**Production note:** These section types map directly to the PRD's "Input/Listening Mode." The PRD also defines an "Output/Practice Mode" (structured prompts for production practice) which has no prototype equivalent — this is net-new work. The Language enum is currently hardcoded to Tagalog/English/Spanish — production needs dynamic language support (PRD targets Norwegian as first demo).

### 1.3 Lesson Parser

The lesson parser converts text files into the domain model. Here's the input format:

```bash
head -30 micro-demo-0.0/tagalog/syllable-mini.txt
```

```output
[NARRATOR]: Syllable Pronunciation Test

Key Phrases:

[TAGALOG-FEMALE-1]: u 

[TAGALOG-FEMALE-1]: in 

[TAGALOG-FEMALE-1]: kit 

[TAGALOG-FEMALE-1]: ra 

[TAGALOG-FEMALE-1]: de 

[NARRATOR]: Natural Speed

[TAGALOG-FEMALE-1]: Kumusta po? Ang ganda ng lugar na ito!

[NARRATOR]: Slow Speed

[TAGALOG-FEMALE-1]: Kumusta... po?... Ang... ganda... ng... lugar... na... ito!

[NARRATOR]: Translated

[TAGALOG-FEMALE-1]: Kumusta po? Ang ganda ng lugar na ito!
[NARRATOR]: How are you? This place is beautiful!```
```

The parser uses regex patterns to classify lines:

- `[SPEAKER]: content` — Dialogue lines, speaker determines voice and language
- `Key Phrases:`, `Natural Speed:`, etc. — Section headers
- Speaker names like `TAGALOG-FEMALE-1` map to specific TTS voices (e.g., `fil-PH-BlessicaNeural`)
- Ellipses (`...`) in Slow Speed sections create natural pauses in the audio

The parser maps speaker patterns to voices:

```bash
grep -n 'register_voice\|provider_id\|TAGALOG\|ENGLISH.*Neural' micro-demo-0.0/tunatale/core/parsers/lesson_parser.py | head -20
```

```output
65:    # Speaker pattern for voice mapping (e.g., TAGALOG-FEMALE-1)
79:        self.register_voice(
83:                provider_id="fil-PH-BlessicaNeural",
84:                language=Language.TAGALOG,
89:        self.register_voice(
93:                provider_id="fil-PH-AngeloNeural",
94:                language=Language.TAGALOG,
100:        self.register_voice(
104:                provider_id="en-US-JennyNeural",
110:        self.register_voice(
114:                provider_id="en-US-GuyNeural",
121:    def register_voice(self, voice: Voice) -> None:
143:            target_language=Language.TAGALOG,  # Default to Tagalog
260:            # Check for dialogue lines (e.g., [TAGALOG-FEMALE-1]: Some text)
309:                if speaker and ('TAGALOG' in speaker.upper() or 'FILIPINO' in speaker.upper()):
667:        DEFAULT_ENGLISH_VOICE = "en-US-GuyNeural"  # Changed to male voice
668:        DEFAULT_TAGALOG_VOICE = "fil-PH-BlessicaNeural"
677:                voice_id = DEFAULT_TAGALOG_VOICE
684:                voice_id = self.voices[speaker].provider_id
685:            # Try to determine voice from speaker name pattern (e.g., TAGALOG-FEMALE-1)
```

**Production note:** The parser is the bridge between the content engine's output (stories in text format) and the audio pipeline's input. In production, the content engine (Part 2) generates structured text that this parser consumes. The hardcoded voice mappings should become configurable per-language. The lesson file format itself is a good intermediate representation — keep it or evolve it into a structured JSON/YAML format.

### 1.4 Text Preprocessing Pipeline

Before text reaches the TTS engine, it passes through a sophisticated preprocessing pipeline in `tts_preprocessor.py`. This handles three Tagalog-specific concerns:

```bash
grep -n 'def process_number_clarification\|def fix_abbreviation_pronunciation\|def fix_syllable_pronunciation\|def convert_single_ellipses\|def preprocess_text_for_tts' micro-demo-0.0/tunatale/core/utils/tts_preprocessor.py
```

```output
230:def fix_abbreviation_pronunciation(text: str) -> str:
444:def process_number_clarification(text: str, section_type: Optional[str] = None) -> str:
573:def preprocess_text_for_tts(text: str, language_code: str, section_type: Optional[str] = None) -> str:
987:def convert_single_ellipses_for_edgetts(text: str, provider_name: str) -> str:
```

The preprocessing pipeline (`preprocess_text_for_tts()`) orchestrates:

1. **Filipino number clarification** — Converts numbers to authentic Filipino forms. Times use the Spanish system ("alas otso y medya" for 8:30), digits get Tagalog breakdown ("isa lima zero" for 150). Auto-applies in slow_speed sections, manual via `<clarify>` tags elsewhere.

2. **Universal abbreviation handler** — Converts abbreviations to phonetic pronunciation for Tagalog speakers only (CR → "see are", AM → "eh em"). Smart filtering protects real Tagalog words (PO, ANG, NG, SA) and common English words from being treated as abbreviations. Uses vowel ratio heuristics to distinguish.

3. **Ellipsis handling** — EdgeTTS naturally handles ellipses as pauses (critical discovery documented in CLAUDE.md). Single ellipses pass through unchanged to EdgeTTS. For non-EdgeTTS providers, ellipses convert to SSML break tags.

**Critical bug fix preserved in CLAUDE.md:** An earlier version converted ellipses to semicolons for EdgeTTS, causing slow-speed sections to play 30% faster (3.82s vs 5.42s). The fix was to simply leave ellipses alone for EdgeTTS.

**Production note:** The preprocessing pipeline is language-specific (Tagalog). For Norwegian (PRD's first target), you'll need equivalent preprocessing for Norwegian phonetics and abbreviation handling. Design the preprocessor as a pluggable per-language module.

### 1.5 Natural Pause System

The pause system creates pedagogically appropriate silence between phrases:

```bash
cat -n micro-demo-0.0/tunatale/core/services/natural_pause_calculator.py | head -60
```

```output
     1	"""Natural pause calculator for linguistic boundaries."""
     2	from typing import Dict
     3	import logging
     4	
     5	logger = logging.getLogger(__name__)
     6	
     7	
     8	class NaturalPauseCalculator:
     9	    """Calculate pauses based on linguistic boundaries."""
    10	    
    11	    def __init__(self):
    12	        """Initialize the pause calculator with hierarchical pause levels."""
    13	        self.pause_levels = {
    14	            'syllable': 300,      # Between syllables: ma-gan-da
    15	            'word': 600,          # Between words: maganda hapon  
    16	            'phrase': 1200,       # Between phrases: maganda hapon | po
    17	            'sentence': 2000,     # Between sentences: Kumusta? | Mabuti.
    18	            'section': 3000       # Between sections
    19	        }
    20	    
    21	    def get_pause_for_boundary(self, boundary_type: str, text_complexity: str = 'normal', 
    22	                             audio_duration_seconds: float = None, phrase_text: str = None) -> int:
    23	        """Get pause duration based on boundary type and complexity.
    24	        
    25	        Args:
    26	            boundary_type: Type of linguistic boundary ('syllable', 'word', 'phrase', 'sentence', 'section')
    27	            text_complexity: Complexity level ('normal' or 'slow')
    28	            audio_duration_seconds: Duration of the audio segment in seconds (for dynamic pauses)
    29	            phrase_text: The actual phrase text to analyze for word count-based multipliers
    30	            
    31	        Returns:
    32	            Pause duration in milliseconds
    33	        """
    34	        # If audio duration is provided, use dynamic calculation with adaptive multipliers
    35	        if audio_duration_seconds is not None:
    36	            # Determine dynamic multiplier based on phrase complexity
    37	            multiplier = self._get_dynamic_multiplier(phrase_text, audio_duration_seconds)
    38	            
    39	            # Calculate desired total pause duration
    40	            desired_pause_ms = int(audio_duration_seconds * multiplier)
    41	            
    42	            # Account for the base silence_between_phrases (0.5s = 500ms) that gets added later
    43	            # Subtract it so our total pause timing is exactly what we want
    44	            base_silence_ms = 500  # Default silence_between_phrases from config
    45	            dynamic_pause = max(0, desired_pause_ms - base_silence_ms)
    46	            
    47	            
    48	            # Adjust for slow speech
    49	            if text_complexity == 'slow':
    50	                dynamic_pause = int(dynamic_pause * 1.2)  # 20% longer for slow sections
    51	                
    52	            return dynamic_pause
    53	        
    54	        # Fallback to original fixed pause system
    55	        base_pause = self.pause_levels.get(boundary_type, 600)  # Default to word-level pause
    56	        
    57	        # Adjust for slow speech
    58	        if text_complexity == 'slow':
    59	            return int(base_pause * 1.5)  # 50% longer for slow sections
    60	        
```

The pause system uses dynamic multipliers based on phrase word count — longer phrases get proportionally longer pauses to give the learner time to process. This scales from 1.5x for single words up to 3.5x for 6+ word phrases, with extra 200ms per second of audio over 3 seconds.

**Production note:** This is battle-tested pedagogical logic. The multiplier values were tuned through listening tests. Preserve these ratios. For production, make them configurable per-language (different languages may need different pause ratios).

### 1.6 Audio Processing & Assembly

The audio processor (`audio_processor.py`) uses pydub to concatenate phrases into complete lessons:

```bash
grep -n 'class AudioProcessorService\|def concatenate_audio\|def normalize_audio\|def add_silence\|def trim_silence\|def process_audio' micro-demo-0.0/tunatale/infrastructure/services/audio/audio_processor.py
```

```output
33:class AudioProcessorService(AudioProcessor):
73:    async def process_audio(
139:    async def concatenate_audio(
233:    async def add_silence(
275:    async def normalize_audio(
315:    async def trim_silence(
```

The full data flow through the audio pipeline:

```
Lesson File (.txt)
    |
    v
[LessonParser] --> Parse lines, assign voices, classify sections
    |
    v
Lesson model (Sections -> Phrases)
    |
    v
[LessonProcessor] --> For each phrase:
    |-- preprocess_text_for_tts() (abbreviations, numbers, ellipses)
    |-- TTSService.synthesize_speech() (generate audio)
    |-- NaturalPauseCalculator (calculate silence durations)
    |-- AudioProcessor.add_silence() (insert pauses)
    |
    v
[AudioProcessor] --> Concatenate all sections
    |-- Add section silence gaps
    |-- Normalize loudness (RMS-based)
    |-- Export to MP3
    |
    v
Output: audio file + metadata JSON
```

**Production note:** This pipeline is solid but synchronous-feeling despite async wrappers. For production with mobile delivery (PRD requirement), the pipeline needs: (1) streaming/chunked generation for responsive playback, (2) offline caching of pre-generated sessions (PRD: "30-minute sessions"), (3) format flexibility (the prototype only outputs MP3).

### 1.7 The Factory Pattern

Service creation is handled by `factories.py`:

```bash
cat -n micro-demo-0.0/tunatale/infrastructure/factories.py | head -50
```

```output
     1	"""Factory functions for creating service instances."""
     2	import logging
     3	from typing import Any, Dict, Optional
     4	
     5	from tunatale.core.ports.audio_processor import AudioProcessor
     6	from tunatale.core.ports.tts_service import TTSService
     7	from tunatale.infrastructure.services.audio.audio_processor import AudioProcessorService
     8	from tunatale.infrastructure.services.tts.edge_tts_service import EdgeTTSService
     9	
    10	# Configure logging
    11	logger = logging.getLogger(__name__)
    12	
    13	
    14	def create_tts_service(config: Any) -> TTSService:
    15	    """Create a TTS service based on configuration.
    16	    
    17	    Args:
    18	        config: TTS configuration (dict or Pydantic model)
    19	        
    20	    Returns:
    21	        An instance of a TTS service
    22	        
    23	    Raises:
    24	        ValueError: If the TTS provider is not supported
    25	    """
    26	    # Convert Pydantic model to dict if needed
    27	    if hasattr(config, 'model_dump'):  # Pydantic v2
    28	        config_dict = config.model_dump()
    29	    elif hasattr(config, 'dict'):  # Pydantic v1
    30	        config_dict = config.dict()
    31	    elif isinstance(config, dict):
    32	        config_dict = config
    33	    else:
    34	        raise ValueError(f"Unsupported config type: {type(config)}")
    35	    
    36	    provider = config_dict.get('provider', 'edge').lower()
    37	    
    38	    if provider == 'multi':
    39	        logger.info("Using Multi-Provider TTS service")
    40	        return create_multi_provider_tts_service(config_dict)
    41	    
    42	    elif provider == 'edge':
    43	        logger.info("Using Edge TTS service with caching disabled")
    44	        # Create edge_tts config with cache disabled by default
    45	        edge_config = config_dict.get('edge_tts', {})
    46	        if 'cache_dir' not in edge_config:
    47	            edge_config['cache_dir'] = None
    48	        return EdgeTTSService(edge_config)
    49	    
    50	    elif provider == 'google':
```

**Production note:** The factory pattern is good but should evolve into proper dependency injection (e.g., a DI container or simple constructor injection). The factory currently has hardcoded knowledge of all providers — production should use a registry pattern where providers register themselves.

---

## PART 2: The Content Engine (micro-demo-0.1)

This is where the AI-powered curriculum generation, story creation, SRS tracking, and constraint enforcement live. It's less architecturally clean than 0.0 — more of a rapidly-iterated prototype with ~50 Python files at the top level.

### 2.1 Configuration

The content engine uses simple path-based config:

```bash
cat -n micro-demo-0.1/config.py
```

```output
     1	import os
     2	from pathlib import Path
     3	
     4	# Base directories
     5	BASE_DIR = Path(__file__).parent
     6	INSTANCE_DIR = BASE_DIR / 'instance'
     7	DATA_DIR = INSTANCE_DIR / 'data'
     8	
     9	# Application data directories
    10	CURRICULA_DIR = DATA_DIR / 'curricula'  # Directory for storing curriculum files
    11	STORIES_DIR = DATA_DIR / 'stories'  # Directory for storing generated stories
    12	SRS_DIR = DATA_DIR / 'srs'  # Directory for Spaced Repetition System data
    13	MOCK_RESPONSES_DIR = DATA_DIR / 'mock_responses'  # Directory for mock LLM responses
    14	UPLOAD_DIR = DATA_DIR / 'uploads'  # Directory for user uploads (e.g., transcripts)
    15	PROMPTS_DIR = BASE_DIR / 'prompts'  # Keep prompts in project root as they are part of the code
    16	
    17	# Logging configuration
    18	LOGS_DIR = DATA_DIR / 'logs'
    19	DEBUG_LOG_PATH = LOGS_DIR / 'debug.log'
    20	
    21	# Create directories if they don't exist
    22	INSTANCE_DIR.mkdir(exist_ok=True)
    23	DATA_DIR.mkdir(exist_ok=True)
    24	CURRICULA_DIR.mkdir(exist_ok=True)
    25	STORIES_DIR.mkdir(exist_ok=True)
    26	SRS_DIR.mkdir(exist_ok=True)
    27	MOCK_RESPONSES_DIR.mkdir(exist_ok=True)
    28	UPLOAD_DIR.mkdir(exist_ok=True)
    29	PROMPTS_DIR.mkdir(exist_ok=True)
    30	LOGS_DIR.mkdir(exist_ok=True)
    31	
    32	# Default configuration
    33	DEFAULT_STORY_LENGTH = int(os.getenv('DEFAULT_STORY_LENGTH', '500'))  # Default to 500 words if not set
    34	
    35	# File paths - standardized structure
    36	CURRICULUM_PATH = CURRICULA_DIR / 'curriculum.json'
    37	COLLOCATIONS_PATH = DATA_DIR / 'collocations.json'
```

**Production note:** This is module-level config with side effects (creates directories on import). Production needs proper configuration management — environment-based settings, no side effects on import, and unified config between the two halves of the app. The directory structure itself (curricula, stories, srs, uploads) maps well to the PRD's data model.

### 2.2 Curriculum Generation

The curriculum system has two parts: the data model and the generation service.

```bash
cat -n micro-demo-0.1/curriculum_models.py | sed -n '1,75p'
```

```output
     1	"""
     2	Data models for managing language learning curriculum structure.
     3	
     4	This module provides dataclasses for representing curriculum structure
     5	and days, along with methods for serialization and deserialization.
     6	"""
     7	
     8	from dataclasses import dataclass, asdict, field
     9	from pathlib import Path
    10	from typing import List, Dict, Any, Optional, TypeVar, Type
    11	import json
    12	
    13	
    14	T = TypeVar('T', bound='Curriculum')
    15	
    16	
    17	@dataclass
    18	class CurriculumDay:
    19	    """Represents a single day in the language learning curriculum.
    20	    
    21	    Attributes:
    22	        day: The day number in the curriculum (1-based).
    23	        title: The title of the day's lesson.
    24	        focus: The main focus area for this day's content.
    25	        collocations: List of target collocations for this day.
    26	        presentation_phrases: List of key phrases for presentation.
    27	        learning_objective: Specific objective for this day's story.
    28	        story_guidance: Optional guidance for story generation.
    29	    """
    30	    day: int
    31	    title: str
    32	    focus: str
    33	    collocations: List[str]
    34	    presentation_phrases: List[str]
    35	    learning_objective: str
    36	    story_guidance: str = ""
    37	    
    38	    def __post_init__(self):
    39	        """Validate the day number is positive."""
    40	        if self.day < 1:
    41	            raise ValueError("Day number must be positive")
    42	
    43	
    44	@dataclass
    45	class Curriculum:
    46	    """Represents a complete language learning curriculum.
    47	    
    48	    Attributes:
    49	        learning_objective: The overall learning objective of the curriculum.
    50	        target_language: The target language for learning.
    51	        learner_level: The proficiency level of the target learners.
    52	        presentation_length: The expected length of presentations in minutes.
    53	        days: List of CurriculumDay objects representing each day's plan.
    54	        metadata: Additional metadata about the curriculum.
    55	    """
    56	    learning_objective: str
    57	    target_language: str
    58	    learner_level: str
    59	    presentation_length: int
    60	    days: List[CurriculumDay] = field(default_factory=list)
    61	    metadata: Dict[str, Any] = field(default_factory=dict)
    62	    
    63	    def get_day(self, day_num: int) -> Optional[CurriculumDay]:
    64	        """Get the curriculum day with the specified day number.
    65	        
    66	        Args:
    67	            day_num: The day number to retrieve (1-based).
    68	            
    69	        Returns:
    70	            The CurriculumDay for the specified day, or None if not found.
    71	        """
    72	        for day in self.days:
    73	            if day.day == day_num:
    74	                return day
    75	        return None
```

A `Curriculum` has a learning objective, target language, CEFR level, and a list of `CurriculumDay`s. Each day has collocations (3-5 word target phrases), presentation phrases, and story guidance. The model supports JSON serialization/deserialization.

**Warning:** The `Curriculum.save()` and `Curriculum.load()` methods contain extensive corruption-detection logging (checking for 'space exploration' strings and 'content' fields). This was debugging code that was never removed — it indicates the curriculum data had corruption issues during development. Remove in production.

The curriculum is generated by the `CurriculumGenerator` in `curriculum_service.py`, which sends a prompt to the LLM (mocked in prototype) and parses the response into the Curriculum model.

**Production note:** The curriculum model is a good starting point but needs to support the PRD's concept of user-provided scripts as input. Add: (1) source script reference, (2) syntactic unit analysis from corpus, (3) CEFR progression mapping, (4) dynamic content stream generation (not fixed day count).

### 2.3 Content Strategy: Go Wider vs Go Deeper

The strategy framework controls how content evolves:

```bash
cat -n micro-demo-0.1/content_strategy.py | sed -n '1,80p'
```

```output
     1	"""
     2	Content Strategy Framework for TunaTale
     3	
     4	This module defines the content generation strategies and configuration
     5	for implementing "Go Wider vs Go Deeper" learning approaches.
     6	"""
     7	
     8	from enum import Enum
     9	from dataclasses import dataclass, field
    10	from typing import Dict, Any, Optional, List
    11	import logging
    12	
    13	logger = logging.getLogger(__name__)
    14	
    15	
    16	@dataclass
    17	class PedagogicalScoringConfig:
    18	    """
    19	    Configuration for pedagogical quality scoring of collocations.
    20	    Adjust these weights to tune collocation selection behavior.
    21	    """
    22	    # Component weights (should sum to 1.0)
    23	    srs_readiness_weight: float = 0.4    # How much to prioritize SRS due status
    24	    language_quality_weight: float = 0.3  # How much to prioritize pure Filipino
    25	    pedagogical_value_weight: float = 0.2 # How much to prioritize usefulness/frequency
    26	    diversity_weight: float = 0.1         # How much to prioritize semantic variety
    27	
    28	    # Language quality scoring parameters
    29	    english_word_penalty: float = -0.5    # Penalty per English word (increased from -0.2)
    30	    digit_penalty: float = -0.3            # Penalty for containing digits
    31	    tagalog_word_bonus: float = 0.1        # Bonus per Tagalog word
    32	    pure_tagalog_bonus: float = 0.3        # Bonus for pure Tagalog (increased from 0.2)
    33	
    34	    # Pedagogical value parameters
    35	    min_frequency_threshold: int = 2       # Minimum corpus appearances
    36	    frequency_bonus_multiplier: float = 0.1 # Bonus per additional appearance
    37	    completeness_bonus: float = 0.2       # Bonus for complete phrases
    38	
    39	    # Diversity parameters
    40	    similarity_penalty: float = -0.15     # Penalty for semantic similarity
    41	    category_diversity_bonus: float = 0.1 # Bonus for different categories
    42	
    43	    # SRS readiness parameters
    44	    low_stability_bonus: float = 0.3      # Bonus for stability < 2.0
    45	    review_overdue_bonus: float = 0.2     # Bonus for overdue items
    46	
    47	    def validate(self) -> bool:
    48	        """Check that weights sum to approximately 1.0."""
    49	        total_weight = (self.srs_readiness_weight + self.language_quality_weight +
    50	                       self.pedagogical_value_weight + self.diversity_weight)
    51	        return abs(total_weight - 1.0) < 0.01
    52	
    53	
    54	# Default scoring configuration - easily tunable
    55	DEFAULT_SCORING_CONFIG = PedagogicalScoringConfig()
    56	
    57	
    58	class ContentStrategy(Enum):
    59	    """
    60	    Content generation strategies for Filipino language learning.
    61	    
    62	    WIDER: Generate new scenarios using familiar vocabulary
    63	    DEEPER: Enhance existing scenarios with advanced Filipino expressions
    64	    """
    65	    WIDER = "wider"
    66	    DEEPER = "deeper"
    67	
    68	
    69	class DifficultyLevel(Enum):
    70	    """
    71	    Language complexity levels for story generation.
    72	    
    73	    BASIC: Current level with English fallbacks
    74	    INTERMEDIATE: More Filipino, fewer English words
    75	    ADVANCED: Native-level expressions and cultural references
    76	    """
    77	    BASIC = "basic"
    78	    INTERMEDIATE = "intermediate"
    79	    ADVANCED = "advanced"
    80	
```

The `PedagogicalScoringConfig` is the brain of collocation selection. It uses four weighted dimensions to score which vocabulary to include in each lesson:

- **SRS readiness** (40%) — Prioritize overdue and low-stability items
- **Language quality** (30%) — Prefer pure Tagalog over English-mixed phrases
- **Pedagogical value** (20%) — Favor high-frequency, complete phrases
- **Diversity** (10%) — Avoid semantic redundancy

The `ContentStrategy` enum (WIDER vs DEEPER) controls the generation approach:
- **WIDER** = 8 new collocations max, expanded contexts, higher review intervals
- **DEEPER** = 3 new collocations max, 70%+ Filipino ratio, advanced verb forms

**Production note:** This scoring system is one of the most valuable pieces of prototype logic. It directly implements the PRD's requirement for balancing "what learners want" with "what they need." The weights should become tunable per-user and per-language. The WIDER/DEEPER strategies map to the PRD's adaptive pacing concept.

### 2.4 Story Generation

The `ContentGenerator` in `story_generator.py` is the heart of the content engine. It coordinates curriculum data, SRS state, and LLM prompts to produce stories:

```bash
cat -n micro-demo-0.1/story_generator.py | sed -n '95,140p'
```

```output
    95	class ContentGenerator:
    96	    def __init__(self):
    97	        self.llm = MockLLM()
    98	        
    99	        # Initialize language detector for English filtering
   100	        self.language_detector = LanguageDetector()
   101	        
   102	        # Initialize collocation extractor for backward compatibility
   103	        from story_collocation_extractor import StoryCollocationExtractor
   104	        self.collocation_extractor = StoryCollocationExtractor()
   105	        # Load prompts for chat-based approach (graceful for testing)
   106	        try:
   107	            self.system_prompt = self._load_prompt('system_prompt.txt')
   108	            self.day_prompt_template = self._load_prompt('day_prompt_template.txt')
   109	        except FileNotFoundError:
   110	            # For testing environments without chat prompts
   111	            self.system_prompt = "Test system prompt"
   112	            self.day_prompt_template = "Test day prompt template"
   113	        
   114	        # Legacy prompts (for backward compatibility)
   115	        self.story_prompt = self._load_prompt('story_prompt.txt')  # Default baseline
   116	        
   117	        # Try to load strategy-specific prompts, but don't fail if not available (for tests)
   118	        try:
   119	            self.story_prompt_deeper = self._load_prompt('story_prompt_deeper.txt')
   120	        except FileNotFoundError:
   121	            self.story_prompt_deeper = None  # For testing environments
   122	            
   123	        try:
   124	            self.story_prompt_wider = self._load_prompt('story_prompt_wider.txt')
   125	        except FileNotFoundError:
   126	            self.story_prompt_wider = None  # For testing environments
   127	        
   128	        # New two-part prompt architecture (with safe initialization)
   129	        try:
   130	            self.prompt_generator = create_prompt_generator()
   131	            self.mock_srs = create_mock_srs()
   132	        except Exception:
   133	            # For testing environments where new prompts may not exist
   134	            self.prompt_generator = None
   135	            self.mock_srs = None
   136	        
   137	        # SRS system using database backend
   138	        self.srs = SRSAdapter()
   139	    
   140	    def _load_prompt(self, filename: str) -> str:
```

The ContentGenerator wires together: MockLLM, LanguageDetector, StoryCollocationExtractor, DayPromptGenerator, MockSRS, SRSAdapter, and multiple prompt templates. It has many fallbacks for testing environments (try/except around most initialization).

The generation flow:
1. Load curriculum day data (collocations, focus, guidance)
2. Query SRS for due-for-review collocations 
3. Build a prompt using the DayPromptGenerator (combines system prompt + day scenario + vocabulary constraints + strategy guidance)
4. Send prompt to LLM (mocked: checks cache, or prompts user for input)
5. Parse the response into story sections
6. Run SRS enforcement (replace English words with known Filipino equivalents)
7. Save story and update SRS tracking

### 2.5 The Mock LLM

Since the prototype doesn't call a real LLM API, `llm_mock.py` implements a cache-and-prompt system:

```bash
cat -n micro-demo-0.1/llm_mock.py | sed -n '9,55p'
```

```output
     9	class MockLLM:
    10	    def __init__(self, cache_dir: Optional[str] = None):
    11	        """
    12	        Initialize the mock LLM with a cache directory for storing and loading responses.
    13	        
    14	        Args:
    15	            cache_dir: Optional custom directory to store mock responses. 
    16	                     If not provided, uses MOCK_RESPONSES_DIR from config.
    17	        """
    18	        self.cache_dir = Path(cache_dir) if cache_dir else config.MOCK_RESPONSES_DIR
    19	        self.cache_dir.mkdir(parents=True, exist_ok=True)
    20	    
    21	    def _get_cache_path(self, prompt: str) -> Path:
    22	        """Generate a cache file path based on the prompt content."""
    23	        # Create a hash of the prompt to use as a filename
    24	        prompt_hash = hashlib.md5(prompt.encode('utf-8')).hexdigest()
    25	        return self.cache_dir / f"{prompt_hash}.json"
    26	    
    27	    def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
    28	        """
    29	        Generate a response using the mock LLM (compatibility method for CurriculumGenerator).
    30	        
    31	        Args:
    32	            prompt: The prompt to generate a response for
    33	            **kwargs: Additional arguments (ignored in mock implementation)
    34	            
    35	        Returns:
    36	            Dictionary containing the mock response in the expected format
    37	        """
    38	        # Delegate to get_response with 'curriculum' as the default response_type
    39	        return self.get_response(prompt, response_type="curriculum")
    40	    
    41	    def get_response(self, prompt: str, response_type: str = "curriculum") -> Dict[str, Any]:
    42	        """
    43	        Get a mock response for the given prompt, either from cache or by prompting the user.
    44	        
    45	        Args:
    46	            prompt: The prompt to generate a response for
    47	            response_type: Type of response (curriculum, story, etc.)
    48	            
    49	        Returns:
    50	            Dictionary containing the mock response
    51	        """
    52	        cache_path = self._get_cache_path(prompt)
    53	        
    54	        # Try to load from cache first
    55	        if cache_path.exists():
```

The MockLLM uses MD5-hashed prompts as cache keys, storing responses as JSON files. When a cached response exists, it's returned directly. When not cached, it prompts the user interactively for input (or falls back to generated responses).

**Production note:** Replace MockLLM with a real LLM client (Claude API). The PRD's Phase 1 approach of "fully mocked content" is exactly what this implements. For Phase 2, swap in the real API while keeping the caching layer for cost control. The cache-by-prompt-hash pattern is worth keeping for development and testing.

### 2.6 Prompt Engineering

The prompt system uses a two-part architecture: system prompt + day-specific prompt.

```bash
cat -n micro-demo-0.1/prompt_generator.py | sed -n '16,73p'
```

```output
    16	class DayPromptGenerator:
    17	    """Generates dynamic day-specific prompts for content generation."""
    18	    
    19	    def __init__(self, mock_srs: Optional[MockSRS] = None):
    20	        """Initialize with mock SRS for vocabulary constraints."""
    21	        self.mock_srs = mock_srs or create_mock_srs()
    22	        
    23	        # El Nido scenario templates for each day
    24	        self.scenario_templates = {
    25	            1: {
    26	                "title": "Welcome to El Nido!",
    27	                "scenario": "arrival_and_first_impressions",
    28	                "context": "Airport pickup, hotel check-in, first local interactions",
    29	                "focus": "Basic greetings and courtesy"
    30	            },
    31	            2: {
    32	                "title": "Getting Around Town",
    33	                "scenario": "navigation_and_transportation", 
    34	                "context": "Asking for directions, taking tricycle/jeepney, finding locations",
    35	                "focus": "Location and movement vocabulary"
    36	            },
    37	            3: {
    38	                "title": "Market and Shopping",
    39	                "scenario": "local_market_shopping",
    40	                "context": "Buying food, souvenirs, negotiating prices, local vendors",
    41	                "focus": "Money, prices, and basic transactions"
    42	            },
    43	            4: {
    44	                "title": "Food and Restaurants",
    45	                "scenario": "dining_experiences",
    46	                "context": "Ordering food, restaurant interactions, trying local cuisine",
    47	                "focus": "Food vocabulary and dining etiquette"
    48	            },
    49	            5: {
    50	                "title": "Accommodation Needs",
    51	                "scenario": "hotel_and_lodging",
    52	                "context": "Hotel services, room issues, asking for help with facilities",
    53	                "focus": "Accommodation and comfort needs"
    54	            },
    55	            6: {
    56	                "title": "Beach and Activities", 
    57	                "scenario": "beach_and_recreation",
    58	                "context": "Beach activities, weather, planning excursions, equipment rental",
    59	                "focus": "Leisure activities and weather"
    60	            },
    61	            7: {
    62	                "title": "Restaurant Confidence",
    63	                "scenario": "advanced_dining",
    64	                "context": "Complex restaurant interactions, special requests, social dining",
    65	                "focus": "Sophisticated dining vocabulary"
    66	            },
    67	            8: {
    68	                "title": "Departure Preparations",
    69	                "scenario": "departure_and_farewell",
    70	                "context": "Checking out, airport procedures, saying goodbye",
    71	                "focus": "Travel logistics and farewells"
    72	            }
    73	        }
```

The prompt generator injects day-specific context (scenario, focus area), vocabulary constraints (from SRS), and strategy guidance into each prompt. The 8-day El Nido scenario progression is hardcoded here.

The system prompt (`prompts/system_prompt.txt`, 152 lines) establishes pedagogical standards, and strategy-specific templates (`story_prompt_wider.txt`, `story_prompt_deeper.txt`) adjust the generation approach.

**Production note:** This is where the PRD's "Curriculum Generation Engine" requirement lives. The hardcoded 8-day El Nido template must become dynamic — the user provides scripts/topics (PRD Section 7.1), and the system generates scenario templates from those inputs. The prompt architecture (system + day + strategy) is sound but the content must be generated, not hardcoded.

### 2.7 The SRS System (Spaced Repetition)

The SRS is the most complex subsystem, spanning 7 files. Here's how they connect:

```bash
cat -n micro-demo-0.1/srs_database.py | sed -n '14,80p'
```

```output
    14	class SRSDatabase:
    15	    """SQLite database interface for SRS collocation storage."""
    16	    
    17	    def __init__(self, db_path: str = "instance/data/srs/tunatale_srs.db"):
    18	        """Initialize the SRS database.
    19	        
    20	        Args:
    21	            db_path: Path to the SQLite database file or ":memory:" for in-memory database
    22	        """
    23	        if db_path == ":memory:":
    24	            # Keep as string for in-memory database
    25	            self.db_path = db_path
    26	            # For in-memory databases, maintain a persistent connection
    27	            self._connection = sqlite3.connect(self.db_path)
    28	            self._connection.row_factory = sqlite3.Row
    29	            # Enable foreign key support
    30	            self._connection.execute("PRAGMA foreign_keys = ON")
    31	        else:
    32	            # Use Path object for file-based database
    33	            self.db_path = Path(db_path)
    34	            # Ensure the directory exists
    35	            self.db_path.parent.mkdir(parents=True, exist_ok=True)
    36	            self._connection = None
    37	        
    38	        # Initialize database schema
    39	        self.init_database()
    40	    
    41	    def _get_connection(self):
    42	        """Get appropriate database connection (persistent for in-memory, temporary for file)."""
    43	        if self._connection:
    44	            return self._connection
    45	        else:
    46	            return sqlite3.connect(self.db_path)
    47	    
    48	    def __enter__(self):
    49	        """Context manager entry."""
    50	        return self
    51	    
    52	    def __exit__(self, exc_type, exc_val, exc_tb):
    53	        """Context manager exit with cleanup."""
    54	        self.close()
    55	    
    56	    def __del__(self):
    57	        """Cleanup when object is garbage collected."""
    58	        self.close()
    59	    
    60	    def init_database(self):
    61	        """Create database tables if they don't exist."""
    62	        if self._connection:
    63	            # Use persistent connection for in-memory databases
    64	            conn = self._connection
    65	            self._create_tables(conn)
    66	        else:
    67	            # Use temporary connection for file-based databases
    68	            with sqlite3.connect(self.db_path) as conn:
    69	                # Enable foreign key support
    70	                conn.execute("PRAGMA foreign_keys = ON")
    71	                self._create_tables(conn)
    72	    
    73	    def _create_tables(self, conn):
    74	        """Create database tables in the given connection."""
    75	        # Create collocations table matching existing JSON structure
    76	        conn.execute("""
    77	            CREATE TABLE IF NOT EXISTS collocations (
    78	                id INTEGER PRIMARY KEY AUTOINCREMENT,
    79	                text TEXT UNIQUE NOT NULL,
    80	                
```

The SRS architecture has these layers:

**`srs_database.py`** — SQLite storage with a `collocations` table tracking: text, stability, review count, last reviewed, first seen day, corpus frequency, and more. Supports both file-based and in-memory databases (good for testing).

**`enhanced_srs_database.py`** — Extends SRSDatabase with bidirectional English<->Filipino translation support via `TranslationPair` and `BilingualCollocation` dataclasses.

**`srs_adapter.py`** — Provides a backward-compatible interface (`CollocationStatus` dataclass) with strategy-aware collocation retrieval, validation filtering (removes voice tags and invalid items), and prioritization by overdue status.

**`srs_enforcer.py`** — The two-pass constraint enforcement system. After story generation, it scans for English words that have known Filipino equivalents and replaces them. This fixes the critical "water/tubig regression" where the LLM would use English even when the learner already knows the Filipino word.

```bash
cat -n micro-demo-0.1/srs_enforcer.py | sed -n '18,80p'
```

```output
    18	class SRSEnforcer:
    19	    """Enforces SRS constraints by replacing English with known Filipino vocabulary."""
    20	    
    21	    def __init__(self, db: SRSDatabase):
    22	        """Initialize the SRS enforcer.
    23	        
    24	        Args:
    25	            db: SRSDatabase instance for accessing vocabulary data
    26	        """
    27	        self.db = db
    28	        self.replacement_dict = self._build_replacement_dictionary()
    29	        self.debug_mode = True  # Show replacement info
    30	    
    31	    def _build_replacement_dictionary(self) -> Dict[str, str]:
    32	        """Build dictionary of English → Filipino replacements.
    33	        
    34	        Returns:
    35	            Dictionary mapping English terms to Filipino equivalents
    36	        """
    37	        replacements = {}
    38	        
    39	        # Critical replacements based on known regressions
    40	        critical_replacements = {
    41	            # Water-related (highest priority - active regression)
    42	            'water': 'tubig',
    43	            'bottled water': 'tubig',
    44	            'service water': 'libre pong tubig',
    45	            'just water': 'tubig lang po',
    46	            
    47	            # Common courtesy (based on database analysis)
    48	            'thank you': 'salamat po',
    49	            'thanks': 'salamat',
    50	            'excuse me': 'paumanhin po',
    51	            
    52	            # Questions/requests
    53	            'how much': 'magkano po',
    54	            'can you': 'pwede po ba',
    55	            'may I': 'pwede po ba ako',
    56	            'please': 'po',
    57	            
    58	            # Common words that often appear in English
    59	            'yes': 'opo',
    60	            'okay': 'sige',
    61	            'good': 'maganda',
    62	            'delicious': 'masarap',
    63	            'expensive': 'mahal',
    64	            
    65	            # Numbers (common regression points)
    66	            'thirty': 'tatlumpu',
    67	            'sixty': 'animnapu', 
    68	            'eighty': 'walumpu',
    69	            'twenty': 'dalawampu',
    70	            
    71	            # Colors
    72	            'blue': 'asul',
    73	            'pink': 'rosas',
    74	            'red': 'pula',
    75	            'green': 'luntian',
    76	            
    77	            # Size/quantity
    78	            'big': 'malaki',
    79	            'small': 'maliit',
    80	            'many': 'marami',
```

The enforcer has a hardcoded dictionary of critical replacements plus dynamic entries loaded from the SRS database. It uses word-boundary regex matching (`\b`) for case-insensitive replacement while preserving case.

**`srs_feedback_system.py`** — Closes the feedback loop: after story generation, it analyzes which SRS-provided collocations were actually used in the story. Only used collocations get marked as "reviewed" — unused ones stay in the retry queue. This prevents SRS drift.

**`srs_llm_enforcer.py`** — Advanced enforcement using a `DeterministicEnglishDetector` for grammar-aware English term detection in dialogue, with SRS cross-referencing.

**`srs_phrase_extractor.py`** — Extracts phrases from generated stories for SRS tracking. Filters out syllable breakdowns and voice tags.

**`srs_debug_analyzer.py`** — Classifies vocabulary into recognition states: UNKNOWN, DORMANT, UNSTABLE, NATURALLY_ACQUIRING, EXPLICITLY_LEARNED, HIGH_STABILITY. Used for debugging SRS behavior.

**Production note:** The SRS system is the prototype's most valuable and most complex subsystem. Key mapping to the PRD:
- PRD Section 7.3 requires FSRS algorithms — the prototype uses a simpler stability/review-count model. Production needs the actual FSRS algorithm (open-source implementations exist).
- PRD requires tracking **syntactic units/collocations (3-5 words)** — the prototype already does this.
- PRD requires **implicit feedback** (no help = success, help request = failure) — the prototype has `srs_feedback_system.py` which is similar but uses story-usage as the signal, not learner help-seeking.
- The two-pass enforcement pattern (generate then enforce) is battle-tested and should be preserved.
- The hardcoded replacement dictionary should become fully dynamic, populated from the SRS database translations.

### 2.8 Pimsleur Breakdown Engine

The `utils/pimsleur_breakdown.py` implements official KWF (Komisyon sa Wikang Filipino) syllabification:

```bash
grep -n 'def syllabify_tagalog_word\|def generate_pimsleur_breakdown\|KWF Rule' micro-demo-0.1/utils/pimsleur_breakdown.py | head -15
```

```output
55:def syllabify_tagalog_word(word: str) -> List[str]:
128:    """Apply KWF Rule 2: Consecutive vowels are always separated."""
241:            # Single consonant - goes with following vowel (KWF Rule 3: V-CV)
246:            # Multiple consonants - split them (KWF Rule 4: VC-CV)
305:        # Single consonant - goes with following vowel (KWF Rule 3)
313:            # Split consonants (KWF Rule 4)
333:    Based on KWF Rule 5 and Filipino phonotactics.
342:    # ng is always treated as single unit (KWF Rule 6)
513:def generate_pimsleur_breakdown(phrase: str) -> List[str]:
```

The Pimsleur breakdown takes a multi-word Filipino phrase and generates a progressive buildup sequence, working right-to-left:

For "Kumusta po" → ["po", "ta", "mus", "Ku", "Kumusta", "Kumusta po"]

It uses 6 official KWF syllabification rules from the Ortograpiyang Pambansa (2013), handling Filipino-specific phonology like the "ng" digraph (always treated as single unit), consecutive vowel separation, and consonant cluster rules.

**Production note:** This is Tagalog-specific. For Norwegian (PRD target), you'll need equivalent syllabification rules. The progressive buildup pattern (Pimsleur method) is language-agnostic in concept but language-specific in implementation. Design a pluggable syllabifier interface.

### 2.9 CLI Structure

The CLI is organized into modular command groups:

```bash
ls -la micro-demo-0.1/cli/ && echo '---' && head -15 micro-demo-0.1/cli/__init__.py
```

```output
total 240
drwxr-xr-x@ 12 wdhaines  staff    384 Oct  4 22:34 .
drwxr-xr-x  74 wdhaines  staff   2368 Oct  4 22:39 ..
-rw-r--r--@  1 wdhaines  staff     67 Sep  8 21:21 __init__.py
drwxr-xr-x@ 10 wdhaines  staff    320 Oct  4 22:35 __pycache__
-rw-r--r--@  1 wdhaines  staff  20769 Oct  4 21:18 analysis_commands.py
-rw-r--r--@  1 wdhaines  staff  11629 Oct  4 21:36 enforcement_commands.py
-rw-r--r--@  1 wdhaines  staff   8125 Oct  4 20:32 generation_commands.py
-rw-r--r--@  1 wdhaines  staff  33365 Oct  4 22:34 srs_commands.py
-rw-r--r--@  1 wdhaines  staff   6280 Oct  4 21:27 translation_commands.py
-rw-r--r--@  1 wdhaines  staff   4749 Oct  3 05:13 utils.py
-rw-r--r--@  1 wdhaines  staff   3395 Oct  4 21:36 view_commands.py
-rw-r--r--@  1 wdhaines  staff  15109 Oct  3 05:15 vocab_commands.py
---
"""
CLI command modules for TunaTale SRS vocabulary management.
"""```
```

The CLI provides commands across 7 modules:
- **generation_commands.py** (8KB) — `generate`, `generate-day`, `continue`, `extend`
- **analysis_commands.py** (20KB) — `analyze`, `analyze --quality`, `analyze --trip-readiness`
- **enforcement_commands.py** (11KB) — SRS constraint enforcement on stories
- **srs_commands.py** (33KB) — SRS management, review scheduling, statistics
- **translation_commands.py** (6KB) — Translation and vocabulary utilities
- **view_commands.py** (3KB) — Display curriculum and content
- **vocab_commands.py** (15KB) — Vocabulary management

**Production note:** The CLI is for developer/operator use. The PRD envisions a React Native mobile app (Android-first) + future web app. The CLI commands map to backend API endpoints. In production: generation_commands → content generation API, srs_commands → SRS API, analysis_commands → analytics API. Keep the CLI for development but build a proper API layer on top.

### 2.10 Test Suite

```bash
ls micro-demo-0.1/tests/test_*.py | wc -l && echo 'test files' && ls micro-demo-0.1/tests/test_*.py | head -20
```

```output
      54
test files
micro-demo-0.1/tests/test_analyze_command.py
micro-demo-0.1/tests/test_analyze_command_cli.py
micro-demo-0.1/tests/test_cli.py
micro-demo-0.1/tests/test_cli_smoke.py
micro-demo-0.1/tests/test_config.py
micro-demo-0.1/tests/test_content_processor_debug.py
micro-demo-0.1/tests/test_content_strategy.py
micro-demo-0.1/tests/test_curriculum_file_discovery.py
micro-demo-0.1/tests/test_curriculum_models.py
micro-demo-0.1/tests/test_curriculum_service.py
micro-demo-0.1/tests/test_data_validation.py
micro-demo-0.1/tests/test_day15_kwf_rules.py
micro-demo-0.1/tests/test_day15_pimsleur_fixes.py
micro-demo-0.1/tests/test_deeper_strategy_bug.py
micro-demo-0.1/tests/test_deterministic_english_detector.py
micro-demo-0.1/tests/test_el_nido_user_journey.py
micro-demo-0.1/tests/test_integration_end_to_end.py
micro-demo-0.1/tests/test_integration_workflow.py
micro-demo-0.1/tests/test_language_filtering.py
micro-demo-0.1/tests/test_main.py
```

54 test files covering content strategy, curriculum models, SRS enforcement, Pimsleur breakdown, story generation, CLI commands, and integration workflows. The tests use pytest with fixtures defined in `conftest.py` that mock config paths, create temporary data directories, and provide test fixtures.

**Production note:** These tests are prototype-grade — many test specific bug fixes (e.g., `test_day15_kwf_rules.py`, `test_deeper_strategy_bug.py`) rather than systematic behavior. For TDD production rebuild, start fresh with:
1. Domain model tests (pure logic, no I/O)
2. Port/adapter contract tests  
3. Integration tests for the generation pipeline
4. E2E tests for the full flow

---

## PART 3: PRD Gap Analysis — What Exists vs What's Needed

### 3.1 What the Prototypes Prove (Keep This)

| Prototype Feature | PRD Requirement | Status |
|---|---|---|
| Pimsleur 4-section lesson format | Audio-first immersion (7.2) | Proven, keep |
| TTS with EdgeTTS + multi-provider | Audio delivery system (7.2) | Proven, keep |
| SRS collocation tracking (3-5 words) | Syntactic unit tracking (7.3) | Proven, keep |
| Two-pass enforcement (generate→enforce) | Content quality control | Proven, keep |
| Wider/Deeper strategy framework | Adaptive pacing (7.2) | Proven, keep |
| Pedagogical scoring config | Collocation selection | Proven, keep |
| Dynamic pause system | Audio quality | Proven, keep |
| KWF syllabification | Pimsleur breakdowns | Proven, Tagalog only |
| Natural pause multipliers | Pedagogical timing | Proven, keep |

### 3.2 What's Missing (Build from Scratch)

| PRD Requirement | Section | Current Status |
|---|---|---|
| User-provided scripts as input | 7.1 | Not implemented (curriculum is LLM-generated from goals) |
| FSRS algorithm (proper) | 7.3 | Simplified stability model, needs real FSRS |
| Implicit feedback from help signals | 7.3 | Prototype uses story-usage, not learner signals |
| Target-language voice commands | 7.2 | Not implemented at all |
| Conceptual definitions | 7.2 | Not implemented (uses direct translation) |
| Literal/structural translations | 7.2 | Not implemented |
| Output/Practice mode | 7.4 | Not implemented (only input/listening mode) |
| Mobile app (React Native) | 8.2, 9.2 | CLI only |
| Offline capability | 8.1 | Not implemented |
| Norwegian language support | Success criteria | Tagalog only |
| Speech recognition for commands | 7.2 | Not implemented |
| Three-phase learning cycle | 6 | Only phase 2 (audio immersion) exists |
| Web app for preparation phase | 9.2 | Not implemented |

### 3.3 What Needs Redesign (Evolve This)

| Feature | Current State | Production Need |
|---|---|---|
| Config system | Module-level globals with side effects | Proper settings management, env-based |
| LLM integration | Mock with MD5-cached responses | Real Claude API with caching layer |
| Prompt templates | Hardcoded 8-day El Nido scenarios | Dynamic generation from user scripts |
| Language support | Hardcoded Tagalog/English | Pluggable per-language modules |
| SRS database | SQLite with raw SQL | Same SQLite but with proper migrations, FSRS |
| Content generation | Flat module organization | Clean domain-driven architecture |
| CLI | argparse-based developer tool | API layer for mobile/web clients |
| Error handling | Inconsistent (some try/except, some not) | Unified error strategy |
| Curriculum model | Fixed day-based structure | Dynamic content streams |

---

## PART 4: Production Rebuild Strategy for Claude (TDD Approach)

This section is written specifically for an AI agent tasked with rebuilding TunaTale as a production application using Test-Driven Development.

### 4.1 Architectural Principles

1. **Hexagonal architecture** — Use the 0.0 pattern (ports/adapters) everywhere, not just the audio pipeline
2. **Domain-driven design** — Core domain has zero dependencies on infrastructure
3. **TDD red-green-refactor** — Every feature starts with a failing test
4. **Language-agnostic core** — Design for Norwegian first, prove it works for Tagalog second
5. **Mobile-first API** — Backend serves a React Native client, CLI is development convenience

### 4.2 Recommended Build Order

**Phase 1: Core Domain Models (Week 1)**

Start with pure domain models that have no I/O, no database, no network calls. These are the foundation for everything else and should be tested exhaustively.

Build in this order:
1. `Language` — Configurable language with code, name, script properties
2. `SyntacticUnit` — A collocation/chunk (3-5 words) with source language, translation, difficulty
3. `LearnerProfile` — CEFR level, target language, native language, goals
4. `ContentStrategy` — Port the WIDER/DEEPER framework + PedagogicalScoringConfig
5. `Curriculum` — Dynamic content stream model (not fixed days)
6. `Lesson` — Keep the 4-section Pimsleur structure from 0.0
7. `SRSItem` — FSRS-based item with stability, difficulty, due date, reps

Tests to write first:
- SyntacticUnit validates length (3-5 words from corpus analysis)
- PedagogicalScoringConfig weights sum to 1.0
- Curriculum generates dynamic content stream from user scripts
- SRSItem implements FSRS scheduling correctly

**Phase 2: SRS Engine (Week 2)**

Port the SRS system with proper FSRS algorithm. This is the PRD's most technically novel requirement.

Build in this order:
1. `FSRSScheduler` — Implement FSRS-5 algorithm (open-source reference: py-fsrs)
2. `ImplicitFeedbackAdapter` — Convert help signals to FSRS ratings
3. `SRSRepository` (port) — Interface for SRS persistence
4. `SQLiteSRSRepository` (adapter) — SQLite implementation with migrations
5. `CollocationSelector` — Port PedagogicalScoringConfig logic for selecting which items to include

Tests to write first:
- FSRS scheduling matches reference implementation for known inputs
- Implicit feedback: no-help maps to `Good`, slowdown maps to `Hard`, translation maps to `Again`
- CollocationSelector respects strategy weights (wider vs deeper behavior)
- SRS items graduate when review frequency falls below natural occurrence

**Phase 3: Content Generation Pipeline (Week 3)**

Build the LLM-powered content generation with the two-pass architecture.

Build in this order:
1. `LLMClient` (port) — Interface for LLM calls
2. `ClaudeLLMClient` (adapter) — Claude API implementation with caching
3. `PromptBuilder` — Dynamic prompt construction from user scripts + SRS state + strategy
4. `ContentGenerator` — Orchestrates prompt building, LLM calls, response parsing
5. `ContentEnforcer` — Port the two-pass enforcement from srs_enforcer.py (but fully dynamic, no hardcoded replacements)
6. `SRSFeedbackProcessor` — Port srs_feedback_system.py for post-generation SRS updates

Tests to write first:
- PromptBuilder includes all due-for-review collocations in prompt
- ContentEnforcer replaces English with known Filipino equivalents
- ContentGenerator respects strategy constraints (wider: 8 new max, deeper: 3 new max)
- SRS feedback only marks actually-used collocations as reviewed

**Phase 4: Audio Pipeline (Week 4)**

Port the proven audio pipeline from 0.0.

Build in this order:
1. `TTSService` (port) — Keep the Protocol from 0.0
2. `EdgeTTSAdapter` — Port edge_tts_service.py with rate limiting and caching
3. `TextPreprocessor` (port) — Language-pluggable preprocessing interface
4. `NorwegianPreprocessor` / `TagalogPreprocessor` — Language-specific implementations
5. `PauseCalculator` — Port natural_pause_calculator.py
6. `AudioAssembler` — Port audio_processor.py
7. `LessonRenderer` — Orchestrates text preprocessing → TTS → pause calculation → assembly

Tests to write first:
- TTSService contract test (verify implementations match protocol)
- Preprocessing is language-specific (Norwegian vs Tagalog produce different output)
- Pause durations match prototype ratios for known inputs
- Audio assembly produces valid MP3 with correct silence gaps

**Phase 5: API & Mobile Integration (Week 5+)**

Build the backend API and connect to mobile client.

Build in this order:
1. REST API layer (FastAPI or similar) exposing: curriculum CRUD, content generation, SRS state, audio streaming
2. Authentication and user management
3. Offline content packaging (pre-generate 30-minute sessions)
4. React Native audio player with learner controls
5. Target-language voice command recognition (limited vocabulary)

### 4.3 Key Design Decisions to Make Early

1. **Content stream vs fixed days**: The prototype uses fixed 8-day curricula. The PRD implies dynamic content streams. Decide: Are "days" a useful abstraction or should content be a continuous stream of units?

2. **FSRS adaptation for implicit feedback**: The PRD's key innovation is using help-seeking behavior as SRS feedback. Decide: How to map the continuous spectrum of learner signals to FSRS's discrete rating scale (Again/Hard/Good/Easy)?

3. **Collocation extraction**: The prototype uses LLM-based extraction (`llm_based_extraction_processor.py`). Decide: Extract collocations from user scripts using NLP (spaCy, which is already a dependency) or LLM, or both?

4. **Language plugin architecture**: Supporting Norwegian + Tagalog means pluggable: syllabification, preprocessing, abbreviation handling, number formatting, voice mappings. Decide: Plugin interface shape.

5. **Audio delivery model**: Generate full lessons or stream chunks? The PRD says "30-minute sessions" but also "adaptive pacing." Decide: Pre-generate sessions with checkpoints, or generate on-demand in smaller chunks?

### 4.4 Files to Study Most Carefully

When implementing each phase, reference these prototype files as your primary source of domain knowledge:

| Production Component | Study These Files |
|---|---|
| SRS scoring logic | `content_strategy.py:PedagogicalScoringConfig` |
| Two-pass enforcement | `srs_enforcer.py`, `srs_llm_enforcer.py` |
| SRS feedback loop | `srs_feedback_system.py`, `srs_usage_validator.py` |
| Audio pipeline | `0.0/tunatale/core/services/lesson_processor.py` |
| TTS integration | `0.0/tunatale/infrastructure/services/tts/edge_tts_service.py` |
| Natural pauses | `0.0/tunatale/core/services/natural_pause_calculator.py` |
| Text preprocessing | `0.0/tunatale/core/utils/tts_preprocessor.py` |
| Pimsleur breakdown | `0.1/utils/pimsleur_breakdown.py` |
| Port/adapter pattern | `0.0/tunatale/core/ports/tts_service.py` |
| Prompt engineering | `0.1/prompts/system_prompt.txt`, `prompt_generator.py` |
| Curriculum model | `0.1/curriculum_models.py`, `curriculum_service.py` |
| Story generation | `0.1/story_generator.py` (large file, focus on generate_story method) |

### 4.5 Anti-patterns to Avoid (Lessons from the Prototype)

1. **Don't hardcode language-specific logic** — The prototype has Tagalog assumptions everywhere. Every language-specific behavior should go through a language plugin.

2. **Don't use module-level side effects** — `config.py` creates directories on import. Keep config pure.

3. **Don't mix concerns in god files** — `story_generator.py` in 0.1 is ~95KB. Keep generators, enforcers, and feedback processors separate.

4. **Don't leave debug corruption checks in models** — `curriculum_models.py` has extensive corruption detection logging. This was debugging, not a feature.

5. **Don't hardcode replacement dictionaries** — `srs_enforcer.py` has a hardcoded water→tubig dictionary. Build replacements dynamically from the SRS database's translation pairs.

6. **Don't create 50+ top-level modules** — The 0.1 codebase has everything at the root level. Use proper package structure from day one.

7. **Don't couple to mock implementations** — `MockSRS`, `MockLLM` are imported directly throughout. Use dependency injection so swapping mock↔real is trivial.

---

## Summary

TunaTale's two prototypes collectively prove the core concept: AI-generated, pedagogically-sound, audio-first language learning with SRS tracking works. The audio pipeline (0.0) has clean architecture. The content engine (0.1) has rich domain logic but needs architectural cleanup. 

The production rebuild should:
1. Unify both halves under hexagonal architecture
2. Start with domain models and SRS (the hardest novel part)
3. Port proven logic (pause timing, scoring config, enforcement) rather than rewriting
4. Add what's missing: FSRS, implicit feedback, Norwegian support, mobile API
5. Use TDD throughout — the prototype's test suite covers specific bugs, production tests should cover behaviors
