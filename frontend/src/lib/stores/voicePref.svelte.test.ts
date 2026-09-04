import { describe, it, expect, beforeEach } from "vitest";

import { voicePref } from "./voicePref.svelte";

beforeEach(() => {
  localStorage.clear();
  voicePref.set(false);
  localStorage.clear();
});

describe("voicePref", () => {
  it("default is disabled (capture off) when storage is empty", () => {
    voicePref.init();
    expect(voicePref.enabled).toBe(false);
  });

  it("reads a stored 'on' as enabled", () => {
    localStorage.setItem("voice", "on");
    voicePref.init();
    expect(voicePref.enabled).toBe(true);
  });

  it("reads a stored 'off' as disabled", () => {
    localStorage.setItem("voice", "off");
    voicePref.init();
    expect(voicePref.enabled).toBe(false);
  });

  it("ignores garbage and stays disabled", () => {
    localStorage.setItem("voice", "banana");
    voicePref.init();
    expect(voicePref.enabled).toBe(false);
  });

  it("set(true) writes 'on' and flips the getter", () => {
    voicePref.set(true);
    expect(voicePref.enabled).toBe(true);
    expect(localStorage.getItem("voice")).toBe("on");
  });

  it("set(false) writes 'off' and flips the getter", () => {
    voicePref.set(true);
    voicePref.set(false);
    expect(voicePref.enabled).toBe(false);
    expect(localStorage.getItem("voice")).toBe("off");
  });

  it("init() re-seeds from storage", () => {
    voicePref.set(true);
    localStorage.setItem("voice", "off");
    voicePref.init();
    expect(voicePref.enabled).toBe(false);
  });
});
