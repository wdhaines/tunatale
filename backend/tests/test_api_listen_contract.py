"""B3: the listen-preview and commit-pending endpoints must publish a real
response schema in the OpenAPI doc — not a bare `dict`.

Without a declared schema there is nothing for a contract test (or a
frontend-generation tool) to check the response shape against. That absence
is exactly the seam that let a frontend agent hallucinate a different
`{untracked, tracked}` shape and a nonexistent `/mark-listened` route and
still pass its own tests.

This test resolves any `$ref` into `components.schemas` and pins the EXACT
property set for the 200 response of each endpoint — it must fail if a field
is renamed, added, or dropped.
"""

from __future__ import annotations

from app.main import app

PREVIEW_PATH = "/api/srs/lesson/{lesson_id}/listen-preview"
COMMIT_PATH = "/api/srs/lesson/{lesson_id}/commit-pending"


def _resolve_schema(schema: dict, components: dict) -> dict:
    """Resolve a (possibly $ref'd) schema to its concrete property dict."""
    if "$ref" in schema:
        # "#/components/schemas/Foo" -> components["schemas"]["Foo"]
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = components["schemas"][name]
    return schema


def _response_schema_for(openapi: dict, path: str, method: str) -> dict:
    op = openapi["paths"][path][method]
    content = op["responses"]["200"]["content"]["application/json"]["schema"]
    return _resolve_schema(content, openapi["components"])


class TestListenPreviewContract:
    def test_preview_response_schema_declares_candidate_fields(self):
        openapi = app.openapi()
        schema = _response_schema_for(openapi, PREVIEW_PATH, "get")
        assert schema, "GET listen-preview has no declared response schema"
        assert set(schema.get("properties", {})) == {"candidates"}

        candidates_schema = schema["properties"]["candidates"]
        item_schema = candidates_schema["items"]
        item_schema = _resolve_schema(item_schema, openapi["components"])
        assert set(item_schema.get("properties", {})) == {
            "kind",
            "text",
            "item_id",
            "grade_class",
            "rating",
            "translation",
            "progress",
            "well_known",
            "due_at",
        }


class TestCommitPendingContract:
    def test_commit_pending_response_schema_declares_fields(self):
        openapi = app.openapi()
        schema = _response_schema_for(openapi, COMMIT_PATH, "post")
        assert schema, "POST commit-pending has no declared response schema"
        assert set(schema.get("properties", {})) == {"status", "applied"}
