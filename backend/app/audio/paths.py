"""Resolve a recorded audio path against THIS machine's audio directory.

``audio_files.file_path`` records where a render was written, and that string
does not survive leaving the machine that wrote it. Measured on the real
Norwegian DB (`tunatale-kbb.15`): of 104 rows, **100 are absolute**
(``/Users/<author>/…/backend/output/audio/<uuid>.opus``) and 4 are relative
(``output/audio/<uuid>.opus``). Both shapes were written within the same minute,
so this is two code paths disagreeing rather than an old format and a new one.

On the production box both shapes miss, for different reasons: the absolute ones
name a home directory that does not exist on Linux, and the relative ones
resolve against the process CWD (``/app`` in the container) rather than the
audio directory. The audio bytes migrate perfectly well — 105 files restored and
decoded in the 2026-09-11 drill — so a 404 here is purely a bookkeeping failure.

The resolver only *locates* a candidate. It deliberately does not check
existence, so a caller's ``if not path.exists(): 404`` keeps working and a
genuinely missing render cannot be laundered into a success.
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings


def resolve_audio_path(file_path: str | Path) -> Path:
    """Where *file_path* actually lives on this machine.

    An absolute path that exists is returned untouched — on the machine that
    wrote it, nothing changes. Otherwise the basename is looked up under
    ``settings.audio_dir``, where every render lands (the tree is flat:
    ``<uuid>.opus``). That covers a DB moved between machines in either
    direction, and a relative path whose CWD is no longer the backend directory.

    Reads the setting rather than taking an injected directory: ``AUDIO_DIR`` is
    what the container sets, and an override nothing passes would be a branch no
    test exercises honestly.
    """
    raw = Path(file_path)
    if raw.is_absolute() and raw.exists():
        return raw
    return Path(settings.audio_dir) / raw.name
