"""Retired one-shot Anki migration / repair / analysis scripts.

Every module here was a single-use migration, data repair, or analysis pass that
has already been applied to the production collection. None is imported by
production code (verified: zero non-test references at archival time). They are
kept — rather than deleted — because several rule files cite them as canonical
patterns (grave-writing, schema-bump workflow, dedupe). They are excluded from
the coverage gate via ``app/anki/archive/*`` in pyproject's ``coverage.run.omit``.

If you need to re-run one: ``uv run python -m app.anki.archive.<name>``.
If you need a new migration, copy the *shape* of one of these into ``app/anki/``.
"""
