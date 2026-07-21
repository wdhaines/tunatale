import { describe, it, expect, beforeEach } from "vitest";

import { captionBlurPref } from "./captionBlurPref.svelte";

beforeEach(() => {
  localStorage.clear();
  captionBlurPref.set(true);
  localStorage.clear();
});

describe("captionBlurPref", () => {
  it("default is enabled (blurring on) when storage is empty", () => {
    captionBlurPref.init();
    expect(captionBlurPref.enabled).toBe(true);
  });

  it("reads a stored 'off' as disabled", () => {
    localStorage.setItem("captionBlur", "off");
    captionBlurPref.init();
    expect(captionBlurPref.enabled).toBe(false);
  });

  it("reads a stored 'on' as enabled", () => {
    localStorage.setItem("captionBlur", "on");
    captionBlurPref.init();
    expect(captionBlurPref.enabled).toBe(true);
  });

  it("ignores garbage and defaults to enabled", () => {
    localStorage.setItem("captionBlur", "banana");
    captionBlurPref.init();
    expect(captionBlurPref.enabled).toBe(true);
  });

  it("set(false) writes 'off' and flips the getter", () => {
    captionBlurPref.set(false);
    expect(captionBlurPref.enabled).toBe(false);
    expect(localStorage.getItem("captionBlur")).toBe("off");
  });

  it("set(true) writes 'on' and flips the getter", () => {
    captionBlurPref.set(false);
    captionBlurPref.set(true);
    expect(captionBlurPref.enabled).toBe(true);
    expect(localStorage.getItem("captionBlur")).toBe("on");
  });

  it("init() re-seeds from storage", () => {
    captionBlurPref.set(false);
    localStorage.setItem("captionBlur", "on");
    captionBlurPref.init();
    expect(captionBlurPref.enabled).toBe(true);
  });
});
