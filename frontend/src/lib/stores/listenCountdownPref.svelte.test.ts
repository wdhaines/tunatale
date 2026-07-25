import { describe, it, expect, beforeEach } from "vitest";

import { listenCountdownPref } from "./listenCountdownPref.svelte";

beforeEach(() => {
  localStorage.clear();
  listenCountdownPref.set("off");
  localStorage.clear();
});

describe("listenCountdownPref", () => {
  it("default is 'off' when storage is empty", () => {
    listenCountdownPref.init();
    expect(listenCountdownPref.value).toBe("off");
  });

  it("reads a stored '10' as 10", () => {
    localStorage.setItem("listenCountdown", "10");
    listenCountdownPref.init();
    expect(listenCountdownPref.value).toBe("10");
  });

  it("reads a stored '30' as 30", () => {
    localStorage.setItem("listenCountdown", "30");
    listenCountdownPref.init();
    expect(listenCountdownPref.value).toBe("30");
  });

  it("reads a stored '60' as 60", () => {
    localStorage.setItem("listenCountdown", "60");
    listenCountdownPref.init();
    expect(listenCountdownPref.value).toBe("60");
  });

  it("reads a stored 'off' as off", () => {
    localStorage.setItem("listenCountdown", "off");
    listenCountdownPref.init();
    expect(listenCountdownPref.value).toBe("off");
  });

  it("ignores garbage and defaults to 'off'", () => {
    localStorage.setItem("listenCountdown", "banana");
    listenCountdownPref.init();
    expect(listenCountdownPref.value).toBe("off");
  });

  it("set('30') writes '30' and flips the getter", () => {
    listenCountdownPref.set("30");
    expect(listenCountdownPref.value).toBe("30");
    expect(localStorage.getItem("listenCountdown")).toBe("30");
  });

  it("set('off') writes 'off' and flips the getter", () => {
    listenCountdownPref.set("30");
    listenCountdownPref.set("off");
    expect(listenCountdownPref.value).toBe("off");
    expect(localStorage.getItem("listenCountdown")).toBe("off");
  });

  it("init() re-seeds from storage", () => {
    listenCountdownPref.set("30");
    localStorage.setItem("listenCountdown", "60");
    listenCountdownPref.init();
    expect(listenCountdownPref.value).toBe("60");
  });
});
