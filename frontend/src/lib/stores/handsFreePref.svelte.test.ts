import { describe, it, expect, beforeEach, vi } from "vitest";

import { handsFreePref } from "./handsFreePref.svelte";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  handsFreePref.init();
});

describe("handsFreePref.enabled", () => {
  it("defaults to off when storage is empty", () => {
    handsFreePref.init();
    expect(handsFreePref.enabled).toBe(false);
  });

  it("reads a stored 'on' as enabled", () => {
    localStorage.setItem("handsFree", "on");
    handsFreePref.init();
    expect(handsFreePref.enabled).toBe(true);
  });

  it("ignores garbage and stays off", () => {
    localStorage.setItem("handsFree", "banana");
    handsFreePref.init();
    expect(handsFreePref.enabled).toBe(false);
  });

  it("set writes through, so the next lesson's mount sees it", () => {
    handsFreePref.set(true);
    expect(localStorage.getItem("handsFree")).toBe("on");
    handsFreePref.init();
    expect(handsFreePref.enabled).toBe(true);
  });

  it("survives a blocked localStorage rather than breaking the toggle", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const put = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => handsFreePref.init()).not.toThrow();
    expect(handsFreePref.enabled).toBe(false);
    expect(() => handsFreePref.set(true)).not.toThrow();
    expect(handsFreePref.enabled).toBe(true); // in-memory still tracks the click
    get.mockRestore();
    put.mockRestore();
  });
});

describe("handsFreePref handoff baton", () => {
  it("is not armed by default — opening a lesson must not start playing", () => {
    expect(handsFreePref.consumeHandoff()).toBe(false);
  });

  it("armHandoff arms it exactly once; a second read is false", () => {
    handsFreePref.armHandoff();
    expect(handsFreePref.consumeHandoff()).toBe(true);
    // ⚠️ The one-shot IS the feature: a reload of the arrival page re-runs
    // onMount, and without the clear it would start playing again on every
    // refresh for the rest of the session.
    expect(handsFreePref.consumeHandoff()).toBe(false);
  });

  it("lives in sessionStorage, not localStorage — a baton is not a preference", () => {
    handsFreePref.armHandoff();
    expect(sessionStorage.getItem("handsFreeHandoff")).toBe("1");
    expect(localStorage.getItem("handsFreeHandoff")).toBeNull();
  });

  it("is independent of enabled: arming does not turn hands-free on", () => {
    handsFreePref.armHandoff();
    expect(handsFreePref.enabled).toBe(false);
  });

  it("survives a blocked sessionStorage", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const put = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => handsFreePref.armHandoff()).not.toThrow();
    expect(handsFreePref.consumeHandoff()).toBe(false);
    get.mockRestore();
    put.mockRestore();
  });
});
