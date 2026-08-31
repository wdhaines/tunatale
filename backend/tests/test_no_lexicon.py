"""NST pronunciation-lexicon facet (stage 1): typed outcomes, factory wiring, packaging.

The lexicon is called by nothing yet — these tests pin the facet's contract so
stages 2/3 can consume it without re-deriving the resolution semantics. All
fixtures are tiny in-test lexicons in the real data format; the committed
extract and its built SQLite artifact are never touched here.
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path

import pytest

from app.languages import (
    LexiconOutcome,
    LexiconResolution,
    PronunciationLexicon,
    get_lexicon,
)
from app.plugins.languages.no import lexicon as lexicon_module
from app.plugins.languages.no.lexicon import (
    DB_PATH,
    NstLexicon,
    build_lexicon_db,
    create_nst_lexicon,
    nst_lexicon_installed,
)

# Real-format rows: headword \t POS \t transcription ($ syllable / _ word
# boundary) \t certainty — a miniature of the committed extract.
FIXTURE_ROWS = [
    ("snømann", "NN", '""sn2:$%mAn', 2),
    ("seg", "PN", '"s{*I', 1),
    ("seg", "VB", '"se:g', 2),
    ("testord", "NN", '"tA:', 1),
    ("testord", "VB", '"te:s', 1),
    ("hørte", "PM|person|SUR", '"h2rt@', 1),
    ("hørte", "NN", '"hæ:$rt@', 1),
    ("hørte", "VB", '"h2rt@', 2),
    ("stille", "JJ", '"stil$@', 1),
]


def _make_db(tmp_path: Path, rows: list[tuple[str, str, str, int]] = FIXTURE_ROWS) -> Path:
    gz = tmp_path / "fixture.tsv.gz"
    payload = "".join(f"{w}\t{p}\t{s}\t{c}\n" for w, p, s, c in rows)
    gz.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
    db = tmp_path / "lexicon.sqlite3"
    build_lexicon_db(gz, db)
    return db


def _lex(tmp_path: Path, rows: list[tuple[str, str, str, int]] = FIXTURE_ROWS) -> NstLexicon:
    return NstLexicon(_make_db(tmp_path, rows))


class TestResolved:
    def test_round_trips_norwegian_letters(self, tmp_path: Path) -> None:
        r = _lex(tmp_path).resolve("snømann")
        assert r == LexiconResolution(
            outcome=LexiconOutcome.RESOLVED,
            word="snømann",
            transcription='""sn2:$%mAn',
            syllables=('""sn2:', "%mAn"),
            pos="NN",
            n_entries=1,
            n_readings=1,
        )

    def test_word_boundaries_split_syllables_too(self, tmp_path: Path) -> None:
        rows = [("fjell_og_vann", "NN", '"fjel_%"vAn', 1)]
        r = _lex(tmp_path, rows).resolve("fjell_og_vann")
        assert r.outcome is LexiconOutcome.RESOLVED
        assert r.syllables == ('"fjel', '%"vAn')

    def test_lookup_falls_back_to_lowercase(self, tmp_path: Path) -> None:
        assert _lex(tmp_path).resolve("Snømann").outcome is LexiconOutcome.RESOLVED

    def test_capitalized_entry_found_exactly(self, tmp_path: Path) -> None:
        rows = [("Oslo", "PM|place|GEO", '"u:$lu', 1)]
        r = _lex(tmp_path, rows).resolve("Oslo")
        assert r.transcription == '"u:$lu'

    def test_whitespace_is_stripped(self, tmp_path: Path) -> None:
        assert _lex(tmp_path).resolve("  snømann ").transcription == '""sn2:$%mAn'


class TestMinCertaintyReduction:
    def test_lowest_certainty_wins_without_pos(self, tmp_path: Path) -> None:
        r = _lex(tmp_path).resolve("seg")
        assert r.outcome is LexiconOutcome.RESOLVED
        assert r.transcription == '"s{*I'
        assert r.pos == "PN"
        assert r.n_entries == 2

    def test_reduction_happens_before_pos_selection(self, tmp_path: Path) -> None:
        # The VB reading of `seg` has certainty 2 > floor 1, so supplying VERB
        # must NOT resurrect it: min-certainty reduction runs first.
        r = _lex(tmp_path).resolve("seg", upos="VERB")
        assert r.transcription == '"s{*I'


class TestPosSelection:
    def test_no_upos_is_ambiguous(self, tmp_path: Path) -> None:
        r = _lex(tmp_path).resolve("testord")
        assert r == LexiconResolution(
            outcome=LexiconOutcome.AMBIGUOUS_NO_POS, word="testord", n_entries=2, n_readings=2
        )

    def test_noun_picks_nn_reading(self, tmp_path: Path) -> None:
        r = _lex(tmp_path).resolve("testord", upos="NOUN")
        assert r.outcome is LexiconOutcome.RESOLVED
        assert r.transcription == '"tA:'
        assert r.pos == "NN"

    def test_verb_picks_vb_reading(self, tmp_path: Path) -> None:
        assert _lex(tmp_path).resolve("testord", upos="VERB").transcription == '"te:s'

    def test_aux_maps_to_vb(self, tmp_path: Path) -> None:
        assert _lex(tmp_path).resolve("testord", upos="AUX").transcription == '"te:s'

    def test_propn_matches_compound_pm_tags(self, tmp_path: Path) -> None:
        # `hørte` has three entries (surname, noun, verb); at min-certainty the
        # PM surname and NN readings remain, and PROPN selects via the PM prefix.
        r = _lex(tmp_path).resolve("hørte", upos="PROPN")
        assert r.outcome is LexiconOutcome.RESOLVED
        assert r.transcription == '"h2rt@'
        assert r.pos == "PM"

    def test_pos_didnt_help_when_matching_reading_was_reduced_away(self, tmp_path: Path) -> None:
        # The only VB reading of `hørte` sits above the certainty floor.
        r = _lex(tmp_path).resolve("hørte", upos="VERB")
        assert r == LexiconResolution(
            outcome=LexiconOutcome.AMBIGUOUS_POS_DIDNT_HELP, word="hørte", n_entries=3, n_readings=2
        )

    def test_pos_didnt_help_when_no_reading_matches(self, tmp_path: Path) -> None:
        r = _lex(tmp_path).resolve("testord", upos="ADV")
        assert r.outcome is LexiconOutcome.AMBIGUOUS_POS_DIDNT_HELP

    def test_empty_upos_behaves_like_none(self, tmp_path: Path) -> None:
        assert _lex(tmp_path).resolve("testord", upos="").outcome is LexiconOutcome.AMBIGUOUS_NO_POS


class TestAbsentAndUnmapped:
    def test_unknown_word_is_absent(self, tmp_path: Path) -> None:
        assert _lex(tmp_path).resolve("ikkeord") == LexiconResolution(LexiconOutcome.ABSENT, "ikkeord")

    def test_deliberately_unmapped_upos_does_not_warn(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            r = _lex(tmp_path).resolve("testord", upos="PUNCT")
        assert r.outcome is LexiconOutcome.AMBIGUOUS_NO_POS
        assert caplog.records == []

    def test_unmapped_upos_warns_and_degrades_to_no_pos(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            r = _lex(tmp_path).resolve("testord", upos="FOO")
        assert r.outcome is LexiconOutcome.AMBIGUOUS_NO_POS
        assert any("FOO" in rec.message for rec in caplog.records)


class TestCapabilityCheck:
    def test_missing_db_is_loud(self, tmp_path: Path) -> None:
        db = tmp_path / "never-built.sqlite3"
        assert not nst_lexicon_installed(db)
        with pytest.raises(FileNotFoundError, match="build_nst_lexicon"):
            NstLexicon(db).resolve("snømann")

    def test_built_db_reports_installed(self, tmp_path: Path) -> None:
        assert nst_lexicon_installed(_make_db(tmp_path))

    def test_connection_is_reused_across_resolves(self, tmp_path: Path) -> None:
        lex = _lex(tmp_path)
        assert lex.resolve("stille").transcription == '"stil$@'
        assert lex.resolve("stille").transcription == '"stil$@'


class TestPackaging:
    def _gz(self, tmp_path: Path, n_rows: int) -> Path:
        gz = tmp_path / "many.tsv.gz"
        payload = "".join(f'ord{i}\tNN\t"O:$rd{i}\t1\n' for i in range(n_rows))
        gz.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
        return gz

    def test_batched_flush_inserts_every_row(self, tmp_path: Path) -> None:
        # 5 rows at batch_size=2 flushes mid-loop twice and leaves a remainder,
        # so both the in-loop flush and the trailing one execute. At the real
        # 50,000 the in-loop path is unreachable from any sane fixture.
        db = tmp_path / "batched.sqlite3"
        build_lexicon_db(self._gz(tmp_path, 5), db, batch_size=2)
        lex = NstLexicon(db)
        rows = [lex.resolve(f"ord{i}") for i in range(5)]
        assert [r.outcome for r in rows] == [LexiconOutcome.RESOLVED] * 5
        # n_entries==1 apiece is what catches a flush that inserts without
        # clearing: the rows would all still resolve, just duplicated.
        assert [r.n_entries for r in rows] == [1] * 5

    def test_exact_multiple_of_batch_leaves_no_remainder(self, tmp_path: Path) -> None:
        # 4 rows at batch_size=2 empties `batch` on the final flush, so the
        # trailing `if batch:` is False — the other side of that branch.
        db = tmp_path / "exact.sqlite3"
        build_lexicon_db(self._gz(tmp_path, 4), db, batch_size=2)
        lex = NstLexicon(db)
        assert [lex.resolve(f"ord{i}").outcome for i in range(4)] == [LexiconOutcome.RESOLVED] * 4
        assert lex.resolve("ord4").outcome is LexiconOutcome.ABSENT

    def test_build_rejects_malformed_extract(self, tmp_path: Path) -> None:
        gz = tmp_path / "bad.tsv.gz"
        gz.write_bytes(gzip.compress(b"only\tthree\tfields\n", mtime=0))
        with pytest.raises(ValueError, match=r"Malformed line"):
            build_lexicon_db(gz, tmp_path / "out.sqlite3")


class TestRegistryWiring:
    def test_languages_without_lexicon_return_none(self) -> None:
        for code in ("en", "sl", "xx"):
            assert get_lexicon(code) is None

    def test_get_lexicon_returns_plugin_lexicon_at_canonical_path(self) -> None:
        lex = get_lexicon("no")
        assert isinstance(lex, NstLexicon)
        assert isinstance(lex, PronunciationLexicon)
        assert isinstance(create_nst_lexicon(), NstLexicon)

    def test_canonical_db_path_sits_in_plugin_data_dir(self) -> None:
        assert DB_PATH.parent == Path(lexicon_module.__file__).parent / "data"
        assert DB_PATH.suffix == ".sqlite3"


class TestCloseReleasesTheConnection:
    """``close()`` is what stops a per-word call site exhausting file descriptors.

    ``lexicon_syllables.lexicon_reading`` constructs an ``NstLexicon`` PER WORD
    behind an lru_cache of 4096, so a whole-wordlist sweep (~50k entries)
    constructs tens of thousands of them.  Before ``close()`` existed the
    connection was released only by the garbage collector, which emits a
    ResourceWarning per instance and — measured on
    ``test_norwegian_breakdown_spans`` — exhausted file descriptors before the
    collector ran, failing with ``sqlite3.OperationalError: unable to open
    database file`` (tunatale-a5p2).
    """

    def test_close_is_safe_before_the_connection_is_ever_opened(self) -> None:
        # The lazy-open contract means a lexicon that is never queried holds no
        # connection; close() must not care. This is the branch a `with` block
        # takes whenever the body raises before the first lookup.
        lex = NstLexicon(DB_PATH)
        lex.close()

    def test_close_is_idempotent(self) -> None:
        lex = NstLexicon(DB_PATH)
        lex.candidate_transcriptions("hus")  # force the lazy open
        lex.close()
        lex.close()  # second call must be a no-op, not an error

    def test_context_manager_closes_and_reopens_on_next_use(self) -> None:
        with NstLexicon(DB_PATH) as lex:
            first = lex.candidate_transcriptions("hus")
        # Closed, but the object stays usable — _connect() re-opens lazily, so a
        # cached instance is not poisoned by having been closed once.
        assert lex.candidate_transcriptions("hus") == first
        lex.close()
