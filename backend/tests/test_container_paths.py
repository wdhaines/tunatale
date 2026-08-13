"""Container-safe mutable paths (Deploy P0.1, tunatale-rn1).

Every oracle here runs in a **fresh subprocess** with a different CWD and a
prepared environment. That is not ceremony — it is the only way to observe the
bug these tests pin:

  * ``conftest.py`` monkeypatches all three ``_MEDIA_DIR`` constants to a temp
    dir, so an in-process assertion sees the patched value and can never catch
    the import-side/serve-side divergence.
  * The divergence itself only appears when the process CWD is not ``backend/``.
    ``settings.media_dir`` defaulted to a CWD-relative ``./media`` while the
    serving route walked ``__file__`` upward, so the two halves coincided under
    the dev CWD and split silently anywhere else — a container, a systemd unit,
    or a restore drill.
  * ``HOME`` relocation works because ``Path("~/…").expanduser()`` is evaluated
    at config-module import. A test that sets ``HOME`` after the import proves
    nothing; only a fresh process does.

Demonstrated 2026-08-12 by the restore drill: with ``MEDIA_DIR`` pointed at a
restored media tree, ``/api/srs/media/{filename}`` served the ORIGINAL bytes
from ``backend/media``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _probe(code: str, env_overrides: dict[str, str], cwd: Path) -> dict:
    """Run *code* in a fresh interpreter from *cwd* and return its JSON stdout.

    The environment is built from a minimal base rather than inherited: a
    developer's exported ``MEDIA_DIR``/``HOME`` would otherwise decide the
    result, which is the same class of silent override ``check_prod_env.py``
    documents at length.
    """
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(BACKEND_DIR),
        "LLM_MODE": "mock",
        **env_overrides,
    }
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"probe failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return json.loads(proc.stdout)


_MEDIA_PROBE = """
import json
from app.config import settings
import app.api.srs as srs
import app.audio.cloze_tts as cloze_tts
import app.cards.media.vocab_media as vocab_media
import app.plugins.anki_sync.sync as sync
print(json.dumps({
    "setting": str(settings.media_dir),
    "srs": str(srs._MEDIA_DIR),
    "cloze_tts": str(cloze_tts._MEDIA_DIR),
    "vocab_media": str(vocab_media._MEDIA_DIR),
    "sync": str(sync._MEDIA_DIR),
}))
"""


def test_media_dir_default_is_absolute_and_cwd_independent(tmp_path):
    """The default media_dir resolves to backend/media from any CWD.

    A CWD-relative default is the import-side half of the divergence: run the
    app from anywhere but ``backend/`` and imports land somewhere the serving
    route will never look.
    """
    result = _probe(_MEDIA_PROBE, {}, cwd=tmp_path)

    assert Path(result["setting"]).is_absolute(), result["setting"]
    assert Path(result["setting"]) == BACKEND_DIR / "media"


def test_all_media_dir_constants_follow_the_setting(tmp_path):
    """All four hardcoded constants derive from settings.media_dir.

    Four, not three: ``app/audio/cloze_tts.py`` carries a fourth copy that the
    scope note missed and that ``conftest.py`` does not pin. Each walked
    ``__file__`` upward by a different number of parents (3, 3, 4, 4) from a
    different depth; all four landed on ``backend/media``, verified before this
    change, so collapsing them onto one setting is behaviour-preserving.
    """
    media = tmp_path / "restored-media"
    media.mkdir()

    result = _probe(_MEDIA_PROBE, {"MEDIA_DIR": str(media)}, cwd=tmp_path)

    assert result["setting"] == str(media)
    for module in ("srs", "cloze_tts", "vocab_media", "sync"):
        assert result[module] == str(media), (
            f"{module}._MEDIA_DIR ignored MEDIA_DIR — the import side and the "
            f"serve side can diverge again: {result[module]!r} != {str(media)!r}"
        )


def test_audio_dir_default_matches_the_pre_change_lifespan_path(tmp_path):
    """audio_dir's default is byte-identical to what main.py hardcoded.

    A dev with no env change must see no behaviour change: the value
    ``lifespan`` used to compute as ``_BACKEND_DIR / "output/audio"``.
    """
    code = """
import json
from app.config import settings
print(json.dumps({"audio_dir": str(settings.audio_dir)}))
"""
    result = _probe(code, {}, cwd=tmp_path)

    assert Path(result["audio_dir"]).is_absolute()
    assert Path(result["audio_dir"]) == BACKEND_DIR / "output/audio"


def test_audio_dir_is_relocatable_by_env(tmp_path):
    """AUDIO_DIR relocates lesson audio — it was not a setting at all before."""
    audio = tmp_path / "restored-audio"

    code = """
import json
from app.config import settings
print(json.dumps({"audio_dir": str(settings.audio_dir)}))
"""
    result = _probe(code, {"AUDIO_DIR": str(audio)}, cwd=tmp_path)

    assert result["audio_dir"] == str(audio)


def test_home_relocates_every_remaining_mutable_path(tmp_path):
    """HOME=/data moves the whole ``~/.tunatale`` family in one move.

    These are deliberately NOT converted one by one — ``expanduser()`` at
    config-import time already relocates them together. This pins that, so a
    later refactor to an eagerly-resolved absolute default cannot quietly break
    the container story. ``model_discovery._CACHE_PATH`` is included because it
    is a module-level constant rather than a setting, and was the one the scope
    note flagged as needing verification.
    """
    home = tmp_path / "data"
    home.mkdir()

    code = """
import json
from app.config import settings
from app.plugins.anki_sync.model_discovery import _CACHE_PATH
print(json.dumps({
    "llm_usage_ledger_path": str(settings.llm_usage_ledger_path),
    "anki_backup_dir": str(settings.anki_backup_dir),
    "db_backup_dir": str(settings.db_backup_dir),
    "migration_backup_dir": str(settings.migration_backup_dir),
    "anki_fallback_log": str(settings.anki_fallback_log),
    "sync_log": str(settings.sync_log),
    "tt_collection_path": str(settings.tt_collection_path),
    "model_discovery_cache": str(_CACHE_PATH),
}))
"""
    result = _probe(code, {"HOME": str(home)}, cwd=tmp_path)

    for name, value in result.items():
        assert Path(value).is_relative_to(home), f"{name} escaped HOME relocation: {value}"
