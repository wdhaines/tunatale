import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  clearMediaTrace,
  mediaTrace,
  mediaTraceEnabled,
  mediaTraceSource,
  readMediaTrace,
  setMediaTraceEnabled,
  setMediaTraceSource,
} from "./mediaTrace";
import { _resetClientLog } from "./clientLog";

describe("mediaTrace", () => {
  beforeEach(() => {
    localStorage.clear();
    _resetClientLog();
  });

  it("is off by default and flips with the enable key", () => {
    expect(mediaTraceEnabled()).toBe(false);
    setMediaTraceEnabled(true);
    expect(localStorage.getItem("mediaTrace")).toBe("on");
    expect(mediaTraceEnabled()).toBe(true);
    setMediaTraceEnabled(false);
    expect(mediaTraceEnabled()).toBe(false);
  });

  it("does nothing at all when off — no storage write, no entry", () => {
    mediaTrace("action:play");
    expect(localStorage.getItem("mediaTraceLog")).toBeNull();
    expect(readMediaTrace()).toEqual([]);
  });

  it("persists an entry with an ISO timestamp prefix when on", () => {
    setMediaTraceEnabled(true);
    mediaTrace("action:play");
    const lines = readMediaTrace();
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z /);
    expect(lines[0]).toContain("action:play");
  });

  it("the 501st entry evicts the oldest, capping at 500", () => {
    setMediaTraceEnabled(true);
    for (let i = 0; i < 501; i++) mediaTrace(`line-${i}`);
    const lines = readMediaTrace();
    expect(lines).toHaveLength(500);
    expect(lines[0]).toContain("line-1");
    expect(lines[499]).toContain("line-500");
    expect(lines.some((l) => l.includes("line-0"))).toBe(false);
  });

  it("entries survive a fresh module import, as a reload would", async () => {
    setMediaTraceEnabled(true);
    mediaTrace("survived-the-reload");
    vi.resetModules();
    const mt = await import("./mediaTrace");
    const lines = mt.readMediaTrace() as string[];
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain("survived-the-reload");
  });

  it("treats a corrupt buffer as empty and overwrites it", () => {
    setMediaTraceEnabled(true);
    // Not JSON, a JSON non-array, and a JSON array of non-strings — all three
    // must be read as empty and replaced by the fresh entry.
    for (const corrupt of [
      "{not json",
      JSON.stringify({ not: "an array" }),
      JSON.stringify([1, 2]),
    ]) {
      localStorage.setItem("mediaTraceLog", corrupt);
      mediaTrace("fresh");
      expect(readMediaTrace()).toEqual([expect.stringContaining("fresh")]);
    }
  });

  it("readMediaTrace returns [] for a non-array or corrupt buffer", () => {
    setMediaTraceEnabled(true);
    localStorage.setItem("mediaTraceLog", JSON.stringify({ not: "an array" }));
    expect(readMediaTrace()).toEqual([]);
    localStorage.setItem("mediaTraceLog", "scrambled");
    expect(readMediaTrace()).toEqual([]);
  });

  it("returns false from mediaTraceEnabled when storage throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    try {
      expect(mediaTraceEnabled()).toBe(false);
      expect(() => mediaTrace("boom")).not.toThrow();
    } finally {
      spy.mockRestore();
    }
  });

  it("swallows a throwing localStorage.setItem", () => {
    setMediaTraceEnabled(true);
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    try {
      expect(() => mediaTrace("boom")).not.toThrow();
    } finally {
      spy.mockRestore();
    }
  });

  it("clearMediaTrace empties the buffer and survives the enable key", () => {
    setMediaTraceEnabled(true);
    mediaTrace("a");
    mediaTrace("b");
    expect(readMediaTrace()).toHaveLength(2);
    clearMediaTrace();
    expect(readMediaTrace()).toEqual([]);
    expect(mediaTraceEnabled()).toBe(true); // a clear must not turn the trace off
  });
});

describe("mediaTrace source label", () => {
  beforeEach(() => {
    localStorage.clear();
    _resetClientLog();
  });

  it("defaults to empty when nothing was declared", () => {
    expect(mediaTraceSource()).toBe("");
  });

  it("round-trips a declared source", () => {
    setMediaTraceSource("car");
    expect(mediaTraceSource()).toBe("car");
  });

  it("persists across reads, because the drive spans navigations", () => {
    setMediaTraceSource("headset");
    expect(mediaTraceSource()).toBe("headset");
    expect(localStorage.getItem("mediaTraceSource")).toBe("headset");
  });

  it("sanitises the label rather than trusting the query string", () => {
    // It reaches the log verbatim and the log is read back as whitespace-
    // delimited fields, so a space or a newline would split one field into two
    // and silently corrupt every parse of that line.
    setMediaTraceSource("my car  \n stereo!!");
    expect(mediaTraceSource()).toBe("my-car-stereo");
  });

  it("caps a long label", () => {
    setMediaTraceSource("x".repeat(200));
    expect(mediaTraceSource().length).toBeLessThanOrEqual(32);
  });

  it("a throwing localStorage yields empty rather than breaking the caller", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("private mode");
    });
    expect(mediaTraceSource()).toBe("");
    spy.mockRestore();
  });

  it("a throwing localStorage on write is swallowed", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("private mode");
    });
    expect(() => setMediaTraceSource("car")).not.toThrow();
    spy.mockRestore();
  });
});
