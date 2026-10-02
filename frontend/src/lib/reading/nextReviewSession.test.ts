import { describe, it, expect } from "vitest";
import { nextSessionAfter, orderSessions, sessionNeighbours } from "./nextReviewSession";

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

// bd tunatale-e6fq. The pager, the sessions index and the hands-free hand-off
// must all walk ONE order. These pin the order itself and the neighbours read
// from it; `nextSessionAfter` above is the hand-off's view of the same thing.
describe("orderSessions", () => {
  it("puts sessions oldest first, whatever order they arrive in", () => {
    const list = [s("c", "2026-09-07"), s("a", "2026-09-02"), s("b", "2026-09-04")];
    expect(orderSessions(list).map((x) => x.id)).toEqual(["a", "b", "c"]);
  });

  it("breaks a same-date tie by id", () => {
    const list = [s("zulu", "2026-09-04"), s("alpha", "2026-09-04"), s("mike", "2026-09-04")];
    expect(orderSessions(list).map((x) => x.id)).toEqual(["alpha", "mike", "zulu"]);
  });

  it("returns a new array and leaves the caller's list alone", () => {
    const list = [s("b", "2026-09-04"), s("a", "2026-09-02")];
    const ordered = orderSessions(list);
    expect(ordered).not.toBe(list);
    expect(list.map((x) => x.id)).toEqual(["b", "a"]);
  });

  it("keeps every field of the sessions it orders", () => {
    const list = [{ id: "a", session_date: "2026-09-02", title: "A Missed Train" }];
    expect(orderSessions(list)[0].title).toBe("A Missed Train");
  });
});

describe("sessionNeighbours", () => {
  const list = [s("c", "2026-09-07"), s("a", "2026-09-02"), s("b", "2026-09-04")];

  it("gives the sessions either side by date", () => {
    const { prev, next } = sessionNeighbours(list, "b");
    expect(prev?.id).toBe("a");
    expect(next?.id).toBe("c");
  });

  it("has no previous on the oldest and no next on the newest", () => {
    expect(sessionNeighbours(list, "a")).toEqual({ prev: null, next: s("b", "2026-09-04") });
    expect(sessionNeighbours(list, "c")).toEqual({ prev: s("b", "2026-09-04"), next: null });
  });

  it("has neither for an id that is not in the list, and for an empty list", () => {
    expect(sessionNeighbours(list, "ghost")).toEqual({ prev: null, next: null });
    expect(sessionNeighbours([], "a")).toEqual({ prev: null, next: null });
  });

  it("walks a same-date run by id, in both directions", () => {
    const sameDay = [s("zulu", "2026-09-04"), s("alpha", "2026-09-04"), s("mike", "2026-09-04")];
    const { prev, next } = sessionNeighbours(sameDay, "mike");
    expect(prev?.id).toBe("alpha");
    expect(next?.id).toBe("zulu");
  });

  it("agrees with the hand-off on what comes next, for every session", () => {
    // One ordering, not two: a pager whose "next" differed from the session a
    // hands-free run moves to would be two answers to one question.
    const mixed = [
      s("c", "2026-09-07"),
      s("zulu", "2026-09-04"),
      s("a", "2026-09-02"),
      s("alpha", "2026-09-04"),
    ];
    for (const x of mixed) {
      expect(sessionNeighbours(mixed, x.id).next).toEqual(nextSessionAfter(mixed, x.id));
    }
  });

  it("stepping forward then back returns to where it started", () => {
    const mixed = [s("c", "2026-09-07"), s("zulu", "2026-09-04"), s("alpha", "2026-09-04")];
    for (const x of orderSessions(mixed).slice(0, -1)) {
      const next = sessionNeighbours(mixed, x.id).next!;
      expect(sessionNeighbours(mixed, next.id).prev?.id).toBe(x.id);
    }
  });
});
