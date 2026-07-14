"""English gloss plugin — always present, no preprocessor, no TT deck."""

from app.languages import LanguageConfig, register
from app.models.language import Language

register(
    "en",
    LanguageConfig(
        language=Language.english(),
        preprocessor_factory=None,
        deck_name=None,
        vocab_notetype=None,
    ),
)
