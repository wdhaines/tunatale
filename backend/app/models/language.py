"""Language configuration model."""

from __future__ import annotations

from dataclasses import dataclass, field

# The narrator (English descriptions/translations) voice — shared across every
# language's voice map and the default narrator for generated lessons. Single-sourced
# here so lesson/story code doesn't re-hardcode the literal.
NARRATOR_VOICE = "en-US-GuyNeural"


@dataclass
class Language:
    """Language configuration including ISO code, display names, script, and TTS voice map."""

    code: str  # ISO 639-1 code, e.g. "sl"
    name: str  # English name, e.g. "Slovene"
    native_name: str  # Native name, e.g. "slovenščina"
    script: str  # Writing system, e.g. "latin"
    tts_voice_map: dict[str, str] = field(default_factory=dict)  # role → EdgeTTS voice name

    @classmethod
    def english(cls) -> Language:
        return cls(
            code="en",
            name="English",
            native_name="English",
            script="latin",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                "female-1": "en-US-AriaNeural",
                "female-2": "en-US-AriaNeural",
                "male-1": "en-US-GuyNeural",
                "male-2": "en-US-GuyNeural",
                "female": "en-US-AriaNeural",  # legacy
                "male": "en-US-GuyNeural",  # legacy
            },
        )
