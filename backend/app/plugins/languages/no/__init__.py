"""Norwegian language plugin."""

from pathlib import Path

from app.cards.vocab_notetype import NORWEGIAN_VOCAB
from app.languages import AlignmentConfig, LanguageConfig, PlannerExample, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.no.a1_morphology import NORWEGIAN_A1_MORPHOLOGY
from app.plugins.languages.no.alignment import MODEL_ID, NORWEGIAN_VOWELS, create_aligner
from app.plugins.languages.no.l2_scoring import score_norwegian_l2
from app.plugins.languages.no.lexicon import create_nst_lexicon
from app.plugins.languages.no.morphology import is_definite_form, is_lemma_plausible
from app.plugins.languages.no.multiword import trapped_pairs
from app.plugins.languages.no.norwegian_breakdown import (
    build_norwegian_breakdown_spans,
    flat_syllables,
    slow_norwegian_word,
)
from app.plugins.languages.no.phoneme_plan import create_phoneme_planner
from app.plugins.languages.no.preprocessor import NorwegianPreprocessor
from app.plugins.languages.no.syllabify import syllabify_norwegian_word

_style_notes = (Path(__file__).parent / "data" / "style.md").read_text(encoding="utf-8").strip()

register(
    "no",
    LanguageConfig(
        language=Language(
            code="no",
            name="Norwegian",
            native_name="norsk",
            script="latin",
            tts_locale="nb-NO",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                "female-1": "nb-NO-PernilleNeural",
                "female-2": "nb-NO-IselinNeural",
                "female-3": "en-US-EmmaMultilingualNeural",
                "female-4": "en-US-ShimmerTurboMultilingualNeural",
                "male-1": "nb-NO-FinnNeural",
                "male-2": "en-US-DerekMultilingualNeural",
                "male-3": "it-IT-GiuseppeMultilingualNeural",
                "male-4": "en-US-DustinMultilingualNeural",
                "female": "nb-NO-PernilleNeural",
                "male": "nb-NO-FinnNeural",
            },
            # The dialogue cast, measured 2026-09-16 through the product's own
            # synthesize() (ffmpeg ebur128 for loudness, Azure STT for WER):
            #   female-1 nb-NO-Pernille  163.3 Hz F0 / 0.722 harmonicity / WER 0.031 / 1 voice-specific error
            #   female-2 nb-NO-Iselin    205.1 Hz F0 / 0.821                / WER 0.042 / 1 voice-specific error
            #   female-3 en-US-Emma      181.8 Hz F0 / 0.773                / WER 0.021
            #   female-4 en-US-Shimmer   149.5 Hz F0 / 0.702                / WER 0.010
            #   male-1   nb-NO-Finn       97.0 Hz F0 / 0.538                / WER 0.021
            #   male-2   en-US-Derek     114.3 Hz F0 / 0.597                / WER 0.010
            #   male-3   it-IT-Giuseppe  128.0 Hz F0 / 0.650                / WER 0.010
            #   male-4   en-US-Dustin    160.0 Hz F0 / 0.734                / WER 0.010
            # Minimum pitch gap 13.8 Hz (women) / 13.7 Hz (men); the user
            # confirmed all eight by ear on a loudness-normalised track.
            # ⚠️ male-2 was en-AU-William until this cast, deliberately: Finn
            # 97.0 vs William 101.9 Hz is 4.9 Hz and 0.005 harmonicity apart —
            # a near-duplicate, the defect this widening exists to remove. Only
            # 2 of the other 31 nb-capable male voices sit closer to Finn than
            # William did.
            # ⚠️ en-US-ShimmerTurboMultilingualNeural is a Turbo voice whose
            # VoiceType is still Neural (read from the live /voices/list, which
            # has only Neural and NeuralHD) — NOT the paid HD line. Keep it.
            # Per-voice loudness gains (dB) applied at assembly, target −20.0
            # LUFS. Measured 2026-09-16 on ONE real lesson sentence set through
            # synthesize() (ffmpeg ebur128), so the cast is mutually
            # consistent — mixing rag.5's set with this one is what the rag.5
            # brief warns against. Constant per voice so intra-voice dynamics
            # are preserved. Keyed by VOICE, not role: a stored lesson pins a
            # RESOLVED voice_id per phrase, so William (male-2 until rag.6)
            # stays at 0.9 even though he left the map — an unknown voice
            # returns 0.0, which would silently un-normalise legacy audio on
            # the next re-render.
            # The narrator (en-US-GuyNeural) speaks ENGLISH, so its level is
            # measured on English text and cannot come from this sentence set.
            # Unchanged from rag.5 at −0.9; do not re-derive it here.
            tts_voice_gain_db={
                "nb-NO-PernilleNeural": 1.2,
                "nb-NO-IselinNeural": -0.1,
                "en-US-EmmaMultilingualNeural": -1.8,
                "en-US-ShimmerTurboMultilingualNeural": 0.4,
                "nb-NO-FinnNeural": 0.8,
                "en-US-DerekMultilingualNeural": 0.2,
                "it-IT-GiuseppeMultilingualNeural": -0.7,
                "en-US-DustinMultilingualNeural": 0.9,
                "en-AU-WilliamMultilingualNeural": 0.9,
                "en-US-GuyNeural": -0.9,
            },
        ),
        preprocessor_factory=NorwegianPreprocessor,
        deck_name="0. 6000 Most Frequent Norwegian Words [Part 1]",
        vocab_notetype=NORWEGIAN_VOCAB,
        l2_scorer=score_norwegian_l2,
        lemmatizer_type="stanza",
        definite_form_fn=is_definite_form,
        lemma_plausible_fn=is_lemma_plausible,
        multiword_traps_fn=trapped_pairs,
        slow_word_fn=slow_norwegian_word,
        variant_separator=",",
        infinitive_marker="å",
        gender_articles={"Masc": "en", "Fem": "ei/en", "Neut": "et"},
        syllabifier_fn=syllabify_norwegian_word,
        planner_example=PlannerExample(
            language_code="no",
            day=5,
            title="At the bakery",
            focus="Ordering pastries and paying",
            collocations=("et br\u00f8d, takk", "hvor mye koster det?"),
            learning_objective="Order food and handle payment in simple exchanges.",
            story_guidance="A quick visit to a Bergen bakery; friendly small talk with the baker.",
        ),
        style_notes=_style_notes,
        function_words_path=Path(__file__).parent / "data" / "function_words.json",
        numbers_path=Path(__file__).parent / "data" / "numbers.json",
        wordfreq_lang="nb",
        breakdown_spans_fn=build_norwegian_breakdown_spans,
        alignment=AlignmentConfig(
            model_id=MODEL_ID,
            vowels=NORWEGIAN_VOWELS,
            aligner_factory=create_aligner,
            syllabify_fn=flat_syllables,
        ),
        a1_morphology=NORWEGIAN_A1_MORPHOLOGY,
        lexicon_factory=create_nst_lexicon,
        phoneme_planner_factory=create_phoneme_planner,
    ),
)
