"""Cut one breakdown chunk out of one whole-word render.

This is the collaborator that joins the two halves of the workstream: the
provenance a breakdown chunk carries (``source_word`` + ``syllable_span``) and
the pure signal work in :mod:`app.audio.slicing`. It owns the parts that touch
the outside world — synthesizing the parent word, running the aligner, caching
boundaries on disk — and delegates every ear-tuned decision to ``slicing``.

Failure is always *fallback*, never an exception. A word the syllabifier cannot
split losslessly, a character the model does not know, a model that raises, an
alignment too degenerate to honour: each returns ``False`` and the caller keeps
today's isolated-TTS audio. Slicing improves a chunk; it must never be able to
break a lesson render.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf

from app.audio.alignment import (
    derive_syllable_bounds,
    resample_to_model_rate,
    trim_silence,
)
from app.audio.slicing import SlicedWord, polish, raw_span, refine_splice

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from app.audio.alignment import CharAligner
    from app.audio.ports import TTSService
    from app.config import Settings

logger = logging.getLogger(__name__)

# The parent is rendered SLOWED. A chunk cut from connected speech at normal
# rate is too short to drill against, and stretching it afterwards costs WSOLA
# artifacts; asking the voice for a slower reading gives real articulation to cut
# from instead. Ear-tuned over four rounds — do not retune without listening.
PARENT_RATE = "-40%"
# Head padding at an interior cut. Kept short on purpose: a chunk's start usually
# sits in a closure already, and padding backwards imports the previous vowel's
# tail and muddies the onset.
_HEAD_PAD_MS = 25.0
# Floor for the tail. The measured distance to the next vowel wins whenever it is
# longer; this only covers the case where alignment offers no ceiling.
_TAIL_PAD_MS = 80.0
# Duration a short chunk is stretched toward.
_TARGET_MS = 400.0

# One aligner per model id per PROCESS. A lesson render touches many words, and
# reloading a ~1.2 GB model per word would dominate the render; the instance is
# deliberately not per-ChunkSlicer, because the CLI preview path builds a fresh
# renderer per invocation.
_ALIGNERS: dict[str, CharAligner] = {}


@dataclass(frozen=True)
class SliceSpec:
    """A request to cut ``syllables[start:stop]`` out of *word*, in *voice_id*."""

    word: str
    start: int
    stop: int
    voice_id: str


def slicing_available(settings: Settings) -> bool:
    """Whether syllable slicing may run: opted in AND the extra is installed.

    ``find_spec`` is the right probe here even though it is forbidden for the
    Anki capability gate. There the driver runs in an isolated ``uv run --with
    anki`` subprocess, so the main venv never has ``anki`` and a ``find_spec``
    check would always say no; here ``transformers`` is imported in-process, so
    its presence in this interpreter is exactly the question being asked.
    """
    return settings.audio_slicing_enabled and importlib.util.find_spec("transformers") is not None


def build_slicers(language_codes: Iterable[str], tts: TTSService, settings: Settings) -> dict[str, ChunkSlicer]:
    """A slicer per language that can actually slice, keyed by language code.

    Empty whenever the capability gate is closed, and it silently omits any
    language with no ``alignment`` wiring — both of which the renderer reads as
    "synthesize every chunk", i.e. today's behaviour.
    """
    from app.languages import get_alignment

    if not slicing_available(settings):
        return {}

    slicers: dict[str, ChunkSlicer] = {}
    for code in language_codes:
        alignment = get_alignment(code)
        if alignment is None:
            continue
        slicers[code] = ChunkSlicer(
            tts=tts,
            aligner_factory=alignment.aligner_factory,
            model_id=alignment.model_id,
            syllabify_fn=alignment.syllabify_fn,
            vowels=alignment.vowels,
            cache_dir=settings.audio_alignment_cache_dir,
        )
    return slicers


class ChunkSlicer:
    """Slices breakdown chunks out of whole-word renders."""

    def __init__(
        self,
        tts: TTSService,
        aligner_factory: Callable[[str], CharAligner],
        model_id: str,
        syllabify_fn: Callable[[str], list[str] | None],
        vowels: Iterable[str],
        cache_dir: Path | None = None,
        parent_rate: str = PARENT_RATE,
    ) -> None:
        self._tts = tts
        self._aligner_factory = aligner_factory
        self._model_id = model_id
        self._syllabify = syllabify_fn
        self._vowels = frozenset(vowels)
        self._cache_dir = cache_dir
        self._parent_rate = parent_rate
        # Parent renders memoised for this slicer's lifetime, so every chunk of a
        # word shares one synthesis and one alignment.
        self._words: dict[tuple[str, str], SlicedWord | None] = {}

    def _aligner(self) -> CharAligner:
        aligner = _ALIGNERS.get(self._model_id)
        if aligner is None:
            logger.info("Loading alignment model %s", self._model_id)
            aligner = self._aligner_factory(self._model_id)
            _ALIGNERS[self._model_id] = aligner
        return aligner

    def _cache_path(self, word: str, voice_id: str) -> Path | None:
        if self._cache_dir is None:
            return None
        key = "\x1f".join([word, voice_id, self._parent_rate, self._model_id])
        return self._cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"

    def _load_bounds(
        self, path: Path | None, syllables: list[str], n_samples: int
    ) -> tuple[list[int], list[int]] | None:
        """Cached boundaries for this exact parent render, or ``None``.

        Boundaries are sample offsets into a specific render, so an entry whose
        recorded length disagrees with the buffer in hand describes different
        audio and must be discarded — reusing it would silently mis-cut every
        chunk of the word.
        """
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            logger.warning("Ignoring unreadable alignment cache entry %s", path)
            return None
        if data.get("n_samples") != n_samples or data.get("syllables") != syllables:
            return None
        return data["bounds"], data["onset_ends"]

    def _store_bounds(
        self, path: Path | None, syllables: list[str], n_samples: int, bounds: list[int], onset_ends: list[int]
    ) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "syllables": syllables,
                    "n_samples": n_samples,
                    "bounds": bounds,
                    "onset_ends": onset_ends,
                }
            ),
            encoding="utf-8",
        )

    async def _parent(self, word: str, voice_id: str) -> SlicedWord | None:
        """Synthesize, align and refine *word* once; ``None`` if it is unsliceable."""
        memo_key = (word, voice_id)
        if memo_key in self._words:
            return self._words[memo_key]
        result = await self._build_parent(word, voice_id)
        self._words[memo_key] = result
        return result

    async def _build_parent(self, word: str, voice_id: str) -> SlicedWord | None:
        syllables = self._syllabify(word)
        if syllables is None or len(syllables) < 2:
            return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            parent_file = Path(tmp_dir) / "parent.wav"
            await self._tts.synthesize(word, voice_id, parent_file, rate=self._parent_rate)
            raw, rate = sf.read(str(parent_file), dtype="float32", always_2d=True)
        samples = trim_silence(raw.mean(axis=1), int(rate))

        cache_path = self._cache_path(word, voice_id)
        cached = self._load_bounds(cache_path, syllables, len(samples))
        if cached is not None:
            bounds, onset_ends = cached
        else:
            derived = self._align(word, samples, int(rate), syllables)
            if derived is None:
                return None
            bounds, onset_ends = derived
            self._store_bounds(cache_path, syllables, len(samples), bounds, onset_ends)

        # Alignment says where the boundary IS; refinement says where it is safe
        # to cut. Collapsing refined boundaries would break the bounds/syllable
        # invariant, so keep the alignment's answer if that happens.
        inner = sorted({refine_splice(samples, int(rate), b) for b in bounds[1:-1]})
        if len(inner) == len(bounds) - 2:
            bounds = [0, *inner, len(samples)]

        return SlicedWord(
            word=word,
            syllables=syllables,
            samples=samples,
            rate=int(rate),
            bounds=bounds,
            onset_ends=onset_ends,
        )

    def _align(
        self, word: str, samples: np.ndarray, rate: int, syllables: list[str]
    ) -> tuple[list[int], list[int]] | None:
        try:
            aligner = self._aligner()
            if not aligner.supports(word):
                return None
            char_spans, n_frames = aligner.char_spans(resample_to_model_rate(samples, rate), word)
        except Exception:
            logger.warning("Alignment failed for %r; falling back to TTS", word, exc_info=True)
            return None
        return derive_syllable_bounds(char_spans, n_frames, len(samples), syllables, self._vowels)

    async def slice_to_file(self, spec: SliceSpec, out_path: Path) -> bool:
        """Write ``spec``'s audio to *out_path*; ``False` means "use TTS instead".

        The file is only created on success, so a caller can treat a ``False``
        return and an absent file as the same thing.
        """
        sw = await self._parent(spec.word, spec.voice_id)
        if sw is None:
            return False
        if not 0 <= spec.start < spec.stop <= len(sw.syllables):
            logger.warning("Span %s outside %r's %d syllables", (spec.start, spec.stop), spec.word, len(sw.syllables))
            return False

        head = int(_HEAD_PAD_MS / 1000.0 * sw.rate)
        tail = int(_TAIL_PAD_MS / 1000.0 * sw.rate)
        span = raw_span(sw, spec.start, spec.stop, head, tail)
        target_rms = float(np.sqrt((sw.samples**2).mean()))
        # A whole-word rebuild is already a complete utterance; stretching it
        # toward the syllable target would make it drag against the phrases
        # around it.
        whole_word = spec.start == 0 and spec.stop == len(sw.syllables)
        chunk = polish(
            span,
            sw.rate,
            target_rms,
            _TARGET_MS,
            stretch=not whole_word,
            normalize=True,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), chunk, sw.rate, subtype="PCM_16")
        return True
