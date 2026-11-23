/**
 * The curriculum page's "Next up" card (tunatale-cs3p slice 2, oracle C3):
 * the first day in display order that is not listened, one tap to open it.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import NextUp from "./NextUp.svelte";
import type { CurriculumSummary } from "#lib/api.js";

vi.mock("#lib/stores/listened.svelte.js", () => ({
  listenedStore: { has: vi.fn() },
}));

import { listenedStore } from "#lib/stores/listened.svelte.js";

const day = (n: number, group = "", group_title = "") => ({
  day: n,
  position: n,
  title: `Title ${n}`,
  focus: "f",
  collocations: ["hei"],
  learning_objective: "o",
  story_guidance: "",
  kind: "thematic",
  pattern: "",
  group,
  group_title,
});

// Display order as the backend sends it: the book's, not the day keys'.
const wolff: CurriculumSummary = {
  id: "c",
  topic: "Wolff",
  language_code: "ceb",
  cefr_level: "A1",
  days: [
    day(1, "w1", "Lesson 1"),
    day(2, "w1", "Lesson 1"),
    day(5, "w1", "Lesson 1"),
    day(3, "w2", "Lesson 2"),
  ],
  proposed: null,
};
const progress = new Map([
  [1, "l1"],
  [2, "l2"],
  [5, "l5"],
  [3, "l3"],
]);

beforeEach(() => {
  vi.mocked(listenedStore.has).mockReset();
});

describe("NextUp", () => {
  it("is the first unlistened day in display order, named with its lesson", () => {
    vi.mocked(listenedStore.has).mockImplementation((id: string) => id === "l1" || id === "l2");
    const { getByRole } = render(NextUp, {
      props: { curriculum: wolff, progress, onSelectDay: vi.fn() },
    });
    const card = getByRole("button");
    expect(card.textContent?.replace(/\s+/g, " ").trim()).toBe("Next up · Lesson 1 Title 5 ▶");
  });

  it("follows display order, not the day key", () => {
    // Day 3 is unlistened and has the smaller key, but day 5 comes first in the book.
    vi.mocked(listenedStore.has).mockImplementation((id: string) => id !== "l5" && id !== "l3");
    const { getByRole } = render(NextUp, {
      props: { curriculum: wolff, progress, onSelectDay: vi.fn() },
    });
    expect(getByRole("button").textContent).toContain("Title 5");
  });

  it("counts a day with no lesson yet as not listened", () => {
    vi.mocked(listenedStore.has).mockReturnValue(true);
    const { getByRole } = render(NextUp, {
      props: { curriculum: wolff, progress: new Map([[1, "l1"]]), onSelectDay: vi.fn() },
    });
    expect(getByRole("button").textContent).toContain("Title 2");
  });

  it("opens that day by its key", async () => {
    vi.mocked(listenedStore.has).mockImplementation(
      (id: string) => id === "l1" || id === "l2" || id === "l5",
    );
    const onSelectDay = vi.fn();
    const { getByRole } = render(NextUp, { props: { curriculum: wolff, progress, onSelectDay } });
    await fireEvent.click(getByRole("button"));
    expect(onSelectDay).toHaveBeenCalledWith(3);
  });

  it("says just Next up for an ungrouped curriculum", () => {
    vi.mocked(listenedStore.has).mockReturnValue(false);
    const plain = { ...wolff, days: [day(1)] };
    const { getByRole } = render(NextUp, {
      props: { curriculum: plain, progress, onSelectDay: vi.fn() },
    });
    expect(getByRole("button").textContent?.replace(/\s+/g, " ").trim()).toBe("Next up Title 1 ▶");
  });

  it("is absent once every day is listened", () => {
    vi.mocked(listenedStore.has).mockReturnValue(true);
    const { queryByRole } = render(NextUp, {
      props: { curriculum: wolff, progress, onSelectDay: vi.fn() },
    });
    expect(queryByRole("button")).toBeNull();
  });
});
