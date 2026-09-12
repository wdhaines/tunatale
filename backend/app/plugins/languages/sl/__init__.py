"""Slovene language plugin."""

from pathlib import Path

from app.cards.vocab_notetype import SLOVENE_VOCAB
from app.languages import LanguageConfig, PlannerExample, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.sl.a1_morphology import SLOVENE_A1_MORPHOLOGY
from app.plugins.languages.sl.l2_scoring import score_slovene_l2
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor
from app.plugins.languages.sl.syllabify import syllabify_slovene_word

_style_notes = (Path(__file__).parent / "data" / "style.md").read_text(encoding="utf-8").strip()

register(
    "sl",
    LanguageConfig(
        language=Language(
            code="sl",
            name="Slovene",
            native_name="slovenščina",
            script="latin",
            tts_locale="sl-SI",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                "female-1": "sl-SI-PetraNeural",
                # Azure serves sl-SI with exactly TWO voices, Petra and Rok, so
                # two of the four dialogue roles cannot be filled natively at
                # all: every Slovene lesson put its second woman and second man
                # in the first one's voice. These two are Multilingual Neural
                # voices speaking Slovene under a <lang xml:lang="sl-SI">
                # wrapper, chosen by measurement on 2026-09-12 (tunatale-rag.4)
                # against the native pair as controls, 15 sentences of real
                # generated lesson text:
                #   Azure STT round-trip, wrapped: every candidate 0 errors in
                #   62 words, identical to Petra and Rok. Intelligibility does
                #   not discriminate, so loudness did.
                #   Integrated LUFS vs the same-gender native (pass within 1.0):
                #     Petra -17.6 | Emma -18.1 d0.5 PASS | Seraphina -19.1 d1.5
                #     | Ava -20.0 d2.4 | Vivienne -20.3 d2.7
                #     Rok   -18.8 | Florian -19.4 d0.6 PASS | William -20.7 d1.9
                # Exactly one candidate per slot passed both gates. Dragon HD
                # (the user's ear preference) was ruled out on cost: a separate
                # billing line at $22/1M with no free allowance, and
                # nondeterministic by design, which leaves no hash-based
                # pronunciation oracle.
                # ⚠️ Petra and Rok are themselves 1.2 LUFS apart, so Slovene
                # dialogue needs loudness normalisation regardless of this
                # change — these voices sit INSIDE that existing spread.
                # Takes effect for lessons generated from here on: a stored
                # lesson blob pins a resolved voice_id per phrase. There are no
                # stored Slovene lessons today, so nothing to backfill.
                "female-2": "en-US-EmmaMultilingualNeural",
                "male-1": "sl-SI-RokNeural",
                "male-2": "de-DE-FlorianMultilingualNeural",
                "female": "sl-SI-PetraNeural",
                "male": "sl-SI-RokNeural",
            },
        ),
        preprocessor_factory=SlovenePreprocessor,
        deck_name="1. Slovene",
        vocab_notetype=SLOVENE_VOCAB,
        l2_scorer=score_slovene_l2,
        lemmatizer_type="classla",
        morphology_profile="slavic",
        syllabifier_fn=syllabify_slovene_word,
        planner_example=PlannerExample(
            language_code="sl",
            day=5,
            title="At the bakery",
            focus="Ordering pastries and paying",
            collocations=("en kruh, prosim", "koliko stane?"),
            learning_objective="Order food and handle payment in simple exchanges.",
            story_guidance="A quick visit to a Ljubljana bakery; friendly small talk with the baker.",
        ),
        style_notes=_style_notes,
        function_words_path=Path(__file__).parent / "data" / "function_words.json",
        numbers_path=Path(__file__).parent / "data" / "numbers.json",
        wordfreq_lang="sl",
        a1_morphology=SLOVENE_A1_MORPHOLOGY,
    ),
)
