import { describe, it, expect, beforeEach } from "vitest";

import { readerProductionPref } from "./readerProductionPref.svelte";

beforeEach(() => {
  localStorage.clear();
  readerProductionPref.set(false);
  localStorage.clear();
});

describe("readerProductionPref", () => {
  it("default is OFF when storage is empty (the user's call, bd tunatale-dvdm.3)", () => {
    readerProductionPref.init();
    expect(readerProductionPref.enabled).toBe(false);
  });

  it("reads a stored 'on' as enabled", () => {
    localStorage.setItem("readerProduction", "on");
    readerProductionPref.init();
    expect(readerProductionPref.enabled).toBe(true);
  });

  it("ignores garbage and defaults to off", () => {
    localStorage.setItem("readerProduction", "banana");
    readerProductionPref.init();
    expect(readerProductionPref.enabled).toBe(false);
  });

  it("set(true) writes 'on' and flips the getter", () => {
    readerProductionPref.set(true);
    expect(readerProductionPref.enabled).toBe(true);
    expect(localStorage.getItem("readerProduction")).toBe("on");
  });

  it("set(false) writes 'off' and flips the getter", () => {
    readerProductionPref.set(true);
    readerProductionPref.set(false);
    expect(readerProductionPref.enabled).toBe(false);
    expect(localStorage.getItem("readerProduction")).toBe("off");
  });
});
