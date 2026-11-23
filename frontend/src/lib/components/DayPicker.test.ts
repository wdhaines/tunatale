/**
 * Tests for DayPicker.svelte.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import DayPicker from "./DayPicker.svelte";
import type { CurriculumSummary } from "#lib/api.js";

vi.mock("#lib/stores/listened.svelte.js", () => ({
  listenedStore: { has: vi.fn().mockReturnValue(false) },
}));

const day = (n: number, position = n) => ({
  day: n,
  position,
  title: `Title ${n}`,
  focus: `focus ${n}`,
  collocations: ["kava"],
  learning_objective: `obj ${n}`,
  story_guidance: "",
  kind: "thematic",
  pattern: "",
  group: "",
  group_title: "",
});

const curriculum: CurriculumSummary = {
  id: "c1",
  topic: "Coffee",
  language_code: "sl",
  cefr_level: "A2",
  days: [day(1), day(2), day(3)],
  proposed: null,
};

describe("DayPicker", () => {
  it("renders one button per day", () => {
    const { getAllByRole } = render(DayPicker, {
      props: { curriculum, onSelectDay: vi.fn() },
    });
    const buttons = getAllByRole("button");
    expect(buttons).toHaveLength(3);
    expect(buttons[0].textContent).toContain("Day 1");
    expect(buttons[2].textContent).toContain("Day 3");
  });

  it("shows each generated day's due count, and no badge at zero or without a count", () => {
    const { getAllByRole, queryAllByTestId } = render(DayPicker, {
      props: {
        curriculum,
        onSelectDay: vi.fn(),
        progress: new Map([
          [1, "l1"],
          [2, "l2"],
          [3, "l3"],
        ]),
        dueCounts: new Map([
          ["l1", 4],
          ["l2", 0],
        ]),
      },
    });
    const buttons = getAllByRole("button");
    const badges = queryAllByTestId("due-badge");
    expect(badges).toHaveLength(1);
    expect(buttons[0].contains(badges[0])).toBe(true);
    expect(badges[0].textContent).toBe("4");
    expect(badges[0].getAttribute("aria-label")).toBe("4 cards due");
  });

  it("names a single due card in the singular", () => {
    const { getByTestId } = render(DayPicker, {
      props: {
        curriculum,
        onSelectDay: vi.fn(),
        progress: new Map([[1, "l1"]]),
        dueCounts: new Map([["l1", 1]]),
      },
    });
    expect(getByTestId("due-badge").getAttribute("aria-label")).toBe("1 card due");
  });

  it("numbers buttons by position, so a deleted day leaves no gap", () => {
    // day 3 was deleted: the keys are 1, 2, 4 but the learner sees Day 1-3.
    const gappy: CurriculumSummary = {
      ...curriculum,
      days: [day(1, 1), day(2, 2), day(4, 3)],
    };
    const { getAllByRole } = render(DayPicker, {
      props: { curriculum: gappy, onSelectDay: vi.fn() },
    });
    const labels = getAllByRole("button").map((b) => b.textContent?.trim());
    expect(labels).toEqual(["Day 1 · Title 1", "Day 2 · Title 2", "Day 3 · Title 4"]);
  });

  it("still selects by the day key, not the displayed position", async () => {
    const onSelectDay = vi.fn().mockResolvedValue(undefined);
    const gappy: CurriculumSummary = {
      ...curriculum,
      days: [day(1, 1), day(2, 2), day(4, 3)],
    };
    const { getByText } = render(DayPicker, { props: { curriculum: gappy, onSelectDay } });
    await fireEvent.click(getByText(/Day 3 ·/));
    expect(onSelectDay).toHaveBeenCalledWith(4);
  });

  it("shows each day's title on its button", () => {
    const { getAllByRole } = render(DayPicker, {
      props: { curriculum, onSelectDay: vi.fn() },
    });
    const buttons = getAllByRole("button");
    expect(buttons[0].textContent).toContain("Title 1");
    expect(buttons[2].textContent).toContain("Title 3");
  });

  it("calls onSelectDay with the correct day when clicked", async () => {
    const onSelectDay = vi.fn().mockResolvedValue(undefined);
    const { getByText } = render(DayPicker, { props: { curriculum, onSelectDay } });
    await fireEvent.click(getByText(/Day 2 ·/));
    expect(onSelectDay).toHaveBeenCalledWith(2);
  });

  it("blocks concurrent clicks when a day is already loading (line 14 guard)", async () => {
    let resolveClick!: () => void;
    const slowSelect = new Promise<void>((r) => {
      resolveClick = r;
    });
    const onSelectDay = vi.fn().mockReturnValue(slowSelect);

    const { getAllByRole } = render(DayPicker, { props: { curriculum, onSelectDay } });
    const buttons = getAllByRole("button") as HTMLButtonElement[];

    await fireEvent.click(buttons[0]);
    expect(buttons[1].disabled).toBe(true);

    await fireEvent.click(buttons[1]);
    expect(onSelectDay).toHaveBeenCalledTimes(1);

    resolveClick();
  });

  it("shows … on the loading button", async () => {
    let resolveClick!: () => void;
    const slowSelect = new Promise<void>((r) => {
      resolveClick = r;
    });
    const onSelectDay = vi.fn().mockReturnValue(slowSelect);

    const { getAllByRole } = render(DayPicker, { props: { curriculum, onSelectDay } });
    const buttons = getAllByRole("button") as HTMLButtonElement[];

    await fireEvent.click(buttons[0]);
    expect(buttons[0].textContent).toContain("…");
    resolveClick();
  });

  it("renders empty state (outlined) for days with no progress", () => {
    const { getAllByRole } = render(DayPicker, {
      props: { curriculum, onSelectDay: vi.fn(), progress: new Map() },
    });
    const buttons = getAllByRole("button") as HTMLButtonElement[];
    expect(buttons[0].classList.contains("state-empty")).toBe(true);
  });

  it("renders generated state (solid blue) for days in progress map", () => {
    const progress = new Map([[1, "lesson-1"]]);
    const { getAllByRole } = render(DayPicker, {
      props: { curriculum, onSelectDay: vi.fn(), progress },
    });
    const buttons = getAllByRole("button") as HTMLButtonElement[];
    expect(buttons[0].classList.contains("state-generated")).toBe(true);
  });

  it("renders listened state (green + checkmark) for listened lessons", async () => {
    const { listenedStore } = await import("#lib/stores/listened.svelte.js");
    vi.mocked(listenedStore.has).mockReturnValue(true);

    const progress = new Map([[1, "lesson-1"]]);
    const { getAllByRole } = render(DayPicker, {
      props: { curriculum, onSelectDay: vi.fn(), progress },
    });
    const buttons = getAllByRole("button") as HTMLButtonElement[];
    expect(buttons[0].classList.contains("state-listened")).toBe(true);
    expect(buttons[0].textContent).toContain("✓");
  });

  describe("pipelineStates prop", () => {
    it("adds pulse class for queued pipeline state", () => {
      const pipelineStates = new Map([[1, "queued"]]);
      const { getAllByRole } = render(DayPicker, {
        props: { curriculum, onSelectDay: vi.fn(), pipelineStates },
      });
      const buttons = getAllByRole("button") as HTMLButtonElement[];
      expect(buttons[0].classList.contains("pulse")).toBe(true);
    });

    it("adds pulse class for generating pipeline state", () => {
      const pipelineStates = new Map([[1, "generating"]]);
      const { getAllByRole } = render(DayPicker, {
        props: { curriculum, onSelectDay: vi.fn(), pipelineStates },
      });
      const buttons = getAllByRole("button") as HTMLButtonElement[];
      expect(buttons[0].classList.contains("pulse")).toBe(true);
    });

    it("adds pulse class for rendering pipeline state", () => {
      const pipelineStates = new Map([[2, "rendering"]]);
      const { getAllByRole } = render(DayPicker, {
        props: { curriculum, onSelectDay: vi.fn(), pipelineStates },
      });
      const buttons = getAllByRole("button") as HTMLButtonElement[];
      expect(buttons[1].classList.contains("pulse")).toBe(true);
    });

    it("adds danger class for failed pipeline state", () => {
      const pipelineStates = new Map([[1, "failed"]]);
      const { getAllByRole } = render(DayPicker, {
        props: { curriculum, onSelectDay: vi.fn(), pipelineStates },
      });
      const buttons = getAllByRole("button") as HTMLButtonElement[];
      expect(buttons[0].classList.contains("danger")).toBe(true);
    });

    it("no extra class for ready pipeline state", () => {
      const pipelineStates = new Map([[1, "ready"]]);
      const { getAllByRole } = render(DayPicker, {
        props: { curriculum, onSelectDay: vi.fn(), pipelineStates },
      });
      const buttons = getAllByRole("button") as HTMLButtonElement[];
      expect(buttons[0].classList.contains("pulse")).toBe(false);
      expect(buttons[0].classList.contains("danger")).toBe(false);
    });

    it("ignores pipelineStates for days not in the map", () => {
      const pipelineStates = new Map([[5, "queued"]]);
      const { getAllByRole } = render(DayPicker, {
        props: { curriculum, onSelectDay: vi.fn(), pipelineStates },
      });
      const buttons = getAllByRole("button") as HTMLButtonElement[];
      expect(buttons[0].classList.contains("pulse")).toBe(false);
      expect(buttons[2].classList.contains("pulse")).toBe(false);
    });
  });

  describe("book lessons (tunatale-cs3p)", () => {
    // The backend sends days in the book's order; a block is a run of one group.
    const inLesson = (n: number, position: number, lesson: number) => ({
      ...day(n, position),
      group: `wolff-${lesson}`,
      group_title: `Lesson ${lesson}`,
    });
    const wolff: CurriculumSummary = {
      ...curriculum,
      days: [
        inLesson(1, 1, 1),
        inLesson(2, 2, 1),
        inLesson(5, 3, 1),
        inLesson(3, 4, 2),
        inLesson(4, 5, 2),
      ],
    };

    it("heads each lesson's run of days with its title and how many are listened", async () => {
      const { listenedStore } = await import("#lib/stores/listened.svelte.js");
      vi.mocked(listenedStore.has).mockImplementation((id: string) => id === "l1" || id === "l5");
      const progress = new Map([
        [1, "l1"],
        [2, "l2"],
        [5, "l5"],
        [3, "l3"],
      ]);
      const { getAllByRole } = render(DayPicker, {
        props: { curriculum: wolff, onSelectDay: vi.fn(), progress },
      });
      const headings = getAllByRole("heading");
      expect(headings.map((h) => h.textContent?.replace(/\s+/g, " ").trim())).toEqual([
        "Lesson 1 2 of 3",
        "Lesson 2 0 of 2",
      ]);
      // Each block holds its own days, in the order sent.
      const blocks = headings.map((h) => h.closest("section")!);
      const titles = (b: Element) => [...b.querySelectorAll("button")].map((x) => x.textContent);
      expect(titles(blocks[0]).map((t) => t?.includes("Title 5"))).toEqual([false, false, true]);
      expect(titles(blocks[1])).toHaveLength(2);
      vi.mocked(listenedStore.has).mockReset().mockReturnValue(false);
    });

    it("draws no heading for an ungrouped curriculum", () => {
      const { queryAllByRole, getAllByRole } = render(DayPicker, {
        props: { curriculum, onSelectDay: vi.fn() },
      });
      expect(queryAllByRole("heading")).toHaveLength(0);
      expect(getAllByRole("button")).toHaveLength(3);
    });

    it("falls back to the group key when a group has no title", () => {
      const untitled: CurriculumSummary = {
        ...curriculum,
        days: [{ ...day(1), group: "unit-a", group_title: "" }],
      };
      const { getByRole } = render(DayPicker, {
        props: { curriculum: untitled, onSelectDay: vi.fn() },
      });
      expect(getByRole("heading").textContent).toContain("unit-a");
    });
  });
});
