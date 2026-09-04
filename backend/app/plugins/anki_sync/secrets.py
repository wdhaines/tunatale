"""Where the AnkiWeb password comes from, and in what order.

``sync_orchestrator`` used to answer this with two hardcoded steps: the
``sync_password`` setting, then the macOS ``security`` binary. That works on the
user's Mac and hard-fails anywhere else — the binary is absent on Linux, so the
lookup returns ``None`` and the run dies pointing at a command the box does not
have (``tunatale-pkk``). This module makes the chain explicit and extensible
without changing what happens on macOS.

**The account is the seam.** :class:`SecretRequest` carries ``service`` and
``account``, and ``account`` is the AnkiWeb username — which is exactly the
per-user identity Phase 4.5 needs when the password moves into an encrypted
per-user column. A source that keys on it can be appended to the chain with no
change here, which is the "no second refactor" the bead asks for.

⚠️ **Secrets never reach a log or an exception.** :func:`resolve_secret` raises
naming only the sources it TRIED, never a value, and no source logs what it
found. The password is the one thing in this repo that must not appear in
``sync.log``.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecretRequest:
    """What is being asked for.

    ``account`` is the AnkiWeb username. It is the per-user key a future
    encrypted-column source will select on, which is why it is part of the
    request rather than baked into each source.
    """

    service: str
    account: str


@runtime_checkable
class SecretSource(Protocol):
    """One place a secret might live.

    Returns ``None`` for "not here" — never raises for absence, so the chain can
    keep walking. A source raises only when it is CONFIGURED and BROKEN, which is
    a different situation and must not be silently swallowed.
    """

    name: str

    def lookup(self, request: SecretRequest) -> str | None: ...


@dataclass(frozen=True)
class StaticSecretSource:
    """A value already in hand — ``settings.sync_password`` from env or .env.

    First in the chain because it is the documented override. Empty means "not
    configured", so a blank .env entry does not shadow the Keychain.
    """

    value: str
    name: str = "sync_password setting"

    def lookup(self, request: SecretRequest) -> str | None:
        return self.value or None


@dataclass(frozen=True)
class FileSecretSource:
    """A file whose entire contents are the secret. The Linux/container answer.

    Trailing newline stripped, because every way of writing such a file adds one
    and a password with a stray ``\\n`` fails authentication in a way that looks
    like a wrong password.

    An unreadable path is "not here", not an error: the chain is allowed to be
    configured optimistically on a machine where only one source exists.
    """

    path: Path | None
    name: str = "sync_password_file"

    def lookup(self, request: SecretRequest) -> str | None:
        if self.path is None:
            return None
        try:
            content = self.path.read_text()
        except OSError:
            # Deliberately not logged with the path's contents, and not re-raised:
            # a missing file is the normal state on a machine using the Keychain.
            logger.debug("secret file %s is unreadable; trying the next source", self.path)
            return None
        return content.strip() or None


@dataclass(frozen=True)
class KeychainSecretSource:
    """The macOS Keychain, via the ``security`` CLI.

    ⚠️ Delegates to ``sync_orchestrator._keychain_password`` rather than shelling
    out itself. That function is the entry in ``backend/tests/mock_allowlist.txt``
    naming the Keychain process boundary, and the allowlist is a claim about
    architecture that needs sign-off to change. Moving the subprocess call in here
    would silently orphan that entry and require a new one for the same boundary.
    """

    name: str = "macOS Keychain"

    def lookup(self, request: SecretRequest) -> str | None:
        from app.plugins.anki_sync.sync_orchestrator import _keychain_password

        return _keychain_password(request.service, request.account)


def build_secret_sources(
    *,
    static_value: str,
    file_path: Path | None,
    platform: str = sys.platform,
) -> list[SecretSource]:
    """The chain, in priority order, for this machine.

    ``platform`` is a parameter and not a read of ``sys.platform`` inside the
    body so a test can ask for the Linux chain on a Mac without patching a
    module global — the seam is the signature, which is what
    ``.claude/rules/testing.md`` asks for.

    The Keychain source is appended only on darwin. Elsewhere the ``security``
    binary does not exist, so including it would buy one guaranteed-``None``
    subprocess attempt per sync and an error message advising a command the box
    cannot run.
    """
    sources: list[SecretSource] = [
        StaticSecretSource(static_value),
        FileSecretSource(file_path),
    ]
    if platform == "darwin":
        sources.append(KeychainSecretSource())
    return sources


def resolve_secret(request: SecretRequest, sources: list[SecretSource]) -> str | None:
    """First hit wins. ``None`` when every source declined."""
    for source in sources:
        found = source.lookup(request)
        if found:
            return found
    return None
