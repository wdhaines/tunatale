/**
 * Self-tests for scripts/coverage-gate.ts.
 *
 * The phantom-detection heuristic is the load-bearing piece of the coverage
 * gate. These tests lock in the classification of every branch shape Opus
 * empirically saw on TunaTale's coverage data. If you tighten or relax the
 * heuristic, update these and re-validate against current coverage output
 * (see comment block at top of scripts/coverage-gate.ts).
 */
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { isPhantom, runGate } from "../scripts/coverage-gate";

describe("isPhantom", () => {
  describe("empty / synthetic", () => {
    it("drops empty source ranges across all branch types", () => {
      expect(isPhantom("if", "", false)).toBe(true);
      expect(isPhantom("binary-expr", "", false)).toBe(true);
      expect(isPhantom("cond-expr", "", false)).toBe(true);
    });

    it("drops synthetic locations regardless of text content", () => {
      expect(isPhantom("if", "looks-real", true)).toBe(true);
      expect(isPhantom("binary-expr", "value || other", true)).toBe(true);
      expect(isPhantom("cond-expr", "e.message", true)).toBe(true);
    });
  });

  describe("cond-expr (ternary `a ? b : c`)", () => {
    it("drops null/undefined/boolean/number literal results", () => {
      expect(isPhantom("cond-expr", "null", false)).toBe(true);
      expect(isPhantom("cond-expr", "undefined", false)).toBe(true);
      expect(isPhantom("cond-expr", "true", false)).toBe(true);
      expect(isPhantom("cond-expr", "false", false)).toBe(true);
      expect(isPhantom("cond-expr", "42", false)).toBe(true);
      expect(isPhantom("cond-expr", "-1.5", false)).toBe(true);
    });

    it("drops quoted-string literal results", () => {
      expect(isPhantom("cond-expr", '"unknown"', false)).toBe(true);
      expect(isPhantom("cond-expr", "'fallback'", false)).toBe(true);
      expect(isPhantom("cond-expr", "`literal`", false)).toBe(true);
    });

    it("keeps property-access expressions as real branches", () => {
      // empirical: srs/+page.svelte L73,145,155 — error.instanceof Error ? e.message : ...
      expect(isPhantom("cond-expr", "e.message", false)).toBe(false);
      expect(isPhantom("cond-expr", "error.message", false)).toBe(false);
    });

    it("keeps identifier and function-call expressions as real branches", () => {
      expect(isPhantom("cond-expr", "String(error)", false)).toBe(false);
      expect(isPhantom("cond-expr", "{ direction }", false)).toBe(false);
    });
  });

  describe("binary-expr (`||`, `&&`, `??`)", () => {
    it("drops template-fragment text starting with `}` (template re-entry)", () => {
      // empirical: SyncButton L87, srs L212, +page.svelte L82, lessonId L161, L167
      expect(isPhantom("binary-expr", "} created, {", false)).toBe(true);
      expect(isPhantom("binary-expr", "} learning · {", false)).toBe(true);
      expect(isPhantom("binary-expr", "} new · {", false)).toBe(true);
      expect(isPhantom("binary-expr", "} days · {data.", false)).toBe(true);
      expect(isPhantom("binary-expr", "} phrase{", false)).toBe(true);
      expect(isPhantom("binary-expr", "}\">← {data.", false)).toBe(true);
    });

    it("drops template-fragment text ending with `{` (interpolation entry)", () => {
      // empirical: Tooltip L39 — span attribute opening into {state} interp
      expect(
        isPhantom("binary-expr", 'class="tt-state tt-state-{state}">{', false),
      ).toBe(true);
      expect(isPhantom("binary-expr", '}">{', false)).toBe(true);
    });

    it("keeps `??`-fallback expressions with identifiers as real branches", () => {
      // empirical: [lessonId]/+page.svelte L75,167
      expect(isPhantom("binary-expr", "?? []", false)).toBe(false);
      expect(isPhantom("binary-expr", "?? section.type", false)).toBe(false);
      expect(isPhantom("binary-expr", '|| "default"', false)).toBe(false);
    });

    it("keeps function-call branches as real", () => {
      expect(isPhantom("binary-expr", "() && fn()", false)).toBe(false);
    });

    it("keeps JS object literals as real branches (start with `{`, end with `}`)", () => {
      // a || { foo: 1 } — sub-location text is the object literal itself
      expect(isPhantom("binary-expr", "{ foo: 1 }", false)).toBe(false);
      expect(isPhantom("binary-expr", "{ direction }", false)).toBe(false);
    });

    it("drops bare JS literal fallbacks (?? '', || 0, ?? false)", () => {
      // empirical: Transcript L98,L99 — getAttribute(...) ?? '' defensive default
      expect(isPhantom("binary-expr", "''", false)).toBe(true);
      expect(isPhantom("binary-expr", '""', false)).toBe(true);
      expect(isPhantom("binary-expr", "0", false)).toBe(true);
      expect(isPhantom("binary-expr", "false", false)).toBe(true);
      expect(isPhantom("binary-expr", "null", false)).toBe(true);
    });

    it("keeps `?? <expr>` shapes where the RHS is non-literal", () => {
      // `?? []` is not a bare literal — it's the whole expression text
      // including the operator. Stays real. Same for property accesses.
      expect(isPhantom("binary-expr", "?? []", false)).toBe(false);
      expect(isPhantom("binary-expr", "?? ''", false)).toBe(false);
    });

    it("drops an operand whose range DUPLICATES another operand's (compiler-made `?? ''`)", () => {
      // empirical 2026-09-11 (i18n sweep): `{t('x')} {cond ? 'a' : 'b'}` and a
      // `{t('x')}` sharing a text run with a sibling compile to a template
      // string with `t(...) ?? ""`; v8 maps BOTH operands to the source
      // expression (Transcript L665 cols 32-45 twice, TranscriptPlaceholder
      // L20 cols 3-5 twice), so the text is an identifier / call and the
      // literal rule above cannot see it. Source `a ?? b` always has two
      // distinct operand ranges, so an identical pair is compiler-made.
      expect(isPhantom("binary-expr", "t(", false, true)).toBe(true);
      expect(isPhantom("binary-expr", "showAddPhrase", false, true)).toBe(true);
    });

    it("keeps the same texts as real when the operand ranges are distinct (control)", () => {
      expect(isPhantom("binary-expr", "t(", false, false)).toBe(false);
      expect(isPhantom("binary-expr", "showAddPhrase", false)).toBe(false);
    });

    it("scopes the duplicate-range rule to binary-expr", () => {
      // Only measured for `??` fallbacks; a ternary keeps its literal-only rule.
      expect(isPhantom("cond-expr", "e.message", false, true)).toBe(false);
      expect(isPhantom("if", "return x;", false, true)).toBe(false);
    });
  });

  describe("if (template {#if} or JS if)", () => {
    it("keeps non-empty if-body ranges as real branches", () => {
      // empirical: srs/+page.svelte L116 — Set toggle
      expect(
        isPhantom("if", "if (next.has(id)) next.delete(id);", false),
      ).toBe(false);
      // empirical: DrillCard.svelte L43 — early return
      expect(isPhantom("if", 'return "";', false)).toBe(false);
    });
  });

  describe("unknown branch types", () => {
    it("keeps unknown branch types as real (conservative default)", () => {
      expect(isPhantom("default-arg", "= someExpr", false)).toBe(false);
      expect(isPhantom("switch", "case 1:", false)).toBe(false);
    });
  });
});

describe("runGate", () => {
  it("passes when all files have 100% across all metrics", () => {
    const fixture = {
      "/abs/path/clean.svelte": {
        path: "/abs/path/clean.svelte",
        statementMap: { "0": { start: { line: 1, column: 0 }, end: { line: 1, column: 10 } } },
        fnMap: {},
        branchMap: {},
        s: { "0": 5 },
        f: {},
        b: {},
      },
    };
    const result = runGate(fixture);
    expect(result.failures).toEqual([]);
    expect(result.dropped).toEqual([]);
  });

  it("fails when a file has uncovered real branches that aren't phantoms", () => {
    // A binary-expr at line 1 with sub-location text "?? section.type" (real branch, not droppable)
    // is uncovered. Gate must fail.
    const fixture = {
      "/abs/path/file.svelte": {
        path: "/abs/path/file.svelte",
        statementMap: {},
        fnMap: {},
        branchMap: {
          "0": {
            type: "binary-expr",
            line: 1,
            loc: { start: { line: 1, column: 0 }, end: { line: 1, column: 20 } },
            // We can't readRange against a fake file path; this test uses isPhantom indirectly.
            // The location read will return synthetic=true (no file exists),
            // which makes isPhantom return true — so this becomes a "dropped" case.
            // For a true integration test of failure path, we need a real file fixture.
            locations: [
              { start: { line: 1, column: 0 }, end: { line: 1, column: 5 } },
              { start: { line: 1, column: 10 }, end: { line: 1, column: 20 } },
            ],
          },
        },
        s: {},
        f: {},
        b: { "0": [0, 0] },
      },
    };
    const result = runGate(fixture);
    // Synthetic ranges (file doesn't exist) → both locations marked phantom →
    // adjusted total is 0 → branchPct = 100 (no-branches path). So this fixture
    // exercises the "dropped" path but not failure. Verify drops.
    expect(result.dropped.length).toBe(2);
    expect(result.failures).toEqual([]);
  });

  describe("duplicate operand ranges (real source file)", () => {
    // readRange needs a file that exists, or every location is synthetic and
    // dropped for the wrong reason — so these write a real one.
    const dir = mkdtempSync(join(tmpdir(), "coverage-gate-"));
    const file = join(dir, "Fixture.svelte");
    writeFileSync(file, "\t\t\t{t('transcript.addPhrase')} {showAddPhrase ? 'a' : 'b'}\n");
    afterAll(() => rmSync(dir, { recursive: true, force: true }));

    const call = { start: { line: 1, column: 4 }, end: { line: 1, column: 6 } }; // "t("
    const ident = { start: { line: 1, column: 32 }, end: { line: 1, column: 45 } }; // "showAddPhrase"

    function fixture(locations: (typeof call)[]) {
      return {
        [file]: {
          path: file,
          statementMap: {},
          fnMap: {},
          branchMap: {
            "0": { type: "binary-expr", line: 1, loc: locations[0], locations },
          },
          s: {},
          f: {},
          b: { "0": [7, 0] },
        },
      };
    }

    it("drops the uncovered operand when both operands share one range", () => {
      const result = runGate(fixture([call, call]));
      expect(result.failures).toEqual([]);
      expect(result.dropped).toHaveLength(1);
      expect(result.dropped[0].locIdx).toBe(1);
    });

    it("still FAILS when the operands are distinct and the uncovered one is an identifier (control)", () => {
      const result = runGate(fixture([call, ident]));
      expect(result.dropped).toEqual([]);
      expect(result.failures).toHaveLength(1);
      expect(result.failures[0].uncoveredSamples[0]).toContain("showAddPhrase");
    });
  });
});
