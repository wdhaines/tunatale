import { describe, it, expect, vi, afterEach } from "vitest";
import { createLocalPref } from "./localPref.svelte";

// A stand-in for any of the real preference stores: "on"/"off" with off as the
// default, which is the shape most of them have.
function probe() {
  return createLocalPref("probe", {
    parse: (raw) => raw === "on",
    serialize: (value) => (value ? "on" : "off"),
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("createLocalPref", () => {
  it("parses the stored value", () => {
    localStorage.setItem("probe", "on");
    const pref = probe();
    expect(pref.value).toBe(true);
  });

  it("falls back to parse(null) when nothing is stored", () => {
    const pref = probe();
    expect(pref.value).toBe(false);
  });

  it("parses an unrecognised stored value through the same parse", () => {
    // The store's parse owns this decision, not the helper: "banana" is not
    // "on", so it reads as the default rather than throwing.
    localStorage.setItem("probe", "banana");
    const pref = probe();
    expect(pref.value).toBe(false);
  });

  it("self-initialises on the first read, with no init() call", () => {
    // The reason prefetchPref self-initialised: consumers deeper in the tree
    // mount before the layout's onMount, so the first read has to apply a
    // stored override itself.
    localStorage.setItem("probe", "on");
    const pref = probe();
    expect(pref.value).toBe(true);
  });

  it("seeds exactly once — a later storage change needs init()", () => {
    const pref = probe();
    expect(pref.value).toBe(false);
    localStorage.setItem("probe", "on");
    expect(pref.value).toBe(false);
    pref.init();
    expect(pref.value).toBe(true);
  });

  it("init() re-seeds from storage", () => {
    const pref = probe();
    pref.set(true);
    expect(localStorage.getItem("probe")).toBe("on");
    localStorage.setItem("probe", "off");
    pref.init();
    expect(pref.value).toBe(false);
  });

  it("set() writes the serialised form and flips the getter", () => {
    const pref = probe();
    pref.set(true);
    expect(pref.value).toBe(true);
    expect(localStorage.getItem("probe")).toBe("on");
  });

  it("falls back to the default when getItem throws (blocked storage)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const pref = probe();
    expect(() => pref.init()).not.toThrow();
    expect(pref.value).toBe(false);
  });

  it("survives a throwing setItem, keeping the in-memory value", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const pref = probe();
    expect(() => pref.set(true)).not.toThrow();
    expect(pref.value).toBe(true);
  });

  it("works with no localStorage at all (SSR)", async () => {
    // Not a stub on a live store: the module is imported fresh with no
    // localStorage in scope, which is what an SSR render actually sees.
    vi.resetModules();
    vi.stubGlobal("localStorage", undefined);
    const { createLocalPref: fresh } = await import("./localPref.svelte");
    const pref = fresh("probe", {
      parse: (raw) => raw === "on",
      serialize: (value) => (value ? "on" : "off"),
    });
    expect(pref.value).toBe(false);
    expect(() => pref.set(true)).not.toThrow();
    expect(pref.value).toBe(true);
  });
});
