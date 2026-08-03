"""Ban direct reads of the SINGULAR ``settings.database_url``.

``database_url`` is the single-language default. On a multi-language install it
names ONE fixed language (Slovene, per the repo's dev ``.env``) no matter what
language the caller is working with, and ``resolve_language_context`` treats it
only as the fallback when ``database_urls[code]`` is absent.

Reading it directly is therefore a silent wrong-language bug, not a loud one: a
query filtered by ``language_code`` just matches nothing.
``grave_ignored_lemma_cards --language no`` opened the Slovene db and printed
"Nothing to grave." for a month while the cards it existed to remove sat in the
Norwegian db. The same read let ``schedule()`` grade a Norwegian card on Slovene
learning steps (Layer 82), which `test_queue_stats_language_isolation.py` could
only cover "by INSPECTION during audit".

Callers must use ``app.languages.resolve_db_path(code, settings)`` (or
``resolve_language_context``). Three readers are allowlisted because the
singular setting is genuinely theirs: the definition, the registry fallback that
implements it, and the single-language map in ``main``.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_singular_database_url import (  # noqa: E402
    ALLOWLIST_PATH,
    do_check,
    evaluate,
    scan_source,
)


class TestScanSource:
    def test_flags_a_direct_attribute_read(self):
        src = "from app.config import settings\npath = settings.database_url\n"
        assert [t for t, _ in scan_source(src)] == ["settings.database_url"]

    def test_flags_the_removeprefix_idiom(self):
        src = 'p = settings.database_url.removeprefix("sqlite:///")\n'
        assert len(scan_source(src)) == 1

    def test_does_not_flag_the_plural_map(self):
        """The whole point is the singular/plural distinction — a substring
        match on "database_url" would flag every correct caller."""
        src = 'db = settings.database_urls["no"]\nfor k in settings.database_urls: pass\n'
        assert scan_source(src) == []

    def test_does_not_flag_comments_or_docstrings(self):
        """These files explain the trap at length; prose must not trip it."""
        src = '"""Do not read settings.database_url here."""\n# settings.database_url is singular\nx = 1\n'
        assert scan_source(src) == []

    def test_does_not_flag_an_unrelated_object(self):
        """Only the settings object. A local named `database_url` is fine."""
        src = "database_url = cfg.database_url\nother.database_url = 1\n"
        assert scan_source(src) == []

    def test_flags_a_module_qualified_read(self):
        src = "import app.config\np = app.config.settings.database_url\n"
        assert len(scan_source(src)) == 1

    def test_flags_a_write_as_well_as_a_read(self):
        """Assigning it is how a script reroutes itself at runtime — same trap."""
        src = 'settings.database_url = "sqlite:///./other.db"\n'
        assert len(scan_source(src)) == 1


class TestParseErrorsFail:
    """A file that will not parse must FAIL, never be skipped.

    The sibling checkers warn-and-skip, which is a hole here: ruff only lints
    ``app`` and ``tests``, so nothing else in the gate reads ``scripts/``. While
    bulk-fixing the archive scripts for this very commit, a bad rewrite left one
    with an ``IndentationError`` — and the warn-and-skip version reported the
    tree clean. An unparseable file is exactly where an unchecked read would
    hide.
    """

    def test_unparseable_file_is_an_error_not_a_skip(self, tmp_path):
        from check_singular_database_url import scan_file

        bad = tmp_path / "broken.py"
        bad.write_text("def f():\nreturn settings.database_url\n")

        with pytest.raises(SyntaxError):
            scan_file(bad)


class TestEvaluate:
    def test_allowlisted_file_passes(self, tmp_path):
        allow = tmp_path / "allow.txt"
        allow.write_text("app/languages.py\n")

        code, messages = evaluate({"app/languages.py": Counter({"settings.database_url": 2})}, allow)

        assert code == 0
        assert messages == []

    def test_non_allowlisted_file_fails_with_the_remedy(self, tmp_path):
        allow = tmp_path / "allow.txt"
        allow.write_text("app/languages.py\n")

        code, messages = evaluate({"app/storage/thing.py": Counter({"settings.database_url": 1})}, allow)

        assert code == 1
        assert len(messages) == 1
        assert "app/storage/thing.py" in messages[0]
        assert "resolve_db_path" in messages[0], "the message must name the fix, not just the sin"

    def test_clean_scan_passes(self, tmp_path):
        allow = tmp_path / "allow.txt"
        allow.write_text("")

        assert evaluate({}, allow) == (0, [])


class TestRealTree:
    """The gate itself: the checked-in tree must be clean."""

    def test_repository_passes(self):
        assert do_check() == 0, "a new settings.database_url read has appeared — see this module's docstring"

    def test_allowlist_holds_only_the_three_sanctioned_readers(self):
        entries = [
            line.strip()
            for line in ALLOWLIST_PATH.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert sorted(entries) == ["app/config.py", "app/languages.py", "app/main.py"]

    @pytest.mark.parametrize("path", ["app/languages.py", "app/main.py"])
    def test_allowlisted_readers_really_do_read_it(self, path):
        """Guards the allowlist against rot: an entry that no longer reads the
        setting is stale and must be dropped, or the list stops meaning anything.

        ``app/config.py`` is excluded — it DEFINES the field rather than reading
        it through a ``settings.`` attribute access.
        """
        assert scan_source(Path(path).read_text()), f"{path} is allowlisted but no longer reads settings.database_url"
