"""A lesson's own card list, built into the units the mint writes (tunatale-h0q3.43).

A Story JSON may carry ``cards``: ``{"set": <slug>, "items": [...]}``, the
lesson's cards in the order they should reach the deck. The author orders them
(n+1: the words a sentence uses before the sentence). Format and its rules:
``tests/test_lesson_card_list.py`` (module docstring); ``lesson_io.validate_story``
enforces them.

Every card built here has ``source == COURSE_SOURCE``. That is what the Anki
writer reads to place a course card first-in-first-out, behind earlier course
cards and ahead of the frequency deck, instead of at the very front where TT's
other additions go.

A ``drill`` item names one of the story's own ``drills`` by its 1-based place
and makes drill cards (``app.srs.drill_card``, tunatale-5i1p.4): one rotating
card, or one per step for a question-and-answer drill.

Card keys use the list's ``set``, never the lesson id: a re-import mints a new
lesson id, and must not mint the same cards twice.

Nothing here writes anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.models.language import Language
from app.models.syntactic_unit import SyntacticUnit
from app.srs.drill_card import drill_cards
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span

COURSE_SOURCE = "course"

_SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_SENTENCE_FIELDS = ("speaker", "role", "text", "spoken", "english")


@dataclass(frozen=True)
class LessonCard:
    """One card to mint, with the voice roles its audio needs (empty for a word)."""

    unit: SyntacticUnit
    role: str = ""  # the answer's voice role
    cue_role: str = ""  # the cue's voice role; "" when the card has no cue


def lesson_cards(cards: dict, drills: list[dict] | tuple = ()) -> list[LessonCard]:
    """The cards a VALID ``cards`` block makes, in mint order.

    *drills* are the story's own ``drills``, which a ``drill`` item names by its
    1-based place (tunatale-5i1p.4): one rotating card, or one per step for a
    question-and-answer drill.
    """
    built: list[LessonCard] = []
    for item in cards["items"]:
        if item["kind"] == "drill":
            n = item["drill"]
            for card in drill_cards(cards["set"], n, drills[n - 1]):
                built.append(LessonCard(unit=replace(card.unit(), source=COURSE_SOURCE), role=item["role"]))
            continue
        if item["kind"] == "word":
            form = item["form"]
            built.append(
                LessonCard(
                    unit=SyntacticUnit(
                        text=form,
                        translation=item["translation"],
                        word_count=1,
                        difficulty=1,
                        source=COURSE_SOURCE,
                        lemma=item.get("lemma") or form,
                        card_type="vocab",
                    )
                )
            )
            continue
        cue = item["cue"]
        base = SentenceCard(
            lesson_id=cards["set"],
            line_index=item["line"],
            speaker=item["speaker"],
            text=item["text"],
            spoken=item["spoken"],
            english=item["english"],
            glosses=_gloss_tuple(item["glosses"]),
            cue=_cue(cue),
            picture=item.get("picture", ""),
        )
        role = item["role"]
        cue_role = cue["role"] if cue is not None else ""
        # One unknown at a time: the blanks come before the whole sentence.
        for blank in item["blanks"]:
            blanked = base.with_blank(blank_span(base.text, blank["form"]), blank["gloss"])
            built.append(LessonCard(unit=replace(blanked.unit(), source=COURSE_SOURCE), role=role, cue_role=cue_role))
        if item["whole"]:
            built.append(LessonCard(unit=replace(base.unit(), source=COURSE_SOURCE), role=role, cue_role=cue_role))
    return built


def _gloss_tuple(glosses: list[dict]) -> tuple[Gloss, ...]:
    return tuple(Gloss(g["word"], g["gloss"]) for g in glosses)


def _cue(cue: dict | None) -> Cue | None:
    if cue is None:
        return None
    return Cue(
        speaker=cue["speaker"],
        text=cue["text"],
        spoken=cue["spoken"],
        english=cue["english"],
        glosses=_gloss_tuple(cue["glosses"]),
    )


def validate_cards(cards: object, *, line_texts: set[str], language: Language | None, drill_count: int = 0) -> None:
    """Refuse a malformed ``cards`` block with a message naming the path (``cards.items[2].role ...``).

    *line_texts* are the story's scene line texts (a card's ``spoken`` must be one);
    *drill_count* is how many ``drills`` the story has (a ``drill`` item names one);
    with a *language*, every ``role`` must be a key of its ``tts_voice_map``.
    Called by ``lesson_io.validate_story`` when the story has a ``cards`` key.
    """
    if not isinstance(cards, dict):
        raise ValueError("cards must be an object")
    card_set = cards.get("set")
    if not isinstance(card_set, str) or not _SLUG.fullmatch(card_set):
        raise ValueError(f"cards.set must be a lowercase slug (a-z, 0-9, hyphens), got {card_set!r}")
    items = cards.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("cards.items must be a non-empty list")
    seen_forms: set[str] = set()
    seen_lines: set[int] = set()
    seen_drills: set[int] = set()
    for i, item in enumerate(items):
        path = f"cards.items[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{path} must be an object")
        kind = item.get("kind")
        if kind not in ("word", "sentence", "drill"):
            raise ValueError(f"{path}.kind must be 'word', 'sentence' or 'drill', got {kind!r}")
        if kind == "word":
            _validate_word(item, path, seen_forms)
        elif kind == "drill":
            _validate_drill(item, path, language, drill_count, seen_drills)
        else:
            _validate_sentence(item, path, line_texts, language, seen_lines)


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_strings(entry: dict, fields: tuple[str, ...], path: str) -> None:
    for field in fields:
        if not _non_empty_str(entry.get(field)):
            raise ValueError(f"{path}.{field} must be a non-empty string")


def _validate_role(role: str, path: str, language: Language | None) -> None:
    if language is not None and role not in language.tts_voice_map:
        raise ValueError(f"{path} {role!r} is not in the {language.code} voice map")


def _validate_spoken(spoken: str, path: str, line_texts: set[str]) -> None:
    if spoken not in line_texts:
        raise ValueError(f"{path} {spoken!r} is not a line of this story")


def _validate_glosses(glosses: object, path: str) -> None:
    if not isinstance(glosses, list) or any(
        not isinstance(g, dict) or not _non_empty_str(g.get("word")) or not _non_empty_str(g.get("gloss"))
        for g in glosses
    ):
        raise ValueError(f"{path} must be a list of word/gloss objects with non-empty strings")


def _validate_word(item: dict, path: str, seen_forms: set[str]) -> None:
    _validate_strings(item, ("form", "translation"), path)
    if "lemma" in item and not _non_empty_str(item["lemma"]):
        raise ValueError(f"{path}.lemma must be a non-empty string")
    form = item["form"]
    if form in seen_forms:
        raise ValueError(f"{path}.form {form!r} is already listed")
    seen_forms.add(form)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _validate_drill(item: dict, path: str, language: Language | None, drill_count: int, seen: set[int]) -> None:
    n = item.get("drill")
    if not _positive_int(n):
        raise ValueError(f"{path}.drill must be a positive integer")
    if n > drill_count:
        raise ValueError(f"{path}.drill {n} is not a drill of this story (it has {drill_count})")
    if n in seen:
        raise ValueError(f"{path}.drill {n} is already listed")
    seen.add(n)
    _validate_strings(item, ("role",), path)
    _validate_role(item["role"], f"{path}.role", language)


def _validate_sentence(
    item: dict,
    path: str,
    line_texts: set[str],
    language: Language | None,
    seen_lines: set[int],
) -> None:
    if "cue" not in item:
        raise ValueError(f"{path} is missing required field 'cue'")
    line = item.get("line")
    if not _positive_int(line):
        raise ValueError(f"{path}.line must be a positive integer")
    if line in seen_lines:
        raise ValueError(f"{path}.line {line} is already listed")
    seen_lines.add(line)
    _validate_strings(item, _SENTENCE_FIELDS, path)
    _validate_role(item["role"], f"{path}.role", language)
    _validate_spoken(item["spoken"], f"{path}.spoken", line_texts)
    _validate_glosses(item.get("glosses"), f"{path}.glosses")
    cue = item["cue"]
    if cue is not None and not isinstance(cue, dict):
        raise ValueError(f"{path}.cue must be null or an object")
    if cue is not None:
        _validate_strings(cue, _SENTENCE_FIELDS, f"{path}.cue")
        _validate_role(cue["role"], f"{path}.cue.role", language)
        _validate_spoken(cue["spoken"], f"{path}.cue.spoken", line_texts)
        _validate_glosses(cue.get("glosses"), f"{path}.cue.glosses")
    picture = item.get("picture")
    if picture is not None and not isinstance(picture, str):
        raise ValueError(f"{path}.picture must be a string")
    whole = item.get("whole")
    if not isinstance(whole, bool):
        raise ValueError(f"{path}.whole must be true or false")
    blanks = item.get("blanks")
    if not isinstance(blanks, list):
        raise ValueError(f"{path}.blanks must be a list")
    text = item["text"]
    spans: set[tuple[int, int]] = set()
    for j, blank in enumerate(blanks):
        blank_path = f"{path}.blanks[{j}]"
        if not isinstance(blank, dict) or not _non_empty_str(blank.get("form")):
            raise ValueError(f"{blank_path}.form must be a non-empty string")
        if not _non_empty_str(blank.get("gloss")):
            raise ValueError(f"{blank_path}.gloss must be a non-empty string")
        form = blank["form"]
        try:
            span = blank_span(text, form)
        except ValueError:
            raise ValueError(f"{blank_path}.form {form!r} is not in {text!r}") from None
        if span in spans:
            raise ValueError(f"{blank_path}.form {form!r} is already a blank")
        spans.add(span)
    if not whole and not blanks:
        raise ValueError(f"{path} makes no card")
