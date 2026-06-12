import { describe, it, expect, beforeEach } from "vitest";
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
});
