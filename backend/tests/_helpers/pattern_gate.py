"""A deck's evidence for the affix pace gate (``app.generation.pattern_gate``), for tests.

A word pattern's evidence is the sentence cards whose blank says one of its
forms (tunatale-ve4p.36), recognised by the language's lemmatizer. The tests'
lemmatizer is a stub that knows four forms of each Cebuano word pattern and
nothing else: install it with :func:`install_gate_lemmatizer`, then add cards
with :func:`add_sentence_card` / :func:`pass_word_pattern`.
"""

from __future__ import annotations

from itertools import count

import app.generation.pattern_gate as pattern_gate
from app.common.guid import compute_guid
from app.models.srs_item import Direction, RevlogRow
from app.models.syntactic_unit import SyntacticUnit
from app.srs.function_words import make_cloze_text
from tests._helpers.lemmatizer import StubLemmatizer

LEARN, REVIEW = 0, 1
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
PASS = ((LEARN, GOOD), (LEARN, GOOD), (REVIEW, GOOD))

# Four forms of each word pattern, two roots in both cells.
FORMS = {
    "mo-mi": ("molakaw", "milakaw", "moinom", "miinom"),
    "mag-nag": ("maglakaw", "naglakaw", "magluto", "nagluto"),
    "ma-na": ("matulog", "natulog", "malipay", "nalipay"),
    "maka-naka": ("makakita", "nakakita", "makaadto", "nakaadto"),
}
_PREFIXES = ("maka", "naka", "mag", "nag", "ma", "na", "mo", "mi")

_ms = count(1_791_500_000_000, 60_000)
_line = count(1)


def install_gate_lemmatizer(monkeypatch) -> StubLemmatizer:
    """Give the gate a lemmatizer that reads each of :data:`FORMS` as a VERB on its root."""
    stub = StubLemmatizer()
    for forms in FORMS.values():
        for form in forms:
            prefix = next(p for p in _PREFIXES if form.startswith(p))
            stub.set_analysis(form, form[len(prefix) :], upos="VERB")
    monkeypatch.setattr(pattern_gate, "get_lemmatizer", lambda code: stub)
    return stub


def grade(srs_db, collocation_id: int, grades) -> None:
    """Append *grades*, ``(review_kind, button)`` pairs, to the card's production revlog."""
    for kind, button in grades:
        srs_db.append_revlog(
            RevlogRow(
                id=next(_ms),
                collocation_id=collocation_id,
                direction=Direction.PRODUCTION,
                button_chosen=button,
                interval=1,
                last_interval=0,
                factor=2500,
                taken_millis=5000,
                review_kind=kind,
            )
        )


def add_sentence_card(srs_db, text: str, *, blank: str = "", key: str = "", grades=PASS) -> None:
    """A sentence card saying *text*, its blank *blank* (the whole line by default), graded *grades*."""
    key = key or f"sent:wolff-99:{next(_line)}"
    unit = SyntacticUnit(
        text=text,
        translation="",
        word_count=len(text.split()),
        difficulty=1,
        source="test",
        card_type="cloze",
        disambig_key=key,
        source_sentence=make_cloze_text(blank, text) if blank else f"{{{{c1::{text}}}}}",
    )
    srs_db.add_collocation(unit, language_code="ceb")
    grade(srs_db, srs_db.get_collocation_id_by_guid(compute_guid(text, "ceb", key)), grades)


def pass_word_pattern(srs_db, pattern: str, n: int = 4, *, grades=PASS) -> None:
    """*n* sentence cards, each saying one of *pattern*'s forms, each graded *grades*."""
    for form in FORMS[pattern][:n]:
        add_sentence_card(srs_db, f"{form.capitalize()} ko.", grades=grades)
