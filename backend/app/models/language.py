"""Language configuration model."""

from __future__ import annotations

from dataclasses import dataclass, field

# The narrator (English titles, scene labels, glosses — and every translation a
# language has no English cast for) voice — shared across every language's voice
# map and the default narrator for generated lessons. Single-sourced here so
# lesson/story code doesn't re-hardcode the literal. Davis replaced Guy on
# 2026-09-29, by ear: the user rejected Guy's intonation (tunatale-ucpg).
NARRATOR_VOICE = "en-US-DavisMultilingualNeural"


@dataclass
class Language:
    """Language configuration including ISO code, display names, script, and TTS voice map."""

    code: str  # ISO 639 code: 639-1 where one exists ("sl", "no", "tl"), else 639-3 ("ceb")
    name: str  # English name, e.g. "Slovene"
    native_name: str  # Native name, e.g. "slovenščina"
    script: str  # Writing system, e.g. "latin"
    tts_voice_map: dict[str, str] = field(default_factory=dict)  # role → EdgeTTS voice name
    # Dialogue role → the English voice that reads that role's TRANSLATIONS in
    # the four translated sections, so a line's English sounds like its speaker.
    # A role absent here (every role, for a language that sets nothing) is read
    # by the narrator, which is exactly the behaviour before this map existed.
    # The phrase keeps role="narrator" either way: that role is structure
    # (cue pairing, key_phrase_groups), and only the voice changes.
    tts_en_voice_map: dict[str, str] = field(default_factory=dict)
    # Constant dB gain applied to a voice at assembly, keyed by VOICE ID (not
    # role — two roles can share one voice). Pins per-voice loudness to a −20.0
    # LUFS target so two characters in one dialogue sit at the same level. A
    # voice absent from the map (any unmeasured voice) gets 0.0 and changes
    # nothing. The gain is looked up under the language of the TEXT, so these
    # are the levels a voice reaches reading THIS language; English phrases
    # resolve against ``english()``'s table, whatever lesson they are in.
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
            # Every voice that reads ENGLISH in any lesson, since an English
            # phrase's gain resolves here (renderer: keyed on the phrase's own
            # language_code). Measured 2026-09-29 on 8 real English translation
            # lines from a stored Norwegian lesson, through synthesize() and the
            # real tts cache, ffmpeg ebur128 integrated LUFS per clip,
            # gain = -20.0 - mean. That set read 0.5 dB quieter than Guy's mean
            # over 60 cached corpus lines (-19.48 LUFS, se 0.07), so each value
            # is anchored to the corpus by subtracting 0.5. Raw means (LUFS):
            # Davis -21.62, Nancy -22.55, Amanda -22.03, Adam -19.94,
            # Emma -18.00, Shimmer -20.06, Derek -21.39, Giuseppe -21.40,
            # Dustin -21.22, Guy -19.98.
            # ⚠️ A voice's English level is NOT its Norwegian level: Giuseppe
            # is -0.7 dB in the nb table and +0.9 here. That is why this is a
            # separate table rather than one gain per voice.
            # ⚠️ Until this table existed it was empty, so the narrator's
            # -0.9 dB (2026-09-13) reached lesson TITLES and never one English
            # line in a section — tests asked the "no" table, the renderer
            # asks "en". Guy stays: stored lessons pin him until rebuilt.
            tts_voice_gain_db={
                "en-US-DavisMultilingualNeural": 1.1,
                "en-US-NancyMultilingualNeural": 2.1,
                "en-US-AmandaMultilingualNeural": 1.5,
                "en-US-AdamMultilingualNeural": -0.6,
                "en-US-EmmaMultilingualNeural": -2.5,
                "en-US-ShimmerTurboMultilingualNeural": -0.4,
                "en-US-DerekMultilingualNeural": 0.9,
                "it-IT-GiuseppeMultilingualNeural": 0.9,
                "en-US-DustinMultilingualNeural": 0.7,
                "en-US-GuyNeural": -0.5,
                # Tagalog / Slovene male-2 read their own English. Same 8-line
                # set and -0.5 anchor, 2026-09-29; raw -20.33 / -19.46 LUFS.
                "en-US-SamuelMultilingualNeural": -0.2,
                "de-DE-FlorianMultilingualNeural": -1.0,
            },
        )
