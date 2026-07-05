// "Auto-download lessons on wifi" preference. When enabled (the default), the
// audio player prefetches a lesson's audio into the service-worker cache while
// on wifi, so later plays are free and work offline (offline-audio Phase 4).
// When disabled, on-demand cache-first still applies — nothing is prefetched in
// the background, so the user only pays for audio they actually play.

const STORAGE_KEY = "prefetchOnWifi";

function createPrefetchPrefStore() {
  let enabled = $state(true);
  // Lazy self-init: consumers deeper in the tree (LessonPlayer) mount before
  // the layout's onMount calls init(), so the first `enabled` read must apply
  // a stored opt-out itself or a direct lesson-page load prefetches anyway.
  let initialized = false;

  function init(): void {
    initialized = true;
    if (typeof localStorage === "undefined") return;
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) {
      enabled = stored === "true";
    }
  }

  function set(next: boolean): void {
    initialized = true;
    enabled = next;
    localStorage.setItem(STORAGE_KEY, String(next));
  }

  function toggle(): void {
    set(!enabled);
  }

  return {
    get enabled(): boolean {
      if (!initialized) init();
      return enabled;
    },
    init,
    set,
    toggle,
  };
}

export const prefetchPrefStore = createPrefetchPrefStore();
