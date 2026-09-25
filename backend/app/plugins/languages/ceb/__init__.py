"""Cebuano language plugin."""

from app.cards.vocab_notetype import CEBUANO_VOCAB
from app.languages import LanguageConfig, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.ceb.preprocessor import CebuanoPreprocessor

register(
    "ceb",
    LanguageConfig(
        language=Language(
            code="ceb",
            name="Cebuano",
            native_name="Binisaya",
            script="latin",
            tts_locale="ceb-PH",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                # Gemini-TTS voices (the user's decision, 2026-09-25,
                # tunatale-u8nz.1), rendered through Cloud TTS with
                # languageCode=ceb-PH. The id shape <locale>-<Name>Gemini
                # mirrors Azure's <locale>-<Name>Neural on purpose, so the
                # registry-wide voice invariants (the locale sliced off the id)
                # hold unchanged. NO adapter renders these ids yet — that is
                # tunatale-u8nz.2, and until it ships a render attempt fails
                # loudly at Azure rather than silently producing English.
                # ⚠️ The cast is PROVISIONAL: Kore and Charon were heard in the
                # voice samples; Aoede and Puck are UNMEASURED placeholders, and
                # u8nz.2 picks the cast by pitch the way the tl cast was picked.
                "female-1": "ceb-PH-KoreGemini",
                "female-2": "ceb-PH-AoedeGemini",
                "male-1": "ceb-PH-CharonGemini",
                "male-2": "ceb-PH-PuckGemini",
                "female": "ceb-PH-KoreGemini",
                "male": "ceb-PH-CharonGemini",
            },
            # Per-voice loudness gains (dB) applied at assembly, target −20.0
            # LUFS. The narrator gain is copied from the sl/no plugins:
            # en-US-GuyNeural is the shared narrator; the gain is resolved per
            # language (get_tts_voice_gain_db takes a code), so the same
            # measured value has to appear in every plugin table. Measured
            # 2026-09-13 at -19.08 LUFS mean.
            # ⚠️ The Gemini gains are UNMEASURED until tunatale-u8nz.2 — only
            # the narrator is pinned here.
            tts_voice_gain_db={
                "en-US-GuyNeural": -0.9,
            },
        ),
        preprocessor_factory=CebuanoPreprocessor,
        deck_name="3. Bisaya",
        mint_deck_name="3. Bisaya::TunaTale",
        vocab_notetype=CEBUANO_VOCAB,
        # Deliberately omitted until their owning beads ship:
        # - planner_example: get_planner_example picks the LOWEST-SORTING other
        #   language that supplies one, and "ceb" sorts before every other
        #   registered code. A Cebuano example would therefore silently replace
        #   the example shown to EVERY other language — and Tagalog, a close
        #   relative, would be shown Cebuano. That is exactly the
        #   planner-language contamination the selector exists to prevent.
        # - wordfreq_lang: wordfreq has no "ceb" (tunatale-u8nz.6).
        # - syllabifier_fn, lemma_table_path, lemmatizer_type (stays at the
        #   shared "lowercase" default): tunatale-u8nz.6 and tunatale-u8nz.5.
        # - l2_scorer, notetype_profiles, style_notes, function_words_path,
        #   numbers_path, phoneme_planner_factory: later Cebuano beads.
    ),
)
