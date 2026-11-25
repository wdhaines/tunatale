import { createLocalPref } from "./localPref.svelte";
import type { LessonMode } from "./lessonModePref.svelte";

// Which side of a LESSON opens (tunatale-cs3p slice 3, the owner's decision 7 of
// 2026-11-13): Read on the Outline tab the first time a lesson is opened, and
// afterwards the side, and the Read tab, last used on that lesson. Per device.
//
// The review-session reader keeps the global, viewport-defaulted lessonModePref:
// the decision was about lessons, and a session has no outline to open on.
//
// Keyed by lesson id and bounded: a long-used device must not grow this forever,
// so the least recently set lessons are forgotten first (they open on Outline
// again, the same as a lesson never opened).

export interface LessonSide {
  mode: LessonMode;
  /** The Read tab: "outline", "key_phrases", "dialogue", "drill", "drill-<n>". */
  tab: string;
}

export const FIRST_OPEN: LessonSide = { mode: "read", tab: "outline" };
export const SIDE_LIMIT = 200;

type Sides = Record<string, LessonSide>;

/** The stored map, trusting only well-formed entries: garbage reads as never opened. */
export function parseSides(raw: string | null): Sides {
  let data: unknown;
  try {
    data = JSON.parse(raw ?? "{}");
  } catch {
    return {};
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) return {};
  const out: Sides = {};
  for (const [id, side] of Object.entries(data as Record<string, unknown>)) {
    if (side === null || typeof side !== "object") continue;
    const { mode, tab } = side as Record<string, unknown>;
    if ((mode === "read" || mode === "listen") && typeof tab === "string") out[id] = { mode, tab };
  }
  return out;
}

const pref = createLocalPref<Sides>("lessonSide", {
  parse: parseSides,
  serialize: (next) => JSON.stringify(next),
});

export const lessonSidePref = {
  get(lessonId: string): LessonSide {
    return pref.value[lessonId] ?? FIRST_OPEN;
  },
  set(lessonId: string, side: LessonSide): void {
    // Re-inserted last, so key order is "least recently set first".
    const { [lessonId]: _old, ...rest } = pref.value;
    const ids = Object.keys(rest);
    const kept = ids.slice(Math.max(0, ids.length - (SIDE_LIMIT - 1)));
    pref.set({ ...Object.fromEntries(kept.map((id) => [id, rest[id]])), [lessonId]: side });
  },
  /** Re-read storage, as a fresh page load would. For tests. */
  reset(): void {
    pref.init();
  },
};
