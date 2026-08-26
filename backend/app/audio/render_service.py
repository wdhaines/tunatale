"""Shared audio render service — extracted from POST /api/audio/render."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import uuid
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio.cues import Cue, CueTiming, build_cue_manifest
from app.audio.transcode import CODEC_EXT, encode_audio
from app.config import settings
from app.generation.section_builder import SECTION_TITLES
from app.models.lesson import SectionType
from app.storage.store import ContentStore

logger = logging.getLogger(__name__)


def _concat_opus_concat_demuxer(file_paths: list[Path], output_path: Path) -> None:
    """Concatenate Opus files byte-for-byte via ffmpeg's concat demuxer with ``-c copy``.

    All input files MUST share stream parameters (codec, sample rate, channels).
    The output is a valid Ogg/Opus file. No re-encoding occurs — every packet
    is copied verbatim, so the concatenated file's audio is bit-identical to the
    concatenation of the inputs' decoded audio.

    Raises ``RuntimeError`` if ffmpeg exits non-zero.
    """
    if not file_paths:
        raise ValueError("concat requires at least one file")

    if len(file_paths) == 1:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(file_paths[0].read_bytes())
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", dir=str(output_path.parent), delete=False) as concat_list:
        for fp in file_paths:
            concat_list.write(f"file '{fp}'\n")
        concat_list_path = concat_list.name

    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_list_path,
                "-c",
                "copy",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed ({proc.returncode}): {proc.stderr}")
    finally:
        Path(concat_list_path).unlink(missing_ok=True)


# Map from slow section type → the structural-twin section type whose L2 line
# cues provide the natural-text scrub source.  slow_speed numbers its L2 lines
# identically to natural_speed (both include every line), and slow_translated
# numbers identically to translated (both skip lines without translations).
_SLOW_TEXT_SOURCE: dict[SectionType, SectionType] = {
    SectionType.SLOW_SPEED: SectionType.NATURAL_SPEED,
    SectionType.SLOW_TRANSLATED: SectionType.TRANSLATED,
    SectionType.SLOW_EN_TRANSLATED: SectionType.EN_TRANSLATED,
}


def derive_section_cues(cues: list[Cue], lesson) -> dict[int, list[Cue]]:
    """Group the full manifest by section_index, rebase, and scrub ellipsis text.

    Returns ``{section_index: [Cue, ...]}`` — one entry per section.  The lesson
    title cue (``section_index=None``) is excluded.

    For SLOW_SPEED / SLOW_TRANSLATED sections, each L2 line cue's ``text`` is
    overwritten with the natural (non-slowed) text from the structural-twin
    section so that the player subtitle never shows ellipsis-broken text.
    """
    l2_code = lesson.language_code

    # Group by section_index, preserving cue order.
    groups: dict[int, list[Cue]] = defaultdict(list)
    for cue in cues:
        if cue.section_index is not None:
            groups[cue.section_index].append(cue)

    # Build text scrub maps for slow sections from their structural twin.
    scrub_maps: dict[int, dict[int, str]] = {}
    for sec_idx, section in enumerate(lesson.sections):
        source_type = _SLOW_TEXT_SOURCE.get(section.section_type)
        if source_type is None:
            continue
        # Find the structural-twin group index.
        twin_idx: int | None = None
        for other_idx, other_sec in enumerate(lesson.sections):
            if other_sec.section_type == source_type:
                twin_idx = other_idx
                break
        if twin_idx is None or twin_idx not in groups:
            continue
        text_map: dict[int, str] = {}
        for c in groups[twin_idx]:
            if c.language_code == l2_code and c.ref and c.ref.get("kind") == "line":
                text_map.setdefault(c.ref["target_index"], c.text)
        if text_map:
            scrub_maps[sec_idx] = text_map

    result: dict[int, list[Cue]] = {}
    for sec_idx, group in sorted(groups.items()):
        first_start = group[0].start_ms
        rebased = []
        for c in group:
            new_c = replace(
                c,
                start_ms=c.start_ms - first_start,
                end_ms=c.end_ms - first_start,
            )
            # Scrub ellipsis text for slow sections.
            if (
                sec_idx in scrub_maps
                and new_c.ref
                and new_c.ref.get("kind") == "line"
                and new_c.language_code == l2_code
            ):
                target = new_c.ref["target_index"]
                if target in scrub_maps[sec_idx]:
                    new_c = replace(new_c, text=scrub_maps[sec_idx][target])
            rebased.append(new_c)
        result[sec_idx] = rebased
    return result


async def render_lesson_audio(
    store: ContentStore,
    renderer,
    audio_dir: Path,
    lesson_id: str,
    lesson,
) -> dict:
    """Render audio for a lesson and persist the results.

    Lifted verbatim from the POST /api/audio/render endpoint. Returns the same
    payload shape so both the endpoint and the pipeline caller get identical
    results.
    """
    old_rows = store.list_audio_files_for_lesson(lesson_id)
    old_file_paths = [r["file_path"] for r in old_rows]

    audio_dir.mkdir(parents=True, exist_ok=True)

    ext = CODEC_EXT.get(settings.audio_delivery_codec, "wav")
    audio_id = str(uuid.uuid4())
    full_path = audio_dir / f"{audio_id}.{ext}"

    section_ids = [str(uuid.uuid4()) for _ in lesson.sections]
    section_paths = [audio_dir / f"{sid}.{ext}" for sid in section_ids]

    cues = await renderer.render(lesson, full_path, section_paths=section_paths)
    cues_json = json.dumps([asdict(c) for c in cues], ensure_ascii=False)

    section_cues = derive_section_cues(cues, lesson)

    store.delete_audio_files_for_lesson(lesson_id)
    store.save_audio_file(audio_id, lesson_id, str(full_path), cues_json=cues_json)
    for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True)):
        sec_cues = section_cues.get(i, [])
        sec_cues_json = json.dumps([asdict(c) for c in sec_cues], ensure_ascii=False) if sec_cues else None
        store.save_audio_file(
            sid,
            lesson_id,
            str(section_paths[i]),
            section_index=i,
            section_type=section.section_type.value,
            cues_json=sec_cues_json,
        )

    for fp in old_file_paths:
        Path(fp).unlink(missing_ok=True)

    sections = [
        {
            "audio_id": sid,
            "section_index": i,
            "section_type": section.section_type.value,
            "title": SECTION_TITLES.get(section.section_type, section.section_type.value),
        }
        for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True))
    ]

    return {
        "audio_id": audio_id,
        "lesson_id": lesson_id,
        "sections": sections,
        "cues": json.loads(cues_json),
    }


def _read_audio_duration(path: Path) -> float:
    """Read the duration of an audio file in seconds via ffprobe.

    Raises ``RuntimeError`` if ffprobe fails or the duration is invalid.
    """
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({proc.returncode}): {proc.stderr}")
    try:
        return float(proc.stdout.strip())
    except ValueError as e:
        raise RuntimeError(f"invalid duration from ffprobe: {proc.stdout.strip()!r}") from e


def _transcode_to_delivery(src: Path, dest: Path, rate: int) -> None:
    """Re-encode *src* into the delivery codec at *rate*, mono.

    Every input to the concat demuxer must share stream parameters exactly. The
    TTS hands back MP3 whatever the filename says, so its output has to be
    normalised before it can sit beside the Opus section files.
    """
    samples, src_rate = sf.read(str(src), dtype="float32", always_2d=True)
    if src_rate != rate:
        idx = np.linspace(0, len(samples) - 1, num=int(round(len(samples) * rate / src_rate)))
        samples = np.stack(
            [np.interp(idx, np.arange(len(samples)), samples[:, c]) for c in range(samples.shape[1])], axis=1
        ).astype("float32")
    if samples.shape[1] > 1:
        samples = samples.mean(axis=1, keepdims=True).astype("float32")
    if settings.audio_delivery_codec == "wav":
        sf.write(str(dest), samples, rate, subtype="PCM_16")
        return
    dest.write_bytes(encode_audio(samples, rate, settings.audio_delivery_codec, settings.audio_delivery_bitrate))


def _write_silence(path: Path, duration_ms: int, rate: int) -> None:
    """Write *duration_ms* of silence in the delivery codec.

    Must match the section files' stream parameters EXACTLY — same codec, rate,
    channel count and bitrate — or ffmpeg's concat demuxer refuses the copy. The
    rate is passed in from the freshly rendered section rather than assumed, and
    the buffer is mono because that is what the renderer produces.
    """
    frames = int(round(rate * duration_ms / 1000))
    samples = np.zeros((frames, 1), dtype="float32")
    if settings.audio_delivery_codec == "wav":
        sf.write(str(path), samples, rate, subtype="PCM_16")
        return
    path.write_bytes(encode_audio(samples, rate, settings.audio_delivery_codec, settings.audio_delivery_bitrate))


def _cues_from_relative(lesson, rel_cues: list[tuple[int, int, int]], section_idx: int, rate: int) -> list[Cue]:
    """Section-relative Cue objects from ``render_section``'s frame triples.

    Goes through the same build_cue_manifest / derive_section_cues pair the full
    render uses, rather than constructing Cue objects by hand: those two carry
    the slow-section text scrubbing and the key-phrase ref wiring, and a
    hand-rolled cue would silently lack both.
    """
    timing = [
        CueTiming(section_index=section_idx, phrase_index=ph_idx, start_frame=start, end_frame=end)
        for ph_idx, start, end in rel_cues
    ]
    manifest = build_cue_manifest(lesson, timing, rate)
    return derive_section_cues(manifest, lesson).get(section_idx, [])


async def reassemble_lesson_audio(
    store: ContentStore,
    renderer,
    tts,
    audio_dir: Path,
    lesson_id: str,
    lesson,
) -> dict:
    """Rebuild a lesson's audio by re-rendering ONLY its KEY_PHRASES section.

    Every other section's file is reused byte-for-byte and the full lesson is
    stitched with ffmpeg's concat demuxer under ``-c copy``, so no audio outside
    KEY_PHRASES is re-synthesized OR re-encoded (tunatale-1d85). The title is
    re-synthesized — one TTS call per lesson, the only such call — because it is
    not persisted separately from the full-lesson file.

    ⚠️ It calls ``renderer.render_section``, NOT ``renderer.render``. ``render``
    renders the WHOLE lesson: it would synthesize every phrase of every section,
    write the full mix into the file registered as KEY_PHRASES, and — when handed
    the existing section paths — OVERWRITE the user's real audio in place.

    Returns the same payload shape as :func:`render_lesson_audio`.
    """
    old_rows = store.list_audio_files_for_lesson(lesson_id)
    old_full = next((r for r in old_rows if r["section_index"] is None), None)
    if old_full is None:
        raise ValueError(f"Full lesson audio not found for lesson {lesson_id!r}")

    section_rows = sorted(
        (r for r in old_rows if r["section_index"] is not None),
        key=lambda r: r["section_index"],
    )
    if len(section_rows) != len(lesson.sections):
        raise ValueError(
            f"{lesson_id!r} has {len(section_rows)} section audio rows for "
            f"{len(lesson.sections)} sections — re-render the lesson instead"
        )

    kp_index = next(
        (i for i, sec in enumerate(lesson.sections) if sec.section_type == SectionType.KEY_PHRASES),
        None,
    )
    if kp_index is None:
        raise ValueError(f"No KEY_PHRASES section in lesson {lesson_id!r}")

    audio_dir.mkdir(parents=True, exist_ok=True)
    ext = CODEC_EXT.get(settings.audio_delivery_codec, "wav")
    boundary_ms = renderer.pause_calculator.get_section_boundary_pause()

    # Scratch lives OUTSIDE audio_dir: that directory is the user's real content
    # (hundreds of MB), and a stray temp file there is indistinguishable from a
    # rendered clip once the process that named it is gone.
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir)

        # ⚠️ The TTS writes MP3 BYTES regardless of the filename — its cache is
        # <digest>.mp3, and LessonRenderer.render names its own temp file
        # "title.mp3" then DECODES it before assembling. Handing that file
        # straight to the concat demuxer alongside Opus sections fails with
        # "Unsupported codec id in stream 0", because one input is a different
        # codec. So decode it and re-encode it to the delivery codec at the
        # sections' own rate before it joins the concat list.
        raw_title = scratch / "title.mp3"
        await tts.synthesize(lesson.title, lesson.narrator_voice, raw_title, rate="+0%")

        new_kp_id = str(uuid.uuid4())
        new_kp_path = audio_dir / f"{new_kp_id}.{ext}"
        kp_cues, kp_rate = await renderer.render_section(
            lesson.sections[kp_index], new_kp_path, kp_index, lesson.language_code
        )

        title_path = scratch / f"title.{ext}"
        _transcode_to_delivery(raw_title, title_path, kp_rate)

        boundary_path = scratch / f"boundary.{ext}"
        _write_silence(boundary_path, boundary_ms, kp_rate)

        section_paths = [new_kp_path if i == kp_index else Path(r["file_path"]) for i, r in enumerate(section_rows)]
        pieces: list[Path] = [title_path]
        for i, sec_path in enumerate(section_paths):
            pieces.append(boundary_path)
            pieces.append(sec_path)
            del i

        new_full_id = str(uuid.uuid4())
        new_full_path = audio_dir / f"{new_full_id}.{ext}"
        _concat_opus_concat_demuxer(pieces, new_full_path)

        # Absolute cue manifest, in MILLISECONDS throughout. Working in frames
        # needs a sample rate, and every rate that appears here cancels out of
        # the final answer — carrying one only creates a constant to get wrong.
        title_ms = round(_read_audio_duration(title_path) * 1000)
        durations_ms = [round(_read_audio_duration(p) * 1000) for p in section_paths]

    stored_cues: list[list[dict]] = []
    for i, r in enumerate(section_rows):
        raw = r.get("cues_json")
        if i == kp_index:
            stored_cues.append([asdict(c) for c in _cues_from_relative(lesson, kp_cues, kp_index, kp_rate)])
        else:
            stored_cues.append(json.loads(raw) if raw else [])

    all_cues: list[Cue] = [
        Cue(
            index=0,
            start_ms=0,
            end_ms=title_ms,
            section_index=None,
            section_type=None,
            phrase_index=0,
            role="narrator",
            language_code=lesson.sections[0].phrases[0].language_code if lesson.sections[0].phrases else "",
            text=lesson.title,
            ref=None,
        )
    ]
    offset_ms = title_ms + boundary_ms
    for i, cue_dicts in enumerate(stored_cues):
        for cd in cue_dicts:
            all_cues.append(
                replace(
                    Cue(**cd),
                    index=len(all_cues),
                    start_ms=cd["start_ms"] + offset_ms,
                    end_ms=cd["end_ms"] + offset_ms,
                )
            )
        offset_ms += durations_ms[i] + boundary_ms
    cues_json = json.dumps([asdict(c) for c in all_cues], ensure_ascii=False)

    old_kp_path = Path(section_rows[kp_index]["file_path"])
    store.delete_audio_files_for_lesson(lesson_id)
    store.save_audio_file(new_full_id, lesson_id, str(new_full_path), cues_json=cues_json)
    new_section_ids = [str(uuid.uuid4()) for _ in section_rows]
    for i, (sid, r) in enumerate(zip(new_section_ids, section_rows, strict=True)):
        sec_cues_json = json.dumps(stored_cues[i], ensure_ascii=False) if stored_cues[i] else None
        store.save_audio_file(
            sid,
            lesson_id,
            str(section_paths[i]),
            section_index=i,
            section_type=r["section_type"],
            cues_json=sec_cues_json,
        )

    # Only the files this function REPLACED are removed, and only after the new
    # rows are committed. Every other section file is still referenced.
    Path(old_full["file_path"]).unlink(missing_ok=True)
    # Unconditional: new_kp_path always carries a fresh uuid4, so it can never
    # be the path we are deleting. A `!=` guard here would be a branch nothing
    # can take.
    old_kp_path.unlink(missing_ok=True)

    return {
        "audio_id": new_full_id,
        "lesson_id": lesson_id,
        "sections": [
            {
                "audio_id": new_section_ids[i],
                "section_index": i,
                "section_type": r["section_type"],
                "title": SECTION_TITLES.get(SectionType(r["section_type"]), r["section_type"]),
            }
            for i, r in enumerate(section_rows)
        ],
        "cues": json.loads(cues_json),
    }
