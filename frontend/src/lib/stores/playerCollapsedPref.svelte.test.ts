import { describe, it, expect, beforeEach, vi } from "vitest";

import { playerCollapsedPref } from "./playerCollapsedPref.svelte";

beforeEach(() => {
  localStorage.clear();
  playerCollapsedPref.init();
});

describe("playerCollapsedPref", () => {
  it("defaults to expanded when storage is empty", () => {
    // A first-time reader must meet the full control set, not a stripped
    // player with no obvious way back.
    expect(playerCollapsedPref.collapsed).toBe(false);
  });

  it("reads a stored 'on' as collapsed", () => {
    localStorage.setItem("playerCollapsed", "on");
    playerCollapsedPref.init();
    expect(playerCollapsedPref.collapsed).toBe(true);
  });

  it("ignores garbage and stays expanded", () => {
    localStorage.setItem("playerCollapsed", "banana");
    playerCollapsedPref.init();
    expect(playerCollapsedPref.collapsed).toBe(false);
  });

  it("set writes through, so the next lesson opens the same way", () => {
    playerCollapsedPref.set(true);
    expect(localStorage.getItem("playerCollapsed")).toBe("on");
    playerCollapsedPref.init();
    expect(playerCollapsedPref.collapsed).toBe(true);
  });

  it("set(false) writes 'off' rather than clearing the key", () => {
    playerCollapsedPref.set(true);
    playerCollapsedPref.set(false);
    expect(localStorage.getItem("playerCollapsed")).toBe("off");
    playerCollapsedPref.init();
    expect(playerCollapsedPref.collapsed).toBe(false);
  });

  it("survives blocked storage rather than breaking the toggle", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const put = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => playerCollapsedPref.init()).not.toThrow();
    expect(playerCollapsedPref.collapsed).toBe(false);
    expect(() => playerCollapsedPref.set(true)).not.toThrow();
    expect(playerCollapsedPref.collapsed).toBe(true); // in-memory still tracks the click
    get.mockRestore();
    put.mockRestore();
  });
});
