import { createLocalPref } from "./localPref.svelte";

// Voice capture is opt-in (the spike's false-positive numbers made that
// deliberate). The non-default state is ON, so `class:active` marks enabled.
// An empty localStorage AND an unrecognised stored value both yield false.
const pref = createLocalPref("voice", {
  parse: (raw) => raw === "on",
  serialize: (next) => (next ? "on" : "off"),
});

export const voicePref = {
  get enabled(): boolean {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
};
