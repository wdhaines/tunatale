// Whether the Read-mode player is collapsed to its minimum, persisted.
//
// On Read the transcript is the content and the sticky player card sits above
// it, so the card's height is taken directly out of what the reader can see.
// Collapsing gives that space back WITHOUT giving up playback: the transport
// and the scrubber stay, only the rows you set once and stop touching go away
// (phase, sentence nav, the setting chips).
//
// Read-mode only, deliberately. In Listen the player IS the content, so there
// is nothing to get out of the way of — LessonPlayer gates both the toggle and
// the effect on `compact`, and this store holds the preference either way.
//
// Same shape as voicePref / captionBlurPref: the default lives here, init()
// seeds from storage on mount (browser-only), set() writes the override.

const STORAGE_KEY = "playerCollapsed";

function createPlayerCollapsedPref() {
  // Expanded by default: a first-time reader should see the full control set
  // and discover the collapse, not meet a stripped player and wonder where the
  // controls went.
  let collapsed = $state(false);

  function init(): void {
    try {
      collapsed = localStorage.getItem(STORAGE_KEY) === "on";
    } catch {
      // Private mode / blocked site data: degrade to expanded rather than
      // breaking the player this is attached to.
      collapsed = false;
    }
  }

  function set(next: boolean): void {
    collapsed = next;
    try {
      localStorage.setItem(STORAGE_KEY, next ? "on" : "off");
    } catch {
      // An unwritable store must not break the toggle.
    }
  }

  return {
    get collapsed(): boolean {
      return collapsed;
    },
    init,
    set,
  };
}

export const playerCollapsedPref = createPlayerCollapsedPref();
