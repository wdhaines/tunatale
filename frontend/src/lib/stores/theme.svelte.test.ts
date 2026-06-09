import { describe, it, expect, vi, beforeEach } from "vitest";

/** Stub window.matchMedia (jsdom doesn't implement it) and let tests fire change events. */
function stubMatchMedia(matches: boolean) {
  const listeners: Array<() => void> = [];
  const mql = {
    matches,
    media: "(prefers-color-scheme: dark)",
    addEventListener: (_type: string, cb: () => void) => listeners.push(cb),
    removeEventListener: vi.fn(),
    fire: () => listeners.forEach((l) => l()),
  };
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(() => mql);
  return mql;
}

import { themeStore, resolveTheme } from "./theme.svelte";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.colorScheme = "";
  stubMatchMedia(false);
  themeStore.set("system"); // reset the singleton to a known state
  localStorage.clear();
});

describe("resolveTheme", () => {
  it("passes through explicit light/dark", () => {
    expect(resolveTheme("light")).toBe("light");
    expect(resolveTheme("dark")).toBe("dark");
  });

  it("resolves system via matchMedia", () => {
    stubMatchMedia(true);
    expect(resolveTheme("system")).toBe("dark");
    stubMatchMedia(false);
    expect(resolveTheme("system")).toBe("light");
  });
});

describe("themeStore", () => {
  it("init with no stored pref applies the resolved system theme", () => {
    stubMatchMedia(false);
    themeStore.init();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("init adopts a valid stored pref", () => {
    localStorage.setItem("theme", "dark");
    themeStore.init();
    expect(themeStore.pref).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("init ignores an invalid stored pref", () => {
    themeStore.set("light");
    localStorage.setItem("theme", "bogus");
    themeStore.init();
    expect(themeStore.pref).toBe("light");
  });

  it("set persists and applies", () => {
    themeStore.set("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
    expect(themeStore.pref).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("resolved getter reflects the current pref", () => {
    themeStore.set("light");
    expect(themeStore.resolved).toBe("light");
  });

  it("cycle goes system → light → dark → system", () => {
    themeStore.set("system");
    themeStore.cycle();
    expect(themeStore.pref).toBe("light");
    themeStore.cycle();
    expect(themeStore.pref).toBe("dark");
    themeStore.cycle();
    expect(themeStore.pref).toBe("system");
  });

  it("re-applies on OS change while following system, but not once pinned", () => {
    const mql = stubMatchMedia(false);
    themeStore.set("system");
    themeStore.init(); // attaches the change listener

    mql.matches = true;
    mql.fire();
    expect(document.documentElement.dataset.theme).toBe("dark");

    // Pin to light — a later OS change must not override the explicit choice.
    themeStore.set("light");
    mql.matches = false;
    mql.fire();
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
