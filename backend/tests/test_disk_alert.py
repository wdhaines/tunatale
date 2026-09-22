"""The prod box's disk alert (tunatale-al6).

The user's decision (2026-09-22): keep all generated audio, and EMAIL at 75% so
there is room to act. The script runs on the box's own python3 (3.12, stdlib
only) from a systemd timer, so everything here is pure logic plus an injected
sender — no network, no real disk.
"""

from datetime import UTC, datetime, timedelta

import pytest

from scripts.disk_alert import Decision, decide, format_message, run

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def test_below_threshold_with_no_prior_alert_sends_nothing():
    assert decide(pct=60.0, threshold=75.0, state={}, now=T0) == Decision(None, {})


def test_crossing_the_threshold_alerts_and_records_it():
    d = decide(pct=76.0, threshold=75.0, state={}, now=T0)
    assert d.kind == "alert"
    assert d.state == {"alerting": True, "last_sent": T0.isoformat()}


def test_staying_above_is_quiet_within_a_day():
    """Hourly timer: one email per crossing, not one per hour."""
    state = {"alerting": True, "last_sent": T0.isoformat()}
    d = decide(pct=80.0, threshold=75.0, state=state, now=T0 + timedelta(hours=23))
    assert d.kind is None
    assert d.state == state


def test_staying_above_reminds_once_a_day():
    state = {"alerting": True, "last_sent": T0.isoformat()}
    later = T0 + timedelta(hours=24)
    d = decide(pct=80.0, threshold=75.0, state=state, now=later)
    assert d.kind == "remind"
    assert d.state == {"alerting": True, "last_sent": later.isoformat()}


def test_recovery_needs_a_margin_so_it_does_not_flap():
    """74.9% after an alert is not recovered: a box hovering at the line would
    otherwise email alert/recover every hour."""
    state = {"alerting": True, "last_sent": T0.isoformat()}
    assert decide(pct=74.9, threshold=75.0, state=state, now=T0 + timedelta(hours=1)).kind is None


def test_dropping_clear_of_the_margin_sends_one_all_clear():
    state = {"alerting": True, "last_sent": T0.isoformat()}
    d = decide(pct=69.0, threshold=75.0, state=state, now=T0 + timedelta(hours=1))
    assert d.kind == "recover"
    assert d.state == {}


def test_message_names_the_numbers_and_what_to_do():
    subject, body = format_message("alert", pct=76.4, used_gb=22.1, total_gb=29.0, threshold=75.0, host="tunatale")
    assert "76%" in subject and "tunatale" in subject
    assert "22.1 GB of 29.0 GB" in body
    assert "docs/deployment.md" in body


def test_run_sends_and_persists_state(tmp_path):
    sent = []
    state_file = tmp_path / "state.json"
    code = run(
        pct=80.0,
        used_gb=23.2,
        total_gb=29.0,
        threshold=75.0,
        host="tunatale",
        state_file=state_file,
        now=T0,
        send=lambda subject, body: sent.append(subject),
        force_test=False,
    )
    assert code == 0
    assert len(sent) == 1
    assert '"alerting": true' in state_file.read_text()


def test_a_failed_send_exits_non_zero_and_does_not_record_it(tmp_path):
    """A send that failed must be retried next hour, and the timer unit must go
    red (systemctl --failed), so the state is NOT advanced."""

    def boom(subject, body):
        raise OSError("smtp down")

    state_file = tmp_path / "state.json"
    code = run(
        pct=80.0,
        used_gb=23.2,
        total_gb=29.0,
        threshold=75.0,
        host="tunatale",
        state_file=state_file,
        now=T0,
        send=boom,
        force_test=False,
    )
    assert code == 1
    assert not state_file.exists()


def test_force_test_sends_even_when_healthy_and_leaves_state_alone(tmp_path):
    sent = []
    state_file = tmp_path / "state.json"
    code = run(
        pct=40.0,
        used_gb=11.0,
        total_gb=29.0,
        threshold=75.0,
        host="tunatale",
        state_file=state_file,
        now=T0,
        send=lambda subject, body: sent.append(subject),
        force_test=True,
    )
    assert code == 0
    assert sent and "TEST" in sent[0]
    assert not state_file.exists()


@pytest.mark.parametrize("raw", ["", "not json", "[]"])
def test_a_corrupt_state_file_is_treated_as_empty(tmp_path, raw):
    state_file = tmp_path / "state.json"
    state_file.write_text(raw)
    sent = []
    run(
        pct=80.0,
        used_gb=23.2,
        total_gb=29.0,
        threshold=75.0,
        host="tunatale",
        state_file=state_file,
        now=T0,
        send=lambda s, b: sent.append(s),
        force_test=False,
    )
    assert len(sent) == 1


def test_the_script_parses_as_python_3_12_the_boxs_interpreter():
    """ruff targets 3.14 and rewrites multi-type excepts into PEP 758 form, a
    SyntaxError on the box's python3.12. The tests here run on 3.14 and could
    never notice, so check the grammar the box will actually use."""
    import ast
    from pathlib import Path

    import scripts.disk_alert as mod

    ast.parse(Path(mod.__file__).read_text(), feature_version=(3, 12))
