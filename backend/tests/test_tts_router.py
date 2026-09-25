"""RoutingTTSService — voice id to adapter, with no fallback between them.

Two hand-written fakes stand in for the two adapters, so the routing is
observed directly: no patching, no network, no ``mock_allowlist.txt`` entry.
"""

from __future__ import annotations

import pytest

from app.audio.ports import TTSService
from app.audio.tts_router import RoutingTTSService

GEMINI_VOICE = "ceb-PH-KoreGemini"
NEURAL_VOICE = "nb-NO-FinnNeural"


class _RecordingTTS:
    """A minimal TTSService that remembers what it was asked to render."""

    def __init__(self, label: str, error: Exception | None = None):
        self.label = label
        self.error = error
        self.calls: list[dict] = []

    async def synthesize(self, text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None) -> None:
        self.calls.append(
            {
                "text": text,
                "voice_id": voice_id,
                "output_path": output_path,
                "rate": rate,
                "phonemes": phonemes,
                "speak_locale": speak_locale,
            }
        )
        if self.error is not None:
            raise self.error
        output_path.write_bytes(self.label.encode())

    async def list_voices(self, language_code=None) -> list[dict]:
        return [{"provider": self.label, "language": language_code}]


def _router(**kw) -> tuple[RoutingTTSService, _RecordingTTS, _RecordingTTS]:
    azure = _RecordingTTS("azure")
    gemini = _RecordingTTS("gemini")
    return RoutingTTSService(azure, gemini, **kw), azure, gemini


async def test_a_gemini_voice_id_reaches_only_the_gemini_adapter(tmp_path):
    """Routing is by id suffix: the id names its provider."""
    router, azure, gemini = _router()
    out = tmp_path / "o.mp3"

    await router.synthesize("Maayong buntag!", GEMINI_VOICE, out, rate="-20%")

    assert [c["voice_id"] for c in gemini.calls] == [GEMINI_VOICE]
    assert azure.calls == []
    # Every argument crosses the seam intact — the router is a router, not a
    # filter, and the renderer below it must stay ignorant of the adapter.
    assert gemini.calls[0]["rate"] == "-20%"
    assert out.read_bytes() == b"gemini"


async def test_a_neural_voice_id_reaches_only_the_azure_adapter(tmp_path):
    router, azure, gemini = _router()
    out = tmp_path / "o.mp3"

    await router.synthesize("hei", NEURAL_VOICE, out)

    assert [c["voice_id"] for c in azure.calls] == [NEURAL_VOICE]
    assert gemini.calls == []
    assert out.read_bytes() == b"azure"


async def test_the_optional_arguments_reach_the_chosen_adapter(tmp_path):
    """phonemes and speak_locale are passed through, not consumed: deciding
    what an adapter can do with them is the adapter's job."""
    router, azure, gemini = _router()

    await router.synthesize(
        "hei",
        NEURAL_VOICE,
        tmp_path / "o.mp3",
        rate="-40%",
        phonemes={"hei": "hei̯"},
        speak_locale="en-US",
    )

    assert azure.calls[0]["phonemes"] == {"hei": "hei̯"}
    assert azure.calls[0]["speak_locale"] == "en-US"
    assert azure.calls[0]["rate"] == "-40%"


@pytest.mark.parametrize(
    "voice_id",
    ["Foo", "ceb-PH-Kore", "ceb-PH-KoreNeural2", "ceb-PH-Koregemini"],
    ids=["bare", "no-suffix", "longer-suffix", "lowercase-suffix"],
)
async def test_a_voice_id_naming_no_provider_raises_and_names_it(tmp_path, voice_id):
    """Routing is a lookup, not a guess. An unroutable id fails at the seam
    rather than being handed to whichever adapter answers first.

    The suffix match is exact and case-sensitive: a lowercase `gemini` is not
    the provider suffix, and routing it to Gemini anyway would push a naming
    mistake into a paid request instead of failing where it can be read.
    """
    router, azure, gemini = _router()

    with pytest.raises(ValueError, match=voice_id):
        await router.synthesize("x", voice_id, tmp_path / "o.mp3")

    assert azure.calls == [] and gemini.calls == []
    assert not (tmp_path / "o.mp3").exists()


async def test_a_gemini_failure_never_falls_back_to_azure(tmp_path):
    """THE no-fallback test.

    An automatic swap would splice a different provider's rendition of the
    "same" voice into a curriculum silently — id parity is not voice parity.
    A Gemini id that fails must fail, having touched only Gemini.
    """
    boom = RuntimeError("quota exhausted")
    azure = _RecordingTTS("azure")
    gemini = _RecordingTTS("gemini", error=boom)
    router = RoutingTTSService(azure, gemini)

    with pytest.raises(RuntimeError, match="quota exhausted"):
        await router.synthesize("x", GEMINI_VOICE, tmp_path / "o.mp3")

    assert len(gemini.calls) == 1
    assert azure.calls == [], "the router retried on the other adapter"


async def test_an_azure_failure_never_falls_back_to_gemini(tmp_path):
    """The same rule in the other direction."""
    azure = _RecordingTTS("azure", error=RuntimeError("bad key"))
    gemini = _RecordingTTS("gemini")
    router = RoutingTTSService(azure, gemini)

    with pytest.raises(RuntimeError, match="bad key"):
        await router.synthesize("x", NEURAL_VOICE, tmp_path / "o.mp3")

    assert len(azure.calls) == 1
    assert gemini.calls == []


async def test_list_voices_delegates_to_azure():
    """Azure is the provider that has a voice list at all."""
    router, azure, _ = _router()

    assert await router.list_voices("nb-NO") == [{"provider": "azure", "language": "nb-NO"}]


async def test_the_router_satisfies_the_port():
    """What makes renderer.py and slicer.py stay ignorant of which adapter
    serves a voice id."""
    router, _, _ = _router()

    assert isinstance(router, TTSService)


def test_the_shared_cache_dir_is_exposed(tmp_path):
    """Callers that read _cache_dir keep working: the renderer's resume path
    and the render-retry test both do."""
    router, _, _ = _router(cache_dir=tmp_path / "cache")

    assert router._cache_dir == tmp_path / "cache"


def test_no_cache_dir_is_none():
    router, _, _ = _router()

    assert router._cache_dir is None
