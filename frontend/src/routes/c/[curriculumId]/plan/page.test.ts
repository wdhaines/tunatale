/**
 * Tests for /c/[curriculumId]/plan — the planner chat page.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  api: {
    planTurn: vi.fn(),
    commitPlan: vi.fn(),
  },
}));

import { api } from "$lib/api";
import type { CurriculumSummary, DayPlan, ProposedBatch } from "$lib/api";
import Page from "./+page.svelte";

const mockPlanTurn = vi.mocked(api.planTurn);
const mockCommitPlan = vi.mocked(api.commitPlan);

const day = (n: number): DayPlan => ({
  day: n,
  title: `Day ${n} title`,
  focus: `focus ${n}`,
  collocations: ["kava"],
  learning_objective: `obj ${n}`,
  story_guidance: "",
});

function makeCurriculum(overrides: Partial<CurriculumSummary> = {}): CurriculumSummary {
  return {
    id: "trip-1",
    topic: "Visiting Ljubljana",
    language_code: "sl",
    cefr_level: "A2",
    days: [],
    proposed: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("/c/[curriculumId]/plan page", () => {
  it("renders topic, CEFR level, and a back link to the curriculum", () => {
    const { getByText, container } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    expect(getByText("Visiting Ljubljana")).toBeTruthy();
    expect(getByText(/A2/)).toBeTruthy();
    expect(container.querySelector('a[href="/c/trip-1"]')).toBeTruthy();
  });

  it("shows a context event line when days are already committed", () => {
    const { getByText } = render(Page, {
      props: { data: { curriculum: makeCurriculum({ days: [day(1), day(2)] }) } },
    });
    expect(getByText(/2 days committed so far/i)).toBeTruthy();
  });

  it("no context line for a fresh curriculum", () => {
    const { queryByText } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    expect(queryByText(/committed so far/i)).toBeNull();
  });

  it("sending a message appends the turn and renders the proposal", async () => {
    const proposed: ProposedBatch = { start_day: 1, days: [day(1)] };
    mockPlanTurn.mockResolvedValue({ reply: "How about this?", proposed });

    const { getByText, getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan my trip" },
    });
    await fireEvent.click(getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(getByText("How about this?")).toBeTruthy();
      expect(getByText("plan my trip")).toBeTruthy();
      expect(getByText(/proposed: day 1/i)).toBeTruthy();
    });
    expect(mockPlanTurn).toHaveBeenCalledWith("trip-1", "plan my trip", 5);
  });

  it("a pure-chat turn keeps the existing proposal on screen", async () => {
    const existing: ProposedBatch = { start_day: 1, days: [day(1)] };
    mockPlanTurn.mockResolvedValue({ reply: "Sure, tell me more", proposed: existing });

    const { getByText, getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeCurriculum({ proposed: existing }) } },
    });
    expect(getByText(/proposed: day 1/i)).toBeTruthy();

    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "hmm" },
    });
    await fireEvent.click(getByRole("button", { name: "Send" }));

    await waitFor(() => expect(getByText("Sure, tell me more")).toBeTruthy());
    expect(getByText(/proposed: day 1/i)).toBeTruthy();
  });

  it("turn failure shows the error and appends nothing", async () => {
    mockPlanTurn.mockRejectedValue(new Error("POST …/turn: Expected 5 days, got 1"));

    const { getByText, getByRole, getByPlaceholderText, queryByText, container } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan" },
    });
    await fireEvent.click(getByRole("button", { name: "Send" }));

    await waitFor(() => expect(getByText(/expected 5 days/i)).toBeTruthy());
    expect(queryByText(/proposed/i)).toBeNull();
    expect(container.querySelectorAll(".msg-user")).toHaveLength(0);
  });

  it("commit appends the event line, clears the proposal, updates the day count", async () => {
    const proposed: ProposedBatch = { start_day: 1, days: [day(1), day(2), day(3)] };
    mockCommitPlan.mockResolvedValue({ id: "trip-1", days: 3 });

    const { getByText, getByRole, queryByText } = render(Page, {
      props: { data: { curriculum: makeCurriculum({ proposed }) } },
    });
    await fireEvent.click(getByRole("button", { name: /commit batch/i }));

    await waitFor(() => {
      expect(getByText("Committed days 1-3.")).toBeTruthy();
      expect(queryByText(/proposed: days 1–3/i)).toBeNull();
    });
    expect(mockCommitPlan).toHaveBeenCalledWith("trip-1");
    expect(getByText(/3 days committed/i)).toBeTruthy();
  });

  it("commit failure surfaces the error and keeps the proposal", async () => {
    const proposed: ProposedBatch = { start_day: 1, days: [day(1)] };
    mockCommitPlan.mockRejectedValue(new Error("POST …/commit: No proposed batch to commit"));

    const { getByText, getByRole } = render(Page, {
      props: { data: { curriculum: makeCurriculum({ proposed }) } },
    });
    await fireEvent.click(getByRole("button", { name: /commit batch/i }));

    await waitFor(() => expect(getByText(/no proposed batch/i)).toBeTruthy());
    expect(getByText(/proposed: day 1/i)).toBeTruthy();
  });

  it("Revise focuses the chat input", async () => {
    const proposed: ProposedBatch = { start_day: 1, days: [day(1)] };
    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeCurriculum({ proposed }) } },
    });
    await fireEvent.click(getByRole("button", { name: /revise/i }));
    expect(document.activeElement).toBe(getByPlaceholderText(/message the planner/i));
  });
});
