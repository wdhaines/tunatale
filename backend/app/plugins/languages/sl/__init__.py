"""Slovene language plugin."""

from app.anki.vocab_notetype import SLOVENE_VOCAB
from app.languages import LanguageConfig, register
from app.models.language import Language
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor

register(
    "sl",
    LanguageConfig(
        language=Language.slovene(),
        preprocessor_factory=SlovenePreprocessor,
        deck_name="1. Slovene",
        vocab_notetype=SLOVENE_VOCAB,
        lemmatizer_type="classla",
        morphology_profile="slavic",
        syllabifier="slovene",
    ),
)
