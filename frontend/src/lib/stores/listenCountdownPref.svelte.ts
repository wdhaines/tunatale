const STORAGE_KEY = "listenCountdown";

type CountdownValue = "off" | "10" | "30" | "60";

const VALID = new Set<string>(["off", "10", "30", "60"]);

function createListenCountdownPref() {
  let value = $state<CountdownValue>("off");

  function init(): void {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null && VALID.has(stored)) {
      value = stored as CountdownValue;
    } else {
      value = "off";
    }
  }

  function set(next: CountdownValue): void {
    value = next;
    localStorage.setItem(STORAGE_KEY, next);
  }

  return {
    get value(): CountdownValue {
      return value;
    },
    init,
    set,
  };
}

export const listenCountdownPref = createListenCountdownPref();
export type { CountdownValue };
