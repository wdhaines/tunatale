import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  clientLog,
  clientLogEnabled,
  setClientLogEnabled,
  flushClientLog,
  _resetClientLog,
} from "./clientLog";
import { traceEvent, startTouchTrace } from "./touchTrace";

function bodyOf(call: unknown[]): { lines: string[] } {
  return JSON.parse((call[1] as RequestInit).body as string);
}

describe("clientLog", () => {
  beforeEach(() => {
    _resetClientLog();
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("is off unless explicitly switched on", async () => {
    expect(clientLogEnabled()).toBe(false);
    clientLog("touchstart");
    await flushClientLog();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("posts buffered lines once enabled", async () => {
    setClientLogEnabled(true);
    clientLog("touchstart target=BUTTON.gloss");
    clientLog("click target=BUTTON.gloss");
    await flushClientLog();

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain("/api/client-log");
    expect((init as RequestInit).method).toBe("POST");
    expect(bodyOf([url, init]).lines).toEqual([
      "touchstart target=BUTTON.gloss",
      "click target=BUTTON.gloss",
    ]);
  });

  it("a flush with nothing buffered makes no request", async () => {
    setClientLogEnabled(true);
    await flushClientLog();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("a failing POST never throws at the caller", async () => {
    // Instrumentation that can break the page it instruments is worse than none.
    setClientLogEnabled(true);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    clientLog("touchstart");
    await expect(flushClientLog()).resolves.toBeUndefined();
  });

  it("a full buffer flushes rather than growing without bound", async () => {
    setClientLogEnabled(true);
    for (let i = 0; i < 60; i++) clientLog(`line${i}`);
    await flushClientLog();
    // The first 50 went in their own request when the cap was hit.
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThanOrEqual(
      1,
    );
    const sent = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.flatMap(
      (c) => bodyOf(c).lines,
    );
    expect(sent).toHaveLength(60);
    expect(new Set(sent).size).toBe(60);
  });

  it("survives storage that throws (private mode)", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(clientLogEnabled()).toBe(false);
    spy.mockRestore();
    const setSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(() => setClientLogEnabled(true)).not.toThrow();
    setSpy.mockRestore();
  });

  it("flushes on its own timer, without an explicit flush", async () => {
    // The batching window is what makes this usable in the field: a whole tap
    // sequence coalesces into one request and the caller never has to think
    // about it. Nothing else exercises the timer — every other test flushes by
    // hand, which is precisely how a broken auto-flush would go unnoticed.
    vi.useFakeTimers();
    setClientLogEnabled(true);
    clientLog("touchstart");
    expect(fetch).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1500);

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(bodyOf((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]).lines).toEqual([
      "touchstart",
    ]);
  });
});

describe("touchTrace", () => {
  beforeEach(() => {
    _resetClientLog();
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("records the delivered target AND what is painted at the coordinates", async () => {
    // The disagreement between these two IS the evidence: when fuzzy
    // tap-targeting redirects a touch to a neighbouring control, `target` and
    // `elementFromPoint` differ.
    setClientLogEnabled(true);
    const btn = document.createElement("button");
    btn.className = "gloss blurred";
    document.body.appendChild(btn);
    const other = document.createElement("button");
    other.className = "skip";
    // jsdom implements no layout and so provides no elementFromPoint at all —
    // define it before spying. That absence is itself why the production code
    // guards the call.
    (document as unknown as { elementFromPoint: unknown }).elementFromPoint = () => other;

    const ev = new MouseEvent("click", { clientX: 10, clientY: 20 });
    Object.defineProperty(ev, "target", { value: btn });
    traceEvent(ev);
    await flushClientLog();

    const line = bodyOf((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]).lines[0];
    expect(line).toContain("click");
    expect(line).toContain("target=BUTTON.gloss.blurred");
    expect(line).toContain("at=BUTTON.skip");
    expect(line).toContain("xy=10,20");
  });

  it("attaches nothing while logging is off", () => {
    const add = vi.spyOn(document, "addEventListener");
    const stop = startTouchTrace();
    expect(add).not.toHaveBeenCalled();
    stop();
    add.mockRestore();
  });

  it("an event dispatched after attaching actually reaches the log", async () => {
    // End-to-end through the real listener, not just a spy on addEventListener.
    // Attaching and then never dispatching leaves the listener itself untested,
    // which is exactly how a tracer that traces nothing would ship green.
    setClientLogEnabled(true);
    (document as unknown as { elementFromPoint: unknown }).elementFromPoint = () => null;
    const stop = startTouchTrace();

    document.dispatchEvent(new MouseEvent("click", { clientX: 5, clientY: 6 }));
    await flushClientLog();
    stop();

    const lines = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.flatMap(
      (c) => bodyOf(c).lines,
    );
    expect(lines.some((l: string) => l.startsWith("trace-start ua="))).toBe(true);
    expect(lines.some((l: string) => l.includes("click") && l.includes("xy=5,6"))).toBe(true);
  });

  it("stops tracing after teardown", async () => {
    setClientLogEnabled(true);
    (document as unknown as { elementFromPoint: unknown }).elementFromPoint = () => null;
    const stop = startTouchTrace();
    stop();
    _resetClientLog();

    document.dispatchEvent(new MouseEvent("click", { clientX: 7, clientY: 8 }));
    await flushClientLog();

    const lines = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.flatMap(
      (c) => bodyOf(c).lines,
    );
    expect(lines.some((l: string) => l.includes("xy=7,8"))).toBe(false);
  });

  it("attaches in the CAPTURE phase and detaches cleanly", () => {
    // Capture, so a handler that stops propagation cannot hide the event from
    // the trace — that would blind exactly the case being chased.
    setClientLogEnabled(true);
    const add = vi.spyOn(document, "addEventListener");
    const remove = vi.spyOn(document, "removeEventListener");
    const stop = startTouchTrace();
    expect(add).toHaveBeenCalled();
    expect(add.mock.calls.every((c) => c[2] === true)).toBe(true);
    stop();
    expect(remove).toHaveBeenCalledTimes(add.mock.calls.length);
    add.mockRestore();
    remove.mockRestore();
  });

  it("describes a non-element target without throwing", async () => {
    setClientLogEnabled(true);
    const ev = new Event("selectstart");
    traceEvent(ev);
    await flushClientLog();
    const line = bodyOf((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]).lines[0];
    expect(line).toContain("target=none");
  });

  it("reads coordinates from a touch, not just a pointer", async () => {
    setClientLogEnabled(true);
    (document as unknown as { elementFromPoint: unknown }).elementFromPoint = () => null;
    const ev = new Event("touchstart");
    Object.defineProperty(ev, "touches", { value: [{ clientX: 33, clientY: 44 }] });
    traceEvent(ev);
    await flushClientLog();
    const line = bodyOf((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]).lines[0];
    expect(line).toContain("xy=33,44");
    expect(line).toContain("at=none");
  });

  it("reports n/a rather than throwing when elementFromPoint is missing or fails", async () => {
    // Both halves of the guard. A tracer that throws takes down the page it is
    // instrumenting, which is strictly worse than no tracer.
    setClientLogEnabled(true);
    delete (document as unknown as { elementFromPoint?: unknown }).elementFromPoint;
    traceEvent(new MouseEvent("click", { clientX: 1, clientY: 2 }));

    (document as unknown as { elementFromPoint: unknown }) = document as never;
    (document as unknown as { elementFromPoint: unknown }).elementFromPoint = () => {
      throw new Error("no layout");
    };
    traceEvent(new MouseEvent("click", { clientX: 3, clientY: 4 }));

    await flushClientLog();
    const lines = bodyOf((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]).lines;
    expect(lines.every((l: string) => l.includes("at=n/a"))).toBe(true);
  });
});
