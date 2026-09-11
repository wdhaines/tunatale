/**
 * On-device log of Media Session button events, read on the phone afterwards.
 *
 * WHY. Which Media Session actions actually arrive from a headset, a car, or a
 * phone varies by hardware, OS and browser, and nobody had recorded it. The
 * server-side client log is not enough: it POSTs over the network and swallows
 * failures, so a phone out of reach of the server records nothing — and that
 * silence looks exactly like "the car sent no buttons". The log therefore
 * persists in localStorage ON THE DEVICE, and the /settings viewer reads it
 * after the drive.
 *
 * OFF unless switched on. The same URL switch in the root layout that turns the
 * client log on (`?mediatrace=on`) is the only affordance a real phone has, and
 * it persists across navigations.
 *
 * Every failure here is swallowed on purpose: instrumentation that can break
 * the page it is instrumenting is worse than none.
 */
import { clientLog } from "./clientLog";

const ENABLED_KEY = "mediaTrace"; // localStorage: "on" | "off"
const BUFFER_KEY = "mediaTraceLog"; // localStorage: JSON string[]
const MAX_ENTRIES = 500;

export function mediaTraceEnabled(): boolean {
  try {
    return localStorage.getItem(ENABLED_KEY) === "on";
  } catch {
    // Private mode, or storage disabled. Not a reason to throw at a caller who
    // only wanted to check a flag.
    return false;
  }
}

export function setMediaTraceEnabled(next: boolean): void {
  try {
    localStorage.setItem(ENABLED_KEY, next ? "on" : "off");
  } catch {
    /* nothing sensible to do, and nothing worth breaking the page for */
  }
}

export function mediaTrace(line: string): void {
  // Does nothing AT ALL when off: no storage read, no clientLog call.
  if (!mediaTraceEnabled()) return;
  // A corrupt buffer (not JSON, or not a JSON array) is treated as empty and
  // overwritten by the write below — one bad value must not kill the trace.
  let entries: string[] = [];
  try {
    const raw = localStorage.getItem(BUFFER_KEY);
    const parsed = raw === null ? [] : JSON.parse(raw);
    if (Array.isArray(parsed)) {
      entries = parsed.filter((e): e is string => typeof e === "string");
    }
  } catch {
    /* treated as empty */
  }
  entries.push(`${new Date().toISOString()} ${line}`);
  // Bounded on-device: a long headset session must not grow without limit.
  if (entries.length > MAX_ENTRIES) {
    entries.splice(0, entries.length - MAX_ENTRIES);
  }
  try {
    localStorage.setItem(BUFFER_KEY, JSON.stringify(entries));
  } catch {
    /* a throwing localStorage is swallowed — the button still worked */
  }
  // The server copy is a bonus; clientLog is itself a no-op unless its own
  // flag is on, which is intended.
  clientLog(`media ${line}`);
}

export function readMediaTrace(): string[] {
  try {
    const raw = localStorage.getItem(BUFFER_KEY);
    if (raw === null) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((e): e is string => typeof e === "string");
  } catch {
    return [];
  }
}

export function clearMediaTrace(): void {
  try {
    localStorage.removeItem(BUFFER_KEY);
  } catch {
    /* nothing worth breaking the page for */
  }
}
