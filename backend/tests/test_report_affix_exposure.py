"""The affix exposure report (tunatale-ve4p.3).

The report answers "which affixes did this lesson actually put in front of the
learner", which is the acceptance instrument for planned affix patterns: a
thematic lesson should carry one pattern on at least three roots in both of its
cells. The lines below are hand-written, not a stored lesson.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

import pytest

from app.languages import get_a1_morphology
from app.srs.lemmatizer import TokenAnalysis
from scripts.report_affix_exposure import exposure, format_exposure, main, meets_target, natural_lines


def _tok(surface: str, lemma: str, upos: str = "VERB") -> TokenAnalysis:
    return TokenAnalysis(surface=surface, lemma=lemma, upos=upos)


# One list per line, as the lemma analysis cache stores them.
_LINES = {
    "Naglakaw si Paul.": [_tok("Naglakaw", "lakaw"), _tok("si", "si", "DET"), _tok("Paul", "paul", "PROPN")],
    "Maglakaw ta.": [_tok("Maglakaw", "lakaw"), _tok("ta", "ta", "PRON")],
    "Mag-ampo ta.": [_tok("Mag-ampo", "ampo"), _tok("ta", "ta", "PRON")],
    "Nagdala ko og kape.": [_tok("Nagdala", "dala"), _tok("ko", "ko", "PRON"), _tok("kape", "kape", "NOUN")],
    "Nag-ampo sila.": [_tok("Nag-ampo", "ampo"), _tok("sila", "sila", "PRON")],
    "Magdala ko.": [_tok("Magdala", "dala"), _tok("ko", "ko", "PRON")],
    "Matulog ko.": [_tok("Matulog", "tulog"), _tok("ko", "ko", "PRON")],
    "Nakahibalo ko.": [_tok("Nakahibalo", "hibalo"), _tok("ko", "ko", "PRON")],
    # A bare root is a verb token but carries no affix; ``ihatag`` read as a
    # noun is not a verb token at all.
    "Lakaw na, ihatag.": [_tok("Lakaw", "lakaw"), _tok("na", "na", "PART"), _tok("ihatag", "hatag", "NOUN")],
}


def _exposure(lines=_LINES):
    return exposure(lines.values(), get_a1_morphology("ceb"))


def test_exposure_counts_verbs_affixes_and_the_unexplained():
    got = _exposure()
    assert (got.verbs, got.bare) == (9, 1)
    assert got.by_feature == {
        "verb:nag": Counter({"naglakaw<lakaw": 1, "nagdala<dala": 1, "nag-ampo<ampo": 1}),
        "verb:mag": Counter({"maglakaw<lakaw": 1, "mag-ampo<ampo": 1, "magdala<dala": 1}),
        "verb:ma": Counter({"matulog<tulog": 1}),
    }
    assert got.unrecognised == Counter({"nakahibalo<hibalo": 1})


def test_a_root_met_in_both_cells_of_a_pattern_is_a_pair():
    assert _exposure().pairs == {"mo-mi": [], "mag-nag": ["ampo", "dala", "lakaw"], "ma-na": []}


def test_the_target_is_three_roots_paired_in_one_pattern():
    assert meets_target(_exposure(), get_a1_morphology("ceb").patterns)


@pytest.mark.parametrize("dropped", ["Magdala ko.", "Nag-ampo sila.", "Maglakaw ta."])
def test_two_pairs_do_not_meet_the_target(dropped):
    """Two pairs is what an unplanned lesson already reached (measured baseline)."""
    lines = {k: v for k, v in _LINES.items() if k != dropped}
    assert not meets_target(_exposure(lines), get_a1_morphology("ceb").patterns)


def test_natural_lines_are_the_target_language_lines_of_the_natural_speed_section():
    lesson = {
        "sections": [
            {"section_type": "key_phrases", "phrases": [{"text": "kape", "language_code": "ceb"}]},
            {
                "section_type": "natural_speed",
                "phrases": [
                    {"text": "Natural Speed", "language_code": "en"},
                    {"text": "Naglakaw si Paul.", "language_code": "ceb"},
                    {"text": "Maglakaw ta.", "language_code": "ceb"},
                ],
            },
        ]
    }
    assert natural_lines(lesson, "ceb") == ["Naglakaw si Paul.", "Maglakaw ta."]
    assert natural_lines({"sections": []}, "ceb") == []


def test_the_formatted_report_names_every_number():
    text = format_exposure("Day 1: A walk", _exposure(), get_a1_morphology("ceb").patterns, missing=2)
    assert "Day 1: A walk" in text
    assert "verb tokens 9 = bare 1 + affixed 8 (recognised 7, unrecognised 1)" in text
    assert "lines with NO cached analysis: 2" in text
    assert "verb:nag  tokens 3  naglakaw<lakaw nagdala<dala nag-ampo<ampo" in text
    assert "unrecognised: nakahibalo<hibalo" in text
    assert "mag-nag  tokens 6  pairs: ampo dala lakaw" in text
    assert "ma-na  tokens 1  pairs: none" in text
    assert "target (one pattern with 3+ same-root pairs): MET" in text


# ── main, over a deck file ───────────────────────────────────────────────────


def _row(analyses) -> str:
    return json.dumps([{"surface": a.surface, "lemma": a.lemma, "upos": a.upos} for a in analyses])


def _deck(tmp_path, lines=_LINES):
    path = tmp_path / "deck.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE lessons (id TEXT, curriculum_id TEXT, day INTEGER, data_json TEXT)")
    con.execute(
        "CREATE TABLE lemma_analysis_cache"
        " (sentence TEXT, language_code TEXT, model_version TEXT, analyses_json TEXT, updated_at TEXT)"
    )
    phrases = [{"text": line, "language_code": "ceb"} for line in lines]
    phrases.append({"text": "Wala ni sa cache.", "language_code": "ceb"})
    lesson = {"title": "A walk", "sections": [{"section_type": "natural_speed", "phrases": phrases}]}
    con.execute("INSERT INTO lessons VALUES ('a-walk', 'c', 1, ?)", (json.dumps(lesson),))
    for line, analyses in lines.items():
        con.execute(
            "INSERT INTO lemma_analysis_cache VALUES (?, 'ceb', 'v2', ?, '2026-10-04 03:01:29')",
            (line, _row(analyses)),
        )
    con.commit()
    con.close()
    return path


def test_main_reports_each_stored_lesson(tmp_path, capsys):
    assert main(["--deck", str(_deck(tmp_path)), "--language", "ceb"]) == 0
    out = capsys.readouterr().out
    assert "Day 1: A walk" in out
    assert "verb tokens 9 = bare 1 + affixed 8 (recognised 7, unrecognised 1)" in out
    # A line nobody analysed must be said out loud, never counted as verb-free.
    assert "lines with NO cached analysis: 1" in out


@pytest.mark.parametrize("stale_inserted", ["before", "after"])
def test_the_newest_analysis_of_a_sentence_wins(tmp_path, capsys, stale_inserted):
    """The cache holds one row per sentence per lemma-table build. An older
    build read ``Naglakaw`` as a noun; the report must use the newest row
    whichever order the rows sit in."""
    deck = _deck(tmp_path)
    stale = _row([_tok("Naglakaw", "naglakaw", "NOUN")])
    con = sqlite3.connect(deck)
    if stale_inserted == "before":
        # Re-insert the fresh row so the stale one has the LOWER rowid.
        fresh = con.execute(
            "SELECT analyses_json FROM lemma_analysis_cache WHERE sentence = 'Naglakaw si Paul.'"
        ).fetchone()[0]
        con.execute("DELETE FROM lemma_analysis_cache WHERE sentence = 'Naglakaw si Paul.'")
        con.execute(
            "INSERT INTO lemma_analysis_cache VALUES ('Naglakaw si Paul.', 'ceb', 'v1', ?, '2026-09-27 03:10:56')",
            (stale,),
        )
        con.execute(
            "INSERT INTO lemma_analysis_cache VALUES ('Naglakaw si Paul.', 'ceb', 'v2', ?, '2026-10-04 03:01:29')",
            (fresh,),
        )
    else:
        con.execute(
            "INSERT INTO lemma_analysis_cache VALUES ('Naglakaw si Paul.', 'ceb', 'v1', ?, '2026-09-27 03:10:56')",
            (stale,),
        )
    con.commit()
    con.close()
    assert main(["--deck", str(deck), "--language", "ceb"]) == 0
    assert "verb tokens 9 = bare 1 + affixed 8 (recognised 7, unrecognised 1)" in capsys.readouterr().out


def test_main_reads_a_deck_that_has_a_wal_beside_it(tmp_path, capsys):
    deck = _deck(tmp_path)
    (tmp_path / "deck.db-wal").write_bytes(b"")
    assert main(["--deck", str(deck), "--language", "ceb"]) == 0
    assert "Day 1: A walk" in capsys.readouterr().out


def test_main_refuses_a_language_with_no_affix_patterns(tmp_path, capsys):
    assert main(["--deck", str(_deck(tmp_path)), "--language", "no"]) == 2
    assert "registers no affix patterns" in capsys.readouterr().err
