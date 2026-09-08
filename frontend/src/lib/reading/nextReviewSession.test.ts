import { describe, it, expect } from "vitest";
import { nextSessionAfter } from "./nextReviewSession";

const s = (id: string, session_date: string) => ({ id, session_date });

describe("nextSessionAfter", () => {
  it("returns the session that follows by date, whatever order the list arrives in", () => {
    const list = [s("c", "2026-09-07"), s("a", "2026-09-02"), s("b", "2026-09-04")];
    expect(nextSessionAfter(list, "a")?.id).toBe("b");
    expect(nextSessionAfter(list, "b")?.id).toBe("c");
  });

  it("returns null on the newest session — a run ends there, it does not wrap", () => {
    const list = [s("a", "2026-09-02"), s("b", "2026-09-04")];
    expect(nextSessionAfter(list, "b")).toBeNull();
  });

  it("returns null when the current id is not in the list", () => {
    expect(nextSessionAfter([s("a", "2026-09-02")], "ghost")).toBeNull();
  });

  it("returns null for an empty list", () => {
    expect(nextSessionAfter([], "a")).toBeNull();
  });

  it("breaks a same-date tie by id, so the order is stable across loads", () => {
    // The list endpoint sorts by date DESC and two sessions can share a day;
    // without a tiebreak the 'next' session would depend on array order and a
    // hands-free run could bounce between the same two.
    const list = [s("zulu", "2026-09-04"), s("alpha", "2026-09-04")];
    expect(nextSessionAfter(list, "alpha")?.id).toBe("zulu");
    expect(nextSessionAfter(list, "zulu")).toBeNull();
  });
});
