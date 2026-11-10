"""The Reference tab's verb chart (tunatale-h0q3.47): its data file and its two routes.

The chart moved out of a private Artifact into ``ceb/data/verb_chart.json`` (owner,
2026-11-09). These tests pin what may ship (only public-domain and CC examples: Wolff's
dictionary ``W``, Bunye & Yap ``B``, Wikipedia ``P``, never Wolff's course ``C``), the
shape the frontend draws from, and that the API serves the file whole.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.languages import get_language, get_verb_chart_path
from app.main import app
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore

# Measured 2026-11-09 from the build of verb_chart.json (scratch build_verb_chart.py):
# 67 examples in the private chart, 4 course examples removed, 2 Wolff ones put back.
GROUPS = ["agent", "patient", "circumstance", "instrument", "stative"]
ROW_COUNT = 15
SOURCE_COUNTS = {"W": 61, "B": 3, "P": 1}
AFFIX_KINDS = {"standard", "variant", "literary", "inferred"}


def _chart() -> dict:
    path = get_verb_chart_path("ceb")
    assert path is not None
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(chart: dict) -> list[dict]:
    return [row for group in chart["groups"] for row in group["rows"]]


def _cells(row: dict) -> list[dict | None]:
    cells: list[dict | None] = list(row["volitional"] or [])
    if isinstance(row["potential"], list):
        cells += row["potential"]
    return cells


class TestFacet:
    def test_cebuano_registers_the_chart(self):
        path = get_verb_chart_path("ceb")
        assert path is not None
        assert path.name == "verb_chart.json"
        assert path.is_file()

    def test_norwegian_registers_none(self):
        assert get_verb_chart_path("no") is None


class TestChartData:
    def test_groups_and_rows(self):
        chart = _chart()
        assert [g["id"] for g in chart["groups"]] == GROUPS
        assert len(_rows(chart)) == ROW_COUNT
        assert [g["verb_type"] for g in chart["groups"]] == ["dynamic"] * 4 + ["stative"]

    def test_row_ids_are_unique(self):
        ids = [row["id"] for row in _rows(_chart())]
        assert len(ids) == len(set(ids))

    def test_every_row_fills_its_columns(self):
        """Four volitional cells and three potential ones, or a whole side marked absent:
        the desktop grid has ten columns and a short row would shift every cell after it."""
        for row in _rows(_chart()):
            assert row["volitional"] is None or len(row["volitional"]) == 4, row["id"]
            assert row["potential"] in (None, "above") or len(row["potential"]) == 3, row["id"]
            assert len(row["glosses"]) == 7, row["id"]

    def test_a_shared_affix_never_starts_a_side(self):
        """``same_as_left`` means "the affix of the cell on my left spans me"; the first
        cell of a side has no left neighbour on that side."""
        for row in _rows(_chart()):
            for side in (row["volitional"], row["potential"]):
                if isinstance(side, list) and side[0] is not None:
                    assert side[0]["same_as_left"] is False, row["id"]

    def test_cells_carry_affixes_or_share_them(self):
        for row in _rows(_chart()):
            for cell in _cells(row):
                if cell is None:
                    continue
                if cell["same_as_left"]:
                    assert cell["affixes"] == [], row["id"]
                else:
                    assert cell["affixes"], row["id"]
                    assert {a["kind"] for a in cell["affixes"]} <= AFFIX_KINDS, row["id"]

    def test_only_shippable_sources(self):
        """The course is all rights reserved: a ``C`` example must never reach the repo."""
        chart = _chart()
        sources = [ex["source"] for row in _rows(chart) for c in _cells(row) if c for ex in c["examples"]]
        assert {s: sources.count(s) for s in set(sources)} == SOURCE_COUNTS
        assert set(chart["sources"]) == set(SOURCE_COUNTS)

    def test_the_course_examples_were_replaced_by_wolffs(self):
        rows = {row["id"]: row for row in _rows(_chart())}
        realis = rows["agent-mo"]["volitional"][0]["examples"]
        assert realis == [
            {
                "ceb": "Mìádto (nìádto, ning-ádto, ming-ádto) ko dídto gahápon",
                "en": "I went there yesterday.",
                "source": "W",
                "source_spelling": "Miadtu (niadtu, ning-adtu, ming-adtu) ku didtu gahápun",
            }
        ]
        command = rows["instrument-i"]["volitional"][3]["examples"]
        assert [ex["ceb"] for ex in command] == ["Isaká ang malíta sa tâas"]
        agent_command = rows["agent-mo"]["volitional"][3]["examples"]
        assert [ex["source"] for ex in agent_command] == ["B"]

    def test_every_footnote_reference_resolves(self):
        chart = _chart()
        notes = {n["n"] for n in chart["footnotes"]}
        refs = [m["footnote"] for m in chart["modes"]]
        refs += [c["footnote"] for m in chart["modes"] for c in m["columns"]]
        refs += [g["footnote"] for g in chart["groups"]]
        refs += [r["footnote"] for r in _rows(chart)]
        refs += [item["footnote"] for item in chart["legend"]]
        assert {r for r in refs if r is not None} <= notes
        assert notes == set(range(1, 9))


@pytest.fixture
def ceb_and_no_app():
    """Cebuano (has a chart) and Norwegian (has none) served side by side."""
    dbs = {"ceb": SRSDatabase(":memory:"), "no": SRSDatabase(":memory:")}
    app.state.srs_dbs = dbs
    app.state.content_stores = {code: ContentStore(":memory:") for code in dbs}
    app.state.languages = {code: get_language(code) for code in dbs}
    app.state.srs_db = dbs["no"]
    app.state.content_store = app.state.content_stores["no"]
    app.state.language = get_language("no")
    try:
        yield
    finally:
        for db in dbs.values():
            db.close()
        for attr in ("srs_dbs", "content_stores", "languages", "srs_db", "content_store", "language"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)


async def _get(path: str, language: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers={"X-TT-Language": language})


class TestReferenceRoutes:
    async def test_cebuano_lists_the_verb_chart(self, ceb_and_no_app):
        resp = await _get("/api/reference", "ceb")
        assert resp.status_code == 200
        assert resp.json() == {"pages": [{"id": "verb-chart", "title": "The verb chart"}]}

    async def test_norwegian_lists_nothing(self, ceb_and_no_app):
        """An empty list, not a 404: the frontend hides the tab on it."""
        resp = await _get("/api/reference", "no")
        assert resp.status_code == 200
        assert resp.json() == {"pages": []}

    async def test_the_chart_is_served_whole(self, ceb_and_no_app):
        """The response equals the file: the response model drops nothing and adds nothing."""
        resp = await _get("/api/reference/verb-chart", "ceb")
        assert resp.status_code == 200
        assert resp.json() == _chart()

    async def test_no_chart_is_a_404(self, ceb_and_no_app):
        resp = await _get("/api/reference/verb-chart", "no")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "No verb chart for language 'no'"}
