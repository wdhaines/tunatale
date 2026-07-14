"""Guardrail tests for the dynamic language-plugin registry.

These tests enforce the structural invariants of the plugin refactor:
- Discovery populates _CONFIGS from plugin packages, not a literal.
- A missing non-en plugin is a hard RuntimeError.
- A single language plugin (plus en) is sufficient.
- app/languages.py has no module-level anki or preprocessor imports.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND = Path(__file__).resolve().parent.parent


class TestRegistryPopulatedByDiscovery:
    """Plugin discovery must fill _CONFIGS and import plugin modules."""

    def test_known_language_codes(self) -> None:
        from app.languages import known_language_codes

        codes = known_language_codes()
        assert "en" in codes
        assert codes & {"sl", "no"}

    def test_plugin_modules_imported(self) -> None:
        from app.languages import known_language_codes  # noqa: F811

        known_language_codes()  # ensure discovery has run
        assert "app.plugins.languages.en" in sys.modules


class TestHardFailWhenNoLanguagePlugin:
    """If no non-en plugin registers, _discover_plugins must raise."""

    def test_raises_runtime_error(self) -> None:
        import app.languages as mod

        # Reset discovery state so _discover_plugins runs fresh
        old_configs = mod._CONFIGS.copy()
        old_discovered = mod._discovered
        try:
            mod._CONFIGS.clear()
            mod._discovered = False
            with (
                patch.dict(
                    sys.modules,
                    {"app.plugins.languages.sl": None, "app.plugins.languages.no": None},
                ),
                patch("importlib.import_module"),
                pytest.raises(RuntimeError, match="No language plugin"),
            ):
                mod._discover_plugins()
        finally:
            mod._CONFIGS.clear()
            mod._CONFIGS.update(old_configs)
            mod._discovered = old_discovered


class TestRegisterDuplicateCode:
    """register() must reject a duplicate language code."""

    def test_raises_value_error(self) -> None:
        from app.languages import LanguageConfig, known_language_codes, register
        from app.models.language import Language

        code = next(iter(known_language_codes()))
        with pytest.raises(ValueError, match="already registered"):
            register(code, LanguageConfig(language=Language.english()))


class TestDiscoverPluginsIdempotent:
    """_discover_plugins() must be a no-op when already executed."""

    def test_second_call_is_noop(self) -> None:
        import app.languages as mod

        # _discover_plugins() has already run at import time
        assert mod._discovered is True
        # Calling it again should just return immediately (line 97)
        mod._discover_plugins()


class TestOnePluginIsSufficient:
    """A deployment with only sl (+ en) must fully work — prove it by removing no."""

    def test_sl_only_no_absent(self) -> None:
        import app.languages as mod

        old_configs = mod._CONFIGS.copy()
        try:
            mod._CONFIGS.pop("no", None)
            assert "no" not in mod.known_language_codes()

            from app.languages import get_deck_name, get_preprocessor, get_vocab_notetype

            preprocessor = get_preprocessor("sl")
            assert preprocessor is not None

            deck = get_deck_name("sl")
            assert "Slovene" in deck

            vnt = get_vocab_notetype("sl")
            assert vnt is not None
        finally:
            mod._CONFIGS.clear()
            mod._CONFIGS.update(old_configs)


def _is_inside_type_checking(tree: ast.Module, target_line: int) -> bool:
    """Return True if *target_line* is inside an ``if TYPE_CHECKING:`` block."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            body_lines = [n.lineno for n in ast.walk(node) if hasattr(n, "lineno")]
            if body_lines and min(body_lines) <= target_line <= max(body_lines):
                return True
    return False


class TestLanguagesModuleTopNoAnkiOrPreprocessorImport:
    """app/languages.py must not import anki or concrete preprocessors at module level."""

    def test_no_module_level_anki_or_preprocessor_import(self) -> None:
        src = (BACKEND / "app" / "languages.py").read_text()
        tree = ast.parse(src)

        forbidden_prefixes = (
            "app.cards.vocab_notetype",
            "app.plugins.anki_sync",
            "app.audio.preprocessing.slovene",
            "app.audio.preprocessing.norwegian",
        )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(p) for p in forbidden_prefixes) and not _is_inside_type_checking(
                        tree, node.lineno
                    ):
                        pytest.fail(f"Module-level import of {alias.name!r} found at line {node.lineno}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and any(node.module.startswith(p) for p in forbidden_prefixes)
                and not _is_inside_type_checking(tree, node.lineno)
            ):
                pytest.fail(f"Module-level import of {node.module!r} found at line {node.lineno}")
