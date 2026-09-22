#!/usr/bin/env python3
"""Email when the prod box's disk passes a threshold (tunatale-al6).

Runs on the BOX, not in the app: the host's own python3 (3.12 on the e2-micro),
stdlib only, from the systemd timer that install-disk-alert.sh writes. Keep it
3.12-compatible — ruff targets 3.14 and rewrites `except (A, B):` into the 3.14
comma form, which is a SyntaxError on the box. Catch one type per clause.

Sends through Gmail's submission port (GCE blocks outbound :25) with an app
password read from a root-only file, never argv or the environment.

    disk_alert.py --to ADDR --password-file /etc/tunatale/smtp-password
    disk_alert.py --to ADDR --password-file ... --test     # send one now

One email when usage crosses the threshold, a reminder every 24h while it stays
above, and one all-clear once it drops RECOVER_MARGIN points below (so a disk
hovering at the line does not email every hour). A failed send exits 1 without
recording anything, so the next run retries and `systemctl --failed` shows it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import smtplib
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

REMIND_EVERY = timedelta(hours=24)
RECOVER_MARGIN = 5.0


@dataclass(frozen=True)
class Decision:
    kind: str | None  # "alert" | "remind" | "recover" | None
    state: dict = field(default_factory=dict)


def decide(*, pct: float, threshold: float, state: dict, now: datetime) -> Decision:
    alerting = bool(state.get("alerting"))
    if pct >= threshold:
        if not alerting:
            return Decision("alert", {"alerting": True, "last_sent": now.isoformat()})
        last = datetime.fromisoformat(state["last_sent"])
        if now - last >= REMIND_EVERY:
            return Decision("remind", {"alerting": True, "last_sent": now.isoformat()})
        return Decision(None, state)
    if alerting and pct < threshold - RECOVER_MARGIN:
        return Decision("recover", {})
    return Decision(None, state)


def format_message(
    kind: str, *, pct: float, used_gb: float, total_gb: float, threshold: float, host: str
) -> tuple[str, str]:
    headline = {
        "alert": f"disk at {pct:.0f}% (threshold {threshold:.0f}%)",
        "remind": f"disk STILL at {pct:.0f}% (threshold {threshold:.0f}%)",
        "recover": f"disk back to {pct:.0f}%, all clear",
        "test": f"TEST alert, disk at {pct:.0f}%",
    }[kind]
    subject = f"[TunaTale {host}] {headline}"
    body = (
        f"{headline}\n\n"
        f"Used {used_gb:.1f} GB of {total_gb:.1f} GB on {host}.\n\n"
        "Where to look first, and the retention policy: docs/deployment.md, "
        "section 'Disk and log hygiene'.\n"
        "  sudo docker system df      # old images (deploy.sh prunes all but two)\n"
        "  backend/scripts/report_audio_retention.py   # lesson audio by last use; see the doc for the command\n"
    )
    return subject, body


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except OSError:
        return {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def run(
    *,
    pct: float,
    used_gb: float,
    total_gb: float,
    threshold: float,
    host: str,
    state_file: Path,
    now: datetime,
    send: Callable[[str, str], None],
    force_test: bool,
) -> int:
    if force_test:
        kind, new_state = "test", None
    else:
        d = decide(pct=pct, threshold=threshold, state=_load_state(state_file), now=now)
        if d.kind is None:
            return 0
        kind, new_state = d.kind, d.state
    subject, body = format_message(kind, pct=pct, used_gb=used_gb, total_gb=total_gb, threshold=threshold, host=host)
    try:
        send(subject, body)
    except OSError as exc:  # smtplib.SMTPException is an OSError
        print(f"disk_alert: send failed: {exc}", file=sys.stderr)
        return 1
    print(f"disk_alert: sent {kind}: {subject}")
    if new_state is not None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(new_state))
    return 0


def gmail_sender(address: str, password: str) -> Callable[[str, str], None]:
    def send(subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = address
        msg["To"] = address
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(address, password)
            smtp.send_message(msg)

    return send


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--to", required=True, help="Gmail address; also the sender and the SMTP login")
    p.add_argument("--password-file", type=Path, required=True)
    p.add_argument("--path", default="/", help="filesystem to measure")
    p.add_argument("--threshold", type=float, default=75.0)
    p.add_argument("--state-file", type=Path, default=Path("/var/lib/tunatale/disk-alert.json"))
    p.add_argument("--test", action="store_true", help="send one test email now, whatever the usage")
    args = p.parse_args(argv)

    usage = shutil.disk_usage(args.path)
    password = args.password_file.read_text().strip()
    return run(
        pct=100.0 * usage.used / usage.total,
        used_gb=usage.used / 1e9,
        total_gb=usage.total / 1e9,
        threshold=args.threshold,
        host=socket.gethostname(),
        state_file=args.state_file,
        now=datetime.now(UTC),
        send=gmail_sender(args.to, password),
        force_test=args.test,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
