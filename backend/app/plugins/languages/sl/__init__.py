"""Slovene language plugin."""

from pathlib import Path

from app.cards.vocab_notetype import SLOVENE_VOCAB
from app.languages import LanguageConfig, register
from app.models.language import Language
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor
from app.plugins.languages.sl.syllabify import syllabify_slovene_word

_style_notes = (Path(__file__).parent / "data" / "style.md").read_text(encoding="utf-8").strip()

register(
    "sl",
    LanguageConfig(
        language=Language.slovene(),
        preprocessor_factory=SlovenePreprocessor,
        deck_name="1. Slovene",
        vocab_notetype=SLOVENE_VOCAB,
        lemmatizer_type="classla",
        morphology_profile="slavic",
        syllabifier_fn=syllabify_slovene_word,
        style_notes=_style_notes,
        function_words_path=Path(__file__).parent / "data" / "function_words.json",
    ),
)
