"""Tests for ``scripts/check_openapi_snapshot.py``.

Uses **synthetic schema dicts** — never the live app schema, which changes on
every endpoint addition.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_openapi_snapshot import _check_untyped, _collect_untyped, _load_grandfather  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def _schema_with_response(
    operation_id: str,
    response_schema: dict,
    *,
    status_code: str = "200",
    media_type: str = "application/json",
) -> dict:
    """Build a minimal OpenAPI schema with one operation and one response."""
    return {
        "paths": {
            "/test": {
                "get": {
                    "operationId": operation_id,
                    "responses": {status_code: {"content": {media_type: {"schema": response_schema}}}},
                }
            }
        }
    }


# ── _collect_untyped ────────────────────────────────────────────────────────


class TestCollectUntyped:
    """Tests for the raw schema→untyped-ops collector."""

    def test_flags_empty_schema(self):
        schema = _schema_with_response("op_empty", {})
        assert _collect_untyped(schema) == ["op_empty"]

    def test_flags_additional_properties(self):
        schema = _schema_with_response("op_dict", {"additionalProperties": True})
        assert _collect_untyped(schema) == ["op_dict"]

    def test_does_not_flag_top_level_ref(self):
        schema = _schema_with_response("op_ref", {"$ref": "#/components/schemas/Foo"})
        assert _collect_untyped(schema) == []

    def test_does_not_flag_array_of_ref(self):
        """Array wrapping a $ref is typed — the items carry the schema."""
        schema = _schema_with_response(
            "op_list",
            {"type": "array", "items": {"$ref": "#/components/schemas/Foo"}},
        )
        assert _collect_untyped(schema) == []

    def test_does_not_flag_anyof_refOrNull(self):
        """anyOf with a $ref and null is an optional typed response."""
        schema = _schema_with_response(
            "op_optional",
            {"anyOf": [{"$ref": "#/components/schemas/Foo"}, {"type": "null"}]},
        )
        assert _collect_untyped(schema) == []

    def test_does_not_flag_allof_ref(self):
        """allOf wrapping a $ref is typed."""
        schema = _schema_with_response(
            "op_allof",
            {"allOf": [{"$ref": "#/components/schemas/Foo"}]},
        )
        assert _collect_untyped(schema) == []

    def test_flags_type_string(self):
        """Bare {"type": "string"} has no structural schema — untyped."""
        schema = _schema_with_response("op_str", {"type": "string"})
        assert _collect_untyped(schema) == ["op_str"]

    def test_non_2xx_ignored(self):
        schema = _schema_with_response("op_404", {}, status_code="404")
        assert _collect_untyped(schema) == []

    def test_non_json_ignored(self):
        schema = _schema_with_response("op_stream", {}, media_type="application/octet-stream")
        assert _collect_untyped(schema) == []


# ── _check_untyped gate ──────────────────────────────────────────────────────


class TestCheckUntyped:
    """Tests for the untyped-endpoint gate with grandfather enforcement."""

    def test_new_untyped_endpoint_fails(self, tmp_path: Path):
        ledger = tmp_path / "grandfather.txt"
        ledger.write_text("existing_op\n", encoding="utf-8")
        schema = _schema_with_response("new_op", {})
        assert _check_untyped(schema, ledger) == 1

    def test_ledgered_endpoint_passes(self, tmp_path: Path):
        ledger = tmp_path / "grandfather.txt"
        ledger.write_text("known_op\n", encoding="utf-8")
        schema = _schema_with_response("known_op", {})
        assert _check_untyped(schema, ledger) == 0

    def test_stale_ledger_entry_fails(self, tmp_path: Path):
        """An operation in the ledger that is now typed must be removed."""
        ledger = tmp_path / "grandfather.txt"
        ledger.write_text("now_typed_op\n", encoding="utf-8")
        schema = _schema_with_response("now_typed_op", {"$ref": "#/components/schemas/Foo"})
        assert _check_untyped(schema, ledger) == 1


# ── _load_grandfather ────────────────────────────────────────────────────────


class TestLoadGrandfather:
    def test_reads_entries(self, tmp_path: Path):
        f = tmp_path / "gf.txt"
        f.write_text("a\nb\n", encoding="utf-8")
        assert _load_grandfather(f) == {"a", "b"}

    def test_missing_file(self, tmp_path: Path):
        assert _load_grandfather(tmp_path / "nope.txt") == set()

    def test_skips_blanks_and_comments(self, tmp_path: Path):
        f = tmp_path / "gf.txt"
        f.write_text("\n# comment\na\n\nb\n", encoding="utf-8")
        assert _load_grandfather(f) == {"a", "b"}

    def test_strips_trailing_reason(self, tmp_path: Path):
        f = tmp_path / "gf.txt"
        f.write_text("some_op  # reason: untyped handler\nanother_op\n", encoding="utf-8")
        assert _load_grandfather(f) == {"some_op", "another_op"}
