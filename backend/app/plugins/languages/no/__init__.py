"""Norwegian language plugin."""

from app.anki.vocab_notetype import NORWEGIAN_VOCAB
from app.languages import LanguageConfig, register
from app.models.language import Language
from app.plugins.languages.no.preprocessor import NorwegianPreprocessor

register(
    "no",
    LanguageConfig(
        language=Language.norwegian(),
        preprocessor_factory=NorwegianPreprocessor,
        deck_name="0. 6000 Most Frequent Norwegian Words [Part 1]",
        vocab_notetype=NORWEGIAN_VOCAB,
        lemmatizer_type="stanza",
        compound_word_breakdown=True,
        variant_separator=",",
        syllabifier="norwegian",
    ),
)
