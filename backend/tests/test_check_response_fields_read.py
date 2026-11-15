"""Tests for ``scripts/check_response_fields_read.py``.

Every case builds its own schema and frontend tree under ``tmp_path``; the live
schema changes with every endpoint, which is exactly what the checker polices.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_response_fields_read import (  # noqa: E402
    do_check,
    frontend_words,
    load_reasoned_allowlist,
    main,
    schema_property_names,
)


def _schema(**schemas: list[str]) -> dict:
    return {
        "components": {"schemas": {name: {"properties": {p: {} for p in props}} for name, props in schemas.items()}}
    }


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_schema_property_names_maps_each_name_to_its_schemas() -> None:
    schema = _schema(A=["x", "y"], B=["y"])
    schema["components"]["schemas"]["C"] = {"type": "string"}  # no properties at all
    assert schema_property_names(schema) == {"x": ["A"], "y": ["A", "B"]}


def test_frontend_words_reads_svelte_and_ts_but_not_generated_or_test_files(tmp_path: Path) -> None:
    src = _tree(
        tmp_path / "src",
        {
            "lib/Card.svelte": "{item.read_in_svelte}",
            "lib/api.ts": "x.read_in_ts?.y",
            "lib/api-types.d.ts": "only_in_types: string;",
            "lib/api-schema.json": '"only_in_schema"',
            "lib/Card.test.ts": "item.only_in_test",
            "test/factories.ts": "only_in_factories",
            "lib/notes.md": "only_in_markdown",
        },
    )
    words = frontend_words(src)
    assert {"read_in_svelte", "read_in_ts", "y"} <= words
    assert not words & {"only_in_types", "only_in_schema", "only_in_test", "only_in_factories", "only_in_markdown"}


def test_a_name_is_read_only_as_a_whole_word(tmp_path: Path) -> None:
    src = _tree(tmp_path / "src", {"a.ts": "x.cue_audio_url_extra; before_cue2"})
    words = frontend_words(src)
    assert "cue_audio_url" not in words
    assert "before_cue" not in words


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("a.ts", "// the pull_required flag\nconst x = 1;"),
        ("a.ts", "/* pull_required\n   spans lines */ const x = 1;"),
        ("a.ts", "/** @param pull_required doc comment */\nfunction f() {}"),
        ("C.svelte", "<!-- pull_required in markup -->\n<p>hi</p>"),
        ("C.svelte", '<div class="x"></div>\n<style>\n\t.pull_required { color: red; }\n</style>'),
        (
            "api.ts",
            "export interface Status {\n  pull_required: boolean;\n  nested: { inner: { pull_required: number } };\n}",
        ),
        ("api.ts", "export type Status = {\n  pull_required: boolean;\n};"),
        ("api.ts", "type Kind = Pick<Status, 'id' | 'pull_required'>;"),
        ("C.svelte", '<script lang="ts">\n\tinterface Props {\n\t\tpull_required: boolean;\n\t}\n</script>'),
        ("api.ts", "async undo(): Promise<{\n    status: string;\n    pull_required: string;\n  }> {\n  return x;\n}"),
        ("api.ts", "const r = await this.request<{ pull_required: boolean }>(url);"),
    ],
    ids=[
        "line-comment",
        "block-comment",
        "jsdoc",
        "html-comment",
        "style",
        "interface",
        "type-literal",
        "type-alias",
        "svelte-props",
        "generic-return-type",
        "generic-argument",
    ],
)
def test_a_mention_that_is_not_a_read_does_not_count(tmp_path: Path, rel: str, text: str) -> None:
    """Comments, CSS and type declarations name a field without reading it (Fable's #429 audit)."""
    assert "pull_required" not in frontend_words(_tree(tmp_path / "src", {rel: text}))


@pytest.mark.parametrize(
    "text",
    [
        "body: JSON.stringify({ card_grades: grades })",  # a request key is a real send
        "const { card_grades } = plan;",  # destructuring reads it
        "if (s.card_grades) go();",
        "const url = `${BASE}/x?n=${p.card_grades}`;",
        'fetch("http://example.com/a"); use(p.card_grades);',  # a URL is not a line comment
        "interface P { a: string }\nconst v = p.card_grades;",  # code after an interface still counts
    ],
    ids=["request-key", "destructure", "property", "template", "url-then-code", "after-interface"],
)
def test_code_that_reads_or_sends_a_field_counts(tmp_path: Path, text: str) -> None:
    assert "card_grades" in frontend_words(_tree(tmp_path / "src", {"a.ts": text}))


def test_svelte_markup_and_script_both_count(tmp_path: Path) -> None:
    text = '<script lang="ts">\n\tconst a = item.in_script;\n</script>\n<p>{item.in_markup}</p>'
    words = frontend_words(_tree(tmp_path / "src", {"C.svelte": text}))
    assert {"in_script", "in_markup"} <= words


def test_an_unread_name_fails_and_names_its_schema() -> None:
    errors = do_check(_schema(Item=["shown", "dropped"]), {"shown"}, {})
    assert len(errors) == 1
    assert "dropped" in errors[0]
    assert "Item" in errors[0]


def test_an_allowlisted_unread_name_passes() -> None:
    assert do_check(_schema(Item=["shown", "dropped"]), {"shown"}, {"dropped": "read by curl"}) == []


def test_a_stale_allowlist_entry_fails_when_the_name_is_now_read() -> None:
    errors = do_check(_schema(Item=["shown"]), {"shown"}, {"shown": "was unread"})
    assert len(errors) == 1
    assert "shown" in errors[0]
    assert "stale" in errors[0]


def test_a_stale_allowlist_entry_fails_when_no_schema_has_the_name() -> None:
    errors = do_check(_schema(Item=["shown"]), {"shown"}, {"gone": "deleted field"})
    assert len(errors) == 1
    assert "gone" in errors[0]


def test_allowlist_requires_a_reason_per_name(tmp_path: Path) -> None:
    path = tmp_path / "allow.txt"
    path.write_text("# header\n\nfoo  # read by curl\n", encoding="utf-8")
    assert load_reasoned_allowlist(path) == {"foo": "read by curl"}

    path.write_text("bar\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bar"):
        load_reasoned_allowlist(path)

    path.write_text("baz  #   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="baz"):
        load_reasoned_allowlist(path)


def test_allowlist_rejects_a_duplicate_name(tmp_path: Path) -> None:
    path = tmp_path / "allow.txt"
    path.write_text("foo  # one\nfoo  # two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="foo"):
        load_reasoned_allowlist(path)


def test_missing_allowlist_file_is_empty(tmp_path: Path) -> None:
    assert load_reasoned_allowlist(tmp_path / "absent.txt") == {}


def _repo(tmp_path: Path, schema: dict, files: dict[str, str], allow: str) -> tuple[Path, Path, Path]:
    src = _tree(tmp_path / "frontend" / "src", files)
    (src / "lib").mkdir(parents=True, exist_ok=True)
    schema_path = src / "lib" / "api-schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    allow_path = tmp_path / "allow.txt"
    allow_path.write_text(allow, encoding="utf-8")
    return schema_path, src, allow_path


def test_main_exits_zero_when_every_name_is_read_or_allowlisted(tmp_path: Path, capsys) -> None:
    schema_path, src, allow = _repo(
        tmp_path, _schema(Item=["shown", "dropped"]), {"lib/C.svelte": "{item.shown}"}, "dropped  # curl only\n"
    )
    assert main(schema_path=schema_path, src_root=src, allowlist_path=allow) == 0
    assert "OK" in capsys.readouterr().out


def test_main_exits_one_and_prints_each_unread_name(tmp_path: Path, capsys) -> None:
    schema_path, src, allow = _repo(tmp_path, _schema(Item=["shown", "dropped"]), {"lib/C.svelte": "{item.shown}"}, "")
    assert main(schema_path=schema_path, src_root=src, allowlist_path=allow) == 1
    err = capsys.readouterr().err
    assert "dropped" in err


def test_main_exits_one_on_an_allowlist_line_without_a_reason(tmp_path: Path, capsys) -> None:
    schema_path, src, allow = _repo(tmp_path, _schema(Item=["dropped"]), {}, "dropped\n")
    assert main(schema_path=schema_path, src_root=src, allowlist_path=allow) == 1
    assert "dropped" in capsys.readouterr().err


def test_live_tree_is_clean() -> None:
    """The committed schema, frontend and allowlist agree (the gate's own run)."""
    assert main() == 0
