import { createLocalPref } from "./localPref.svelte";

// Hands-free mode, persisted so it survives the navigation BETWEEN lessons.
//
// It lives here rather than on the playback controller because the controller
// is per-lesson: the page recreates LessonPlayer via {#key audio.audio_id}, so
// a controller-only flag is destroyed by the very navigation hands-free now
// performs at the end of its pass sequence. The controller still owns the
// runtime flag (setHandsFree drives the advance); this store is what seeds it
// on the far side of a page change.
//
// Two facts, deliberately kept apart:
//   enabled  — the user's setting, localStorage, survives a restart like every
//              other player preference. createLocalPref owns it, including the
//              blocked-storage and no-storage cases.
//   handoff  — a ONE-SHOT baton meaning "this navigation was performed by
//              hands-free, so start playing on arrival". sessionStorage, and
//              consumed on read. Without the distinction, merely opening a
//              lesson with hands-free left on would start playing at you.

const HANDOFF_KEY = "handsFreeHandoff";

const pref = createLocalPref("handsFree", {
  parse: (raw) => raw === "on",
  serialize: (next) => (next ? "on" : "off"),
});

export const handsFreePref = {
  get enabled(): boolean {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,

  // Arm the baton immediately before a hands-free navigation.
  armHandoff(): void {
    try {
      sessionStorage.setItem(HANDOFF_KEY, "1");
    } catch {
      // Unarmed means the next page loads paused — the degraded case, not a
      // broken one.
    }
  },

  // Read-and-clear. One-shot by construction: a reload of the arrival page must
  // not start playing a second time.
  consumeHandoff(): boolean {
    try {
      const armed = sessionStorage.getItem(HANDOFF_KEY) === "1";
      sessionStorage.removeItem(HANDOFF_KEY);
      return armed;
    } catch {
      return false;
    }
  },
};
