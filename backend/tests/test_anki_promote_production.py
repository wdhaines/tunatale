"""The just-in-time promotion phase (tunatale-qf6.2, piece C).

When a word's recognition card graduates, its production counterpart is minted
— one card, one image fetch, at that moment. This is the phase in
``run_full_sync`` that does it, over both populations at once:

- the **forward trigger** — a word that graduated since the last sync;
- the **backlog drain** — the ~1550 Norwegian words that were already in review
  when the recognition-only notetype gained its production capability. A pure
  forward trigger would never serve them, which is why one code path covers
  both: "has a graduated recognition and no production direction" describes
  them identically.

The pacing is deliberate and settled with the user (2026-08-15): 10 per sync.
At a 3 new-cards/day introduction cap there is nothing to gain from minting
faster than the learner can meet the cards, and every mint costs an image
fetch. The budget is on *work done*, not rows read — a word this phase cannot
serve is skipped cheaply rather than wedging the drain behind it.

Two populations it must NOT touch, both live in the real collections today:

- a note whose notetype has no ``Production`` template. This is the Slovene
  control: all 77 of its vocab rows lacking production are phonics notes on
  ``Basic`` and clozes on ``Cloze``, neither of which can carry a second card.
  Minting there would put a card at an ord with no template — an orphan
  Check Database deletes. `card_type` is NOT the discriminator (those 77 rows
  are stored as ``vocab``); the collection is.
- a word whose image search comes back empty. Rule 3 of the mint: an ord=1 card
  with a blank ``Image`` is an *empty card* to Anki. Those words route to the
  **cloze fallback** (piece D) instead, built from the note's own example
  sentence — as do closed-class words, before any fetch is spent on them. What
  neither shape can serve is counted as ``unservable`` and left for the LLM
  tier, which is not built.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from hashlib import sha256

import pytest

from app.cards.field_map import get_profile
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync import sync as sync_mod
from app.plugins.anki_sync.add_production_template import IMAGE_FIELD, add_production_template
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase

SEED_MID = 1694414741634
BASIC_MID = 1694414741999
DECK_ID = 17
DECK_NAME = "Imported Deck"
LANG = "no"

SEED_NOTETYPE = "6000 Most Frequent Norwegian Words"
BASIC_NOTETYPE = "Basic"

SEED_FIELDS = (
    "Frequency index",
    "Norwegian word",
    "Word class",
    "Article",
    "IPA",
    "English translation",
    "Inflections",
    "Example sentences",
)
CLOZE_MID = 1694414741777

_SCHEMA = """
    CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER,
        usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
    CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
    CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
        lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
    CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
        lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
    CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
    CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
    CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
        PRIMARY KEY (ntid, ord));
    CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB);
"""


#: The deck's own inflection table, in the shape the real field carries it:
#: grammar labels in <thead>, forms in <tbody> cells, some with an article.
NOUN_TABLE = (
    '<table class="tg"><thead><tr><th>entall</th><th>flertall</th></tr></thead>'
    "<tbody><tr><td>en&nbsp;valp</td><td>valpen</td><td>valper</td><td>valpene</td></tr></tbody></table>"
)

#: What the phase names the picture it fetches for "house": the gloss stem plus
#: the bytes' hash, so two words glossed alike cannot overwrite each other.
IMG_FILENAME = f"img_house_{sha256(b'IMGDATA').hexdigest()[:8]}.jpg"


@dataclass
class _Media:
    """What the peer-sync media generator returns (app.api.anki._build_media_fn)."""

    image_bytes: bytes | None = b"IMGDATA"
    image_ext: str | None = "jpg"
    image_status: str | None = "ok"
    audio_bytes: bytes | None = None
    audio_source: str | None = None


class _MediaFn:
    """A stand-in for the media generator — the network boundary, passed in.

    Records its calls so a test can assert the phase did *not* fetch (the
    reuse-existing-image case) without patching anything.
    """

    def __init__(self, media: _Media | None = None) -> None:
        self._media = _Media() if media is None else media
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, word, english, *, used_image_urls, source_sentence="", grammar=""):
        self.calls.append((word, english))
        return self._media


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute("INSERT INTO decks VALUES (?, ?, 50, 0, NULL)", (DECK_ID, DECK_NAME))

    conn.execute("INSERT INTO notetypes VALUES (?, ?, 50, 0, X'')", (SEED_MID, SEED_NOTETYPE))
    for ord_, name in enumerate(SEED_FIELDS):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (SEED_MID, ord_, name))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Card 1', 50, 0, X'')", (SEED_MID,))

    # The Slovene-control shape: a single-template notetype that can never carry
    # a production card, in the same deck.
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 50, 0, X'')", (BASIC_MID, BASIC_NOTETYPE))
    for ord_, name in enumerate(("Front", "Back")):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (BASIC_MID, ord_, name))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Card 1', 50, 0, X'')", (BASIC_MID,))

    # Anki's built-in Cloze notetype: where a word that cannot be imaged lands.
    conn.execute("INSERT INTO notetypes VALUES (?, 'Cloze', 50, 0, X'')", (CLOZE_MID,))
    for ord_, name in enumerate(("Text", "Back Extra")):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (CLOZE_MID, ord_, name))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Cloze', 50, 0, X'')", (CLOZE_MID,))

    profile = get_profile(SEED_NOTETYPE)
    assert profile is not None
    add_production_template(conn, SEED_NOTETYPE, profile)
    conn.commit()
    return conn


def _add_note(
    conn: sqlite3.Connection,
    note_id: int,
    word: str,
    english: str,
    *,
    mid: int = SEED_MID,
    word_class: str = "noun",
    examples: str = "",
    inflections: str = "",
) -> int:
    """Insert a note plus its recognition card; returns the card id."""
    names = [r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid = ? ORDER BY ord", (mid,))]
    parts = [""] * len(names)
    if mid == SEED_MID:
        for name, value in (
            ("Norwegian word", word),
            ("English translation", english),
            ("Word class", word_class),
            ("Example sentences", examples),
            ("Inflections", inflections),
        ):
            parts[names.index(name)] = value
    else:
        parts[0], parts[1] = word, english
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 100, 7, '', ?, ?, 0, 0, '')",
        (note_id, f"g-{word}", mid, "\x1f".join(parts), word),
    )
    card_id = note_id * 10
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 0, 100, 7, 2, 2, 900, 30, 2500, 9, 0, 0, 0, 0, 0, '')",
        (card_id, note_id, DECK_ID),
    )
    conn.commit()
    return card_id


def _add_word(
    db: SRSDatabase,
    word: str,
    english: str,
    *,
    note_id: int,
    card_id: int,
    state: SRSState = SRSState.REVIEW,
    with_production: bool = False,
    disambig: str = "noun",
    last_review: str = "2026-08-01T12:00:00+00:00",
) -> int:
    """Seed the TT side: a collocation with a recognition direction, optionally production."""
    from datetime import datetime

    unit = SyntacticUnit(
        text=word, translation=english, word_count=1, difficulty=1, source="anki", frequency=0, disambig_key=disambig
    )
    directions = {
        Direction.RECOGNITION: DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at_rollover_utc(anki_today()),
            state=state,
            reps=9 if state is SRSState.REVIEW else 0,
            anki_card_id=card_id,
            last_review=datetime.fromisoformat(last_review),
        )
    }
    if with_production:
        directions[Direction.PRODUCTION] = DirectionState(
            direction=Direction.PRODUCTION,
            due_at=due_at_rollover_utc(anki_today()),
            state=SRSState.NEW,
            anki_card_id=card_id + 1,
        )
    return db.upsert_by_guid(unit, LANG, directions, anki_note_id=note_id)


def _make_sync(conn: sqlite3.Connection, db: SRSDatabase, anki_media_dir=None) -> AnkiSync:
    return AnkiSync(
        db=db,
        _reader=OfflineReader(conn, DECK_NAME, language_code=LANG),
        _writer=OfflineWriter(conn, media_dir=anki_media_dir),
    )


def _cloze_id(db: SRSDatabase, lemma: str) -> int:
    """The cloze collocation for *lemma*.

    Not ``get_collocation_by_lemma_with_id``: the word's vocab row carries the
    same lemma, so that lookup is ambiguous and silently returns the wrong one.
    """
    with db._get_conn() as conn:
        row = conn.execute("SELECT id FROM collocations WHERE card_type = 'cloze' AND lemma = ?", (lemma,)).fetchone()
    assert row is not None, f"no cloze collocation for {lemma!r}"
    return row["id"]


def _cloze_unit(db: SRSDatabase, lemma: str) -> SyntacticUnit:
    return db.get_collocation_by_id(_cloze_id(db, lemma))[1].syntactic_unit


def _prod_cards(conn: sqlite3.Connection, note_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM cards WHERE nid = ? AND ord > 0", (note_id,)).fetchall()


def _image_field(conn: sqlite3.Connection, note_id: int) -> str:
    row = conn.execute("SELECT flds, mid FROM notes WHERE id = ?", (note_id,)).fetchone()
    names = [r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid = ? ORDER BY ord", (row["mid"],))]
    return row["flds"].split("\x1f")[names.index(IMAGE_FIELD)]


class TestPromoteProductionCards:
    async def test_mints_a_production_card_for_a_graduated_word(self, tmp_path) -> None:
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "hus", "house", note_id=1000, card_id=card_id)
        anki_media = tmp_path / "collection.media"
        anki_media.mkdir()
        media_fn = _MediaFn()

        report = await _make_sync(conn, db, anki_media).promote_production_cards(_media_fn=media_fn)

        assert (report.minted, report.adopted, report.unservable, report.no_template) == (1, 0, 0, 0)
        assert report.awaiting == 1

        # One production card in Anki, in the recognition card's deck, fronted by
        # the image that was fetched for it — never a blank Image (mint rule 3).
        cards = _prod_cards(conn, 1000)
        assert len(cards) == 1
        assert cards[0]["did"] == DECK_ID
        assert _image_field(conn, 1000) == f'<img src="{IMG_FILENAME}">'

        # ...and one production direction in TT, linked to that card. No second
        # collocation: the word already had one.
        assert db.count_collocations() == 1
        prod = db.get_collocation_by_id(coll_id)[1].directions[Direction.PRODUCTION]
        assert prod.state is SRSState.NEW
        assert prod.anki_card_id == cards[0]["id"]
        assert prod.anki_due == cards[0]["due"]

        # The bytes land in both media stores, as sync_create_new does it.
        assert (anki_media / IMG_FILENAME).read_bytes() == b"IMGDATA"
        assert (sync_mod._MEDIA_DIR / IMG_FILENAME).read_bytes() == b"IMGDATA"
        assert db.get_image_filename(coll_id) == IMG_FILENAME

    async def test_two_words_sharing_a_gloss_keep_their_own_pictures(self, tmp_path) -> None:
        """The filename carries the bytes' hash, so the second fetch cannot
        overwrite the first word's picture in place — a real hazard at 1550
        words, where a shared English gloss is common."""
        conn = _make_conn()
        db = SRSDatabase(":memory:")
        for i, word in enumerate(("beslutning", "avgjørelse")):
            card_id = _add_note(conn, 1000 + i, word, "decision")
            _add_word(db, word, "decision", note_id=1000 + i, card_id=card_id)
        anki_media = tmp_path / "collection.media"
        anki_media.mkdir()

        class _PerWordMedia(_MediaFn):
            async def __call__(self, word, english, **kwargs):
                self.calls.append((word, english))
                return _Media(image_bytes=f"IMG-{word}".encode())

        await _make_sync(conn, db, anki_media).promote_production_cards(_media_fn=_PerWordMedia())

        first, second = _image_field(conn, 1000), _image_field(conn, 1001)
        assert first != second, (first, second)
        assert (sync_mod._MEDIA_DIR / first.removeprefix('<img src="').removesuffix('">')).read_bytes() == (
            b"IMG-beslutning"
        )

    async def test_routes_a_word_it_cannot_image_to_a_cloze(self) -> None:
        """Piece D: the fallback, built from the note's own fields."""
        conn = _make_conn()
        card_id = _add_note(
            conn,
            1000,
            "valp",
            "puppy",
            examples="Valpen er veldig leken (<i>The puppy is very playful</i>)",
            inflections=NOUN_TABLE,
        )
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "valp", "puppy", note_id=1000, card_id=card_id)

        report = await _make_sync(conn, db).promote_production_cards(
            _media_fn=_MediaFn(_Media(image_bytes=None, image_status="no_results"))
        )

        assert (report.minted, report.clozed, report.unservable) == (0, 1, 0)
        # No production card and no image on the vocab note — the word went the
        # other way entirely.
        assert _prod_cards(conn, 1000) == []
        assert _image_field(conn, 1000) == ""
        assert db.get_collocation_by_id(coll_id)[1].directions.keys() == {Direction.RECOGNITION}

        # A separate cloze collocation, blanking the surface as the sentence
        # spells it — not the dictionary headword, which is not in the sentence.
        unit = _cloze_unit(db, "valp")
        assert unit.card_type == "cloze"
        assert unit.source_sentence == "{{c1::Valpen}} er veldig leken"
        assert unit.source_sentence_translation == "The puppy is very playful"
        assert unit.translation == "puppy"

    async def test_routes_a_closed_class_word_to_a_cloze_without_spending_a_fetch(self) -> None:
        """`Word class` is the first filter: a picture of a preposition is noise.

        The deck's POS label resolves through the language registry, so this is
        the same closed-class test `/listen` applies — not a parallel one.
        """
        conn = _make_conn()
        card_id = _add_note(
            conn,
            1000,
            "foran",
            "in front of",
            word_class="preposition",
            examples="Bilen står foran huset (<i>The car is in front of the house</i>)",
        )
        db = SRSDatabase(":memory:")
        _add_word(db, "foran", "in front of", note_id=1000, card_id=card_id, disambig="preposition")
        media_fn = _MediaFn()

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=media_fn)

        assert (report.clozed, report.minted) == (1, 0)
        assert media_fn.calls == [], "a closed-class word must not cost an image fetch"
        assert _prod_cards(conn, 1000) == []
        assert _cloze_unit(db, "foran").source_sentence == "Bilen står {{c1::foran}} huset"

    async def test_declines_when_no_example_sentence_can_carry_a_cloze(self) -> None:
        """Neither shape is available: counted, not guessed at."""
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "beslutning", "decision", examples="Katten sover (<i>The cat sleeps</i>)")
        db = SRSDatabase(":memory:")
        _add_word(db, "beslutning", "decision", note_id=1000, card_id=card_id)

        report = await _make_sync(conn, db).promote_production_cards(
            _media_fn=_MediaFn(_Media(image_bytes=None)),
        )

        assert (report.minted, report.clozed, report.unservable) == (0, 0, 1)
        assert db.count_collocations() == 1
        assert _prod_cards(conn, 1000) == []

    async def test_refuses_a_cloze_that_would_collide_with_the_word_itself(self) -> None:
        """A cloze row carries no disambig, so a word that has none either would
        share its identity — and `add_collocation` would merge into the vocab row
        and hang a production direction there with no Anki card behind it. 28 rows
        in the real deck have an empty `Word class`."""
        conn = _make_conn()
        card_id = _add_note(
            conn,
            1000,
            "valp",
            "puppy",
            word_class="",
            examples="Valpen er veldig leken (<i>The puppy is very playful</i>)",
            inflections=NOUN_TABLE,
        )
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "valp", "puppy", note_id=1000, card_id=card_id, disambig="")

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=_MediaFn(_Media(image_bytes=None)))

        assert (report.clozed, report.unservable) == (0, 1)
        assert db.count_collocations() == 1
        assert db.get_collocation_by_id(coll_id)[1].directions.keys() == {Direction.RECOGNITION}

    async def test_a_word_covered_by_a_cloze_stops_being_a_candidate(self) -> None:
        """Otherwise the drain returns the same word every sync, forever.

        A cloze is a separate note on the `Cloze` notetype, so it adds no
        production direction to the vocab collocation — the selection query has
        to exclude the word explicitly, by lemma.
        """
        conn = _make_conn()
        card_id = _add_note(
            conn,
            1000,
            "valp",
            "puppy",
            examples="Valpen er veldig leken (<i>The puppy is very playful</i>)",
            inflections=NOUN_TABLE,
        )
        db = SRSDatabase(":memory:")
        _add_word(db, "valp", "puppy", note_id=1000, card_id=card_id)
        sync = _make_sync(conn, db)
        first = await sync.promote_production_cards(_media_fn=_MediaFn(_Media(image_bytes=None)))
        assert first.clozed == 1

        again = await sync.promote_production_cards(_media_fn=_MediaFn(_Media(image_bytes=None)))

        assert (again.awaiting, again.clozed) == (0, 0)
        assert db.count_collocations() == 2, "no second cloze for the same word"

    async def test_an_inflection_cloze_is_not_production_coverage(self) -> None:
        """A morphology cloze drills ONE inflected form; it is not the word's
        production card, so it must not silence the promotion.

        `create_inflection_cloze` writes `card_type='cloze'` with the **base**
        lemma and a `morph:` disambig, which a lemma-shaped exclusion cannot tell
        apart from the base cloze this phase mints. `db_lemma_cache` already
        draws that line (`get_inflection_clozes_for_lemma`); the selection query
        has to draw the same one, or the affordance qf6.4 exists to expose would
        quietly cost the word its production card forever.
        """
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "hus", "house", note_id=1000, card_id=card_id)
        db.add_collocation(
            SyntacticUnit(
                text="huset",
                translation="the house",
                word_count=1,
                difficulty=1,
                source="llm",
                lemma="hus",
                disambig_key="morph:noun-def-sg",
                card_type="cloze",
                source_sentence="Jeg ser {{c1::huset}}",
            ),
            language_code=LANG,
        )

        report = await _make_sync(conn, db).promote_production_cards(
            _media_fn=_MediaFn(_Media(image_bytes=b"jpeg")),
        )

        assert report.minted == 1, "an inflection cloze silenced the promotion"
        assert db.get_collocation_by_id(coll_id)[1].directions.keys() == {
            Direction.RECOGNITION,
            Direction.PRODUCTION,
        }

    async def test_a_word_whose_lemma_is_unset_stops_being_a_candidate_once_clozed(self, monkeypatch) -> None:
        """The spelling-variant shape, where the lemma-keyed exclusion could not fire.

        `upsert_by_guid` — the Anki import path every candidate arrives by —
        leaves `lemma` NULL for a multi-token headword, and 10 of the real deck's
        1550 candidates are exactly that (`mot, imot`, `fra, ifra`, `selv, sjøl`
        …), 7 of them closed-class and so routed straight to a cloze. `z.lemma =
        c.lemma` is NULL against a NULL lemma, never true, so the word would come
        back every sync forever.

        Pins the active language to the deck's own, which is what `_tt_settings`
        does per request in production. The suite-wide conftest default is `sl`,
        and Slovene declares no `variant_separator` — so under the default this
        word is unservable for a reason that has nothing to do with what is
        being tested here.
        """
        monkeypatch.setattr(sync_mod.settings, "target_language", LANG)
        conn = _make_conn()
        card_id = _add_note(
            conn,
            1000,
            "mot, imot",
            "against",
            word_class="preposition",
            examples="Vi går mot byen (<i>We walk towards the city</i>)",
        )
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "mot, imot", "against", note_id=1000, card_id=card_id, disambig="preposition")
        with db._get_conn() as raw:
            assert raw.execute("SELECT lemma FROM collocations WHERE id = ?", (coll_id,)).fetchone()["lemma"] is None
        sync = _make_sync(conn, db)
        first = await sync.promote_production_cards(_media_fn=_MediaFn(_Media(image_bytes=None)))
        assert first.clozed == 1

        again = await sync.promote_production_cards(_media_fn=_MediaFn(_Media(image_bytes=None)))

        assert (again.awaiting, again.clozed) == (0, 0)
        assert db.count_collocations() == 2, "no second cloze for the same word"

    async def test_the_cloze_it_creates_is_mintable_by_sync_create_new(self) -> None:
        """The card-adding contract (.claude/rules/anki-sync.md): the row this
        phase drops must be one the existing create path can turn into a note."""
        conn = _make_conn()
        card_id = _add_note(
            conn,
            1000,
            "valp",
            "puppy",
            examples="Valpen er veldig leken (<i>The puppy is very playful</i>)",
            inflections=NOUN_TABLE,
        )
        db = SRSDatabase(":memory:")
        _add_word(db, "valp", "puppy", note_id=1000, card_id=card_id)
        sync = _make_sync(conn, db)
        await sync.promote_production_cards(_media_fn=_MediaFn(_Media(image_bytes=None)))

        await sync.sync_create_new(deck_name=DECK_NAME, model_name=SEED_NOTETYPE)

        note = conn.execute("SELECT id, flds FROM notes WHERE mid = ?", (CLOZE_MID,)).fetchone()
        assert note is not None, "the cloze collocation never reached Anki"
        assert note["flds"].split("\x1f")[0] == "{{c1::Valpen}} er veldig leken"
        # ...and TT holds the ids, so the cloze is not stranded.
        cloze_id = _cloze_id(db, "valp")
        assert db.get_collocation_by_id(cloze_id)[1].directions[Direction.PRODUCTION].anki_card_id is not None

    async def test_ignores_a_word_whose_recognition_has_not_graduated(self) -> None:
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        db = SRSDatabase(":memory:")
        _add_word(db, "hus", "house", note_id=1000, card_id=card_id, state=SRSState.NEW)
        media_fn = _MediaFn()

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=media_fn)

        assert (report.awaiting, report.minted) == (0, 0)
        assert media_fn.calls == []
        assert _prod_cards(conn, 1000) == []

    async def test_ignores_a_word_that_already_has_a_production_direction(self) -> None:
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        db = SRSDatabase(":memory:")
        _add_word(db, "hus", "house", note_id=1000, card_id=card_id, with_production=True)

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=_MediaFn())

        assert (report.awaiting, report.minted) == (0, 0)
        assert _prod_cards(conn, 1000) == []

    async def test_skips_a_note_whose_notetype_cannot_carry_production(self) -> None:
        """The Slovene control: a single-template notetype must not be minted into."""
        conn = _make_conn()
        card_id = _add_note(conn, 2000, "sound", "phonics", mid=BASIC_MID)
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "sound", "phonics", note_id=2000, card_id=card_id)
        media_fn = _MediaFn()

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=media_fn)

        assert (report.minted, report.no_template) == (0, 1)
        assert _prod_cards(conn, 2000) == []
        assert db.get_collocation_by_id(coll_id)[1].directions.keys() == {Direction.RECOGNITION}
        # Not even an image fetch: an unservable word costs nothing.
        assert media_fn.calls == []

    async def test_defers_a_word_whose_image_search_comes_back_empty(self) -> None:
        """Rule 3 — no card, and crucially no image write either."""
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "beslutning", "decision")
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=card_id)
        media_fn = _MediaFn(_Media(image_bytes=None, image_status="no_results"))

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=media_fn)

        assert (report.minted, report.unservable) == (0, 1)
        assert _prod_cards(conn, 1000) == []
        assert _image_field(conn, 1000) == ""
        assert db.get_collocation_by_id(coll_id)[1].directions.keys() == {Direction.RECOGNITION}

    async def test_reuses_an_image_tt_already_has(self, tmp_path) -> None:
        """A word imaged at add time must not get a second, different picture."""
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "hus", "house", note_id=1000, card_id=card_id)
        from app.cards.media.vocab_media import store_tt_media

        store_tt_media(db, coll_id, "image", "img_existing.jpg", b"OLDIMAGE")
        anki_media = tmp_path / "collection.media"
        anki_media.mkdir()
        media_fn = _MediaFn()

        report = await _make_sync(conn, db, anki_media).promote_production_cards(_media_fn=media_fn)

        assert report.minted == 1
        assert media_fn.calls == []
        assert _image_field(conn, 1000) == '<img src="img_existing.jpg">'
        # The existing file is copied into Anki's media dir, not re-fetched.
        assert (anki_media / "img_existing.jpg").read_bytes() == b"OLDIMAGE"

    async def test_caps_the_batch_at_the_promotion_limit(self) -> None:
        conn = _make_conn()
        db = SRSDatabase(":memory:")
        for i, word in enumerate(("hus", "bil", "bok")):
            card_id = _add_note(conn, 1000 + i, word, f"en {word}")
            _add_word(db, word, f"en {word}", note_id=1000 + i, card_id=card_id)

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=_MediaFn(), limit=2)

        assert (report.awaiting, report.minted) == (3, 2)
        minted_notes = conn.execute("SELECT COUNT(DISTINCT nid) FROM cards WHERE ord > 0").fetchone()[0]
        assert minted_notes == 2

    async def test_promotes_the_most_recently_graduated_first(self) -> None:
        """The forward trigger: a word that just graduated jumps the backlog."""
        conn = _make_conn()
        db = SRSDatabase(":memory:")
        old_card = _add_note(conn, 1000, "hus", "house")
        _add_word(db, "hus", "house", note_id=1000, card_id=old_card, last_review="2026-01-01T12:00:00+00:00")
        fresh_card = _add_note(conn, 1001, "bil", "car")
        _add_word(db, "bil", "car", note_id=1001, card_id=fresh_card, last_review="2026-08-16T12:00:00+00:00")

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=_MediaFn(), limit=1)

        assert report.minted == 1
        assert len(_prod_cards(conn, 1001)) == 1, "the freshly graduated word should be promoted first"
        assert _prod_cards(conn, 1000) == []

    async def test_adopts_a_card_anki_already_generated(self) -> None:
        """End-to-end self-heal: Anki minted ord=1 itself; TT adopts and links it."""
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        anki_card_id = 987654
        conn.execute(
            "INSERT INTO cards VALUES (?, 1000, ?, 1, 100, 7, 0, 0, 55, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (anki_card_id, DECK_ID),
        )
        conn.commit()
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "hus", "house", note_id=1000, card_id=card_id)

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=_MediaFn())

        assert (report.minted, report.adopted) == (0, 1)
        assert [c["id"] for c in _prod_cards(conn, 1000)] == [anki_card_id]
        prod = db.get_collocation_by_id(coll_id)[1].directions[Direction.PRODUCTION]
        assert (prod.anki_card_id, prod.anki_due) == (anki_card_id, 55)

    async def test_a_second_sync_is_a_no_op(self) -> None:
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        db = SRSDatabase(":memory:")
        _add_word(db, "hus", "house", note_id=1000, card_id=card_id)
        sync = _make_sync(conn, db)
        await sync.promote_production_cards(_media_fn=_MediaFn())

        again = await sync.promote_production_cards(_media_fn=_MediaFn())

        assert (again.awaiting, again.minted, again.adopted) == (0, 0, 0)
        assert len(_prod_cards(conn, 1000)) == 1

    async def test_dry_run_counts_the_backlog_and_writes_nothing(self) -> None:
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        db = SRSDatabase(":memory:")
        coll_id = _add_word(db, "hus", "house", note_id=1000, card_id=card_id)
        media_fn = _MediaFn()

        report = await _make_sync(conn, db).promote_production_cards(_media_fn=media_fn, dry_run=True)

        assert (report.awaiting, report.minted) == (1, 0)
        assert media_fn.calls == []
        assert _prod_cards(conn, 1000) == []
        assert db.get_collocation_by_id(coll_id)[1].directions.keys() == {Direction.RECOGNITION}

    async def test_without_a_media_generator_nothing_is_minted(self) -> None:
        """No image source (the CLI/peer path that threads no media_fn) → defer."""
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "hus", "house")
        db = SRSDatabase(":memory:")
        _add_word(db, "hus", "house", note_id=1000, card_id=card_id)

        report = await _make_sync(conn, db).promote_production_cards()

        assert (report.minted, report.unservable) == (0, 1)
        assert _prod_cards(conn, 1000) == []

    async def test_logs_the_drain_and_names_each_deferred_word(self, caplog) -> None:
        conn = _make_conn()
        card_id = _add_note(conn, 1000, "beslutning", "decision")
        db = SRSDatabase(":memory:")
        _add_word(db, "beslutning", "decision", note_id=1000, card_id=card_id)

        with caplog.at_level(logging.INFO, logger="app.anki.sync"):
            await _make_sync(conn, db).promote_production_cards(_media_fn=_MediaFn(_Media(image_bytes=None)))

        assert "PRODUCTION_MINT awaiting=1 minted=0 adopted=0 clozed=0 unservable=1 no_template=0" in caplog.text
        assert "beslutning" in caplog.text

    async def test_quiet_when_there_is_nothing_to_promote(self, caplog) -> None:
        db = SRSDatabase(":memory:")
        with caplog.at_level(logging.INFO, logger="app.anki.sync"):
            report = await _make_sync(_make_conn(), db).promote_production_cards(_media_fn=_MediaFn())

        assert report.awaiting == 0
        assert "PRODUCTION_MINT" not in caplog.text


class TestWordsAwaitingProduction:
    """The selection query, at the boundaries the phase depends on."""

    def _seed(self, db: SRSDatabase, **kwargs) -> int:
        return _add_word(db, kwargs.pop("word"), "gloss", note_id=1000, card_id=10000, **kwargs)

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (SRSState.REVIEW, 1),
            (SRSState.NEW, 0),
            (SRSState.LEARNING, 0),
            (SRSState.RELEARNING, 0),
            # Narrower than Layer 65's introduction gate on purpose: creating a
            # card for a word the user has hidden would resurrect it.
            (SRSState.SUSPENDED, 0),
            (SRSState.BURIED, 0),
            (SRSState.KNOWN, 0),
        ],
    )
    def test_only_a_word_in_active_review_is_a_candidate(self, srs_db, state, expected) -> None:
        self._seed(srs_db, word="hus", state=state)
        assert len(srs_db.list_words_awaiting_production(limit=10)) == expected
        assert srs_db.count_words_awaiting_production() == expected

    def test_an_unlinked_word_is_not_a_candidate(self, srs_db) -> None:
        """No Anki note means nothing to mint against — sync_create_new's job first."""
        unit = SyntacticUnit(text="ny", translation="new", word_count=1, difficulty=1, source="llm", frequency=0)
        srs_db.upsert_by_guid(
            unit,
            LANG,
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=due_at_rollover_utc(anki_today()),
                    state=SRSState.REVIEW,
                )
            },
        )
        assert srs_db.list_words_awaiting_production(limit=10) == []

    def test_a_candidate_carries_what_the_mint_needs(self, srs_db) -> None:
        coll_id = self._seed(srs_db, word="hus")
        (cand,) = srs_db.list_words_awaiting_production(limit=10)
        assert (cand.collocation_id, cand.anki_note_id) == (coll_id, 1000)
        assert cand.item.syntactic_unit.text == "hus"


class TestClozeMaterial:
    """``OfflineReader.get_cloze_material`` — the fallback's inputs, by profile."""

    def test_reads_the_examples_inflections_and_part_of_speech(self) -> None:
        conn = _make_conn()
        _add_note(
            conn, 1000, "valp", "puppy", word_class="noun", examples="Valpen sover (<i>x</i>)", inflections=NOUN_TABLE
        )

        material = OfflineReader(conn, DECK_NAME, language_code=LANG).get_cloze_material(1000)

        assert material.examples == "Valpen sover (<i>x</i>)"
        assert material.inflections == NOUN_TABLE
        # The deck says "noun"; the registry speaks UPOS.
        assert material.upos == "NOUN"

    def test_maps_the_decks_closed_class_labels(self) -> None:
        conn = _make_conn()
        _add_note(conn, 1000, "foran", "in front of", word_class="preposition")
        _add_note(conn, 1001, "og", "and", word_class="conjunction")
        reader = OfflineReader(conn, DECK_NAME, language_code=LANG)

        assert reader.get_cloze_material(1000).upos == "ADP"
        assert reader.get_cloze_material(1001).upos == "CCONJ"

    def test_a_label_the_deck_never_uses_maps_to_nothing(self) -> None:
        conn = _make_conn()
        _add_note(conn, 1000, "hm", "hm", word_class="klingon")
        assert OfflineReader(conn, DECK_NAME, language_code=LANG).get_cloze_material(1000).upos is None

    def test_yields_nothing_for_a_notetype_with_no_profile(self) -> None:
        """A deck TT has no field map for gets no cloze source rather than a guess."""
        conn = _make_conn()
        _add_note(conn, 2000, "sound", "phonics", mid=BASIC_MID)

        material = OfflineReader(conn, DECK_NAME, language_code=LANG).get_cloze_material(2000)

        assert material == ("", "", None)

    def test_yields_nothing_for_a_note_that_no_longer_exists(self) -> None:
        conn = _make_conn()
        assert OfflineReader(conn, DECK_NAME, language_code=LANG).get_cloze_material(424242) == ("", "", None)

    def test_yields_nothing_when_the_notetype_lacks_the_declared_fields(self) -> None:
        """The profile names fields this particular collection's notetype omits —
        an older revision of the shared deck. Reading by name rather than by ord
        is what makes that a blank instead of a wrong field."""
        conn = _make_conn()
        _add_note(conn, 1000, "valp", "puppy")
        conn.execute("DELETE FROM fields WHERE ntid = ? AND name IN ('Example sentences', 'Inflections')", (SEED_MID,))
        conn.commit()

        material = OfflineReader(conn, DECK_NAME, language_code=LANG).get_cloze_material(1000)

        assert (material.examples, material.inflections) == ("", "")
        assert material.upos == "NOUN"
