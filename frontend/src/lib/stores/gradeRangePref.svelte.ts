import { parseRange, serializeRange, type GradeRange } from "#lib/grading/grade.js";
import { createLocalPref } from "./localPref.svelte";

// The Grade page's due range (tunatale-5i1p.3): three days until the viewer
// changes it, then remembered (owner, 2026-11-11). Null = every gradable row.
const pref = createLocalPref<GradeRange>("gradeRange", {
  parse: parseRange,
  serialize: serializeRange,
});

export const gradeRangePref = {
  get value(): GradeRange {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
};
