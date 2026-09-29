"""Retired storage tooling: one-shot data migrations already applied to the store.

The modules here are single-use migrations over stored lessons/curricula that
have since been applied and are kept only as a record of the repair (and as
guardrail-tested reference for the store API they drive). None is imported by
production code (verified: zero non-test references at archival time). They live
under ``scripts/`` (not ``app/``) so the coverage gate (``source = ["app"]``)
and ``ruff check app`` skip them — retired tooling shouldn't gate the build.

If you need to re-run one: ``uv run python -m scripts.storage_archive.<name>``
(``lowercase_glosses`` has the CLI entry point for this; ``backfill_curriculum_day_titles``
is library-only, so call it against a ``ContentStore`` directly).
If you need a new storage migration, copy the *shape* of one of these into
``app/storage/``.
"""
