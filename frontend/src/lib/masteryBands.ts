/**
 * Twin-rail mastery bands (bd tunatale-yh47): per-direction strength encoded as
 * a coloured, length-proportional fill on one of two thin rails under a word.
 * The band vocabulary is defined backend-side (`mastery.py::direction_band`);
 * this module only turns a band name into fill geometry and labels.
 */
import { t } from "./i18n/i18n.svelte";

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

export interface RailStyle {
  /** Fill colour, as a `var(--band-<band>)` reference; null when no fill. */
  fill: string | null;
  /** Fill width as a percentage string (e.g. `"60%"`); null when no fill. */
  pct: string | null;
  /** Whether the rail is "no card": its track paints dashed in muted ink. */
  dashed: boolean;
}

/**
 * A side nothing has been learned on yet. An ABSENT band, the band `"none"`
 * (no card for this direction) and `"new"` (a card that has never been
 * studied) are one state as far as the learner is concerned — whether a row
 * exists in the database is not something they can act on. The reader's blue
 * and the popover's wording both read this, so they cannot disagree.
 */
export function isUnstarted(band: string | null | undefined): boolean {
  return band == null || band === "none" || band === "new";
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
 * CSS custom properties the component `<style>` paint rule reads.
 *
 * An ABSENT band reads as `"none"` — no card on that side — so an untracked
 * word paints two dashed rails through this one function. It used to return
 * `null` there and leave untracked words to a second CSS rule
 * (`.paint-untracked`) that hard-coded the same two dashes, plus a third
 * "invisible track" case for an absent produce band that the API could not
 * actually produce: a resolved item always sets BOTH bands to a
 * `direction_band` string, and an unresolved one leaves both null.
 */
export function railPropsFor(bands: {
  understand_band?: string | null;
  produce_band?: string | null;
}): string {
  const u = railStyle(bands.understand_band ?? "none");
  const p = railStyle(bands.produce_band ?? "none");
  return (
    `--rail-u-fill: ${u.fill ?? "transparent"}; ` +
    `--rail-u-pct: ${u.pct ?? "0%"}; ` +
    `--rail-u-track: ${u.dashed ? RAIL_TRACK_DASHED : RAIL_TRACK_SOLID}; ` +
    `--rail-p-fill: ${p.fill ?? "transparent"}; ` +
    `--rail-p-pct: ${p.pct ?? "0%"}; ` +
    `--rail-p-track: ${p.dashed ? RAIL_TRACK_DASHED : RAIL_TRACK_SOLID};`
  );
}

/** Human-readable label for a band, for the readaloud/help surfaces.
 *
 * `none` and `new` deliberately share ONE label. They differ only in whether a
 * card row exists, which is invisible to the learner and not something they
 * act on; the rails still tell them apart (dashed track vs empty solid track).
 * Naming them differently — "No card" vs "Not started" — read as a meaningful
 * distinction that the interface then never cashed out. */
export function bandLabel(band: MasteryBand): string {
  switch (band) {
    case "none":
    case "new":
      return t("masteryBands.bandNew");
    case "learning":
      return t("masteryBands.bandLearning");
    case "days":
      return t("masteryBands.bandDays");
    case "weeks":
      return t("masteryBands.bandWeeks");
    case "months":
      return t("masteryBands.bandMonths");
    case "solid":
      return t("masteryBands.bandSolid");
    case "suspended":
      return t("masteryBands.bandSuspended");
  }
}

/**
 * How long a band's underlying card is expected to hold, as a label. The ruler
 * is the direction's FSRS stability in days. KNOWN cards (stability 10000+) are
 * "marked known" — the measurement is meaningless there, so the label says so.
 */
export function holdsLabel(stability: number | null): string | null {
  if (stability == null) return null;
  if (stability >= 10000) return t("masteryBands.markedKnown");
  if (stability < 1) return t("masteryBands.holdsLessThanDay");
  if (stability < 7) {
    const days = Math.round(stability);
    return t("masteryBands.holdsDays", { count: days });
  }
  if (stability < 60) {
    const weeks = Math.max(1, Math.round(stability / 7));
    return t("masteryBands.holdsWeeks", { count: weeks });
  }
  if (stability < 365) return t("masteryBands.holdsMonths", { count: Math.round(stability / 30) });
  const years = Math.round((stability / 365) * 10) / 10;
  return t("masteryBands.holdsYears", { count: years });
}

/** One side's label: the band name plus a stability qualifier when available. */
export function sideLabel(band: MasteryBand, stability: number | null): string {
  const bl = bandLabel(band);
  const hl = holdsLabel(stability);
  return hl != null ? `${bl} · ${hl}` : bl;
}

const BAND_SET = new Set<string>([
  "none",
  "new",
  "learning",
  "days",
  "weeks",
  "months",
  "solid",
  "suspended",
]);

/** Two-line label for the word popover / preview tooltip. */
export function masterySides(bands: {
  understand_band?: string | null;
  produce_band?: string | null;
  understand_stability?: number | null;
  produce_stability?: number | null;
}): readonly [string, string] | null {
  // An absent band means "no card on this side", the same as `"none"`. A word
  // with no card at all therefore reads both sides rather than the bare "not
  // tracked" label it used to get, which named no direction at all.
  const ub = bands.understand_band ?? "none";
  const pb = bands.produce_band ?? "none";
  if (!BAND_SET.has(ub) || !BAND_SET.has(pb)) return null;
  return [
    t("masteryBands.understand", {
      label: sideLabel(ub as MasteryBand, bands.understand_stability ?? null),
    }),
    t("masteryBands.produce", {
      label: sideLabel(pb as MasteryBand, bands.produce_stability ?? null),
    }),
  ];
}
