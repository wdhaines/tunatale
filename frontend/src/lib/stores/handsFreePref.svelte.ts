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

// "repeat" is hands-free ON that loops back to this lesson's key phrases
// instead of moving to the next lesson (the user's call, 2026-09-29: a third
// state of the one chip, saved like the other two). The stored strings are the
// modes themselves, so a pre-existing "on" / "off" reads unchanged.
export type HandsFreeMode = "off" | "on" | "repeat";

const pref = createLocalPref<HandsFreeMode>("handsFree", {
  parse: (raw) => (raw === "on" || raw === "repeat" ? raw : "off"),
  serialize: (next) => next,
});

export const handsFreePref = {
  get mode(): HandsFreeMode {
    return pref.value;
  },
  get enabled(): boolean {
    return pref.value !== "off";
  },
  get repeat(): boolean {
    return pref.value === "repeat";
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
