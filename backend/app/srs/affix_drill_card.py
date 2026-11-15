"""The affix drill card: one rotating card per affix pattern, from a checked chain (tunatale-5i1p.6).

Brief: ``.beads-tasks/briefs/brief-ve4p-affix-drill-card-2026-11.md``. The card
is a substitution drill card (:class:`app.srs.drill_card.DrillCard`) whose
steps come from the language plugin's ``pattern_chain`` hook instead of a
story's drill: "swap one thing", the sentence before and one cue. It adds no
card model and no sync code; the drill-card plumbing (review payload, Grade
plan, the "TT Sentence" note and its rotation) serves it as it is.

* **Key** ``sent:affix-<pattern>:drill-1``: one card per pattern, ever. A
  later lesson of the same pattern mints nothing, even when its chain would
  open on another root.
* **Frozen at mint:** the chain lives in the row's ``card_data``. A later edit
  to the plugin's tables changes no card.
* **Not a course card:** its source is the drill card's own (``user``), so the
  Anki writer puts it at the front of the new queue, like every drill card
  (tunatale-5i1p.8), not in the course's first-in-first-out region.

Which roots it says is the plugin's call; this module tells it which roots the
studied lesson's drill voiced and which roots count as understood: the learner's
recognition card is in review, and no listened drill of the pattern voiced it.
"""

from __future__ import annotations

from app.generation.drill_lines import understood
from app.generation.grammar_lesson import find_pattern
from app.languages import get_a1_morphology
from app.models.lesson import Lesson
from app.models.srs_item import Direction
from app.srs.drill_card import DrillCard, DrillStep
from app.srs.sentence_card import SENTENCE_KEY_PREFIX

_DRILL = 1


def affix_card_set(pattern_key: str) -> str:
    """The card set an affix drill card is filed under: ``affix-<pattern>``."""
    return f"affix-{pattern_key}"


def _recorded(lesson: Lesson | None) -> dict | None:
    return (lesson.generation_metadata or {}).get("affix_drill") if lesson is not None else None


def _voiced(recorded: dict) -> tuple[str, ...]:
    return (*recorded["roots"], *recorded["new_roots"])


def affix_card_key(lesson: Lesson) -> str | None:
    """The key of the affix drill card *lesson*'s drill would mint, or ``None`` when it would mint none."""
    recorded = _recorded(lesson)
    bundle = get_a1_morphology(lesson.language_code)
    if recorded is None or bundle is None or bundle.pattern_chain is None:
        return None
    if find_pattern(lesson.language_code, recorded["pattern"]) is None:
        return None
    return f"{SENTENCE_KEY_PREFIX}{affix_card_set(recorded['pattern'])}:drill-{_DRILL}"


def affix_drill_card(db, store, lesson: Lesson) -> DrillCard | None:
    """The affix drill card a study of *lesson* would mint, or ``None``. Reads only."""
    if affix_card_key(lesson) is None:
        return None
    recorded = _recorded(lesson)
    code = lesson.language_code
    pattern = find_pattern(code, recorded["pattern"])
    heard = set(_voiced(recorded))
    for listened in db.get_listened_lessons():
        other = _recorded(store.get_readable_content(listened["lesson_id"]))
        if other is not None and other["pattern"] == pattern.key:
            heard.update(_voiced(other))

    def known(root: str) -> bool:
        return root not in heard and any(
            understood(item.directions.get(Direction.RECOGNITION))
            for _, item in db.get_collocations_by_lemma_with_id(root)
        )

    chain = get_a1_morphology(code).pattern_chain(pattern, _voiced(recorded), known)
    if chain is None:
        return None
    return DrillCard(
        card_set=affix_card_set(pattern.key),
        drill=_DRILL,
        kind="substitution",
        model=chain.model,
        steps=tuple(DrillStep(step.cue, step.answer, step.english) for step in chain.steps),
    )


def mint_affix_drill_card(db, store, lesson: Lesson) -> tuple[int, DrillCard] | None:
    """Mint the affix drill card *lesson*'s pattern takes, once. Returns ``(row id, card)``, or ``None``.

    ``None`` when the deck already has the pattern's card (by key, not by text:
    another lesson's chain would open on another sentence), or there is none to
    mint. Written as the card-adding contract asks (``.claude/rules/anki-sync.md``):
    ``add_collocation``, NEW, production only, no Anki ids. The next sync makes its note.
    """
    key = affix_card_key(lesson)
    if key is None or db.list_sibling_rows(key):
        return None
    card = affix_drill_card(db, store, lesson)
    if card is None:
        return None
    db.add_collocation(card.unit(), language_code=lesson.language_code)
    [(_, _, cid)] = db.list_sibling_rows(key)
    return cid, card
