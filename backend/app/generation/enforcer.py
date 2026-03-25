"""Content enforcer: two-pass L1 → L2 replacement using SRS database.

The replacement dictionary is fully dynamic — built from SRS collocations'
translation fields. No hardcoded vocabulary.
"""

from __future__ import annotations

import logging
import re

from app.srs.database import SRSDatabase

logger = logging.getLogger(__name__)


class ContentEnforcer:
    """Replaces L1 words/phrases in generated text with their L2 equivalents.

    Uses the SRS database to build the replacement dictionary dynamically.
    Matches are word-boundary-aware and case-insensitive.
    """

    def __init__(self, srs_db: SRSDatabase) -> None:
        self._db = srs_db
        self._cached_patterns: list[tuple[re.Pattern, str]] | None = None

    def get_replacement_dict(self) -> dict[str, str]:
        """Build {L1_translation → L2_text} mapping from the SRS database."""
        items = self._db.get_new_collocations(limit=10000)
        due_items = self._db.get_due_collocations(__import__("datetime").date.today())
        all_items = {i.syntactic_unit.text: i for i in items + due_items}

        replacements: dict[str, str] = {}
        for item in all_items.values():
            translation = item.syntactic_unit.translation.strip().lower()
            l2_text = item.syntactic_unit.text
            if translation:
                replacements[translation] = l2_text
        return replacements

    def enforce(self, text: str, day_number: int | None = None) -> str:
        """Replace known L1 phrases in text with their L2 equivalents.

        Args:
            text: Input text (story dialogue) that may contain L1 words.
            day_number: Optional day number for violation recording.

        Returns:
            Text with known L1 phrases replaced by their L2 equivalents.
        """
        if not text:
            return text

        if self._cached_patterns is None:
            replacements = self.get_replacement_dict()
            if not replacements:
                return text
            self._cached_patterns = [
                (re.compile(r"(?<!\w)" + re.escape(l1) + r"(?!\w)", re.IGNORECASE), l2)
                for l1, l2 in sorted(replacements.items(), key=lambda x: -len(x[0]))
            ]

        if not self._cached_patterns:
            return text

        result = text
        for pattern, l2_phrase in self._cached_patterns:
            new_result = pattern.sub(l2_phrase, result)
            if new_result != result:
                logger.debug("Enforcer replaced → %r", l2_phrase)
                result = new_result

        return result
