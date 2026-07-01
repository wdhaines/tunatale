import { describe, it, expect, vi, beforeEach } from "vitest";

/** Stub window.matchMedia (jsdom doesn't implement it) keyed off the mobile breakpoint. */
function stubMatchMedia(matches: boolean) {
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(() => ({
    matches,
    media: "(max-width: 640px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

import { lessonModePref, viewportDefault } from "./lessonModePref.svelte";

beforeEach(() => {
  localStorage.clear();
  stubMatchMedia(false);
  lessonModePref.set("read"); // reset the singleton to a known state
  localStorage.clear();
});

describe("viewportDefault", () => {
  it("returns 'listen' when the mobile media query matches", () => {
    stubMatchMedia(true);
    expect(viewportDefault()).toBe("listen");
  });

  it("returns 'read' when the mobile media query does not match", () => {
    stubMatchMedia(false);
    expect(viewportDefault()).toBe("read");
  });
});

describe("lessonModePref", () => {
  it("init with no stored value uses the mobile viewport default", () => {
    stubMatchMedia(true);
    lessonModePref.init();
    expect(lessonModePref.mode).toBe("listen");
  });

  it("init with no stored value uses the desktop viewport default", () => {
    stubMatchMedia(false);
    lessonModePref.init();
    expect(lessonModePref.mode).toBe("read");
  });

  it("init honors a stored value over the viewport default", () => {
    localStorage.setItem("lessonMode", "listen");
    stubMatchMedia(false); // viewport would say 'read'
    lessonModePref.init();
    expect(lessonModePref.mode).toBe("listen");
  });

  it("set persists the override", () => {
    lessonModePref.set("listen");
    expect(lessonModePref.mode).toBe("listen");
    expect(localStorage.getItem("lessonMode")).toBe("listen");
  });
});
