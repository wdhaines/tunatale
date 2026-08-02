"""Tests for ``scripts/check_openapi_snapshot.py``.

Uses **synthetic schema dicts** — never the live app schema, which changes on
every endpoint addition.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_openapi_snapshot import _check_untyped, _collect_untyped  # noqa: E402

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


class TestZeroTolerance:
    """The untyped gate after the ledger drained 70 -> 0 (2026-08-02).

    There is no grandfather file and no membership test any more: ANY untyped
    operation fails. These replace the three ratchet tests (new-untyped /
    ledgered-passes / stale-entry), whose premise — a ledger to be a member of —
    no longer exists. Both of the behaviours those pinned are subsumed: under
    zero tolerance an untyped op fails whether or not it was ever ledgered, and
    a now-typed op simply passes with nothing left to go stale.
    """

    def test_clean_schema_passes(self):
        schema = _schema_with_response("typed_op", {"$ref": "#/components/schemas/Foo"})
        assert _check_untyped(schema) == 0

    def test_any_untyped_endpoint_fails(self):
        schema = _schema_with_response("some_op", {})
        assert _check_untyped(schema) == 1

    def test_previously_ledgered_operation_gets_no_free_pass(self):
        """The last two drained entries, re-offered untyped, must still fail.

        A membership-based implementation would let these through; that is the
        thing the ledger's deletion had to not preserve.
        """
        for op_id in (
            "get_review_queue_api_srs_review_queue_get",
            "get_lesson_review_queue_api_srs_lesson__lesson_id__review_queue_get",
        ):
            assert _check_untyped(_schema_with_response(op_id, {})) == 1

    def test_failure_message_names_the_fix(self, capsys):
        _check_untyped(_schema_with_response("some_op", {}))
        out = capsys.readouterr().out
        assert "some_op" in out
        assert "response_model=" in out
        assert "grandfather" not in out.lower()


# ── binary endpoints must not advertise JSON ─────────────────────────────────


class TestBinaryEndpointsDeclareNonJson:
    """Binary-stream endpoints must declare their real media type, not JSON.

    These three return ``Response``/``FileResponse`` with a binary body and can
    never return JSON, but FastAPI advertises ``application/json`` unless the
    route declares otherwise. That is not merely cosmetic: the generated
    frontend types described a JSON body for a byte stream, and the endpoints
    counted as untyped-JSON debt they could never repay.
    """

    BINARY_OPS = {
        "download_lesson_zip_api_audio_lesson__lesson_id__zip_get": "application/zip",
        "get_audio_api_audio__audio_id__get": "application/octet-stream",
        "serve_media_api_srs_media__filename__get": "application/octet-stream",
    }

    def _content_types(self) -> dict[str, set[str]]:
        from app.main import app

        schema = app.openapi()
        found: dict[str, set[str]] = {}
        for methods in schema["paths"].values():
            for op in methods.values():
                if not isinstance(op, dict):
                    continue
                op_id = op.get("operationId")
                if op_id in self.BINARY_OPS:
                    found[op_id] = set(op["responses"]["200"].get("content", {}))
        return found

    def test_all_three_operations_are_present(self):
        # Guards the test itself: a renamed operation-id must fail loudly here
        # rather than silently asserting nothing.
        assert set(self._content_types()) == set(self.BINARY_OPS)

    def test_binary_endpoints_do_not_advertise_json(self):
        for op_id, content in self._content_types().items():
            assert "application/json" not in content, f"{op_id} still advertises JSON"

    def test_binary_endpoints_declare_their_media_type(self):
        found = self._content_types()
        for op_id, media_type in self.BINARY_OPS.items():
            assert media_type in found[op_id], f"{op_id} missing {media_type}"
