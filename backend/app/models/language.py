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
    # Constant dB gain applied to a voice at assembly, keyed by VOICE ID (not
    # role — two roles can share one voice). Pins per-voice loudness to a −20.0
    # LUFS target so two characters in one dialogue sit at the same level. A
    # voice absent from the map (any English/narrator voice, any unmeasured
    # voice) gets 0.0 and changes nothing.
    tts_voice_gain_db: dict[str, float] = field(default_factory=dict)
    # The SSML locale this language's text is written in ("nb-NO", "sl-SI").
    # Needed only because a voice map may name a voice from ANOTHER locale — a
    # Multilingual Neural voice filling a role the native catalogue cannot
    # (sl-SI ships two voices for four roles). The adapter wraps such a render
    # in <lang> so the voice does not guess the language and get it wrong. A
    # language that leaves this unset behaves exactly as before: no wrapper.
    tts_locale: str | None = None

    @classmethod
    def english(cls) -> Language:
        return cls(
            code="en",
            name="English",
            native_name="English",
            script="latin",
            tts_locale="en-US",
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
