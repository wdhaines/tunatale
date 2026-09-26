import { createLocalPref } from "./localPref.svelte";

// "Auto-download lessons on wifi" preference. When enabled (the default), the
// audio player prefetches a lesson's audio into the service-worker cache while
// on wifi, so later plays are free and work offline (offline-audio Phase 4).
// When disabled, on-demand cache-first still applies — nothing is prefetched in
// the background, so the user only pays for audio they actually play.

const pref = createLocalPref("prefetchOnWifi", {
  parse: (raw) => (raw === null ? true : raw === "true"),
  serialize: (next) => String(next),
});

export const prefetchPrefStore = {
  get enabled(): boolean {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
  toggle(): void {
    pref.set(!pref.value);
  },
};
