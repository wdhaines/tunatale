"""The torch-free table lemmatizer (tunatale-kbb.18).

Most tests use a tiny fixture table so they pin the engine's rules. The class at
the bottom loads the REAL committed Norwegian table and pins it against lemmas
the laptop's Stanza produced — the parity the prod engine exists for.
"""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import pytest

from app.config import settings
from app.languages import all_lemma_table_paths, get_lemma_table_path
from app.srs import lemma_table
from app.srs.lemma_table import (
    LemmaTable,
    TableLemmatizer,
    build_lemma_table_db,
    db_path_for,
    ensure_lemma_table_db,
    tokenize,
)
from app.srs.lemmatizer import (
    LowercaseLemmatizer,
    TokenAnalysis,
    _serialize_analyses,
    analyze_sentence_cached,
    get_lemmatizer,
    model_version_for,
)

ROWS = [
    ("Hansen", "PROPN", "Hansen", 1),
    ("deg", "PRON", "du", 1),
    ("deg", "DET", "du", 0),
    ("noe", "PRON", "noe", 0),
    ("noe", "DET", "noen", 1),
    ("prisen", "NOUN", "pris", 1),
    ("ser", "VERB", "se", 1),
    ("så", "VERB", "se", 1),
    ("så", "ADV", "så", 0),
]


def write_extract(path: Path, rows=ROWS, header: str = "#source_version=9.9.9") -> Path:
    body = header + "\n" + "".join(f"{s}\t{u}\t{lem}\t{d}\n" for s, u, lem, d in rows)
    path.write_bytes(gzip.compress(body.encode("utf-8"), mtime=0))
    return path


@pytest.fixture
def extract(tmp_path) -> Path:
    return write_extract(tmp_path / "lemmas.tsv.gz")


@pytest.fixture
def lem(extract) -> TableLemmatizer:
    return TableLemmatizer("no", extract)


def pairs(analyses: list[TokenAnalysis]) -> list[tuple[str, str, str]]:
    return [(a.surface, a.lemma, a.upos) for a in analyses]


class TestTokenize:
    def test_splits_words_from_punctuation(self):
        assert tokenize("Jeg ser deg i morgen.") == ["Jeg", "ser", "deg", "i", "morgen", "."]

    def test_keeps_internal_hyphens_and_apostrophes(self):
        assert tokenize("e‑post, gps-en og Ola's bil") == ["e‑post", ",", "gps-en", "og", "Ola's", "bil"]

    def test_every_non_space_mark_is_its_own_token(self):
        assert tokenize("Hva?! — nei…") == ["Hva", "?", "!", "—", "nei", "…"]


class TestBuild:
    def test_db_sits_beside_the_extract(self, tmp_path):
        assert db_path_for(tmp_path / "x.tsv.gz") == tmp_path / "x.sqlite3"

    def test_builds_rows_and_meta(self, extract):
        db = ensure_lemma_table_db(extract)
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM lemmas").fetchone()[0] == len(ROWS)
        assert dict(conn.execute("SELECT key, value FROM meta"))["source_version"] == "9.9.9"

    def test_reuses_a_current_build(self, extract):
        db = ensure_lemma_table_db(extract)
        stamp = db.stat().st_mtime_ns
        assert ensure_lemma_table_db(extract) == db
        assert db.stat().st_mtime_ns == stamp

    def test_rebuilds_when_the_extract_changes(self, extract):
        ensure_lemma_table_db(extract)
        write_extract(extract, ROWS + [("kaffen", "NOUN", "kaffe", 1)])
        table = LemmaTable(ensure_lemma_table_db(extract))
        assert [r.lemma for r in table.readings("kaffen")] == ["kaffe"]

    def test_rebuilds_an_unreadable_db(self, extract):
        db_path_for(extract).write_bytes(b"not a database")
        table = LemmaTable(ensure_lemma_table_db(extract))
        assert [r.lemma for r in table.readings("prisen")] == ["pris"]

    def test_builds_in_batches(self, extract, monkeypatch):
        monkeypatch.setattr(lemma_table, "_INSERT_BATCH", 2)
        conn = sqlite3.connect(ensure_lemma_table_db(extract))
        assert conn.execute("SELECT COUNT(*) FROM lemmas").fetchone()[0] == len(ROWS)

    def test_missing_extract_is_an_error_not_an_empty_table(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ensure_lemma_table_db(tmp_path / "absent.tsv.gz")

    def test_missing_header_refuses(self, tmp_path):
        bad = write_extract(tmp_path / "bad.tsv.gz", header="deg\tPRON\tdu\t1")
        with pytest.raises(ValueError, match="source_version"):
            build_lemma_table_db(bad, tmp_path / "bad.sqlite3")
        assert not (tmp_path / "bad.sqlite3").exists()
        assert list(tmp_path.glob("*.tmp")) == []

    @pytest.mark.parametrize("defaults", [(0, 0), (1, 1)])
    def test_a_surface_needs_exactly_one_default(self, tmp_path, defaults):
        rows = [("så", "VERB", "se", defaults[0]), ("så", "ADV", "så", defaults[1])]
        bad = write_extract(tmp_path / "bad.tsv.gz", rows)
        with pytest.raises(ValueError, match="exactly one default"):
            build_lemma_table_db(bad, tmp_path / "bad.sqlite3")

    def test_main_builds_every_registered_table(self, capsys):
        assert lemma_table.main([]) == 0
        out = capsys.readouterr().out
        assert out.count("lemma table:") == len(all_lemma_table_paths()) >= 1


class TestTableLemmatizer:
    def test_default_readings(self, lem):
        assert pairs(lem.analyze_sentence("Jeg så deg.", "no")) == [
            ("Jeg", "jeg", ""),
            ("så", "se", "VERB"),
            ("deg", "du", "PRON"),
            (".", "$.", "PUNCT"),
        ]

    def test_exact_casing_wins_then_lowercase(self, lem):
        assert pairs(lem.analyze_sentence("Prisen Hansen", "no")) == [
            ("Prisen", "pris", "NOUN"),
            ("Hansen", "Hansen", "PROPN"),
        ]

    def test_numbers_and_unknown_words(self, lem):
        assert pairs(lem.analyze_sentence("1994 Etterforskningsteamet", "no")) == [
            ("1994", "1994", "NUM"),
            ("Etterforskningsteamet", "etterforskningsteamet", ""),
        ]

    def test_a_context_tag_selects_its_reading(self, lem):
        assert pairs(lem.analyze_sentence_with_tags("Og så noe", "no", {1: "ADV", 2: "PRON"}))[1:] == [
            ("så", "så", "ADV"),
            ("noe", "noe", "PRON"),
        ]

    def test_a_tag_outside_the_readings_falls_back_to_the_default(self, lem):
        assert pairs(lem.analyze_sentence_with_tags("så", "no", {0: "NOUN"})) == [("så", "se", "VERB")]

    def test_other_language_is_lowercased(self, lem):
        assert pairs(lem.analyze_sentence("Dober dan", "sl")) == [("Dober", "dober", ""), ("dan", "dan", "")]
        assert lem.analyze("Prisen", "sl") == ("prisen", "", "")

    def test_single_word_api(self, lem):
        assert lem.lemmatize("Prisen", "no") == "pris"
        assert lem.analyze("deg", "no") == ("du", "", "")

    def test_cache_keys(self, lem):
        assert model_version_for(lem).startswith("table-9.9.9-")
        assert lem.compatible_cache_versions == ("9.9.9+f2",)


class TestCache:
    def test_real_model_rows_win_over_the_table(self, lem, srs_db):
        exact = [TokenAnalysis(surface="så", lemma="så", upos="ADV")]
        srs_db.set_sentence_analysis("så", "no", "9.9.9+f2", _serialize_analyses(exact))
        assert analyze_sentence_cached(srs_db, lem, "så", "no", model_version_for(lem)) == exact
        # read-only: nothing written under the table's own key
        assert srs_db.get_sentence_analysis("så", "no", model_version_for(lem)) is None

    def test_computes_and_stores_under_its_own_key(self, lem, srs_db):
        got = analyze_sentence_cached(srs_db, lem, "så", "no", model_version_for(lem))
        assert pairs(got) == [("så", "se", "VERB")]
        assert srs_db.get_sentence_analysis("så", "no", model_version_for(lem)) is not None


class TestFactory:
    @pytest.fixture(autouse=True)
    def _fresh_factory(self):
        get_lemmatizer.cache_clear()
        yield
        get_lemmatizer.cache_clear()

    def test_table_setting_serves_a_language_with_a_table(self, monkeypatch, extract):
        from app.languages import _CONFIGS

        monkeypatch.setattr(settings, "lemmatizer_type", "table")
        monkeypatch.setattr(_CONFIGS["no"], "lemma_table_path", extract)
        assert isinstance(get_lemmatizer("no"), TableLemmatizer)

    def test_table_setting_leaves_a_language_without_one_lowercase(self, monkeypatch):
        monkeypatch.setattr(settings, "lemmatizer_type", "table")
        assert get_lemma_table_path("en") is None
        assert isinstance(get_lemmatizer("en"), LowercaseLemmatizer)


@pytest.fixture(scope="module")
def real():
    # Module scope instantiates BEFORE the function-tier autoclose fixture, so
    # nothing else closes this connection (tunatale-zcgs: it was GC'd at an
    # arbitrary later moment, warning inside whichever test ran next).
    lemmatizer = TableLemmatizer("no", get_lemma_table_path("no"))
    yield lemmatizer
    lemmatizer.close()


def test_close_releases_the_table_connection(tmp_path):
    """tunatale-zcgs: LemmaTable held its connection for the object's lifetime
    with no way to release it."""
    db = tmp_path / "t.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('source_version', '1'), ('source_id', 'x');"
    )
    conn.commit()
    conn.close()
    table = LemmaTable(db)
    table.close()
    with pytest.raises(sqlite3.ProgrammingError):
        table._conn.execute("SELECT 1")


@pytest.mark.skipif(
    get_lemma_table_path("no") is None or not get_lemma_table_path("no").exists(),
    reason="the committed Norwegian lemma table is missing",
)
class TestCommittedNorwegianTable:
    """Parity with the laptop's Stanza (1.14.0, in-context) on a pinned set.

    Each expectation is what ``StanzaLemmatizer.analyze_sentence`` returned on
    the Mac, 2026-09-18. These words have ONE reading in the table, so the table
    alone must reproduce them; context-dependent words are the resolver's job.
    """

    @pytest.mark.parametrize(
        "sentence,expected",
        [
            ("Jeg ser deg i morgen.", ["jeg", "se", "du", "i", "morgen", "$."]),
            ("Hun liker meg.", ["hun", "like", "jeg", "$."]),
            ("Han vasker seg hver dag.", ["han", "vaske", "seg", "hver", "dag", "$."]),
            ("Prisen er for høy.", ["pris", "være", "for", "høy", "$."]),
            # "Alle" is left out: alle/all is a two-lemma word, the resolver's job.
            ("Deltakere må registrere seg.", ["deltaker", "måtte", "registrere", "seg", "$."]),
        ],
    )
    def test_matches_in_context_stanza(self, real, sentence, expected):
        assert [a.lemma for a in real.analyze_sentence(sentence, "no")] == expected

    def test_reproduces_stanza_including_its_artifacts(self, real):
        # Wrong as Norwegian, but it is what existing cards were keyed with.
        assert real.lemmatize("mappen", "no") == "mapp"
        assert real.lemmatize("snømenn", "no") == "snøm"

    def test_reuses_the_rows_the_laptop_cached(self, real):
        assert real.compatible_cache_versions == ("1.14.0+f2",)


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_every_file_a_table_build_writes_is_gitignored(suffix):
    """tunatale-i3kd. The commit-gate test fingerprints the WORKING TREE, and in
    CI the table does not exist yet, so a parallel worker builds it mid-run. The
    ``<name>.<pid>.tmp`` staging file was ignored but SQLite's companions were
    not — run 35753114899 failed on
    ``?? .../stanza_lemmas.sqlite3.3286.tmp-journal``. Locally the table is
    already built, which is why the flake never reproduced here."""
    import subprocess

    for extract in all_lemma_table_paths():
        table = lemma_table.db_path_for(extract)
        staged = table.with_name(f"{table.name}.3286.tmp{suffix}")
        rel = staged.relative_to(Path(__file__).resolve().parents[2])
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", str(rel)],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
        )
        assert proc.returncode == 0, f"{rel} is not gitignored"
