import { describe, it, expect, beforeEach, vi } from "vitest";
import { prefetchPrefStore } from "./prefetchPref.svelte";

beforeEach(() => {
  localStorage.clear();
  prefetchPrefStore.set(true); // reset the singleton to a known state
  localStorage.clear();
});

describe("prefetchPrefStore", () => {
  it("defaults to enabled", () => {
    expect(prefetchPrefStore.enabled).toBe(true);
  });

  it("init with no stored value leaves the default", () => {
    prefetchPrefStore.init();
    expect(prefetchPrefStore.enabled).toBe(true);
  });

  it("init reads a stored false", () => {
    localStorage.setItem("prefetchOnWifi", "false");
    prefetchPrefStore.init();
    expect(prefetchPrefStore.enabled).toBe(false);
  });

  it("init reads a stored true", () => {
    localStorage.setItem("prefetchOnWifi", "true");
    prefetchPrefStore.init();
    expect(prefetchPrefStore.enabled).toBe(true);
  });

  it("set persists the value", () => {
    prefetchPrefStore.set(false);
    expect(prefetchPrefStore.enabled).toBe(false);
    expect(localStorage.getItem("prefetchOnWifi")).toBe("false");
  });

  it("toggle flips and persists", () => {
    prefetchPrefStore.set(true);
    prefetchPrefStore.toggle();
    expect(prefetchPrefStore.enabled).toBe(false);
    expect(localStorage.getItem("prefetchOnWifi")).toBe("false");
    prefetchPrefStore.toggle();
    expect(prefetchPrefStore.enabled).toBe(true);
  });

  it("lazy init without localStorage (SSR) keeps the default", async () => {
    vi.resetModules();
    vi.stubGlobal("localStorage", undefined);
    try {
      const { prefetchPrefStore: fresh } = await import("./prefetchPref.svelte");
      expect(fresh.enabled).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("first enabled read applies a stored opt-out without an init() call (backlog #36)", async () => {
    // LessonPlayer's onMount runs BEFORE the layout's onMount (children mount
    // first), so the store must lazily self-init on first read or a direct
    // lesson-page load prefetches despite the user's opt-out.
    vi.resetModules();
    localStorage.clear();
    localStorage.setItem("prefetchOnWifi", "false");
    const { prefetchPrefStore: fresh } = await import("./prefetchPref.svelte");
    expect(fresh.enabled).toBe(false);
  });
});
