"""Language configuration model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Language:
    """Language configuration including ISO code, display names, script, and TTS voice map."""

    code: str  # ISO 639-1 code, e.g. "sl"
    name: str  # English name, e.g. "Slovene"
    native_name: str  # Native name, e.g. "slovenščina"
    script: str  # Writing system, e.g. "latin"
    tts_voice_map: dict[str, str] = field(default_factory=dict)  # role → EdgeTTS voice name

    @classmethod
    def slovene(cls) -> Language:
        return cls(
            code="sl",
            name="Slovene",
            native_name="slovenščina",
            script="latin",
            tts_voice_map={
                "narrator": "en-US-GuyNeural",
                "female-1": "sl-SI-PetraNeural",
                "female-2": "sl-SI-PetraNeural",
                "male-1": "sl-SI-RokNeural",
                "male-2": "sl-SI-RokNeural",
                "female": "sl-SI-PetraNeural",  # legacy
                "male": "sl-SI-RokNeural",  # legacy
            },
        )

    @classmethod
    def english(cls) -> Language:
        return cls(
            code="en",
            name="English",
            native_name="English",
            script="latin",
            tts_voice_map={
                "narrator": "en-US-GuyNeural",
                "female-1": "en-US-AriaNeural",
                "female-2": "en-US-AriaNeural",
                "male-1": "en-US-GuyNeural",
                "male-2": "en-US-GuyNeural",
                "female": "en-US-AriaNeural",  # legacy
                "male": "en-US-GuyNeural",  # legacy
            },
        )
