/**
 * The verb chart's desktop grid (tunatale-h0q3.47), computed from the chart data so the
 * Svelte table only loops. Ten columns: verb type, trigger, aspect, four volitional
 * (realis, irrealis, dependent, imperative) and three potential.
 *
 * Every chart row draws as up to three table rows: a caption line (seven English
 * glosses), the affix chips, then the examples (left out when neither side has one).
 * A cell marked `same_as_left` shares the chip of the cell on its left, which then
 * spans it; examples never span. Literary rows are LEFT OUT unless shown, never hidden:
 * a hidden row inside a rowspan throws the grid off.
 */
import type {
  VerbChart,
  VerbChartAffix,
  VerbChartCell,
  VerbChartExample,
  VerbChartRow,
} from "#lib/api.js";

export type GridCell =
  | { kind: "vtype"; label: string; hint: string; rowspan: number }
  | {
      kind: "trigger";
      group: string;
      label: string;
      hint: string;
      footnote: number | null;
      rowspan: number;
      colspan: number;
    }
  | { kind: "aspect"; label: string; hint: string | null; footnote: number | null; rowspan: number }
  | { kind: "gloss"; text: string; pstart: boolean }
  | { kind: "chips"; affixes: VerbChartAffix[]; colspan: number; pstart: boolean }
  | { kind: "examples"; examples: VerbChartExample[]; pstart: boolean }
  | { kind: "none"; colspan: number; rowspan: number; pstart: boolean }
  | { kind: "above"; rowspan: number };

export interface GridRow {
  key: string;
  /** The chart row this table row draws, for highlighting the row a link opened. */
  rowId: string;
  group: string;
  line: "caption" | "chips" | "examples";
  /** First table row of its trigger group (draws the group's top rule). */
  groupStart: boolean;
  cells: GridCell[];
}

const SIDE_WIDTH = { volitional: 4, potential: 3 } as const;

/** Affix chips shown in a cell: a literary or archaic one only when asked for. */
export function visibleAffixes(affixes: VerbChartAffix[], showLiterary: boolean): VerbChartAffix[] {
  return affixes.filter((a) => showLiterary || a.kind !== "literary");
}

/** The rows a group shows: literary rows only when asked for. */
export function visibleRows(rows: VerbChartRow[], showLiterary: boolean): VerbChartRow[] {
  return rows.filter((r) => showLiterary || !r.literary);
}

function sides(row: VerbChartRow): Array<VerbChartCell | null> {
  const cells: Array<VerbChartCell | null> = [...(row.volitional ?? [])];
  if (Array.isArray(row.potential)) cells.push(...row.potential);
  return cells;
}

function hasExamples(row: VerbChartRow): boolean {
  return sides(row).some((c) => c !== null && c.examples.length > 0);
}

/** How many table rows a chart row takes: caption, chips, and examples if any. */
export function rowHeight(row: VerbChartRow): number {
  return hasExamples(row) ? 3 : 2;
}

/** The chip cells of one side: a `same_as_left` cell widens its left neighbour. */
function chipCells(
  cells: Array<VerbChartCell | null>,
  potential: boolean,
  showLiterary: boolean,
): GridCell[] {
  const out: GridCell[] = [];
  cells.forEach((cell, i) => {
    const pstart = potential && i === 0;
    if (cell === null) {
      out.push({ kind: "none", colspan: 1, rowspan: 1, pstart });
      return;
    }
    if (cell.same_as_left) return;
    let colspan = 1;
    while (cells[i + colspan]?.same_as_left) colspan++;
    out.push({
      kind: "chips",
      affixes: visibleAffixes(cell.affixes, showLiterary),
      colspan,
      pstart,
    });
  });
  return out;
}

function exampleCells(cells: Array<VerbChartCell | null>, potential: boolean): GridCell[] {
  return cells.map((cell, i) => ({
    kind: "examples",
    examples: cell?.examples ?? [],
    pstart: potential && i === 0,
  }));
}

/** The whole grid. Each returned row's cells, plus the rowspans reaching into it, cover ten columns. */
export function layoutChart(chart: VerbChart, showLiterary: boolean): GridRow[] {
  const verbTypes = new Map(chart.verb_types.map((v) => [v.id, v]));
  const groups = chart.groups.map((g) => ({ ...g, rows: visibleRows(g.rows, showLiterary) }));
  const span = (rows: VerbChartRow[]) => rows.reduce((n, r) => n + rowHeight(r), 0);
  const dynamicSpan = groups
    .filter((g) => g.verb_type === "dynamic")
    .reduce((n, g) => n + span(g.rows), 0);
  const out: GridRow[] = [];
  let vtypeDrawn = false;

  for (const group of groups) {
    const stative = group.verb_type === "stative";
    group.rows.forEach((row, ri) => {
      const height = rowHeight(row);
      const lead: GridCell[] = [];
      if (!stative && !vtypeDrawn) {
        const vt = verbTypes.get("dynamic");
        lead.push({
          kind: "vtype",
          label: vt?.label ?? "",
          hint: vt?.hint ?? "",
          rowspan: dynamicSpan,
        });
        vtypeDrawn = true;
      }
      if (ri === 0) {
        lead.push({
          kind: "trigger",
          group: group.id,
          label: group.label,
          hint: group.hint,
          footnote: group.footnote,
          rowspan: span(group.rows),
          colspan: stative ? 2 : 1,
        });
      }
      if (!row.same_aspect_as_above) {
        let aspectSpan = height;
        for (let k = ri + 1; k < group.rows.length && group.rows[k].same_aspect_as_above; k++) {
          aspectSpan += rowHeight(group.rows[k]);
        }
        lead.push({
          kind: "aspect",
          label: row.aspect,
          hint: row.hint,
          footnote: row.footnote,
          rowspan: aspectSpan,
        });
      }

      // A side with no cells of its own is one cell down the whole chart row.
      const whole = (side: "volitional" | "potential"): GridCell | null => {
        const v = row[side];
        if (v === "above") return { kind: "above", rowspan: height };
        if (v === null)
          return {
            kind: "none",
            colspan: SIDE_WIDTH[side],
            rowspan: height,
            pstart: side === "potential",
          };
        return null;
      };
      const volWhole = whole("volitional");
      const potWhole = whole("potential");
      const vol = Array.isArray(row.volitional) ? row.volitional : [];
      const pot = Array.isArray(row.potential) ? row.potential : [];

      const caption: GridCell[] = [...lead];
      if (volWhole) caption.push(volWhole);
      else
        caption.push(
          ...row.glosses
            .slice(0, 4)
            .map((text) => ({ kind: "gloss" as const, text, pstart: false })),
        );
      if (potWhole) caption.push(potWhole);
      else
        caption.push(
          ...row.glosses
            .slice(4)
            .map((text, i) => ({ kind: "gloss" as const, text, pstart: i === 0 })),
        );

      const chips: GridCell[] = [];
      if (!volWhole) chips.push(...chipCells(vol, false, showLiterary));
      if (!potWhole) chips.push(...chipCells(pot, true, showLiterary));

      const base = { rowId: row.id, group: group.id };
      out.push({
        ...base,
        key: `${row.id}:caption`,
        line: "caption",
        groupStart: ri === 0,
        cells: caption,
      });
      out.push({ ...base, key: `${row.id}:chips`, line: "chips", groupStart: false, cells: chips });
      if (height === 3) {
        const examples: GridCell[] = [];
        if (!volWhole) examples.push(...exampleCells(vol, false));
        if (!potWhole) examples.push(...exampleCells(pot, true));
        out.push({
          ...base,
          key: `${row.id}:examples`,
          line: "examples",
          groupStart: false,
          cells: examples,
        });
      }
    });
  }
  return out;
}

/** One phone-card entry per column: the affix in force there (a shared one resolved from the left). */
export interface CardEntry {
  column: string;
  label: string;
  hint: string;
  affixes: VerbChartAffix[];
  examples: VerbChartExample[];
}

export function cardEntries(
  chart: VerbChart,
  row: VerbChartRow,
  side: "volitional" | "potential",
  showLiterary: boolean,
): CardEntry[] {
  const cells = row[side];
  if (!Array.isArray(cells)) return [];
  const mode = chart.modes.find((m) => m.id === side);
  const out: CardEntry[] = [];
  let inForce: VerbChartAffix[] = [];
  cells.forEach((cell, i) => {
    const col = mode?.columns[i];
    if (cell === null) {
      inForce = [];
      return;
    }
    if (!cell.same_as_left) inForce = cell.affixes;
    out.push({
      column: col?.id ?? String(i),
      label: col?.label ?? "",
      hint: col?.hint ?? "",
      affixes: visibleAffixes(inForce, showLiterary),
      examples: cell.examples,
    });
  });
  return out;
}

/** The distinct affix labels of a row, volitional then potential: a card's summary line. */
export function rowSummary(row: VerbChartRow, showLiterary: boolean): VerbChartAffix[] {
  const seen = new Set<string>();
  const out: VerbChartAffix[] = [];
  for (const cell of sides(row)) {
    for (const a of cell === null ? [] : visibleAffixes(cell.affixes, showLiterary)) {
      if (a.kind === "variant" || seen.has(a.label)) continue;
      seen.add(a.label);
      out.push(a);
    }
  }
  return out;
}
