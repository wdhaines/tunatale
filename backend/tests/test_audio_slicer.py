"""Tests for ChunkSlicer — cutting a breakdown chunk out of one whole-word render.

No model and no torch: the aligner is a constructor-injected collaborator, so
these run everywhere. The fake reports uniform per-character frames, which is
enough to exercise every decision the slicer makes.
"""

from __future__ import annotations

import importlib.util
import json

import numpy as np
import pytest
import soundfile as sf

from app.audio import slicer as slicer_module
from app.audio.slicer import PARENT_RATE, ChunkSlicer, SliceSpec, build_slicers, slicing_available
from app.config import Settings

_RATE = 24_000
_PARENT_MS = 900.0


@pytest.fixture(autouse=True)
def _fresh_process(monkeypatch):
    """Empty the process-global aligner cache before each test.

    ``ChunkSlicer`` deliberately keeps one aligner per model id for the life of
    the process, so without this a fake loaded by one test would answer another
    test's calls — the cross-test state leak that makes a guard look green while
    testing nothing.
    """
    monkeypatch.setattr(slicer_module, "_ALIGNERS", {})


class FakeTTS:
    """Writes a deterministic tone instead of calling EdgeTTS. Records its calls."""

    def __init__(self, duration_ms: float = _PARENT_MS, rate: int = _RATE) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._duration_ms = duration_ms
        self._rate = rate

    async def synthesize(self, text: str, voice_id: str, output_path, rate: str = "+0%") -> None:
        self.calls.append((text, voice_id, rate))
        n = int(self._duration_ms / 1000.0 * self._rate)
        t = np.arange(n, dtype=np.float32) / self._rate
        # A tone with an amplitude ramp so different spans have different energy,
        # and silence-free so trim_silence keeps the whole buffer.
        samples = (0.4 * (1.0 + 0.5 * np.sin(2 * np.pi * 3.0 * t)) * np.sin(2 * np.pi * 180.0 * t)).astype(np.float32)
        sf.write(str(output_path), samples, self._rate, subtype="PCM_16")


class FakeAligner:
    """Spreads a word's characters evenly across frames."""

    def __init__(self, n_frames_per_char: int = 10, supported: bool = True) -> None:
        self.n_frames_per_char = n_frames_per_char
        self._supported = supported
        self.calls = 0

    def supports(self, word: str) -> bool:
        return self._supported

    def char_spans(self, samples, word):
        self.calls += 1
        step = self.n_frames_per_char
        spans = [(i * step, (i + 1) * step) for i in range(len(word))]
        return spans, len(word) * step


class NotchedTTS(FakeTTS):
    """A very short parent with a single silent notch near its middle.

    Every boundary's ±30 ms splice-refinement window then finds the SAME
    quietest point, so all the refined boundaries collapse onto one sample —
    the case where refinement would destroy the bounds/syllable invariant.
    """

    async def synthesize(self, text: str, voice_id: str, output_path, rate: str = "+0%") -> None:
        self.calls.append((text, voice_id, rate))
        n = int(0.100 * _RATE)
        t = np.arange(n, dtype=np.float32) / _RATE
        samples = (0.4 * np.sin(2 * np.pi * 180.0 * t)).astype(np.float32)
        samples[int(0.047 * _RATE) : int(0.053 * _RATE)] = 0.0
        sf.write(str(output_path), samples, _RATE, subtype="PCM_16")


class RaisingAligner:
    def supports(self, word: str) -> bool:
        return True

    def char_spans(self, samples, word):
        raise RuntimeError("model exploded")


def _syllabify(word: str) -> list[str] | None:
    """Stand-in for the plugin's flat_syllables."""
    table = {
        "haden": ["ha", "den"],
        "politiet": ["po", "li", "ti", "et"],
        "jeg": ["jeg"],
        "busstasjon": None,
    }
    return table.get(word, [word])


def _slicer(tmp_path, tts=None, aligner=None, factory_calls=None, **kw):
    aligner = aligner if aligner is not None else FakeAligner()

    def factory(model_id: str):
        if factory_calls is not None:
            factory_calls.append(model_id)
        return aligner

    return ChunkSlicer(
        tts=tts or FakeTTS(),
        aligner_factory=factory,
        model_id=kw.pop("model_id", "fake/model"),
        syllabify_fn=_syllabify,
        vowels=frozenset("aeiouyæøå"),
        cache_dir=kw.pop("cache_dir", tmp_path / "cache"),
        **kw,
    )


def _duration_ms(path) -> float:
    samples, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return len(samples) / rate * 1000.0


class TestSliceToFile:
    async def test_writes_a_chunk_shorter_than_the_parent(self, tmp_path):
        slicer = _slicer(tmp_path)
        out = tmp_path / "chunk.wav"
        assert await slicer.slice_to_file(SliceSpec("haden", 0, 1, "nb-NO-X"), out) is True
        assert out.exists()
        assert 0 < _duration_ms(out) < _PARENT_MS

    async def test_parent_is_synthesized_once_per_word(self, tmp_path):
        """Every chunk of a word comes out of ONE render — the whole point."""
        tts = FakeTTS()
        slicer = _slicer(tmp_path, tts=tts)
        for i in range(2):
            await slicer.slice_to_file(SliceSpec("haden", i, i + 1, "nb-NO-X"), tmp_path / f"c{i}.wav")
        assert len(tts.calls) == 1
        assert tts.calls[0] == ("haden", "nb-NO-X", PARENT_RATE)

    async def test_parent_is_rendered_slowed_down(self, tmp_path):
        tts = FakeTTS()
        slicer = _slicer(tmp_path, tts=tts)
        await slicer.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "c.wav")
        assert tts.calls[0][2] == PARENT_RATE
        assert PARENT_RATE.startswith("-"), "the parent must be slower than normal speech"

    async def test_different_voices_get_different_parents(self, tmp_path):
        tts = FakeTTS()
        slicer = _slicer(tmp_path, tts=tts)
        await slicer.slice_to_file(SliceSpec("haden", 0, 1, "voice-a"), tmp_path / "a.wav")
        await slicer.slice_to_file(SliceSpec("haden", 0, 1, "voice-b"), tmp_path / "b.wav")
        assert [c[1] for c in tts.calls] == ["voice-a", "voice-b"]

    async def test_later_span_differs_from_earlier_span(self, tmp_path):
        slicer = _slicer(tmp_path)
        first, second = tmp_path / "0.wav", tmp_path / "1.wav"
        await slicer.slice_to_file(SliceSpec("haden", 0, 1, "v"), first)
        await slicer.slice_to_file(SliceSpec("haden", 1, 2, "v"), second)
        a, _ = sf.read(str(first), dtype="float32")
        b, _ = sf.read(str(second), dtype="float32")
        assert not np.array_equal(a[: min(len(a), len(b))], b[: min(len(a), len(b))])


class TestSlicerDeclinesToSlice:
    async def test_word_whose_pieces_do_not_rejoin(self, tmp_path):
        """s-overlap compounds: never slice a lossy split — the slicer indexes
        by position, so a dropped character misplaces every cut."""
        slicer = _slicer(tmp_path)
        out = tmp_path / "c.wav"
        assert await slicer.slice_to_file(SliceSpec("busstasjon", 0, 1, "v"), out) is False
        assert not out.exists()

    async def test_monosyllabic_word(self, tmp_path):
        slicer = _slicer(tmp_path)
        out = tmp_path / "c.wav"
        assert await slicer.slice_to_file(SliceSpec("jeg", 0, 1, "v"), out) is False
        assert not out.exists()

    async def test_span_outside_the_syllable_count(self, tmp_path):
        slicer = _slicer(tmp_path)
        out = tmp_path / "c.wav"
        assert await slicer.slice_to_file(SliceSpec("haden", 0, 9, "v"), out) is False
        assert not out.exists()

    async def test_word_the_model_does_not_support(self, tmp_path):
        slicer = _slicer(tmp_path, aligner=FakeAligner(supported=False))
        out = tmp_path / "c.wav"
        assert await slicer.slice_to_file(SliceSpec("haden", 0, 1, "v"), out) is False
        assert not out.exists()

    async def test_aligner_raising_falls_back_instead_of_propagating(self, tmp_path, caplog):
        """A broken model must degrade to TTS, never break a lesson render."""
        slicer = _slicer(tmp_path, aligner=RaisingAligner())
        out = tmp_path / "c.wav"
        with caplog.at_level("WARNING"):
            assert await slicer.slice_to_file(SliceSpec("haden", 0, 1, "v"), out) is False
        assert not out.exists()
        assert "haden" in caplog.text

    async def test_degenerate_alignment(self, tmp_path):
        """Alignment collapsing every character onto one frame yields no usable cuts."""
        slicer = _slicer(tmp_path, aligner=FakeAligner(n_frames_per_char=0))
        out = tmp_path / "c.wav"
        assert await slicer.slice_to_file(SliceSpec("politiet", 1, 2, "v"), out) is False
        assert not out.exists()


class TestSpliceRefinementCannotBreakTheWord:
    async def test_collapsing_refinement_keeps_the_alignments_boundaries(self, tmp_path):
        """Refinement is a nudge, never a veto.

        It moves each boundary to the quietest nearby point, and on a word whose
        syllables are short enough that several boundaries share one quiet point
        that would leave fewer cuts than syllables. Dropping to the collapsed
        list would desynchronise ``bounds`` from ``syllables`` and mis-cut (or
        index past) every chunk, so the alignment's own answer has to survive.
        """
        slicer = _slicer(tmp_path, tts=NotchedTTS())
        for i in range(4):
            out = tmp_path / f"c{i}.wav"
            assert await slicer.slice_to_file(SliceSpec("politiet", i, i + 1, "v"), out) is True
            assert _duration_ms(out) > 0


class TestWholeWordSpansAreNotStretched:
    async def test_whole_word_span_keeps_its_length(self, tmp_path):
        """A whole-word rebuild is already a full utterance; stretching it toward
        the syllable target would make it drag against the phrases around it."""
        slicer = _slicer(tmp_path)
        whole, syllable = tmp_path / "whole.wav", tmp_path / "syl.wav"
        await slicer.slice_to_file(SliceSpec("haden", 0, 2, "v"), whole)
        await slicer.slice_to_file(SliceSpec("haden", 0, 1, "v"), syllable)
        # The parent is ~900 ms, so an unstretched whole-word span stays near it
        # while a single syllable is stretched UP toward the 400 ms target.
        assert _duration_ms(whole) > 600.0
        assert _duration_ms(syllable) < 600.0


class TestAlignerIsLoadedOnce:
    async def test_factory_runs_once_across_words_and_slicers(self, tmp_path):
        """A lesson render must never reload the model."""
        calls: list[str] = []
        first = _slicer(tmp_path, factory_calls=calls, model_id="shared/model")
        await first.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "a.wav")
        await first.slice_to_file(SliceSpec("politiet", 1, 2, "v"), tmp_path / "b.wav")
        second = _slicer(tmp_path, factory_calls=calls, model_id="shared/model")
        await second.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "c.wav")
        assert calls == ["shared/model"]

    async def test_a_different_model_id_loads_separately(self, tmp_path):
        calls: list[str] = []
        a = _slicer(tmp_path, factory_calls=calls, model_id="model/one")
        b = _slicer(tmp_path, factory_calls=calls, model_id="model/two")
        await a.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "a.wav")
        await b.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "b.wav")
        assert calls == ["model/one", "model/two"]


class TestBoundaryCache:
    async def test_second_process_reuses_cached_boundaries(self, tmp_path):
        """The cache must remove the model from the path, not just memoise in-process."""
        cache = tmp_path / "cache"
        aligner = FakeAligner()
        warm = _slicer(tmp_path, aligner=aligner, cache_dir=cache)
        await warm.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "warm.wav")
        assert aligner.calls == 1

        # Simulate a restart: drop the in-process aligner so only the ON-DISK
        # cache can carry the boundaries over.
        slicer_module._ALIGNERS.clear()
        # A fresh slicer whose aligner would raise: only a cache hit can succeed.
        cold = _slicer(tmp_path, aligner=RaisingAligner(), cache_dir=cache, model_id="fake/model")
        out = tmp_path / "cold.wav"
        assert await cold.slice_to_file(SliceSpec("haden", 1, 2, "v"), out) is True
        assert out.exists()

    async def test_cache_is_keyed_by_voice(self, tmp_path):
        cache = tmp_path / "cache"
        warm = _slicer(tmp_path, cache_dir=cache)
        await warm.slice_to_file(SliceSpec("haden", 0, 1, "v-one"), tmp_path / "a.wav")
        slicer_module._ALIGNERS.clear()
        cold = _slicer(tmp_path, aligner=RaisingAligner(), cache_dir=cache)
        assert await cold.slice_to_file(SliceSpec("haden", 0, 1, "v-two"), tmp_path / "b.wav") is False

    async def test_cache_is_keyed_by_model(self, tmp_path):
        cache = tmp_path / "cache"
        warm = _slicer(tmp_path, cache_dir=cache, model_id="model/one")
        await warm.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "a.wav")
        cold = _slicer(tmp_path, aligner=RaisingAligner(), cache_dir=cache, model_id="model/two")
        assert await cold.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "b.wav") is False

    async def test_stale_cache_whose_parent_length_changed_is_ignored(self, tmp_path):
        """Boundaries are sample offsets — they are meaningless against a
        different render, so a length mismatch must re-align, not mis-cut."""
        cache = tmp_path / "cache"
        warm = _slicer(tmp_path, cache_dir=cache)
        await warm.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "a.wav")
        entry = next(cache.glob("*.json"))
        data = json.loads(entry.read_text())
        data["n_samples"] += 1234
        entry.write_text(json.dumps(data))

        slicer_module._ALIGNERS.clear()
        aligner = FakeAligner()
        again = _slicer(tmp_path, aligner=aligner, cache_dir=cache)
        assert await again.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "b.wav") is True
        assert aligner.calls == 1, "a stale entry must trigger a real re-alignment"

    async def test_unreadable_cache_entry_is_ignored(self, tmp_path):
        cache = tmp_path / "cache"
        warm = _slicer(tmp_path, cache_dir=cache)
        await warm.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "a.wav")
        entry = next(cache.glob("*.json"))
        entry.write_text("{not json")

        slicer_module._ALIGNERS.clear()
        aligner = FakeAligner()
        again = _slicer(tmp_path, aligner=aligner, cache_dir=cache)
        assert await again.slice_to_file(SliceSpec("haden", 0, 1, "v"), tmp_path / "b.wav") is True
        assert aligner.calls == 1

    async def test_works_without_a_cache_dir(self, tmp_path):
        slicer = _slicer(tmp_path, cache_dir=None)
        out = tmp_path / "c.wav"
        assert await slicer.slice_to_file(SliceSpec("haden", 0, 1, "v"), out) is True


class TestSlicingAvailable:
    def test_off_by_default(self):
        """``audio_slicing_enabled`` is opt-in — a default install renders as today."""
        assert Settings().audio_slicing_enabled is False
        assert slicing_available(Settings()) is False

    def test_requires_transformers_even_when_enabled(self, monkeypatch):
        """Exercised through the real import machinery, not by patching app code."""
        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *a, **k: None if name == "transformers" else real_find_spec(name, *a, **k),
        )
        assert slicing_available(Settings(audio_slicing_enabled=True)) is False

    def test_enabled_with_transformers_present(self, monkeypatch):
        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *a, **k: object() if name == "transformers" else real_find_spec(name, *a, **k),
        )
        assert slicing_available(Settings(audio_slicing_enabled=True)) is True

    def test_disabled_setting_beats_a_present_transformers(self, monkeypatch):
        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *a, **k: object() if name == "transformers" else real_find_spec(name, *a, **k),
        )
        assert slicing_available(Settings(audio_slicing_enabled=False)) is False


class TestBuildSlicers:
    @pytest.fixture
    def _transformers_present(self, monkeypatch):
        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *a, **k: object() if name == "transformers" else real_find_spec(name, *a, **k),
        )

    def test_empty_when_the_gate_is_closed(self, _transformers_present):
        assert build_slicers(["no", "sl"], FakeTTS(), Settings(audio_slicing_enabled=False)) == {}

    def test_empty_without_transformers_even_when_enabled(self, monkeypatch):
        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *a, **k: None if name == "transformers" else real_find_spec(name, *a, **k),
        )
        assert build_slicers(["no"], FakeTTS(), Settings(audio_slicing_enabled=True)) == {}

    def test_builds_one_per_language_with_alignment_wiring(self, _transformers_present):
        slicers = build_slicers(["no", "sl", "en"], FakeTTS(), Settings(audio_slicing_enabled=True))
        assert set(slicers) == {"no"}, "only Norwegian has an aligner registered"
        assert isinstance(slicers["no"], ChunkSlicer)

    def test_the_slicer_carries_the_registered_model(self, _transformers_present):
        from app.languages import get_alignment

        slicers = build_slicers(["no"], FakeTTS(), Settings(audio_slicing_enabled=True))
        assert slicers["no"]._model_id == get_alignment("no").model_id

    def test_building_does_not_load_the_model(self, _transformers_present):
        """Constructing a slicer must not import transformers or download 1.2 GB —
        the aligner is created lazily, on the first word that needs it."""
        build_slicers(["no"], FakeTTS(), Settings(audio_slicing_enabled=True))
        assert slicer_module._ALIGNERS == {}


@pytest.mark.parametrize("span", [(0, 1), (1, 2), (0, 2)])
async def test_every_span_of_a_two_syllable_word_produces_audio(tmp_path, span):
    slicer = _slicer(tmp_path)
    out = tmp_path / f"c{span[0]}{span[1]}.wav"
    assert await slicer.slice_to_file(SliceSpec("haden", span[0], span[1], "v"), out) is True
    assert _duration_ms(out) > 0
