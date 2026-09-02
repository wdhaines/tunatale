"""Unit tests for the gloss-mismatch scanner (scripts/scan_gloss_mismatch.py).

Fixtures are synthetic (hand-built cards + in-memory lessons); no real deck is
ever opened, and every DB is a throwaway connection a test constructs itself.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from scan_gloss_mismatch import (  # noqa: E402
    EXCLUDED_COLLOCATION_IDS,
    CardResolver,
    _DETERMINERS,
    build_triples,
    discriminator_flag,
    family_words,
    is_gang_surface,
    load_cards,
    main,
    naive_flag,
    naive_words,
)
from app.models.lesson import Lesson, Phrase, Section, SectionType  # noqa: E402
from app.models.syntactic_unit import BackField, serialize_extras  # noqa: E402
from app.cards.field_map import inflection_labels  # noqa: E402


# ── families ─────────────────────────────────────────────────────────────────


class TestFamily:
    def test_irregular_verb_collapse(self):
        assert "find" in family_words("found")
        assert "go" in family_words("went")
        assert "be" in family_words("was")
        assert "become" in family_words("became")
        assert "lie" in family_words("lay")

    def test_irregular_plural_collapse(self):
        assert "person" in family_words("people")
        assert "child" in family_words("children")

    def test_regular_suffix_collapse(self):
        assert "picture" in family_words("pictures")
        assert "walk" in family_words("walked")
        assert "walk" in family_words("walking")
        assert "watch" in family_words("watches")
        assert "movie" in family_words("movies")

    def test_gloss_families_drop_articles(self):
        assert "the" not in family_words("in the hall")
        assert "hall" in family_words("in the hall")
        assert _DETERMINERS.isdisjoint(family_words("the hall"))


# ── naive control ────────────────────────────────────────────────────────────


class TestNaive:
    def test_disjoint_flags(self):
        assert naive_flag("hall", "the time (med en gang = at once)")

    def test_shared_word_does_not_flag(self):
        assert not naive_flag("find", "find the way")

    def test_stopwords_filtered(self):
        assert naive_words("to the hall") == {"hall"}
        assert naive_flag("hall", "in the hall") is False


# ── discriminator ────────────────────────────────────────────────────────────


class TestDiscriminator:
    @staticmethod
    def _flag(card_gloss: str, gen_gloss: str) -> bool:
        return discriminator_flag(card_gloss, gen_gloss)

    def test_named_true_positives_all_flag(self):
        # The six disagreements the issue names, exactly as they fall out of the deck.
        assert self._flag("pipe", "touch")
        assert self._flag("ground", "reason")
        assert self._flag("place", "quietly, still")
        assert self._flag("oh", "to (infinitive marker)")
        assert self._flag("whole", "completely")
        assert self._flag("slip", "get away, escape")

    def test_morphological_noise_does_not_flag(self):
        # The named noise class: English inflection on the CARD side reads as a
        # disagreement under the naive rule, but never under the discriminator.
        assert not self._flag("find", "found")
        assert not self._flag("go", "went, walked")
        assert not self._flag("lie", "lay")
        assert not self._flag("become", "was, became")
        assert not self._flag("picture", "pictures")

    def test_agreement_does_not_flag(self):
        assert not self._flag("the hall", "in the hall")
        assert not self._flag("house", "the house")

    def test_gang_class_flags(self):
        assert self._flag("hall", "the time (med en gang = at once)")
        assert self._flag("hall", "at that time (den gangen)")


# ── gang recall helpers ──────────────────────────────────────────────────────


class TestGangSurface:
    def test_gang_and_gangen(self):
        assert is_gang_surface("gang")
        assert is_gang_surface("gangen")
        assert is_gang_surface("GANGEN")

    def test_other_surfaces(self):
        assert not is_gang_surface("går")
        assert not is_gang_surface("hall")


# ── CardResolver ─────────────────────────────────────────────────────────────


class TestCardResolver:
    @staticmethod
    def _card(cid: int, text: str, gloss: str, lemma=None, card_type="base", forms=()):
        from scan_gloss_mismatch import Card

        return Card(id=cid, text=text, gloss=gloss, lemma=lemma, card_type=card_type, inflection_forms=forms)

    def _resolver(self, cards):
        return CardResolver(cards, language_code="no")

    def test_lemma_resolution(self):
        cards = [self._card(1, "gang", "hall", lemma="gang")]
        resolver = self._resolver(cards)
        assert resolver.resolve("gang", "gang").id == 1
        assert resolver.resolve("gangen", "gangen") is None  # inflection needs the index

    def test_inflection_resolution(self):
        cards = [self._card(1, "gang", "hall", lemma="gang", forms=("gangen",))]
        resolver = self._resolver(cards)
        assert resolver.resolve("gangen", "gangen").id == 1

    def test_variant_list_resolution(self):
        cards = [self._card(7, "mot, imot", "towards")]
        resolver = self._resolver(cards)
        assert resolver.resolve("imot", "imot").id == 7
        assert resolver.resolve("mot", "mot").id == 7

    def test_morphology_cloze_takes_precedence(self):
        base = self._card(1, "gang", "hall", lemma="gang")
        cloze = self._card(9, "gangen", "the time", lemma="gangen", card_type="cloze")
        resolver = self._resolver([base, cloze])
        assert resolver.resolve("gangen", "gangen").id == 9

    def test_multi_claim_inflection_dropped(self):
        a = self._card(1, "gang", "hall", lemma="gang", forms=("gangen",))
        b = self._card(2, "gå", "go", lemma="gå", forms=("gangen",))
        resolver = self._resolver([a, b])
        assert resolver.resolve("gangen", "gangen") is None

    def test_unresolved_surface(self):
        resolver = self._resolver([self._card(1, "gang", "hall", lemma="gang")])
        assert resolver.resolve("uforesett", "uforesett") is None


# ── synthetic deck scanning ──────────────────────────────────────────────────


def _card_row(cid: int, text: str, gloss: str, lemma: str | None, extras: str = "", card_type: str = "base"):
    return cid, "no", text, 1, lemma, None, card_type, None, extras, gloss


def _dialogue(*sentences: str) -> Section:
    return Section(
        SectionType.NATURAL_SPEED,
        [Phrase(text=s, voice_id="tts_0", language_code="no") for s in sentences],
    )


def _lesson(day: int, section: Section, glosses: dict[str, str]) -> Lesson:
    return Lesson(
        title=f"Lesson {day}",
        language_code="no",
        sections=[section],
        generation_metadata={"token_glosses": glosses},
    )


def _build_db(path: Path, cards, lessons: list[Lesson]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE collocations ("
        " id INTEGER PRIMARY KEY, language_code TEXT, text TEXT, word_count INTEGER,"
        " lemma TEXT, lemma_key TEXT, card_type TEXT, disambig_key TEXT, extras TEXT, translation TEXT)"
    )
    conn.executemany(
        "INSERT INTO collocations (id, language_code, text, word_count, lemma, lemma_key, card_type,"
        " disambig_key, extras, translation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        cards,
    )
    conn.execute(
        "CREATE TABLE lessons (id TEXT PRIMARY KEY, curriculum_id TEXT, day INTEGER, data_json TEXT, created_at TEXT)"
    )
    for i, lesson in enumerate(lessons):
        day = _lesson_day(lesson, i)
        conn.execute(
            "INSERT INTO lessons (id, curriculum_id, day, data_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"lesson-{i}", "cur", day, lesson.to_json(), f"2026-01-0{i + 1}"),
        )
    conn.commit()
    conn.close()


def _lesson_day(lesson: Lesson, fallback: int) -> int:
    tail = lesson.title.split()[-1]
    return int(tail) if tail.isdigit() else fallback


class TestLoadCards:
    def test_reads_rows_and_builds_cards(self, tmp_path):
        path = tmp_path / "deck.sqlite"
        label = next(iter(inflection_labels()))
        extras = serialize_extras(
            (BackField(label=label, html="<table><tbody><tr><td>gangen</td></tr></tbody></table>"),)
        )
        _build_db(
            path,
            [
                _card_row(1789, "gang", "hall", "gang", extras=extras),
                _card_row(3113, "med en gang", "at once", None),
            ],
            [],
        )
        conn = sqlite3.connect(path)
        try:
            cards = load_cards(conn, "no", EXCLUDED_COLLOCATION_IDS)
        finally:
            conn.close()
        assert [c.id for c in cards] == [1789]
        assert "gangen" in cards[0].inflection_forms

    def test_excluded_ids_are_dropped(self, tmp_path):
        path = tmp_path / "deck.sqlite"
        _build_db(path, [_card_row(3113, "med en gang", "at once", None)], [])
        conn = sqlite3.connect(path)
        try:
            cards = load_cards(conn, "no", EXCLUDED_COLLOCATION_IDS)
        finally:
            conn.close()
        assert cards == []

    def test_inflection_forms_extracted_from_extras(self, tmp_path):
        path = tmp_path / "deck.sqlite"
        label = next(iter(inflection_labels()))
        extras = serialize_extras(
            (BackField(label=label, html="<table><tbody><tr><td>gangen</td></tr></tbody></table>"),)
        )
        _build_db(path, [_card_row(1789, "gang", "hall", "gang", extras=extras)], [])
        conn = sqlite3.connect(path)
        try:
            cards = load_cards(conn, "no", set())
        finally:
            conn.close()
        assert "gangen" in cards[0].inflection_forms


class TestBuildTriples:
    def test_occurrences_are_preserved_and_day_attributed(self, tmp_path):
        path = tmp_path / "deck.sqlite"
        label = next(iter(inflection_labels()))
        gang_extras = serialize_extras(
            (BackField(label=label, html="<table><tbody><tr><td>gangen</td></tr></tbody></table>"),)
        )
        vere_extras = serialize_extras(
            (BackField(label=label, html="<table><tbody><tr><td>var</td></tr></tbody></table>"),)
        )
        _build_db(
            path,
            [
                _card_row(1789, "gang", "hall", "gang", extras=gang_extras),
                _card_row(2937, "være", "be", "være", extras=vere_extras),
            ],
            [
                _lesson(6, _dialogue("Vi går i gangen."), {"gangen": "the time (back then)"}),
                _lesson(7, _dialogue("Da var det gang igjen."), {"gang": "that time"}),
            ],
        )
        conn = sqlite3.connect(path)
        try:
            cards = load_cards(conn, "no", EXCLUDED_COLLOCATION_IDS)
            triples = build_triples(conn, cards, "no")
        finally:
            conn.close()
        assert [(t.surface, t.day, t.card.id, t.gen_gloss) for t in triples] == [
            ("gangen", 6, 1789, "the time (back then)"),
            ("gang", 7, 1789, "that time"),
        ]

    def test_resolution_requires_inflection_index_for_inflected_forms(self, tmp_path):
        path = tmp_path / "deck.sqlite"
        _build_db(
            path,
            [_card_row(1789, "gang", "hall", "gang")],
            [_lesson(6, _dialogue("Vi går i gangen."), {"gangen": "the time (back then)"})],
        )
        conn = sqlite3.connect(path)
        try:
            cards = load_cards(conn, "no", set())
            triples = build_triples(conn, cards, "no")
        finally:
            conn.close()
        assert [(t.surface, t.day) for t in triples] == []  # 'gangen' has no card without the index

    def test_non_natural_speed_sections_skipped(self, tmp_path):
        path = tmp_path / "deck.sqlite"
        slow = Section(
            SectionType.SLOW_SPEED,
            [Phrase(text="Vi går i gangen.", voice_id="tts_0", language_code="no")],
        )
        _build_db(
            path,
            [_card_row(1789, "gang", "hall", "gang")],
            [_lesson(6, slow, {"gangen": "the time"})],
        )
        conn = sqlite3.connect(path)
        try:
            triples = build_triples(conn, load_cards(conn, "no", set()), "no")
        finally:
            conn.close()
        assert triples == []


class TestMain:
    def test_end_to_end_summary(self, tmp_path, capsys):
        path = tmp_path / "deck.sqlite"
        label = next(iter(inflection_labels()))
        extras = serialize_extras(
            (BackField(label=label, html="<table><tbody><tr><td>gangen</td></tr></tbody></table>"),)
        )
        _build_db(
            path,
            [_card_row(1789, "gang", "hall", "gang", extras=extras)],
            [_lesson(6, _dialogue("Vi går i gangen."), {"gangen": "the time (back then)"})],
        )
        assert main(["--db", str(path)]) == 0
        out = capsys.readouterr().out
        assert "corpus: 1 triples" in out
        assert "1 flagged" in out
        assert "gang recall" in out and "1/1" in out
        assert "review list" in out
        assert "#1789 'hall'→'the time (back then)'" in out

    def test_missing_db_exits_nonzero(self, tmp_path, capsys):
        assert main(["--db", str(tmp_path / "nope.sqlite")]) == 1
        assert "no such deck" in capsys.readouterr().err

    def test_english_inflection_noise_not_flagged(self, tmp_path, capsys):
        path = tmp_path / "deck.sqlite"
        label = next(iter(inflection_labels()))
        extras = serialize_extras(
            (BackField(label=label, html="<table><tbody><tr><td>fant</td></tr></tbody></table>"),)
        )
        _build_db(
            path,
            [_card_row(2990, "finne", "find", "finne", extras=extras)],
            [_lesson(1, _dialogue("Fant nøkkelen."), {"fant": "found"})],
        )
        assert main(["--db", str(path)]) == 0
        out = capsys.readouterr().out
        assert "0 flagged" in out
        assert "review list" not in out
