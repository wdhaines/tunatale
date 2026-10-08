"""Every settings path that defaults into ~/.tunatale is redirected in tests.

`conftest.py::_settings_overrides` pins each user-data path to `tmp_path`, one
line per field, and a field added later without its line leaks: the suite
writes into the developer's real ~/.tunatale. That happened with
`warning_log`: every full gate pushed ~1K fixture warnings into the real log,
and its rotation deleted the only evidence of a production gloss failure
(tunatale-0c2t, tunatale-sijo).

Stated as a PROPERTY over every field rather than a list of fields, so the next
path added to Settings inherits the guard instead of needing its own line here.
"""

from pathlib import Path

import pytest

from app.config import Settings, settings, tt_home


def _defaults_under_tt_home() -> dict[str, Path]:
    home = tt_home().resolve()
    found: dict[str, Path] = {}
    for name, field in Settings.model_fields.items():
        if field.default_factory is None:
            continue
        default = field.default_factory()
        if isinstance(default, Path) and default.resolve().is_relative_to(home):
            found[name] = default
    return found


def test_the_property_sees_fields_at_all():
    """Control: an empty scan would make the real test vacuous."""
    assert "llm_usage_ledger_path" in _defaults_under_tt_home()


def test_no_settings_path_points_into_the_real_tt_home_during_tests():
    home = tt_home().resolve()
    leaking = {
        name: getattr(settings, name)
        for name in _defaults_under_tt_home()
        if Path(getattr(settings, name)).resolve().is_relative_to(home)
    }
    assert not leaking, (
        f"settings paths still inside the real {home} during tests: {sorted(leaking)} — "
        "pin each to tmp_path in conftest.py::_settings_overrides"
    )


async def test_no_test_can_mint_a_gemini_token():
    """The third paid vendor has no key to blank, so its credential is the pin.

    With the conftest stand-in removed this mints a real token from the
    developer's service account, and a Cebuano card test goes on to render
    live audio with it (22 requests in one run, 2026-10-08).
    """
    import app.audio.gemini_tts as gemini_tts

    with pytest.raises(RuntimeError, match="reached from a test"):
        await gemini_tts.default_token_provider()


async def test_a_card_render_in_a_gemini_voice_comes_back_empty_not_live():
    """What a card test sees: no audio, at once, the same as in CI where there is no credential."""
    from app.cards.media.tts import generate_tts_audio

    assert await generate_tts_audio("miuban", voice="ceb-PH-KoreGemini") is None
