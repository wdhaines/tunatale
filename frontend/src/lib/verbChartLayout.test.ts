/**
 * The verb chart's grid arithmetic (tunatale-h0q3.47), checked against the real chart
 * file the backend serves. The published chart's own check, kept: every table row,
 * together with the rowspans reaching into it, covers exactly ten columns, with the
 * literary rows shown and hidden.
 */
import { describe, expect, it } from "vitest";
import type { VerbChart, VerbChartRow } from "#lib/api.js";
import chartJson from "../../../backend/app/plugins/languages/ceb/data/verb_chart.json";
import {
  cardEntries,
  layoutChart,
  rowHeight,
  rowSummary,
  visibleAffixes,
  type GridCell,
  type GridRow,
} from "./verbChartLayout.js";

const chart = chartJson as VerbChart;

function span(cell: GridCell): { rowspan: number; colspan: number } {
  const rowspan = "rowspan" in cell ? cell.rowspan : 1;
  const colspan = "colspan" in cell ? cell.colspan : cell.kind === "above" ? 3 : 1;
  return { rowspan, colspan };
}

/** Columns each table row covers, counting the rowspans that reach down into it. */
function widths(grid: GridRow[]): number[] {
  const carried: number[] = Array.from({ length: grid.length }, () => 0);
  return grid.map((row, i) => {
    let own = 0;
    for (const cell of row.cells) {
      const { rowspan, colspan } = span(cell);
      own += colspan;
      for (let k = 1; k < rowspan; k++) carried[i + k] += colspan;
    }
    return own + carried[i];
  });
}

describe("layoutChart on the real chart", () => {
  it.each([false, true])("every table row covers ten columns (literary shown: %s)", (lit) => {
    const grid = layoutChart(chart, lit);
    expect(widths(grid)).toEqual(Array.from({ length: grid.length }, () => 10));
  });

  it("leaves the literary rows out unless they are shown", () => {
    const ids = (lit: boolean) => [...new Set(layoutChart(chart, lit).map((r) => r.rowId))];
    expect(ids(false)).toHaveLength(12);
    expect(ids(true)).toHaveLength(15);
    expect(ids(true).filter((id) => !ids(false).includes(id))).toEqual([
      "patient-paga",
      "circumstance-paga",
      "instrument-paga",
    ]);
  });

  it("spans a shared affix across its columns: agent perfective mo- covers irrealis and dependent", () => {
    const chips = layoutChart(chart, false).find((r) => r.key === "agent-mo:chips")!;
    const drawn = chips.cells.map((c) =>
      c.kind === "chips" ? `${c.affixes.map((a) => a.label).join("|")}×${c.colspan}` : c.kind,
    );
    expect(drawn).toEqual([
      "mi-, ni-|ming-, ning-×1",
      "mo-×2",
      "∅, pag-×1",
      "naka-, ka-×1",
      "maka-, ka-×2",
    ]);
  });

  it("draws one dynamic verb-type cell spanning every dynamic row, and the stative trigger two columns wide", () => {
    const grid = layoutChart(chart, false);
    const vtypes = grid.flatMap((r) => r.cells.filter((c) => c.kind === "vtype"));
    const stativeRows = grid.filter((r) => r.group === "stative");
    expect(vtypes).toHaveLength(1);
    expect(vtypes[0]).toMatchObject({
      label: "Dynamic",
      rowspan: grid.length - stativeRows.length,
    });
    expect(stativeRows[0].cells[0]).toMatchObject({
      kind: "trigger",
      label: "Stative",
      colspan: 2,
    });
  });

  it("hides the archaic naga- chip until literary forms are shown", () => {
    const chipLabels = (lit: boolean) =>
      layoutChart(chart, lit)
        .find((r) => r.key === "agent-mag:chips")!
        .cells.flatMap((c) => (c.kind === "chips" ? c.affixes.map((a) => a.label) : []));
    expect(chipLabels(false)).not.toContain("naga-");
    expect(chipLabels(true)).toContain("naga-");
  });

  it("marks the potential side 'same as above' on the agent progressive", () => {
    const caption = layoutChart(chart, false).find((r) => r.key === "agent-mag:caption")!;
    expect(caption.cells.at(-1)).toEqual({ kind: "above", rowspan: 3 });
  });

  it("joins the stative's two progressive rows under one aspect label", () => {
    const grid = layoutChart(chart, false);
    const aspects = grid
      .filter((r) => r.group === "stative")
      .flatMap((r) => r.cells.filter((c) => c.kind === "aspect"));
    expect(aspects.map((c) => (c.kind === "aspect" ? `${c.label}:${c.rowspan}` : ""))).toEqual([
      "Perfective:3",
      "Progressive:6",
    ]);
  });
});

// Shapes the real chart does not have, for the branches it never reaches.
const bare = (over: Partial<VerbChartRow>): VerbChartRow => ({
  id: "x",
  aspect: "A",
  hint: null,
  same_aspect_as_above: false,
  literary: false,
  footnote: null,
  glosses: ["", "", "", "", "", "", ""],
  volitional: null,
  potential: null,
  ...over,
});
const tiny = (row: VerbChartRow, verbTypes = chart.verb_types, modes = chart.modes): VerbChart => ({
  ...chart,
  verb_types: verbTypes,
  modes,
  groups: [{ id: "g", label: "G", hint: "", verb_type: "dynamic", footnote: null, rows: [row] }],
});
const cell = (label: string) => ({
  affixes: [{ label, kind: "standard" as const }],
  same_as_left: false,
  examples: [],
});

describe("layoutChart edge shapes", () => {
  it("draws a side with no forms as one empty cell down the row", () => {
    const grid = layoutChart(tiny(bare({})), false);
    expect(grid).toHaveLength(2);
    expect(widths(grid)).toEqual([10, 10]);
    expect(grid[0].cells.filter((c) => c.kind === "none")).toEqual([
      { kind: "none", colspan: 4, rowspan: 2, pstart: false },
      { kind: "none", colspan: 3, rowspan: 2, pstart: true },
    ]);
  });

  it("draws a missing cell inside a side as a dash, with an empty example slot", () => {
    const row = bare({
      volitional: [cell("a-"), null, cell("b-"), cell("c-")],
      potential: [
        { ...cell("d-"), examples: [{ ceb: "x", en: "y", source: "W", source_spelling: "x" }] },
        null,
        null,
      ],
    });
    const grid = layoutChart(tiny(row), false);
    expect(rowHeight(row)).toBe(3);
    expect(widths(grid)).toEqual([10, 10, 10]);
    expect(grid[1].cells[1]).toEqual({ kind: "none", colspan: 1, rowspan: 1, pstart: false });
    expect(grid[2].cells[1]).toEqual({ kind: "examples", examples: [], pstart: false });
  });

  it("labels the verb type blank when the chart names none", () => {
    const grid = layoutChart(tiny(bare({}), []), false);
    expect(grid[0].cells[0]).toEqual({ kind: "vtype", label: "", hint: "", rowspan: 2 });
  });
});

describe("cardEntries (phone cards)", () => {
  const rows = new Map(chart.groups.flatMap((g) => g.rows).map((r) => [r.id, r]));

  it("resolves a shared affix from the left: agent perfective, volitional", () => {
    const entries = cardEntries(chart, rows.get("agent-mo")!, "volitional", false);
    expect(entries.map((e) => `${e.label}:${e.affixes.map((a) => a.label).join("|")}`)).toEqual([
      "Realis:mi-, ni-|ming-, ning-",
      "Irrealis:mo-",
      "Dependent:mo-",
      "Imperative:∅, pag-",
    ]);
  });

  it("gives nothing for a side that is 'above' or absent", () => {
    expect(cardEntries(chart, rows.get("agent-mag")!, "potential", false)).toEqual([]);
  });

  it("skips a missing cell, and a shared cell after it inherits nothing", () => {
    const row = bare({
      volitional: [cell("a-"), null, { affixes: [], same_as_left: true, examples: [] }, cell("c-")],
    });
    const entries = cardEntries(tiny(row), row, "volitional", false);
    expect(entries.map((e) => `${e.column}:${e.affixes.map((a) => a.label).join("|")}`)).toEqual([
      "realis:a-",
      "dependent:",
      "imperative:c-",
    ]);
  });

  it("falls back to the column index when the chart has no such mode", () => {
    const row = bare({ volitional: [cell("a-"), null, null, null] });
    const entries = cardEntries(tiny(row, chart.verb_types, []), row, "volitional", false);
    expect(entries).toEqual([
      { column: "0", label: "", hint: "", affixes: [cell("a-").affixes[0]], examples: [] },
    ]);
  });
});

describe("rowSummary and visibleAffixes", () => {
  it("lists a row's distinct standard affixes, no variants", () => {
    const row = chart.groups[0].rows[0];
    expect(rowSummary(row, false).map((a) => a.label)).toEqual([
      "mi-, ni-",
      "mo-",
      "∅, pag-",
      "naka-, ka-",
      "maka-, ka-",
    ]);
  });

  it("skips missing cells", () => {
    expect(
      rowSummary(bare({ volitional: [null, cell("a-"), cell("a-"), null] }), false).map(
        (a) => a.label,
      ),
    ).toEqual(["a-"]);
  });

  it("drops literary affixes unless shown", () => {
    const affixes = [
      { label: "a", kind: "standard" as const },
      { label: "b", kind: "literary" as const },
    ];
    expect(visibleAffixes(affixes, false).map((a) => a.label)).toEqual(["a"]);
    expect(visibleAffixes(affixes, true).map((a) => a.label)).toEqual(["a", "b"]);
  });
});
