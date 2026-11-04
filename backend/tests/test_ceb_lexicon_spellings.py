"""The committed Wolff lexicon spells each oracle word as settled (tunatale-h0q3.38).

``tests/data/ceb_handbook_spelling_oracle.tsv`` lists every spelling the
whole-word rebuild changed, and every word a plain switch to whole-word counts
would have changed but should not, each with the spelling the lexicon must write
(KEEP) and the one it must not (REJECT). Only the Handbook-spelled fields are
read: a record also carries Wolff's own spelling, which is not converted.
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from pathlib import Path

import pytest

from app.plugins.languages.ceb.wolff_cards import LEXICON_PATH

ORACLE = Path(__file__).parent / "data" / "ceb_handbook_spelling_oracle.tsv"
_WORD = re.compile(r"[^\W\d_][\ẁ-ͯ\-]*")


def handbook_texts(record: dict) -> list[str]:
    """Every converted text of one lexicon record: forms, short forms, examples, phrases, attested words."""
    out = []
    for form in record["forms"]:
        out.append(form["hb"] or "")
        out.extend(hb for _, hb in form["short"])
        for sense in form["senses"]:
            out.append(sense["phrase"] or "")
            out.append(sense["ex"][0] if sense["ex"] else "")
    for hb, _, example, _ in record.get("attested", []):
        out += [hb, example]
    return out


def handbook_words(records: list[dict]) -> set[str]:
    return {
        unicodedata.normalize("NFC", w).lower()
        for rec in records
        for text in handbook_texts(rec)
        for w in _WORD.findall(text)
    }


def oracle_rows() -> list[tuple[str, str, str, str]]:
    lines = ORACLE.read_text(encoding="utf-8").splitlines()
    rows = [tuple(line.split("\t")) for line in lines if line and not line.startswith("#")]
    assert rows[0] == ("keep", "reject", "verdict", "why")
    return rows[1:]


@pytest.fixture(scope="module")
def words() -> set[str]:
    with gzip.open(LEXICON_PATH, "rt", encoding="utf-8") as fh:
        return handbook_words(json.load(fh)["entries"])


def test_handbook_words_skip_wolffs_own_spelling():
    rec = {
        "forms": [
            {
                "wolff": "panabákù",
                "hb": "panabákò",
                "short": [["iskína", "eskína"]],
                "senses": [{"phrase": "Sa ka", "ex": ["Màáyo", "Good."]}, {"phrase": None, "ex": None}],
            }
        ],
        "attested": [["mabálì", 1, "Dílì mabálì", "It can't be."]],
    }
    assert handbook_words([rec]) == {"panabákò", "eskína", "sa", "ka", "màáyo", "mabálì", "dílì"}


def test_the_oracle_covers_the_rebuild():
    rows = oracle_rows()
    assert len(rows) == 179
    assert {r[2] for r in rows} == {
        "corpus",
        "corpus-root",
        "follows-root",
        "other-word",
        "kept",
        "rule",
        "neutral",
        "residual",
    }


@pytest.mark.parametrize(("keep", "reject", "verdict", "why"), oracle_rows())
def test_the_lexicon_writes_the_settled_spelling(words, keep, reject, verdict, why):
    assert keep in words, f"{keep} ({verdict}: {why}) is missing"
    assert reject not in words, f"{reject} is written where {keep} is settled ({verdict}: {why})"
