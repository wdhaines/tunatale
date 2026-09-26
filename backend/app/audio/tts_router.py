"""Voice id to adapter — the two providers, told apart by the id itself.

There are two implementations behind ``TTSService`` and this is what sits in
front of them. The dispatch is a suffix match on the voice id, which is the
only signal the registry already carries: ``<locale>-<Name>Neural`` is one
provider, ``<locale>-<Name>Gemini`` is the other, and an id matching neither
is a bug that fails here rather than a request that fails at the provider.

**This is routing, not fallback.** An adapter that fails propagates; it is
never retried on the other one. Two reasons, both load-bearing:

- A silent swap would splice a second provider's rendition of the "same" voice
  into a curriculum without saying so, and voice-id parity is not voice
  parity — measured in ``AzureTTSService._lang_locale`` for a different
  reason, and true by definition here.
- It would spend the quota the failure just proved was scarce. A throttled
  provider retried against a different one turns one clipped render into two
  billed requests.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from app.audio.ports import TTSService

# The provider suffix each adapter's voice ids end with. These are the ids'
# own naming convention, not a per-language lookup — see
# scripts/check_language_literals.py.
_GEMINI_SUFFIX = "Gemini"
_AZURE_SUFFIX = "Neural"


def provider_for(voice_id: str) -> Literal["azure", "gemini"]:
    """Which provider owns *voice_id*, or a ``ValueError`` naming it.

    The whole routing rule, as a public function, because not every caller wants
    an adapter and only wants to know whose bill a voice lands on — the
    render-cost report prices each provider in its own unit and must split the
    keys before it can price either. The match stays exact and case-sensitive
    and the refusal stays the same one :meth:`RoutingTTSService._adapter_for`
    raises: two copies of one rule would be one rule too many.
    """
    if voice_id.endswith(_GEMINI_SUFFIX):
        return "gemini"
    if voice_id.endswith(_AZURE_SUFFIX):
        return "azure"
    raise ValueError(
        f"{voice_id!r} names no known TTS provider: a voice id must end in {_GEMINI_SUFFIX!r} or {_AZURE_SUFFIX!r}"
    )


class RoutingTTSService:
    """Dispatch a synthesis to the adapter that owns the voice id.

    Both adapters are built up front and are never swapped, selected, or
    re-ordered at runtime; this class only decides which one a given id belongs
    to. Either adapter's failure propagates untouched.
    """

    def __init__(self, azure: TTSService, gemini: TTSService, cache_dir: Path | None = None) -> None:
        self._azure = azure
        self._gemini = gemini
        # Exposed because callers read it — the renderer's resume path and the
        # render-retry tests both do — and the one cache directory is shared by
        # both adapters, each in its own key space.
        self._cache_dir = cache_dir

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        rate: str = "+0%",
        phonemes: Mapping[str, str] | None = None,
        speak_locale: str | None = None,
    ) -> None:
        """Hand *voice_id* to the adapter that serves it.

        Args:
            text: Text to synthesize.
            voice_id: ``<locale>-<Name>Gemini`` or ``<locale>-<Name>Neural``.
            output_path: Destination file path for the synthesized audio.
            rate: Speech rate adjustment, passed through uninterpreted.
            phonemes: Per-token IPA, passed through uninterpreted.
            speak_locale: The locale *text* is written in, passed through
                uninterpreted. An adapter that cannot use it degrades with a
                warning; one that can, does.
        """
        adapter = self._adapter_for(voice_id)
        await adapter.synthesize(
            text,
            voice_id,
            output_path,
            rate=rate,
            phonemes=phonemes,
            speak_locale=speak_locale,
        )

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        """Return Azure's voice list; the other provider has none to list."""
        return await self._azure.list_voices(language_code)

    def _adapter_for(self, voice_id: str) -> TTSService:
        """The adapter that owns *voice_id*, or a ``ValueError`` naming it.

        The match is exact and case-sensitive, and it is the whole routing
        rule: an id matching neither suffix is a registry bug, and raising
        here is what keeps it from becoming a billed request. :func:`provider_for`
        IS that rule, asked for its answer rather than for the adapter.
        """
        if provider_for(voice_id) == "gemini":
            return self._gemini
        return self._azure
