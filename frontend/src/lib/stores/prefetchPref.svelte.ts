// "Auto-download lessons on wifi" preference. When enabled (the default), the
// audio player prefetches a lesson's audio into the service-worker cache while
// on wifi, so later plays are free and work offline (offline-audio Phase 4).
// When disabled, on-demand cache-first still applies — nothing is prefetched in
// the background, so the user only pays for audio they actually play.

const STORAGE_KEY = "prefetchOnWifi";

function createPrefetchPrefStore() {
  let enabled = $state(true);

  function init(): void {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) {
      enabled = stored === "true";
    }
  }

  function set(next: boolean): void {
    enabled = next;
    localStorage.setItem(STORAGE_KEY, String(next));
  }

  function toggle(): void {
    set(!enabled);
  }

  return {
    get enabled(): boolean {
      return enabled;
    },
    init,
    set,
    toggle,
  };
}

export const prefetchPrefStore = createPrefetchPrefStore();
