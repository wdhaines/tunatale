import { beforeEach, describe, expect, it } from "vitest";

import { gradeRangePref } from "./gradeRangePref.svelte";

beforeEach(() => {
  localStorage.clear();
  gradeRangePref.set(3);
  localStorage.clear();
});

describe("gradeRangePref", () => {
  it("defaults to three days (the owner's call, 2026-11-11)", () => {
    gradeRangePref.init();
    expect(gradeRangePref.value).toBe(3);
  });

  it("remembers a changed range, all included", () => {
    gradeRangePref.set(7);
    expect(localStorage.getItem("gradeRange")).toBe("7");
    gradeRangePref.init();
    expect(gradeRangePref.value).toBe(7);

    gradeRangePref.set(null);
    expect(localStorage.getItem("gradeRange")).toBe("all");
    gradeRangePref.init();
    expect(gradeRangePref.value).toBeNull();
  });

  it("reads garbage as the default", () => {
    localStorage.setItem("gradeRange", "soon");
    gradeRangePref.init();
    expect(gradeRangePref.value).toBe(3);
  });
});
