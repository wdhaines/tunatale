/**
 * Records the full touch -> click sequence to the durable client log.
 *
 * The question this exists to answer, which nothing else could: when a tap does
 * not do what it should, WHICH element received it, and did a `click` ever
 * arrive at all? Those three outcomes need different fixes and look identical
 * from the outside:
 *
 *   selectstart fires and click never does  -> the gesture became a selection
 *   click fires on the wrong element        -> hit-testing / tap-target size
 *   click fires on the right element        -> the handler or its reactivity
 *
 * `elementFromPoint` is the load-bearing part. `event.target` is where the
 * browser DELIVERED the event; elementFromPoint is what is actually painted at
 * those coordinates. When fuzzy tap-targeting redirects a touch to a
 * neighbouring control, the two disagree — and that disagreement is the
 * evidence.
 */
import { clientLog, clientLogEnabled } from "./clientLog";

const TRACED = ["touchstart", "pointerdown", "pointerup", "click", "selectstart"] as const;

function describe(el: EventTarget | null): string {
  if (!(el instanceof Element)) return "none";
  const cls =
    typeof el.className === "string" ? el.className.trim().split(/\s+/).slice(0, 3).join(".") : "";
  return cls ? `${el.tagName}.${cls}` : el.tagName;
}

function coords(e: Event): { x: number; y: number } | null {
  const touch = (e as TouchEvent).touches?.[0];
  if (touch) return { x: touch.clientX, y: touch.clientY };
  const p = e as PointerEvent;
  return typeof p.clientX === "number" ? { x: p.clientX, y: p.clientY } : null;
}

function paintedAt(x: number, y: number): string {
  // Guarded: a tracer that throws defeats its own purpose, and
  // elementFromPoint is absent in some environments (jsdom implements no
  // layout, so it does not provide it at all).
  try {
    return typeof document.elementFromPoint === "function"
      ? describe(document.elementFromPoint(x, y))
      : "n/a";
  } catch {
    return "n/a";
  }
}

export function traceEvent(e: Event): void {
  const xy = coords(e);
  const at = xy ? paintedAt(xy.x, xy.y) : "n/a";
  const where = xy ? `${Math.round(xy.x)},${Math.round(xy.y)}` : "-";
  clientLog(`${e.type} target=${describe(e.target)} at=${at} xy=${where}`);
}

/** Attach the tracer. Returns a teardown; a no-op when logging is off. */
export function startTouchTrace(): () => void {
  if (!clientLogEnabled()) return () => {};
  // Capture phase: a handler that stops propagation must not be able to hide
  // the event from the trace — that would blind exactly the case being chased.
  const listener = (e: Event) => traceEvent(e);
  for (const type of TRACED) document.addEventListener(type, listener, true);
  clientLog(`trace-start ua=${navigator.userAgent.slice(0, 120)}`);
  return () => {
    for (const type of TRACED) document.removeEventListener(type, listener, true);
  };
}
