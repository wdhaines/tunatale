import { createLocalPref } from "./localPref.svelte";

const COUNTDOWN_VALUES = ["off", "10", "30", "60"] as const;

type CountdownValue = (typeof COUNTDOWN_VALUES)[number];

// find, not a Set plus a cast: an unrecognised stored string (or no string at
// all) resolves to the default, and the narrowing is the value's own type.
function parseCountdown(raw: string | null): CountdownValue {
  return COUNTDOWN_VALUES.find((v) => v === raw) ?? "off";
}

const pref = createLocalPref<CountdownValue>("listenCountdown", {
  parse: parseCountdown,
  serialize: (next) => next,
});

export const listenCountdownPref = {
  get value(): CountdownValue {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
};

export type { CountdownValue };
