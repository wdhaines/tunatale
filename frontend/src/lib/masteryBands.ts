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

export interface RailStyle {
  /** Inline CSS for the fill element; null when the rail is an empty track. */
  fillStyle: string | null;
  /** Whether the whole rail renders dashed (the "no card" band). */
  dashed: boolean;
}

/**
 * Decode a band into rail geometry. The strength bands fill `var(--band-<band>)`
 * across their share of the rail; `new`/`suspended` draw an empty track (no
 * fill); `none` draws an empty DASHED track; null/undefined/unknown draw
 * nothing (the consumer then renders no rail element at all).
 */
export function railStyle(band: string | null | undefined): RailStyle {
  switch (band) {
    case "learning":
    case "days":
    case "weeks":
    case "months":
    case "solid":
      return {
        fillStyle: `width: ${RAIL_FILL_PCT[band]}%; background-color: var(--band-${band});`,
        dashed: false,
      };
    case "new":
    case "suspended":
      return { fillStyle: null, dashed: false };
    case "none":
      return { fillStyle: null, dashed: true };
    default:
      return { fillStyle: null, dashed: false };
  }
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
