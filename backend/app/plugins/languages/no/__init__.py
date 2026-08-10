"""Norwegian language plugin."""

from pathlib import Path

from app.cards.vocab_notetype import NORWEGIAN_VOCAB
from app.languages import AlignmentConfig, LanguageConfig, PlannerExample, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.no.alignment import MODEL_ID, NORWEGIAN_VOWELS, create_aligner
from app.plugins.languages.no.morphology import is_definite_form
from app.plugins.languages.no.multiword import trapped_pairs
from app.plugins.languages.no.norwegian_breakdown import (
    build_norwegian_breakdown_spans,
    flat_syllables,
    slow_norwegian_word,
)
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
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                "female-1": "nb-NO-PernilleNeural",
                "female-2": "nb-NO-PernilleNeural",
                "male-1": "nb-NO-FinnNeural",
                "male-2": "nb-NO-FinnNeural",
                "female": "nb-NO-PernilleNeural",
                "male": "nb-NO-FinnNeural",
            },
        ),
        preprocessor_factory=NorwegianPreprocessor,
        deck_name="0. 6000 Most Frequent Norwegian Words [Part 1]",
        vocab_notetype=NORWEGIAN_VOCAB,
        lemmatizer_type="stanza",
        definite_form_fn=is_definite_form,
        multiword_traps_fn=trapped_pairs,
        slow_word_fn=slow_norwegian_word,
        variant_separator=",",
        infinitive_marker="å",
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
        wordfreq_lang="nb",
        breakdown_spans_fn=build_norwegian_breakdown_spans,
        alignment=AlignmentConfig(
            model_id=MODEL_ID,
            vowels=NORWEGIAN_VOWELS,
            aligner_factory=create_aligner,
            syllabify_fn=flat_syllables,
        ),
    ),
)
