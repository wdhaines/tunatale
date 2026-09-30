import { createLocalPref } from "./localPref.svelte";

// "Practise production in the reader" (bd tunatale-dvdm.3): a word whose
// PRODUCTION direction is a due review renders blurred, to be recalled before
// it is read. OFF by default, the user's call — with it off the reader is
// exactly the recognition-only reader it has always been.
const pref = createLocalPref("readerProduction", {
  parse: (raw) => raw === "on",
  serialize: (next) => (next ? "on" : "off"),
});

export const readerProductionPref = {
  get enabled(): boolean {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
};
