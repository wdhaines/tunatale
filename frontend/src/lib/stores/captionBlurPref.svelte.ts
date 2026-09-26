import { createLocalPref } from "./localPref.svelte";

// Blurring the caption behind the audio is ON by default: a learner hearing a
// phrase for the first time should read it, not be able to skip past it.
const pref = createLocalPref("captionBlur", {
  parse: (raw) => raw !== "off",
  serialize: (next) => (next ? "on" : "off"),
});

export const captionBlurPref = {
  get enabled(): boolean {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
};
