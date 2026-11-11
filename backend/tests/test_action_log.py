"""The action log: which learner-facing writes actually get used (tunatale-5i1p.1).

The live server's ``run/api.log`` restarts with the server and carries no dates,
so nobody could say which actions the owner uses — and the owner will not remove
a feature on a guess. These tests defend one property: **a line is written if and
only if a learner-facing write succeeded, and the line names the route
template**, never the concrete path or a request body.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.action_log import daily_counts, format_counts, is_counted, main, record
from app.config import Settings, settings
from app.main import app
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


def _setup_app_state(language) -> None:
    """The minimum ``app.state`` a data route needs (see tests/_helpers/api_app_state)."""
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    app.state.srs_db = SRSDatabase(":memory:")
    app.state.content_store = ContentStore(":memory:")
    app.state.language = language


async def _post_ignored_lemma() -> object:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/srs/ignored-lemmas", json={"lemma": "snø", "language_code": "no"})


async def test_a_successful_write_appends_exactly_one_line(tmp_path, monkeypatch, language_no):
    log = tmp_path / "logs" / "actions.log"
    monkeypatch.setattr(settings, "action_log", log)
    _setup_app_state(language_no)

    resp = await _post_ignored_lemma()

    assert resp.status_code == 200
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d POST /api/srs/ignored-lemmas lang=\S+ user=\S+",
        lines[0],
    ), lines[0]


async def test_a_read_writes_nothing(tmp_path, monkeypatch, language_no):
    log = tmp_path / "logs" / "actions.log"
    monkeypatch.setattr(settings, "action_log", log)
    _setup_app_state(language_no)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/srs/items")

    assert resp.status_code == 200
    assert not log.exists()


async def test_a_failed_write_writes_nothing(tmp_path, monkeypatch, language_no):
    log = tmp_path / "logs" / "actions.log"
    monkeypatch.setattr(settings, "action_log", log)
    _setup_app_state(language_no)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/ignored-lemmas", json={})

    assert resp.status_code == 422
    assert not log.exists()


async def test_an_excluded_route_writes_nothing(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "actions.log"
    monkeypatch.setattr(settings, "action_log", log)
    monkeypatch.setattr(settings, "client_log", tmp_path / "client.log")
    monkeypatch.setattr(settings, "client_log_enabled", True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/client-log", json={"lines": ["hello"]})

    assert resp.status_code == 200
    assert not log.exists()


async def test_the_route_template_is_logged_not_the_concrete_path(tmp_path, monkeypatch, language_no):
    """The report counts routes; a concrete id would give every row its own line.

    Reuses the setup of test_api_srs_admin.py::test_reset_item_puts_it_back_in_new_state
    (``POST /api/srs/items/{item_id}/reset``), in Norwegian.
    """
    log = tmp_path / "logs" / "actions.log"
    monkeypatch.setattr(settings, "action_log", log)
    _setup_app_state(language_no)
    db = app.state.srs_db
    db.add_collocation(
        SyntacticUnit(text="hvala", translation="thank you", word_count=1, difficulty=1, source="corpus"),
        language_code="no",
    )
    rows, _ = db.list_collocations()
    row_id = rows[0][0]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/srs/items/{row_id}/reset")

    assert resp.status_code == 200
    (line,) = log.read_text(encoding="utf-8").splitlines()
    route = line.split()[2]
    assert "{" in route
    assert str(row_id) not in route


async def test_a_failed_append_does_not_fail_the_request(tmp_path, monkeypatch, caplog, language_no):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(settings, "action_log", blocker / "actions.log")
    _setup_app_state(language_no)

    with caplog.at_level(logging.WARNING):
        resp = await _post_ignored_lemma()

    assert resp.status_code == 200
    assert any("ACTION_LOG_WRITE_FAILED" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    ("method", "route", "status_code", "expected"),
    [
        ("POST", "/api/srs/drill-feedback", 200, True),
        ("DELETE", "/api/srs/sentence-blanks", 204, True),
        ("PUT", "/api/srs/items/{item_id}", 200, True),
        ("PATCH", "/api/srs/items/{item_id}", 200, True),
        ("POST", "/api/srs/drill-feedback", 399, True),
        ("POST", "/api/srs/drill-feedback", 400, False),
        ("POST", "/api/srs/drill-feedback", 500, False),
        ("GET", "/api/srs/review-queue", 200, False),
        ("OPTIONS", "/api/srs/drill-feedback", 200, False),
        ("POST", "/api/auth/login", 200, False),
        ("POST", "/api/client-log", 200, False),
        ("POST", "/health", 200, False),
    ],
)
def test_is_counted_literal_table(method, route, status_code, expected):
    assert is_counted(method, route, status_code) is expected


def _record_literal_lines(path) -> None:
    when = datetime(2026, 11, 10, 23, 59, 59, tzinfo=timezone(timedelta(hours=8)))
    record(path, when=when, method="POST", route="/api/srs/drill-feedback", language="no", user_id=None)
    record(path, when=when, method="POST", route="/api/srs/drill-feedback", language="no", user_id=2)
    record(path, when=when, method="DELETE", route="/api/srs/ignored-lemmas", language="no", user_id=2)
    record(
        path,
        when=datetime(2026, 11, 11, 0, 0, 1, tzinfo=timezone(timedelta(hours=8))),
        method="POST",
        route="/api/srs/drill-feedback",
        language="no",
        user_id=2,
    )


def test_record_and_daily_counts_are_literal(tmp_path):
    path = tmp_path / "logs" / "actions.log"
    _record_literal_lines(path)

    assert path.read_text(encoding="utf-8").splitlines()[0] == (
        "2026-11-10T23:59:59+08:00 POST /api/srs/drill-feedback lang=no user=-"
    )
    assert daily_counts(path) == (
        {
            ("2026-11-10", "POST", "/api/srs/drill-feedback"): 2,
            ("2026-11-10", "DELETE", "/api/srs/ignored-lemmas"): 1,
            ("2026-11-11", "POST", "/api/srs/drill-feedback"): 1,
        },
        0,
    )


def test_daily_counts_missing_file_and_malformed_lines(tmp_path):
    assert daily_counts(tmp_path / "absent.log") == ({}, 0)

    path = tmp_path / "actions.log"
    path.write_text(
        "2026-11-10T23:59:59+08:00 POST /api/srs/drill-feedback lang=no user=-\ngarbage\n\n",
        encoding="utf-8",
    )
    assert daily_counts(path) == ({("2026-11-10", "POST", "/api/srs/drill-feedback"): 1}, 2)


def test_format_counts_is_literal():
    assert format_counts(
        {
            ("2026-11-10", "POST", "/api/srs/drill-feedback"): 2,
            ("2026-11-10", "DELETE", "/api/srs/ignored-lemmas"): 1,
            ("2026-11-11", "POST", "/api/srs/drill-feedback"): 1,
        }
    ) == [
        "2026-11-10     1  DELETE /api/srs/ignored-lemmas",
        "2026-11-10     2  POST /api/srs/drill-feedback",
        "2026-11-11     1  POST /api/srs/drill-feedback",
    ]


def test_main_prints_counts_and_malformed_lines(tmp_path, capsys):
    path = tmp_path / "logs" / "actions.log"
    _record_literal_lines(path)

    assert main([str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "2026-11-10     1  DELETE /api/srs/ignored-lemmas",
        "2026-11-10     2  POST /api/srs/drill-feedback",
        "2026-11-11     1  POST /api/srs/drill-feedback",
    ]

    with path.open("a", encoding="utf-8") as fh:
        fh.write("garbage\n")

    assert main([str(path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "malformed lines: 1"


def test_main_without_argv_reads_the_settings_path(tmp_path, capsys):
    record(
        settings.action_log,
        when=datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC),
        method="POST",
        route="/api/srs/drill-feedback",
        language="no",
        user_id=None,
    )

    assert main([]) == 0
    assert capsys.readouterr().out.splitlines() == ["2026-11-10     1  POST /api/srs/drill-feedback"]


def test_the_default_path_is_the_dated_log():
    assert Settings().action_log.parts[-2:] == ("logs", "actions.log")
