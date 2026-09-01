/**
 * Durable browser-side logging — the frontend's half of ~/.tunatale/logs.
 *
 * WHY. Two backend counters were fixed on 2026-08-31 whose only fault was
 * reaching a logger nobody reads. The browser has the same defect and had no
 * fix at all: a device console dies with the tab, and the machine that could
 * read it is not the machine running the app. That made a real bug
 * undiagnosable — a gloss that will not reveal on Android Brave reproduces on no
 * emulator, because Playwright's tap() dispatches at a geometric centre with no
 * fuzzy tap-targeting.
 *
 * OFF unless switched on. This posts to a write endpoint, and an always-on
 * tracer would be both a privacy surface and a stream of noise.
 *
 * Every failure here is swallowed on purpose: instrumentation that can break the
 * page it is instrumenting is worse than none.
 */
import { BASE_URL } from "./api";

const STORAGE_KEY = "clientLog";

/** Matches the server's own cap, so a full buffer is one accepted request. */
const MAX_BUFFER = 50;
/** Batch window. Long enough to coalesce a whole tap sequence into one POST. */
const FLUSH_MS = 1000;

let buffer: string[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

export function clientLogEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "on";
  } catch {
    // Private mode, or storage disabled. Not a reason to throw at a caller who
    // only wanted to write a log line.
    return false;
  }
}

export function setClientLogEnabled(next: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, next ? "on" : "off");
  } catch {
    /* nothing sensible to do, and nothing worth breaking the page for */
  }
}

export async function flushClientLog(): Promise<void> {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  if (buffer.length === 0) return;
  const lines = buffer;
  buffer = [];
  try {
    await fetch(`${BASE_URL}/api/client-log`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lines }),
      // The interesting lines are often the last ones before a navigation.
      keepalive: true,
    });
  } catch {
    /* offline, 404 because the server flag is off, auth expired — all fine */
  }
}

export function clientLog(line: string): void {
  if (!clientLogEnabled()) return;
  // Bounded even before the server's cap: if the network is down, an unbounded
  // buffer would grow for as long as the tab lives.
  if (buffer.length >= MAX_BUFFER) {
    void flushClientLog();
  }
  buffer.push(line);
  if (timer === null) {
    timer = setTimeout(() => void flushClientLog(), FLUSH_MS);
  }
}

/** Test seam — drops buffered lines and any pending flush. */
export function _resetClientLog(): void {
  buffer = [];
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}
