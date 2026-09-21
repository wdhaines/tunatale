"""TT_HOME relocates every TunaTale data path at once (tunatale-qyw0).

The laptop's live instance must not share ~/.tunatale with the dev server. It
cannot get there by overriding HOME: measured 2026-09-21, `security
find-generic-password` under a relocated HOME exits 44 (item not found) for the
AnkiWeb password that exits 0 under the real one, so AnkiWeb sync would break
while reporting "no credentials". So TunaTale's own home is its own setting.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings, tt_home

DEFAULT_HOME = Path("~/.tunatale").expanduser()


def _home_paths(s: Settings) -> dict[str, Path]:
    """Every Path setting, whatever it is called — the property, not a list."""
    return {name: value for name, value in s.__dict__.items() if isinstance(value, Path)}


def test_unset_means_the_usual_home(monkeypatch):
    monkeypatch.delenv("TT_HOME", raising=False)
    assert tt_home() == DEFAULT_HOME


def test_every_path_under_the_default_home_moves_with_tt_home(monkeypatch, tmp_path):
    monkeypatch.delenv("TT_HOME", raising=False)
    before = _home_paths(Settings(_env_file=None))
    under_home = {n: p for n, p in before.items() if p.is_relative_to(DEFAULT_HOME)}
    # Control: the property below must be tested against something.
    assert len(under_home) >= 10, under_home

    monkeypatch.setenv("TT_HOME", str(tmp_path / "live"))
    after = _home_paths(Settings(_env_file=None))
    for name, old in under_home.items():
        assert after[name] == tmp_path / "live" / old.relative_to(DEFAULT_HOME), name


def test_paths_outside_the_home_do_not_move(monkeypatch, tmp_path):
    """media_dir / audio_dir live in the checkout; TT_HOME must not drag them."""
    monkeypatch.delenv("TT_HOME", raising=False)
    before = _home_paths(Settings(_env_file=None))
    monkeypatch.setenv("TT_HOME", str(tmp_path / "live"))
    after = _home_paths(Settings(_env_file=None))
    for name, old in before.items():
        if not old.is_relative_to(DEFAULT_HOME):
            assert after[name] == old, name


def test_tilde_in_tt_home_is_expanded(monkeypatch):
    monkeypatch.setenv("TT_HOME", "~/TunaTaleLive/data/.tunatale")
    assert tt_home() == Path("~/TunaTaleLive/data/.tunatale").expanduser()
