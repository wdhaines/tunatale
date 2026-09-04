const STORAGE_KEY = "voice";

// Voice capture is opt-in (the spike's false-positive numbers made that
// deliberate). The non-default state is ON, so `class:active` marks enabled.
// An empty localStorage AND an unrecognised stored value both yield false.
function createVoicePref() {
  let enabled = $state(false);

  function init(): void {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "on") {
      enabled = true;
    } else {
      enabled = false;
    }
  }

  function set(next: boolean): void {
    enabled = next;
    localStorage.setItem(STORAGE_KEY, next ? "on" : "off");
  }

  return {
    get enabled(): boolean {
      return enabled;
    },
    init,
    set,
  };
}

export const voicePref = createVoicePref();
