"""The "TT Sentence" notetype as Anki itself reads it (tunatale-h0q3.55).

TT writes the notetype with raw SQL (``add_sentence_notetype``), and a note's
fields with ``sentence_note_fields``. Whether that is a cloze notetype Anki
accepts — and whether one note becomes one sibling card per blank plus the
whole line — is Anki's call, so this asks Anki:

- Check Database passes over the notetype and the note, and generates the
  cards; a normal-kind notetype would generate ONE card, so three proves the
  cloze kind took.
- Each card has exactly one active cloze (``class="cloze"``) and it is the
  card's own: ord N is cloze N+1. That is what the CSS keys the blank's gloss
  on, so if Anki marked it differently the gloss would never show.
- The whole line (cloze 1) wraps the blanks, so its front hides the whole line.
- Nothing from the back (the English, the glosses) reaches a front.

What this does NOT cover: the CSS taking effect in a browser or on AnkiDroid
(``:has()``), which the owner checks on the phone before the live migration;
TT's writer and sync (phase 2).
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from app.cards.sentence_notetype import FIELD_NAMES
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span
from app.srs.sentence_note import sentence_note_fields
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection

#: add_sentence_notetype takes max(now_ms, max id + 1); the fixture's notetype ids are small.
NOW_MS = 1790000000000
NOTE_ID = 1790000000500

_BASE = SentenceCard(
    lesson_id="wolff-1",
    line_index=4,
    speaker="Ínting",
    text="Nâa ba si Místir Abáya?",
    spoken="Nâa ba si Místir Abáya?",
    english="Is Mr. Abaya there?",
    glosses=(Gloss("ba", "question particle"),),
    cue=Cue(speaker="Inday", text="Únsa may átò?", spoken="Únsa may átò?", english="What can I do for you?"),
)
LINE = [
    _BASE.with_blank(blank_span(_BASE.text, "nâa"), "there (near you)"),
    _BASE.with_blank(blank_span(_BASE.text, "ba"), "yes/no question particle"),
    _BASE,
]


@pytest.mark.oracle
def test_one_note_renders_a_sibling_card_per_cloze(synthetic_collection: SyntheticCollection) -> None:
    fields = sentence_note_fields(LINE, cue_audio="cue.mp3", answer_audio="answer.mp3", source="Wolff 1 · line 4")
    assert len(fields) == len(FIELD_NAMES)
    synthetic_collection.add_note(NOTE_ID, "sentence-guid", fields, mid=NOW_MS)
    synthetic_collection.save()
    with sqlite3.connect(synthetic_collection.path) as conn:
        assert add_sentence_notetype(conn, now_ms=NOW_MS) == "created"
        assert conn.execute("SELECT id FROM notetypes WHERE id = ?", (NOW_MS,)).fetchone() is not None

    raw = run_oracle(
        synthetic_collection.path,
        [{"op": "check_database"}, {"op": "note_ords"}, {"op": "render_cards"}],
    ).raw()

    assert raw["check_database_0"]["ok"], raw["check_database_0"]["report"]
    sfld = fields[0]
    assert raw["note_ords_1"]["ords"][sfld] == [0, 1, 2]

    cards = raw["render_cards_2"]
    for ord_ in (0, 1, 2):
        card = cards[f"{sfld}/{ord_}"]
        for side in ("question", "answer"):
            active = re.findall(r'class="cloze" data-(?:cloze="[^"]*" data-)?ordinal="(\d+)"', card[side])
            assert active == [str(ord_ + 1)], (ord_, side, card[side])
        assert "Is Mr. Abaya there?" not in card["question"]
        assert "yes/no question particle" not in card["question"]
        assert "Is Mr. Abaya there?" in card["answer"]
        assert '<div class="tt-bg" data-c="3">' in card["answer"]

    whole_front = cards[f"{sfld}/0"]["question"]
    # Anki copies the hidden text into a data-cloze attribute; what shows is the rest.
    assert "Místir" not in re.sub(r'data-cloze="[^"]*"', "", whole_front)
    blank_front = cards[f"{sfld}/2"]["question"]
    assert "Nâa" in blank_front and "Místir Abáya?" in blank_front
