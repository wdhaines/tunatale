"""EBU R128 loudness normalization via ffmpeg two-pass."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

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
        # ⚠️ errors="replace" is REQUIRED, not defensive tidying. With text=True
        # the decode happens INSIDE subprocess.run, so undecodable bytes raise
        # THERE — before the `except json.JSONDecodeError` below, which would not
        # catch a UnicodeDecodeError anyway. ffmpeg echoes the input filename and
        # its metadata to stderr, and ID3v1 tags are Latin-1 BY SPECIFICATION, so
        # a Norwegian word in a downloaded MP3's tags emits a bare 0xe5 ('å').
        # The escape reached the pre-stage's fetch and, under the old
        # return_exceptions=False, discarded the entire batch (tunatale-ouk.12;
        # observed byte-identical on three consecutive passes at position 1554).
        # Safe: the loudnorm JSON parsed below is ASCII, so replacing bytes
        # elsewhere in stderr cannot affect it.
        errors="replace",
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
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-af", af, "-ar", "44100", "-b:a", "128k", str(dst)],
        capture_output=True,
        text=True,
        # Same reason as _measure_loudness — and this one interpolates
        # result.stderr into its RuntimeError, so a strict decode would turn a
        # reportable ffmpeg failure into an unrelated UnicodeDecodeError.
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg loudnorm failed ({result.returncode}): {result.stderr[-300:]}")


def normalize_audio(src_bytes: bytes, *, target_lufs: float = TARGET_LUFS) -> bytes:
    """Two-pass EBU R128 normalization. Returns normalized MP3 bytes.

    Fails soft: if ffmpeg errors or produces an empty file, the ORIGINAL bytes
    are returned unchanged — un-normalized audio beats corrupt/zero-byte audio
    in the Anki media dir.
    """
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as src_f:
        src_path = Path(src_f.name)
        src_f.write(src_bytes)

    dst_path: Path | None = None
    try:
        stats = _measure_loudness(src_path)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as dst_f:
            dst_path = Path(dst_f.name)

        try:
            _apply_normalization(src_path, dst_path, stats, target_lufs)
        except RuntimeError:
            logger.warning("Loudness normalization failed — keeping original audio", exc_info=True)
            return src_bytes
        normalized = dst_path.read_bytes()
        if not normalized:
            logger.warning("Loudness normalization produced an empty file — keeping original audio")
            return src_bytes
        return normalized
    finally:
        src_path.unlink(missing_ok=True)
        if dst_path is not None:
            dst_path.unlink(missing_ok=True)
