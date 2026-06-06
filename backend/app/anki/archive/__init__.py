"""Retired Anki tooling: one-shot migrations / repairs / analysis + unused clients.

Most modules here were single-use migrations, data repairs, or analysis passes
already applied to the production collection. A few are retired *infrastructure*
that fell out of use (e.g. ``anki_connect`` — the AnkiConnect HTTP client, whose
only consumers were dead test scaffolding; ``bootstrap_tt_revlog`` — the one-time
tt_revlog seeding tool). None is imported by production code (verified: zero
non-test references at archival time). They are kept — rather than deleted —
because several rule files cite them as canonical patterns (grave-writing,
schema-bump workflow, dedupe). They are excluded from the coverage gate via
``app/anki/archive/*`` in pyproject's ``coverage.run.omit``.

If you need to re-run one: ``uv run python -m app.anki.archive.<name>``.
If you need a new migration, copy the *shape* of one of these into ``app/anki/``.
"""
