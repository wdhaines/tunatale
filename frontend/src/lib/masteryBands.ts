/**
 * Twin-rail mastery bands (bd tunatale-yh47): per-direction strength encoded as
 * a coloured, length-proportional fill on one of two thin rails under a word.
 * The band vocabulary is defined backend-side (`mastery.py::direction_band`);
 * this module only turns a band name into fill geometry and labels.
 */

export type MasteryBand =
  | "none"
  | "new"
  | "learning"
  | "days"
  | "weeks"
  | "months"
  | "solid"
  | "suspended";

/** Fill length, as a percentage of the rail, per strength band. */
export const RAIL_FILL_PCT: Record<"learning" | "days" | "weeks" | "months" | "solid", number> = {
  learning: 20,
  days: 40,
  weeks: 60,
  months: 80,
  solid: 100,
};

/** Solid track: the empty rail behind any fill, in the neutral track ink. */
export const RAIL_TRACK_SOLID =
  "linear-gradient(var(--band-track, #e4e9e6), var(--band-track, #e4e9e6))";

/** Dashed track: the "no card" rail, drawn as thin dashes in the muted ink. */
export const RAIL_TRACK_DASHED =
  "repeating-linear-gradient(90deg, var(--color-muted, #6b7280) 0 3px, transparent 3px 6px)";

/** Invisible track: a band that is absent (null) paints no rail at all. */
const RAIL_TRACK_NONE = "linear-gradient(transparent, transparent)";

export interface RailStyle {
  /** Fill colour, as a `var(--band-<band>)` reference; null when no fill. */
  fill: string | null;
  /** Fill width as a percentage string (e.g. `"60%"`); null when no fill. */
  pct: string | null;
  /** Whether the rail is "no card": its track paints dashed in muted ink. */
  dashed: boolean;
}

/**
 * Decode a band into the per-rail values the paint rules need. The strength
 * bands fill `var(--band-<band>)` across their share of the rail;
 * `new`/`suspended` draw an empty track (no fill); `none` draws an empty
 * DASHED track; null/undefined/unknown draw nothing (the consumer then paints
 * no rail layer at all).
 */
export function railStyle(band: string | null | undefined): RailStyle {
  switch (band) {
    case "learning":
    case "days":
    case "weeks":
    case "months":
    case "solid":
      return {
        fill: `var(--band-${band})`,
        pct: `${RAIL_FILL_PCT[band]}%`,
        dashed: false,
      };
    case "new":
    case "suspended":
      return { fill: null, pct: null, dashed: false };
    case "none":
      return { fill: null, pct: null, dashed: true };
    default:
      return { fill: null, pct: null, dashed: false };
  }
}

/**
 * The two bands a word (or a collocation span) carries, turned into the inline
 * CSS custom properties the component `<style>` paint rule reads. `null` when
 * the word is untracked (no understood rail) — the caller then paints nothing.
 * A missing produce band paints an invisible track, going all the way down to
 * no produce rail.
 */
export function railPropsFor(bands: {
  understand_band?: string | null;
  produce_band?: string | null;
}): string | null {
  if (bands.understand_band == null) return null;
  const u = railStyle(bands.understand_band);
  const p = railStyle(bands.produce_band ?? null);
  const produceTrack =
    bands.produce_band == null ? RAIL_TRACK_NONE : p.dashed ? RAIL_TRACK_DASHED : RAIL_TRACK_SOLID;
  return (
    `--rail-u-fill: ${u.fill ?? "transparent"}; ` +
    `--rail-u-pct: ${u.pct ?? "0%"}; ` +
    `--rail-u-track: ${u.dashed ? RAIL_TRACK_DASHED : RAIL_TRACK_SOLID}; ` +
    `--rail-p-fill: ${p.fill ?? "transparent"}; ` +
    `--rail-p-pct: ${p.pct ?? "0%"}; ` +
    `--rail-p-track: ${produceTrack};`
  );
}

/** Human-readable label for a band, for the readaloud/help surfaces. */
export function bandLabel(band: MasteryBand): string {
  switch (band) {
    case "none":
      return "No card";
    case "new":
      return "Not started";
    case "learning":
      return "Learning";
    case "days":
      return "Days";
    case "weeks":
      return "Weeks";
    case "months":
      return "Months";
    case "solid":
      return "Half a year +";
    case "suspended":
      return "Suspended";
  }
}

/**
 * How long a band's underlying card is expected to hold, as a label. The ruler
 * is the direction's FSRS stability in days. KNOWN cards (stability 10000+) are
 * "marked known" — the measurement is meaningless there, so the label says so.
 */
export function holdsLabel(stability: number | null): string | null {
  if (stability == null) return null;
  if (stability >= 10000) return "marked known";
  if (stability < 1) return "holds < 1 day";
  if (stability < 7) {
    const days = Math.round(stability);
    return days === 1 ? "holds ~1 day" : `holds ~${days} days`;
  }
  if (stability < 60) {
    const weeks = Math.max(1, Math.round(stability / 7));
    return weeks === 1 ? "holds ~1 week" : `holds ~${weeks} weeks`;
  }
  if (stability < 365) return `holds ~${Math.round(stability / 30)} months`;
  const years = Math.round((stability / 365) * 10) / 10;
  return `holds ~${years} years`;
}
