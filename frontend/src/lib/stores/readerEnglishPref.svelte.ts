// The reader's English translation preference (bd tunatale-685k). Where there
// used to be two independent disclosure toggles (gloss + interlinear), the user
// chose a single value that cycles: Off → Idiomatic → Literal → Both → Off.
// Idiomatic is the line translation (interlinear), Literal is the per-word gloss.
import { createLocalPref } from "./localPref.svelte";

export type ReaderEnglish = "off" | "idiomatic" | "literal" | "both";

const ORDER: ReaderEnglish[] = ["off", "idiomatic", "literal", "both"];

const pref = createLocalPref<ReaderEnglish>("readerEnglish", {
  parse: (raw: string | null): ReaderEnglish => {
    if (raw === "idiomatic" || raw === "literal" || raw === "both") {
      return raw;
    }
    return "off";
  },
  serialize: (next: ReaderEnglish) => next,
});

export const readerEnglishPref = {
  get value(): ReaderEnglish {
    return pref.value;
  },
  get showIdiomatic(): boolean {
    return pref.value === "idiomatic" || pref.value === "both";
  },
  get showLiteral(): boolean {
    return pref.value === "literal" || pref.value === "both";
  },
  init: pref.init,
  set(next: ReaderEnglish): void {
    pref.set(next);
  },
  next(): void {
    const i = ORDER.indexOf(pref.value);
    const next = ORDER[(i + 1) % ORDER.length];
    pref.set(next);
  },
};
