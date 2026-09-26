import { createLocalPref } from "./localPref.svelte";

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
// Expanded is the default: a first-time reader should see the full control set
// and discover the collapse, not meet a stripped player and wonder where the
// controls went.

const pref = createLocalPref("playerCollapsed", {
  parse: (raw) => raw === "on",
  serialize: (next) => (next ? "on" : "off"),
});

export const playerCollapsedPref = {
  get collapsed(): boolean {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
};
