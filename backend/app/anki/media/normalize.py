"""EBU R128 loudness normalization via ffmpeg two-pass."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

TARGET_LUFS = -23.0
TARGET_LRA = 7.0
TARGET_TP = -2.0


def _measure_loudness(path: Path) -> dict:
    """First pass: measure loudness stats via loudnorm filter. Returns dict or {}."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(path),
            "-af",
            f"loudnorm=I={TARGET_LUFS}:LRA={TARGET_LRA}:TP={TARGET_TP}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    stderr = result.stderr
    json_start = stderr.rfind("{")
    json_end = stderr.rfind("}") + 1
    if json_start == -1 or json_end == 0:
        return {}
    try:
        return json.loads(stderr[json_start:json_end])
    except json.JSONDecodeError:
        return {}


def _apply_normalization(src: Path, dst: Path, stats: dict, target_lufs: float) -> None:
    """Second pass: apply loudnorm with measured stats."""
    if not stats:
        af = f"loudnorm=I={target_lufs}:LRA={TARGET_LRA}:TP={TARGET_TP}"
    else:
        il = stats.get("input_i", "-99")
        lra = stats.get("input_lra", "0")
        tp = stats.get("input_tp", "-99")
        thr = stats.get("input_thresh", "-99")
        off = stats.get("target_offset", "0")
        af = (
            f"loudnorm=I={target_lufs}:LRA={TARGET_LRA}:TP={TARGET_TP}"
            f":measured_I={il}:measured_LRA={lra}:measured_TP={tp}"
            f":measured_thresh={thr}:offset={off}:linear=true:print_format=none"
        )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-af", af, "-ar", "44100", "-b:a", "128k", str(dst)],
        capture_output=True,
        text=True,
    )


def normalize_audio(src_bytes: bytes, *, target_lufs: float = TARGET_LUFS) -> bytes:
    """Two-pass EBU R128 normalization. Returns normalized MP3 bytes."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as src_f:
        src_path = Path(src_f.name)
        src_f.write(src_bytes)

    dst_path: Path | None = None
    try:
        stats = _measure_loudness(src_path)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as dst_f:
            dst_path = Path(dst_f.name)

        _apply_normalization(src_path, dst_path, stats, target_lufs)
        return dst_path.read_bytes()
    finally:
        src_path.unlink(missing_ok=True)
        if dst_path is not None:
            dst_path.unlink(missing_ok=True)
