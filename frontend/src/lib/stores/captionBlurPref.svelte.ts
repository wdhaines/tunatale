const STORAGE_KEY = "captionBlur";

function createCaptionBlurPref() {
  let enabled = $state(true);

  function init(): void {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "off") {
      enabled = false;
    } else if (stored === "on") {
      enabled = true;
    } else {
      enabled = true;
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

export const captionBlurPref = createCaptionBlurPref();
