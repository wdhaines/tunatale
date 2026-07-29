"""Norwegian language plugin."""

from pathlib import Path

from app.cards.vocab_notetype import NORWEGIAN_VOCAB
from app.languages import AlignmentConfig, LanguageConfig, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.no.alignment import MODEL_ID, NORWEGIAN_VOWELS, create_aligner
from app.plugins.languages.no.norwegian_breakdown import (
    build_norwegian_breakdown,
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
        breakdown_fn=build_norwegian_breakdown,
        slow_word_fn=slow_norwegian_word,
        variant_separator=",",
        syllabifier_fn=syllabify_norwegian_word,
        style_notes=_style_notes,
        function_words_path=Path(__file__).parent / "data" / "function_words.json",
        breakdown_spans_fn=build_norwegian_breakdown_spans,
        alignment=AlignmentConfig(
            model_id=MODEL_ID,
            vowels=NORWEGIAN_VOWELS,
            aligner_factory=create_aligner,
            syllabify_fn=flat_syllables,
        ),
    ),
)
