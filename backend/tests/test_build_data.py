"""Built data artifacts: one entry point builds them, and a missing one is loud (tunatale-ip8q).

The NST lexicon's SQLite build is gitignored and was built by nothing that ships:
the Dockerfile and ``switch.sh`` ran ``python -m app.srs.lemma_table`` (the lemma
tables only), and the lexicon's builder lives in ``scripts/``, which the prod image
does not even copy. So prod and the laptop instance ran WITHOUT it, and
``lexicon_has_secondary_stress`` answered ``None`` — "no signal" — for every word.
That silently disabled the veto against over-splitting compounds: measured
2026-09-29, the same call answered ``'for, svare'`` for ``forsvare`` on the live
venv and ``'forsvare'`` on dev.

The fix makes built data a registry facet (``LanguageConfig.built_data``) that
``python -m app.build_data`` builds, and gives it a ``--check`` mode that exits
non-zero when an artifact is missing or stale — the laptop instance refuses to
start on it, which is the loud failure the silent ``None`` never was.

Fixtures only: the committed 4.6 MB extract and its 44 MB build are never
touched here.
"""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import pytest

from app import build_data
from app.languages import BuiltData, all_built_data, get_built_data
from app.plugins.languages.no.lexicon import DB_PATH, EXTRACT_PATH, NstLexicon, build_lexicon_db


def _extract(tmp_path: Path, rows: list[tuple[str, str, str, int]], name: str = "lex.tsv.gz") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    gz = tmp_path / name
    payload = "".join(f"{w}\t{p}\t{s}\t{c}\n" for w, p, s, c in rows)
    gz.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
    return gz


_ROWS = [("forsvare", "VB", '"f%rs$vA:$r@', 1)]
_OTHER_ROWS = [("snømann", "NN", '""sn2:$%mAn', 2)]


def _artifact(tmp_path: Path, rows=_ROWS) -> BuiltData:
    return BuiltData(extract=_extract(tmp_path, rows), db=tmp_path / "lex.sqlite3", build=build_lexicon_db)


def _words(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {row[0] for row in conn.execute("SELECT word FROM entries")}


class TestEnsure:
    def test_a_missing_artifact_is_built(self, tmp_path):
        art = _artifact(tmp_path)

        assert build_data.ensure(art) == art.db

        assert NstLexicon(art.db).all_transcriptions("forsvare")

    def test_a_current_artifact_is_left_alone(self, tmp_path):
        art = _artifact(tmp_path)
        build_data.ensure(art)
        before = art.db.stat().st_mtime_ns

        build_data.ensure(art)

        assert art.db.stat().st_mtime_ns == before

    def test_a_changed_extract_is_rebuilt(self, tmp_path):
        art = _artifact(tmp_path)
        build_data.ensure(art)
        _extract(tmp_path, _OTHER_ROWS)  # same path, new bytes

        build_data.ensure(art)

        assert _words(art.db) == {"snømann"}

    def test_a_build_with_no_stamp_is_rebuilt(self, tmp_path):
        """The dev machines' existing builds came from scripts/, unstamped; they
        cannot be proven current, so the first ensure rebuilds them once."""
        art = _artifact(tmp_path)
        build_lexicon_db(art.extract, art.db)

        assert build_data.is_current(art) is False
        build_data.ensure(art)
        assert build_data.is_current(art) is True

    def test_a_missing_extract_is_an_error_not_an_empty_build(self, tmp_path):
        art = BuiltData(extract=tmp_path / "absent.tsv.gz", db=tmp_path / "x.sqlite3", build=build_lexicon_db)

        with pytest.raises(FileNotFoundError):
            build_data.ensure(art)
        assert not art.db.exists()


class TestMain:
    def test_build_mode_builds_every_artifact(self, tmp_path):
        arts = [_artifact(tmp_path / "a"), _artifact(tmp_path / "b", _OTHER_ROWS)]

        assert build_data.main([], artifacts=arts, lemma_tables=[]) == 0

        assert all(build_data.is_current(a) for a in arts)

    def test_build_mode_builds_the_lemma_tables_too(self, tmp_path):
        """One entry point for both kinds, so the Dockerfile cannot build one and miss the other."""
        from app.srs.lemma_table import db_path_for
        from tests.test_lemma_table import write_extract

        extract = write_extract(tmp_path / "x_lemmas.tsv.gz")

        assert build_data.main([], artifacts=[], lemma_tables=[extract]) == 0

        assert db_path_for(extract).exists()

    def test_check_mode_fails_loudly_on_a_missing_artifact(self, tmp_path, capsys):
        art = _artifact(tmp_path)

        rc = build_data.main(["--check"], artifacts=[art], lemma_tables=[])

        assert rc == 1
        assert str(art.db) in capsys.readouterr().err

    def test_check_mode_builds_nothing(self, tmp_path):
        art = _artifact(tmp_path)

        build_data.main(["--check"], artifacts=[art], lemma_tables=[])

        assert not art.db.exists()

    def test_check_mode_passes_when_everything_is_current(self, tmp_path):
        art = _artifact(tmp_path)
        build_data.ensure(art)

        assert build_data.main(["--check"], artifacts=[art], lemma_tables=[]) == 0

    def test_check_mode_reports_a_missing_lemma_table_build(self, tmp_path, capsys):
        extract = tmp_path / "x_lemmas.tsv.gz"
        extract.write_bytes(b"")

        assert build_data.main(["--check"], artifacts=[], lemma_tables=[extract]) == 1
        assert "x_lemmas.sqlite3" in capsys.readouterr().err


class TestRegistry:
    def test_norwegian_registers_the_nst_lexicon(self):
        assert BuiltData(extract=EXTRACT_PATH, db=DB_PATH, build=build_lexicon_db) in get_built_data("no")

    def test_every_registered_artifact_ships_its_extract(self):
        """The extract is the committed half; a registration naming a missing one
        would make every build fail, so pin it here rather than at deploy."""
        for art in all_built_data():
            assert art.extract.exists(), art.extract
