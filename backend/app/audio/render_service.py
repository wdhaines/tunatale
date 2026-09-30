"""Shared audio render service — extracted from POST /api/audio/render."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
import uuid
import weakref
from collections import defaultdict
from collections.abc import Awaitable, Callable, Collection, Iterable, Iterator
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio import assembly as _assembly
from app.audio.alignment import resample_to_model_rate
from app.audio.cues import Cue, CueTiming, build_cue_manifest
from app.audio.paths import resolve_audio_path
from app.audio.ports import TTSExhausted
from app.audio.transcode import CODEC_EXT, encode_audio_stream
from app.config import settings
from app.generation.section_builder import SECTION_TITLES
from app.models.lesson import SectionType
from app.storage.store import ContentStore

logger = logging.getLogger(__name__)


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


async def _with_render_retries[T](
    attempt: Callable[[], Awaitable[T]],
    what: str,
    *,
    max_attempts: int | None = None,
    cooldown_s: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Re-run *attempt* while it keeps exhausting its TTS ladder.

    One clip out of ~169 running out of retries used to abort a whole lesson,
    and a human had to notice and re-trigger it (tunatale-uxm0). Since
    ``settings.tts_cache_dir`` was wired (tunatale-5lp6) that re-trigger was the
    ONLY thing standing between "failed" and "finished": every clip that did
    synthesize is already on disk, so a later pass is a pile of cache hits plus
    whatever the earlier one missed. Each pass therefore strictly advances, and
    the passes converge.

    ⚠️ Retries ``TTSExhausted`` and nothing else. A bare ``RuntimeError`` from
    the adapter means a missing key or region, which will not fix itself in
    fifteen seconds — retrying it only buries the message under two warnings.

    The cooldown is paid strictly BETWEEN passes: a render that succeeds first
    time (which, at the shipped concurrency of 1, is every render measured so
    far) pays nothing at all.
    """
    if max_attempts is None:
        max_attempts = settings.tts_render_max_attempts
    if cooldown_s is None:
        cooldown_s = settings.tts_render_retry_cooldown_s

    for pass_no in range(1, max_attempts + 1):
        try:
            return await attempt()
        except TTSExhausted as exc:
            if pass_no == max_attempts:
                raise
            # Loud on purpose. "Trying hard" that prints nothing for two
            # minutes is indistinguishable from a hang, and the operator has
            # no other way to tell a converging render from a stuck one.
            logger.warning(
                "Render of %s exhausted its TTS ladder on pass %d of %d (%s); "
                "retrying in %.0fs — cached clips are kept, so this pass only "
                "synthesizes what the last one missed",
                what,
                pass_no,
                max_attempts,
                exc,
                cooldown_s,
            )
            await sleep(cooldown_s)
    raise AssertionError("unreachable: the final pass either returns or re-raises")  # pragma: no cover


# ── one render at a time, per process ────────────────────────────────────────
#
# bd tunatale-rwkz.2. A render holds an entire lesson as float32 PCM (~340 MB
# for a 58-minute review session) plus its section buffers plus a WAV copy for
# ffmpeg, so one render nearly fills the 953 MB box production runs on. On
# 2026-09-19 two ran at once, went to swap, and took 47 and 58 minutes — with the
# machine starved badly enough that Docker's DNS timed out and Caddy 502'd.
#
# ⚠️ NOT the same guard as ``app_state.review_renders``, which refuses
# a second render of the SAME session id. That one correctly let the 2026-09-19
# pair through: they were two different ids. This is about the machine.
#
# Keyed by event loop, and lazily, because a module-level Semaphore would bind
# whichever loop imported this module (module-level side effects are banned here
# for exactly this class of reason) and every test loop would then share — or
# deadlock on — one foreign primitive. The map is weak so a finished loop's
# entry goes with it.
_render_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


def _render_gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _render_gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(max(1, settings.max_concurrent_renders))
        _render_gates[loop] = gate
    return gate


async def render_lesson_audio(
    store: ContentStore,
    renderer,
    audio_dir: Path,
    lesson_id: str,
    lesson,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Render audio for a lesson and persist the results.

    Lifted verbatim from the POST /api/audio/render endpoint. Returns the same
    payload shape so both the endpoint and the pipeline caller get identical
    results.

    Serialised against every other render in this process — see ``_render_gate``.
    A caller that arrives while another render holds the gate WAITS (the event
    loop stays free, so the API keeps answering) rather than being refused: a
    refusal would trade a slow box for a lesson with no audio.

    *on_progress* is handed straight to ``renderer.render`` (tunatale-hbnd), so a
    caller watching a long render reads clip counts instead of a spinner. It is
    NOT called while the gate above is held by another render: a queued render
    has not started, and there is nothing to count yet.
    """
    async with _render_gate():
        return await _render_lesson_audio(store, renderer, audio_dir, lesson_id, lesson, on_progress)


async def _render_lesson_audio(
    store: ContentStore,
    renderer,
    audio_dir: Path,
    lesson_id: str,
    lesson,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """The render itself. Split out so the gate above is a single statement and
    ``async with`` releases it on every path, success or failure."""
    old_rows = store.list_audio_files_for_lesson(lesson_id)
    # A previous render of THIS code path left a full row with no file, and
    # Path(None) is a TypeError — so the file-less row is skipped where the
    # paths are read, not stored as a path.
    old_file_paths = [resolve_audio_path(r["file_path"]) for r in old_rows if r["file_path"] is not None]

    audio_dir.mkdir(parents=True, exist_ok=True)

    ext = CODEC_EXT.get(settings.audio_delivery_codec, "wav")
    audio_id = str(uuid.uuid4())

    section_ids = [str(uuid.uuid4()) for _ in lesson.sections]
    section_paths = [audio_dir / f"{sid}.{ext}" for sid in section_ids]

    # No full file: the concatenation is ~140s of a ~345s render and nothing
    # plays it (tunatale-guzo.3). The row below still carries the full-timeline
    # cues, which is what the player and GET /api/audio/lesson/{id} need.
    cues = await _with_render_retries(
        lambda: renderer.render(lesson, None, section_paths=section_paths, on_progress=on_progress),
        f"lesson {lesson_id!r}",
    )
    cues_json = json.dumps([asdict(c) for c in cues], ensure_ascii=False)

    section_cues = derive_section_cues(cues, lesson)

    store.delete_audio_files_for_lesson(lesson_id)
    store.save_audio_file(audio_id, lesson_id, None, cues_json=cues_json)
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


def _read_title_pcm(src: Path, rate: int) -> np.ndarray:
    """Decode *src* to mono float32 at *rate*.

    The TTS hands back MP3 at its own rate whatever the caller names the file,
    and the lesson is assembled as ONE stream at the assembly rate, so the
    title has to be converted before it can sit in it.

    Downmix FIRST so the resample is single-channel, then hand the resample to
    ``resample_to_model_rate`` — its docstring already rejects hand-rolled
    resamplers, and np.interp has no anti-aliasing filter and aliases on any
    downsample. This is the body the old ``_transcode_to_delivery`` carried,
    minus the encode: the title no longer round-trips through the delivery
    codec on its way into the lesson.
    """
    raw, src_rate = sf.read(str(src), dtype="float32", always_2d=True)
    mono = raw.mean(axis=1).astype("float32")
    return resample_to_model_rate(mono, src_rate, rate).reshape(-1, 1).astype("float32")


def _decode_section_pcm(src: Path, rate: int) -> np.ndarray:
    """Decode *src* to mono float32 at *rate* as raw ``f32le`` from ffmpeg.

    ffmpeg rather than soundfile because the inputs are Opus, and rather than
    a second encode because a section that has already been encoded does not
    need to be encoded again to join a stream.

    Raises ``RuntimeError`` if the file cannot be decoded, **or** if it decodes
    to no samples at all. Both are loud on purpose. A stored ``file_path`` that
    no longer resolves, and a row holding a file that was truncated or never
    finished, each used to reach the concat demuxer and each used to come out
    the other side as a lesson quietly missing a section (tunatale-c7tx): the
    join swallowed the failure, because a mid-list entry that cannot be opened
    is not an error ffmpeg reports — it exits **0** and truncates. The empty
    case exits 0 too, which is why the length check is not folded into the
    return-code check.
    """
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg could not decode a lesson section ({proc.returncode}): {src} "
            f"({proc.stderr.decode(errors='replace').strip()})"
        )
    pcm = np.frombuffer(proc.stdout, dtype="<f4")
    if pcm.size == 0:
        raise RuntimeError(f"ffmpeg decoded a lesson section to no samples: {src}")
    return pcm.astype("float32").reshape(-1, 1)


def _lesson_pcm_pieces(
    piece_descriptions: list[str],
    title_path: Path,
    section_paths: list[Path],
    boundary_frames: int,
    rate: int,
    piece_ms: list[int],
) -> Iterator[np.ndarray]:
    """Yield the full lesson's PCM one piece at a time, in layout order.

    A generator, not a list: a lesson is minutes of audio and each piece is
    decoded when the encoder asks for it, so peak memory is one section rather
    than the lesson — the same reason ``encode_audio_stream`` exists.

    *piece_ms* is appended to as each piece is yielded, carrying that piece's
    ``round(n_samples * 1000 / rate)``. It is the only measurement of how long
    anything is, by design: the manifest's timing has to describe the samples
    the encoder received, and the files those samples came from are not the
    file that came out.

    Nothing is decoded ahead. Piece k+1 does not exist until the consumer asks
    for it, which is what makes a missing or unreadable section fail at its own
    seam instead of part-way into a multi-minute encode, and it is observable
    from outside — see the laziness test in the reassembly tests.
    """
    # ONE shared silence buffer for every boundary. It is written out on the
    # spot and never retained, so allocating it per boundary would cost a
    # transient allocation of a 3-second buffer for nothing.
    boundary = np.zeros((boundary_frames, 1), dtype="float32")
    for description in piece_descriptions:
        if description == "title":
            samples = _read_title_pcm(title_path, rate)
        elif description == "boundary":
            samples = boundary
        else:
            samples = _decode_section_pcm(section_paths[int(description.split("_")[1])], rate)
        piece_ms.append(round(samples.shape[0] * 1000 / rate))
        yield samples


def _write_full_lesson_pcm(path: Path, pieces: Iterable[np.ndarray], rate: int) -> None:
    """Write *pieces* to *path* as ONE continuous file — never joined first.

    ⚠️ ONE encode, not a join of already-encoded parts. A stream-copied Opus
    join is not sample-preserving: each piece's encoder pre-skip and final-frame
    padding survive inside the joined stream, so every seam adds about one
    Opus frame of decoded audio — measured at +20 ms per seam, cumulative, and
    this layout has one boundary per section. ``encode_audio_stream``'s
    docstring carries the same warning and the render path has always acted on
    it; this is the reassembly path doing the same thing.

    WAV is the streaming soundfile write ``LessonRenderer._write_audio_stream``
    uses: soundfile writes incrementally through an open handle, so the PCM
    never has to be held whole on this path either.
    """
    if settings.audio_delivery_codec == "wav":
        with sf.SoundFile(str(path), "w", samplerate=rate, channels=1, subtype="PCM_16") as out:
            for samples in pieces:
                out.write(samples)
        return
    encode_audio_stream(pieces, rate, settings.audio_delivery_codec, settings.audio_delivery_bitrate, path)


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
    section_types: Collection[SectionType] = (SectionType.KEY_PHRASES,),
) -> dict:
    """Rebuild a lesson's audio by re-rendering the requested sections.

    Every section named in *section_types* is re-rendered; every other section's
    file is reused byte-for-byte, so no audio outside the requested sections is
    re-synthesized OR re-encoded (tunatale-1d85). The title is re-synthesized —
    one TTS call per lesson for the lesson title, plus one per re-rendered
    section title, since the TTS cache is keyed (voice, rate, text) and a renamed
    title misses — because it is not persisted separately from the full-lesson
    file.

    ⚠️ The FULL file IS re-encoded, and has to be. It used to be joined with
    ffmpeg's concat demuxer under ``-c copy``, on the reasoning that copying
    packets cannot change audio. It can: a stream-copied Opus join is not
    sample-preserving, because each piece's encoder pre-skip and final-frame
    padding stop being trimmed once the piece sits inside a joined stream. Every
    seam then adds about one Opus frame of decoded audio — measured at +20 ms per
    seam, cumulative — and this layout has one boundary per section, so a
    7-section lesson ran +112 ms ahead of its own caption timeline by the end.
    ffprobe could not see any of it: FORMAT duration is computed from granule
    positions and cancels the pre-skip on both sides. So the pieces are decoded
    to PCM and encoded ONCE (see ``_write_full_lesson_pcm``), which is what the
    render path has always done. The per-section FILES are still untouched.

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

    targets = [i for i, sec in enumerate(lesson.sections) if sec.section_type in section_types]
    if not targets:
        # The default set keeps the exact legacy message, byte for byte; the
        # generic phrase avoids an f-string whose "No " would be its own
        # constant and trip the language-literal checker (bare code "no").
        if section_types == (SectionType.KEY_PHRASES,):
            raise ValueError(f"No KEY_PHRASES section in lesson {lesson_id!r}")
        requested = ", ".join(t.name for t in section_types)
        raise ValueError(f"lesson {lesson_id!r} has no {requested} section(s)")

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
        # "title.mp3" then DECODES it before assembling. So the title is decoded
        # too (``_read_title_pcm``), at the assembly rate and downmixed to mono,
        # rather than encoded into the delivery codec and decoded back.
        raw_title = scratch / "title.mp3"
        await tts.synthesize(lesson.title, lesson.narrator_voice, raw_title, rate="+0%")

        new_target_paths = {i: audio_dir / f"{str(uuid.uuid4())}.{ext}" for i in targets}
        target_rel_cues: dict[int, list[tuple[int, int, int]]] = {}
        target_rates: dict[int, int] = {}
        for i in targets:
            rel_cues, rate = await _with_render_retries(
                lambda i=i: renderer.render_section(lesson.sections[i], new_target_paths[i], i, lesson.language_code),
                f"section {i} ({lesson.sections[i].section_type.value}) of {lesson_id!r}",
            )
            target_rel_cues[i] = rel_cues
            target_rates[i] = rate

        # One assembly rate for the whole stream: it is what the title is
        # resampled to, what every section is decoded at, and the rate the
        # ms->frames conversion below divides back out of. A disagreement is
        # therefore real breakage — there is no one timeline to put both
        # sections' timings on. Each target's OWN rate still goes into its own
        # per-section cues.
        rates = sorted(set(target_rates.values()))
        if len(rates) > 1:
            raise ValueError(
                f"re-rendered sections {[t.name for t in section_types]} report differing "
                f"rates {rates}; one lesson is one stream and cannot be assembled at two"
            )
        assembly_rate = rates[0]

        # ⚠️ resolve_audio_path, not Path(...), for the REUSED rows. A recorded
        # path is where a render was written, and four Norwegian rows hold a
        # relative ``output/audio/<uuid>.opus`` that resolves against neither
        # this process's CWD nor anything else the reassemble might have handed
        # it to. Taken literally it pointed at a file that does not exist, and
        # the lesson came out truncated at exactly that section (tunatale-c7tx).
        # Same resolver the serving endpoints already use (tunatale-kbb.15).
        section_paths = [
            new_target_paths[i] if i in targets else resolve_audio_path(r["file_path"])
            for i, r in enumerate(section_rows)
        ]

        # The piece ORDER is fixed by the number of sections alone — no duration
        # enters it — so it is settled before anything is decoded. The layout
        # that carries the TIMING is asked for again below, from the sample
        # counts the encoder actually received.
        piece_order = _assembly.lesson_layout([0] * len(section_paths), 0, boundary_ms).piece_descriptions

        # ONE encode from ONE stream of PCM: every piece is decoded at the
        # assembly rate as the encoder reaches it and never held longer than
        # that. piece_ms is appended to per piece and is where all the timing
        # below comes from.
        piece_ms: list[int] = []
        new_full_id = str(uuid.uuid4())
        new_full_path = audio_dir / f"{new_full_id}.{ext}"
        try:
            _write_full_lesson_pcm(
                new_full_path,
                _lesson_pcm_pieces(
                    piece_order,
                    raw_title,
                    section_paths,
                    _assembly._ms_to_frames(boundary_ms, assembly_rate),
                    assembly_rate,
                    piece_ms,
                ),
                assembly_rate,
            )
        except BaseException:
            # A piece that fails to decode stops the stream, but the encoder
            # still finalises everything it was fed — a truncated full file in
            # audio_dir, beside re-rendered section files no row will ever
            # reference. The old concat path unlinked its output on failure;
            # this keeps that, and also drops the new sections.
            new_full_path.unlink(missing_ok=True)
            for path in new_target_paths.values():
                path.unlink(missing_ok=True)
            raise

        # Absolute timing in MILLISECONDS throughout, and from SAMPLE COUNTS.
        # ffprobe would answer a different question: the duration of the FILES
        # this lesson was built from, not of the samples the encoder was
        # handed. Those differ by the padding those files carry, and the full
        # file's own audio is what every cue in the manifest has to land on.
        title_ms = piece_ms[0]
        # Every boundary is the same shared buffer, so the first is every one.
        boundary_ms = next(ms for ms, desc in zip(piece_ms, piece_order, strict=True) if desc == "boundary")
        durations_ms = [ms for ms, desc in zip(piece_ms, piece_order, strict=True) if desc.startswith("section_")]

    # Shared layout owns the piece order and the N-vs-N-1 boundary count for
    # BOTH the stream written above and the cue manifest below.

    # Per-section cues, in the SAME form derive_section_cues stores: reused
    # sections keep what they already had; the re-rendered ones are rebuilt.
    stored_cues: list[list[dict]] = [
        [asdict(c) for c in _cues_from_relative(lesson, target_rel_cues[i], i, target_rates[i])]
        if i in targets
        else (json.loads(r["cues_json"]) if r.get("cues_json") else [])
        for i, r in enumerate(section_rows)
    ]

    # The FULL manifest goes through build_cue_manifest, exactly as
    # LessonRenderer.render does — it is NOT the per-section cues re-offset.
    #
    # ⚠️ Re-offsetting them was the first implementation and it was wrong in two
    # ways at once, both silent. (1) The stored per-section cues have already
    # been ellipsis-scrubbed by derive_section_cues, which rewrites SLOW_* L2
    # text to the natural text from the structural-twin section — so the full
    # manifest came out carrying natural text where a rendered lesson carries
    # the raw ellipsis text, leaving two kinds of full manifest in one database.
    # (2) The title cue was hand-rolled and drifted from build_cue_manifest's:
    # ref was None instead of {"kind": "narration"}. Building from TIMINGS and
    # letting build_cue_manifest supply every field from the lesson removes both
    # by construction, and is why _cues_from_relative already worked this way.
    #
    # ms -> frames at assembly_rate: build_cue_manifest divides back out by the
    # same rate, so the value cancels and only its consistency matters.
    timing: list[CueTiming] = [
        CueTiming(
            section_index=None,
            phrase_index=0,
            start_frame=0,
            end_frame=_assembly._ms_to_frames(title_ms, assembly_rate),
        )
    ]
    section_cue_timings: dict[int, list[CueTiming]] = {}
    for i, cue_dicts in enumerate(stored_cues):
        section_cue_timings[i] = [
            CueTiming(
                section_index=i,
                phrase_index=cd["phrase_index"],
                start_frame=_assembly._ms_to_frames(cd["start_ms"], assembly_rate),
                end_frame=_assembly._ms_to_frames(cd["end_ms"], assembly_rate),
            )
            for cd in cue_dicts
        ]
    merged = _assembly.merge_section_cues(section_cue_timings, title_ms, durations_ms, boundary_ms, assembly_rate)
    timing.extend(merged)
    all_cues = build_cue_manifest(lesson, timing, assembly_rate)
    cues_json = json.dumps([asdict(c) for c in all_cues], ensure_ascii=False)

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
    # A NULL is skipped: since tunatale-guzo.3 a full row is routinely a
    # timeline record with no encoded file, and there is nothing to unlink.
    # (Reassembly itself still encodes one — see new_full_path above.)
    if old_full["file_path"] is not None:
        resolve_audio_path(old_full["file_path"]).unlink(missing_ok=True)
    # Unconditional: every new target path carries a fresh uuid4, so it can never
    # be the path being deleted. A `!=` guard here would be a branch nothing can
    # take.
    for i in targets:
        resolve_audio_path(section_rows[i]["file_path"]).unlink(missing_ok=True)

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
