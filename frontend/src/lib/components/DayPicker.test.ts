/**
 * Tests for DayPicker.svelte.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import DayPicker from "./DayPicker.svelte";
import type { CurriculumSummary } from "$lib/api";

vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: { has: vi.fn().mockReturnValue(false) },
}));

const day = (n: number) => ({
  day: n,
  title: `Title ${n}`,
  focus: `focus ${n}`,
  collocations: ["kava"],
  learning_objective: `obj ${n}`,
  story_guidance: "",
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
    const { listenedStore } = await import("$lib/stores/listened.svelte");
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
});
