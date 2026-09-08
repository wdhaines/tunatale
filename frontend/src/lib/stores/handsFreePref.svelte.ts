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
//              other player preference.
//   handoff  — a ONE-SHOT baton meaning "this navigation was performed by
//              hands-free, so start playing on arrival". sessionStorage, and
//              consumed on read. Without the distinction, merely opening a
//              lesson with hands-free left on would start playing at you.

const ENABLED_KEY = "handsFree";
const HANDOFF_KEY = "handsFreeHandoff";

function createHandsFreePref() {
  let enabled = $state(false);

  // Always establishes a clean state — a stored "on", else off — so an empty
  // storage also resets in-memory carryover between test cases and re-mounts.
  function init(): void {
    try {
      enabled = localStorage.getItem(ENABLED_KEY) === "on";
    } catch {
      // Private mode / blocked site data: the setting degrades to off rather
      // than breaking the player it is attached to.
      enabled = false;
    }
  }

  function set(next: boolean): void {
    enabled = next;
    try {
      localStorage.setItem(ENABLED_KEY, next ? "on" : "off");
    } catch {
      // Same as init: an unwritable store must not break the toggle.
    }
  }

  // Arm the baton immediately before a hands-free navigation.
  function armHandoff(): void {
    try {
      sessionStorage.setItem(HANDOFF_KEY, "1");
    } catch {
      // Unarmed means the next page loads paused — the degraded case, not a
      // broken one.
    }
  }

  // Read-and-clear. One-shot by construction: a reload of the arrival page must
  // not start playing a second time.
  function consumeHandoff(): boolean {
    try {
      const armed = sessionStorage.getItem(HANDOFF_KEY) === "1";
      sessionStorage.removeItem(HANDOFF_KEY);
      return armed;
    } catch {
      return false;
    }
  }

  return {
    get enabled(): boolean {
      return enabled;
    },
    init,
    set,
    armHandoff,
    consumeHandoff,
  };
}

export const handsFreePref = createHandsFreePref();
