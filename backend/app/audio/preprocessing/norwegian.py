"""Norwegian text preprocessor for TTS synthesis (pass-through)."""

from app.models.lesson import SectionType


class NorwegianPreprocessor:
    """Norwegian text preprocessor (pass-through; reserved for future transforms)."""

    def preprocess(self, text: str, section_type: SectionType) -> str:
        return text
