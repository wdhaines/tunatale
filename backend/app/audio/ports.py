"""Audio port protocols."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable


class TTSExhausted(RuntimeError):
    """A clip's retry ladder ran out — the provider kept pushing back.

    Distinct from the bare ``RuntimeError`` an adapter raises for a missing key
    or region, because only THIS one is worth re-running: a throttling episode
    is exogenous and passes, a missing ``AZURE_SPEECH_KEY`` does not. The
    render-level retry loop
    (``render_service._with_render_retries``) retries this and nothing else.

    Still a ``RuntimeError``, so ``api/audio.py``'s mapping to a 503 carrying
    the adapter's own message is unchanged.
    """


class TTSQuotaExceeded(RuntimeError):
    """The monthly Azure character allowance is spent. NOT retryable.

    Distinct from ``TTSExhausted`` — deliberately NOT a subclass — because a
    throttling episode passes and a spent monthly allowance lasts until the
    month resets: ``_with_render_retries`` would re-run a render straight into
    the same wall and burn the retry budget for nothing. Still a
    ``RuntimeError``, so ``api/audio.py``'s mapping to a 503 carrying the
    refusal message through unchanged.
    """


# A line cut up to be said one word at a time: its words in order, each as the
# parts a SHORT pause separates (one part for a word said whole). A tuple of
# tuples so it can sit in a dedupe key as it is.
Enunciation = tuple[tuple[str, ...], ...]


@runtime_checkable
class TTSService(Protocol):
    """Protocol for text-to-speech synthesis services."""

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        rate: str = "+0%",
        phonemes: Mapping[str, str] | None = None,
        speak_locale: str | None = None,
        enunciation: Enunciation | None = None,
    ) -> None:
        """Synthesize *text*, optionally wrapping known tokens in ``<phoneme>``.

        *phonemes* maps a lowercased surface token to its IPA. It is per-token,
        not whole-text IPA. ``None`` and ``{}`` must behave identically to a
        provider without the capability at all, including the cache key.

        *speak_locale* declares the locale *text* is written in, for a voice
        that is not named for it — a Multilingual voice filling a dialogue role
        in another language. ``None``, and a locale the voice already speaks,
        must behave identically to a provider without the capability, cache key
        included; an adapter that cannot emit the markup degrades with a warning
        rather than raising.

        *enunciation* asks for *text* one word at a time, with a pause after
        every word and a shorter one between the parts of a split word. *text*
        is still the whole line as written. How the pause is produced is each
        adapter's own business — one writes it into its markup from the parts,
        another asks for it in an instruction and says *text* — which is why
        both travel. ``None`` must behave identically to a provider without the
        capability, cache key included.
        """
        ...

    async def list_voices(self, language_code: str | None = None) -> list[dict]: ...
