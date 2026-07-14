"""Guardrail tests for the Norwegian-breakdown plugin relocation.

Verifies that:
- ``get_breakdown`` routes to the plugin-registered callable (``no``) and
  returns ``None`` for languages without one (``sl``, ``en``).
- The end-to-end wiring through ``build_word_breakdown`` produces the expected
  compound breakdown — proof that the registry accessor works, not just that
  the function object exists.
- ``section_builder.py`` has no module-level import of any ``norwegian_breakdown``
  path — it must resolve the function through ``get_breakdown()`` at call time.
"""

import ast
from pathlib import Path

from app.generation.section_builder import build_word_breakdown
from app.languages import get_breakdown, get_slow_word

# -- Registry accessor -------------------------------------------------------


def test_get_breakdown_no_returns_callable():
    fn = get_breakdown("no")
    assert callable(fn)


def test_get_breakdown_sl_returns_none():
    assert get_breakdown("sl") is None


def test_get_breakdown_en_returns_none():
    assert get_breakdown("en") is None


def test_get_slow_word_no_returns_callable():
    fn = get_slow_word("no")
    assert callable(fn)


def test_get_slow_word_sl_returns_none():
    assert get_slow_word("sl") is None


# -- End-to-end wiring -------------------------------------------------------


def test_build_word_breakdown_norwegian_compound_via_registry():
    """build_word_breakdown routes Norwegian compounds through the registry.

    Reproduces the golden-sequence assertion from test_norwegian_breakdown.py
    to prove the plugin-registered function is called, not a stale import.
    """
    result = build_word_breakdown("etterforskningsteamet", "no")
    assert result[0] == "etterforskningsteamet"
    assert result[-1] == "etterforskningsteamet"
    assert "team" in " ".join(result)
    assert "et" in " ".join(result)


# -- AST guard ----------------------------------------------------------------


def test_section_builder_has_no_module_level_norwegian_breakdown_import():
    """section_builder.py must not import norwegian_breakdown at module level.

    The breakdown function must be resolved through the registry accessor
    (``get_breakdown``) so core stays free of Norwegian-specific dependencies.
    """
    src = Path(__file__).resolve().parent.parent / "app" / "generation" / "section_builder.py"
    tree = ast.parse(src.read_text())
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "norwegian_breakdown" in node.module:
            msg = (
                f"section_builder.py has a module-level import from {node.module} "
                f"(line {node.lineno}); the breakdown function must be resolved "
                "through get_breakdown() at call time."
            )
            raise AssertionError(msg)
