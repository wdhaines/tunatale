"""Tagalog language plugin."""

from app.cards.vocab_notetype import TAGALOG_VOCAB
from app.languages import LanguageConfig, PlannerExample, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.tl.preprocessor import TagalogPreprocessor

register(
    "tl",
    LanguageConfig(
        language=Language(
            code="tl",
            name="Tagalog",
            native_name="Tagalog",
            script="latin",
            tts_locale="fil-PH",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                # PROVISIONAL: Blessica and Angelo are Azure's only two standard
                # fil-PH voices. Emma and Florian are Multilingual voices whose
                # SecondaryLocaleList includes fil-PH (orchestrator-verified from
                # the Azure voice list 2026-09-22). The voice-cast bead
                # (tunatale-w4m7.3) measures and may replace them.
                "female-1": "fil-PH-BlessicaNeural",
                "female-2": "en-US-EmmaMultilingualNeural",
                "male-1": "fil-PH-AngeloNeural",
                "male-2": "de-DE-FlorianMultilingualNeural",
                "female": "fil-PH-BlessicaNeural",
                "male": "fil-PH-AngeloNeural",
            },
            # Per-voice loudness gains (dB) applied at assembly, target −20.0
            # LUFS. The narrator gain is copied from the sl/no plugins:
            # en-US-GuyNeural is the shared narrator; the gain is resolved per
            # language (get_tts_voice_gain_db takes a code), so the same
            # measured value has to appear in every plugin table. Measured
            # 2026-09-13 at -19.08 LUFS mean.
            # ⚠️ The fil-PH and Multilingual gains are UNMEASURED until
            # tunatale-w4m7.3 — only the narrator is pinned here.
            tts_voice_gain_db={
                "en-US-GuyNeural": -0.9,
            },
        ),
        preprocessor_factory=TagalogPreprocessor,
        deck_name="2. Pimsleur Tagalog",
        vocab_notetype=TAGALOG_VOCAB,
        planner_example=PlannerExample(
            language_code="tl",
            day=5,
            title="At the market",
            focus="Buying fruit and asking prices",
            collocations=("magkano po ito?", "salamat po"),
            learning_objective="Ask prices and buy food politely in simple exchanges.",
            story_guidance="A morning at a Quezon City palengke; friendly haggling with a fruit vendor.",
        ),
        wordfreq_lang="fil",
        # Deliberately omitted until their owning beads ship:
        # - l2_scorer: Tagalog has no letters that distinguish it from English,
        #   and None is a LOUD condition at the call site (see get_l2_scorer's
        #   docstring) — the deck-import bead uses a NotetypeProfile instead.
        # - style_notes, function_words_path, numbers_path: the content bead
        #   tunatale-w4m7.4 owns them.
        # - syllabifier_fn, morphology_profile, a1_morphology, lemma_table_path,
        #   lemmatizer_type (stays at the default): other beads own these.
    ),
)
