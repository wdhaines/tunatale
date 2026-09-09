"""A durable file sink for WARNING, so a post-mortem does not need a live process.

bd tunatale-y0bk.6. Every diagnostic signal on the gloss path was volatile until
this: `logger.warning` went to the dev server's stdout on a tty (`start-dev.sh`
runs uvicorn `--log-level warning`, no redirect), and `GET /api/llm/activity` is a
300-event in-memory ring that a `--reload` restart empties. On 2026-09-08 two
review sessions shipped with zero hover translations and the only trace was a
warning nobody could read afterwards; the ring had already lost everything before
14:56 by the time it was queried.

What survived and was decisive that day was `~/.tunatale/llm_usage.log` — file
backed, one line per request, precisely so it outlives a reload. This is the same
shape for warnings.
"""

from __future__ import annotations

import logging

from app.logging_sink import install_warning_file_handler, llm_failure_mirror

# ── the handler ───────────────────────────────────────────────────────────────


class TestInstallWarningFileHandler:
    def test_a_warning_reaches_the_file(self, tmp_path):
        path = tmp_path / "warnings.log"
        handler = install_warning_file_handler(path)
        try:
            logging.getLogger("app.test.sink").warning("gloss pass degraded: %s", "no")
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

        assert "gloss pass degraded: no" in path.read_text(encoding="utf-8")

    def test_info_does_not(self, tmp_path):
        """The sink is for what someone will need later, not a second app log.

        `logging.basicConfig(level=INFO)` in main.py means INFO is flowing; a sink
        that swallowed it would rotate away the warnings it exists to keep.
        """
        # ⚠️ The root level MUST be lowered for this test to test anything. With
        # the default root level (WARNING) an .info() call is dropped at the
        # LOGGER before any handler sees it, so the handler's own level is never
        # exercised — this test passed with the handler set to INFO. Production
        # runs `basicConfig(level=INFO)` in main.py, which is what makes the
        # handler level the thing that actually filters.
        root = logging.getLogger()
        previous = root.level
        root.setLevel(logging.INFO)
        path = tmp_path / "warnings.log"
        handler = install_warning_file_handler(path)
        try:
            logging.getLogger("app.test.sink").info("routine")
            logging.getLogger("app.test.sink").warning("kept")
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)
            root.setLevel(previous)

        text = path.read_text(encoding="utf-8")
        assert "routine" not in text
        assert "kept" in text

    def test_timestamps_are_utc_and_say_so(self, tmp_path):
        """⚠️ `sync.log` is LOCAL time while every other durable record here is UTC.

        Mixing them cost real confusion on 2026-09-08: `review_sessions.created_at`
        is UTC, the box is EDT, and a 4-hour error puts you in the wrong burst of
        LLM calls entirely. This sink is UTC and stamps a `Z` so a reader never has
        to guess which convention a line is in.
        """
        path = tmp_path / "warnings.log"
        handler = install_warning_file_handler(path)
        try:
            logging.getLogger("app.test.sink").warning("stamped")
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert "Z " in first, first

    def test_installing_twice_does_not_double_write(self, tmp_path):
        """uvicorn --reload re-runs the lifespan; a second handler duplicates every
        line and halves the effective rotation budget."""
        path = tmp_path / "warnings.log"
        first = install_warning_file_handler(path)
        second = install_warning_file_handler(path)
        try:
            assert second is first
            logging.getLogger("app.test.sink").warning("once")
        finally:
            first.close()
            logging.getLogger().removeHandler(first)

        assert path.read_text(encoding="utf-8").count("once") == 1

    def test_installing_at_a_new_path_moves_the_sink(self, tmp_path):
        """A changed path must actually take effect.

        Returning the existing handler regardless of path would make
        `install_warning_file_handler(p)` a lie whenever one was already
        attached — and in a test suite that has booted the real lifespan, every
        later caller would silently keep writing to the production log file
        instead of its own tmp_path. That is exactly how this branch was found:
        two tests failed in the full suite and passed alone.
        """
        first_path = tmp_path / "first.log"
        second_path = tmp_path / "second.log"
        first = install_warning_file_handler(first_path)
        second = install_warning_file_handler(second_path)
        try:
            assert second is not first
            logging.getLogger("app.test.sink").warning("moved")
        finally:
            second.close()
            logging.getLogger().removeHandler(second)

        assert "moved" in second_path.read_text(encoding="utf-8")
        assert "moved" not in first_path.read_text(encoding="utf-8")

    def test_an_unwritable_path_is_not_fatal(self, tmp_path):
        """A logging problem must never stop the app booting — the same
        fail-open contract `rotate_db_backups` has."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")

        assert install_warning_file_handler(blocker / "nested" / "warnings.log") is None


# ── the LLM failure mirror ────────────────────────────────────────────────────


class TestLlmFailureMirror:
    """Every failed LLM call must reach the durable sink.

    `LLMClient` logs a warning on SOME failure paths (a 429 retry, a fallback
    attempt) and not others — a hard failure with `allow_fallback=False` raises
    with nothing logged. Mirroring the activity callback covers all of them in one
    place rather than auditing each raise site.
    """

    def test_a_failed_call_is_logged(self, caplog):
        recorded = []
        mirror = llm_failure_mirror(recorded.append)

        with caplog.at_level(logging.WARNING):
            mirror({"provider": "groq", "status": 429, "error": "rate limited", "latency_ms": 120})

        assert "429" in caplog.text
        assert "rate limited" in caplog.text

    def test_a_successful_call_is_not(self, caplog):
        recorded = []
        mirror = llm_failure_mirror(recorded.append)

        with caplog.at_level(logging.WARNING):
            mirror({"provider": "groq", "status": "success", "latency_ms": 300})

        assert caplog.text == ""

    def test_the_wrapped_callback_still_runs(self):
        """The ring buffer is the live UI's feed and must not lose events to this."""
        recorded = []
        mirror = llm_failure_mirror(recorded.append)

        mirror({"provider": "groq", "status": "success"})
        mirror({"provider": "groq", "status": 500, "error": "boom"})

        assert len(recorded) == 2
