"""Every route that renders ``<main>`` must style that element.

Svelte styles are component-scoped, so a ``main`` rule in another route's
``<style>`` block does not apply.  The review-session reader rendered at full
viewport width (22fc16b, 2026-09-03) because its ``<main>`` had no style at all.

Detection is regex-based (Svelte files, not Python), so prose is safe — comments
explaining the bug must not count as renders.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_main_styling import (  # noqa: E402
    check_source,
    do_check,
    evaluate,
)


class TestCheckSource:
    def test_element_selector_conforms(self):
        src = "<main></main>\n<style>\nmain { max-width: 760px; }\n</style>"
        assert check_source(src) == []

    def test_class_selector_conforms(self):
        """The settings route styles <main class="settings"> via .settings."""
        src = '<main class="settings"></main>\n<style>\n.settings { max-width: 42rem; }\n</style>'
        assert check_source(src) == []

    def test_violation_when_class_present_but_no_rule(self):
        src = '<main class="x"></main>\n<style>\n.foo { color: red; }\n</style>'
        assert check_source(src) == ["missing style for <main>"]

    def test_violation_when_no_style_block_at_all(self):
        """The 22fc16b bug: <main> rendered with no style block."""
        src = "<main></main>\n<p>Hello</p>"
        assert check_source(src) == ["missing style for <main>"]

    def test_no_main_not_applicable(self):
        src = "<div></div>\n<style>\ndiv { color: blue; }\n</style>"
        assert check_source(src) == []

    def test_main_only_in_comments_not_applicable(self):
        src = "<!-- <main> is unstyled -->\n<style>\nbody { margin: 0; }\n</style>"
        assert check_source(src) == []

    def test_selector_declared_only_in_css_comment_is_violation(self):
        """A `main { ... }` inside a CSS /* */ comment must not count."""
        src = "<main></main>\n<style>\n/* main { max-width: 700px; } */\n</style>"
        assert check_source(src) == ["missing style for <main>"]

    def test_main_styled_twice_conforms(self):
        """Base rule + media query counts as presence, reported once."""
        src = (
            "<main></main>\n<style>\n"
            "main { max-width: 700px; }\n"
            "@media (min-width: 768px) { main { max-width: 900px; } }\n"
            "</style>"
        )
        assert check_source(src) == []

    def test_main_with_id(self):
        src = '<main id="app"></main>\n<style>\n#app { width: 100%; }\n</style>'
        assert check_source(src) == []

    def test_main_with_id_but_no_rule(self):
        src = '<main id="app"></main>\n<style>\nbody { margin: 0; }\n</style>'
        assert check_source(src) == ["missing style for <main>"]

    def test_multiple_elements_only_main_checked(self):
        """Only <main> elements need styling; other elements are irrelevant."""
        src = "<div></div>\n<main></main>\n<style>\nmain { width: 100%; }\n</style>"
        assert check_source(src) == []

    def test_empty_source(self):
        assert check_source("") == []


class TestEvaluate:
    def test_all_conform(self):
        results = {
            "a/+page.svelte": [],
            "b/+page.svelte": [],
        }
        code, messages = evaluate(results)
        assert code == 0
        assert messages == []

    def test_one_violation(self):
        results = {
            "a/+page.svelte": [],
            "b/+page.svelte": ["missing style for <main>"],
        }
        code, messages = evaluate(results)
        assert code == 1
        assert len(messages) == 1
        assert "b/+page.svelte" in messages[0]

    def test_empty_results(self):
        code, messages = evaluate({})
        assert code == 0
        assert messages == []


class TestRealTree:
    def test_repository_passes(self):
        assert do_check() == 0, (
            "a route renders <main> without styling it — see backend/scripts/check_main_styling.py docstring"
        )


class TestSelectorBoundaries:
    """Regressions found auditing the first implementation (2026-09-04).

    Each of these silently returned the WRONG answer when the selector search
    ran unanchored over whole-file text. None was reachable on the tree at the
    time, which is exactly why a checker needs them pinned: its whole value is
    that it has no silent holes.
    """

    def test_dot_main_does_not_satisfy_element_selector(self):
        # `.main` styles whatever carries class="main" — not a bare <main>.
        assert check_source("<main>x</main><style>.main{color:red}</style>")

    def test_class_ending_in_main_does_not_satisfy(self):
        assert check_source("<main>x</main><style>.remain{color:red}</style>")
        assert check_source("<main>x</main><style>.domain{color:red}</style>")

    def test_element_does_not_satisfy_hyphenated_class(self):
        assert check_source('<main class="a">x</main><style>.a-wide{color:red}</style>')

    def test_selector_list_conforms(self):
        # `main, footer { }` is a perfectly good rule for <main>; requiring `{`
        # immediately after the selector reported it as a violation.
        assert not check_source("<main>x</main><style>main, footer{color:red}</style>")

    def test_compound_selector_conforms(self):
        assert not check_source('<main class="w">x</main><style>main.w{color:red}</style>')

    def test_rule_outside_style_block_does_not_count(self):
        # The invariant is a rule in the component's own <style> block.
        src = '<main>x</main><script>let s = "main {"</script><style>.z{color:red}</style>'
        assert check_source(src)
