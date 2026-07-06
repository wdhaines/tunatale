import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  api: {
    planTurn: vi.fn(),
    commitPlan: vi.fn(),
    getPipeline: vi.fn(),
    getLlmActivity: vi.fn().mockResolvedValue({ latest: 0, events: [] }),
    retryPipelineDay: vi.fn(),
  },
}));

vi.mock("$lib/stores/pipeline.svelte", () => ({
  pipelineStore: { start: vi.fn(), stop: vi.fn(), status: null, error: "" },
}));

vi.mock("$lib/stores/llmActivity.svelte", () => ({
  llmActivityStore: {
    events: [],
    currentLine: "",
    latestSeq: 0,
    refresh: vi.fn(),
    reset: vi.fn(),
  },
}));

vi.mock("$lib/stores/rateLimit.svelte", () => ({
  rateLimitStore: {
    status: null,
    probeError: "",
    refresh: vi.fn(),
    probe: vi.fn(),
    set: vi.fn(),
  },
}));

// Keep RateLimitWidget mocked (deeply nested store)
vi.mock("$lib/components/RateLimitWidget.svelte", () => ({
  default: ({}) => "<span>LLM —</span>",
}));

import { api } from "$lib/api";
import { pipelineStore } from "$lib/stores/pipeline.svelte";
import { rateLimitStore } from "$lib/stores/rateLimit.svelte";
import type { CurriculumSummary, DayPlan, ProposedBatch } from "$lib/api";
import Page from "./+page.svelte";

const mockPlanTurn = vi.mocked(api.planTurn);
const mockCommitPlan = vi.mocked(api.commitPlan);
const mockPipelineStoreStart = vi.mocked(pipelineStore.start);
const mockPipelineStoreStop = vi.mocked(pipelineStore.stop);

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

  it("refreshes the rate-limit store on mount and after each planner turn", async () => {
    const refresh = vi.mocked(rateLimitStore.refresh);
    mockPlanTurn.mockResolvedValue({ reply: "ok", proposed: null });

    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1)); // mount

    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan my trip" },
    });
    await fireEvent.click(getByRole("button", { name: "Send" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2)); // after the turn
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

  it("commit appends the event line, clears the proposal, updates the day count and starts pipeline", async () => {
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
    expect(mockPipelineStoreStart).toHaveBeenCalledWith("trip-1");
  });

  it("commit failure surfaces the error, keeps the proposal, does not start pipeline", async () => {
    const proposed: ProposedBatch = { start_day: 1, days: [day(1)] };
    mockCommitPlan.mockRejectedValue(new Error("POST …/commit: No proposed batch to commit"));

    const { getByText, getByRole } = render(Page, {
      props: { data: { curriculum: makeCurriculum({ proposed }) } },
    });
    await fireEvent.click(getByRole("button", { name: /commit batch/i }));

    await waitFor(() => expect(getByText(/no proposed batch/i)).toBeTruthy());
    expect(getByText(/proposed: day 1/i)).toBeTruthy();
    expect(mockPipelineStoreStart).not.toHaveBeenCalled();
  });

  it("Revise focuses the chat input", async () => {
    const proposed: ProposedBatch = { start_day: 1, days: [day(1)] };
    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeCurriculum({ proposed }) } },
    });
    await fireEvent.click(getByRole("button", { name: /revise/i }));
    expect(document.activeElement).toBe(getByPlaceholderText(/message the planner/i));
  });

  it("renders PipelineCard and LlmActivityLog when pipeline has non-ready days", () => {
    Object.defineProperty(pipelineStore, "status", {
      value: {
        active: true,
        days: [
          {
            day: 1,
            state: "generating",
            lesson_id: null,
            has_audio: false,
            error: null,
            retryable: true,
            detail: "generating story",
          },
        ],
      },
      configurable: true,
    });
    const { getByText } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    expect(getByText("generating")).toBeTruthy();
    expect(getByText("No LLM activity yet")).toBeTruthy();
  });

  it("Retry on a failed day restarts pipeline polling immediately", async () => {
    Object.defineProperty(pipelineStore, "status", {
      value: {
        active: false,
        days: [
          {
            day: 1,
            state: "failed",
            lesson_id: null,
            has_audio: false,
            error: "boom",
            retryable: true,
            detail: null,
          },
        ],
      },
      configurable: true,
    });
    vi.mocked(api.retryPipelineDay).mockResolvedValue({ status: "queued" });

    const { getByRole } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    await fireEvent.click(getByRole("button", { name: "Retry" }));
    await waitFor(() => {
      expect(api.retryPipelineDay).toHaveBeenCalledWith("trip-1", 1);
      // onRefresh restarts the store so the new state lands without waiting
      // out the 10s idle cadence.
      expect(mockPipelineStoreStart).toHaveBeenCalledWith("trip-1");
    });
  });

  it("renders the current activity line and a Listen link for an already-ready day", async () => {
    Object.defineProperty(pipelineStore, "status", {
      value: {
        active: true,
        days: [
          {
            day: 1,
            state: "ready",
            lesson_id: "l1",
            has_audio: true,
            error: null,
            retryable: null,
            detail: null,
          },
          {
            day: 2,
            state: "rendering",
            lesson_id: null,
            has_audio: false,
            error: null,
            retryable: null,
            detail: null,
          },
        ],
      },
      configurable: true,
    });
    const { llmActivityStore } = await import("$lib/stores/llmActivity.svelte");
    Object.defineProperty(llmActivityStore, "events", {
      value: [
        {
          seq: 2,
          kind: "pipeline",
          timestamp: 1,
          curriculum_id: "trip-1",
          day: 1,
          state: "rendering",
          message: "Rendering audio",
        },
      ],
      configurable: true,
    });
    Object.defineProperty(llmActivityStore, "currentLine", {
      value: "current: day 1 rendering",
      configurable: true,
    });
    try {
      const { getByText, getByRole } = render(Page, {
        props: { data: { curriculum: makeCurriculum() } },
      });
      expect(getByText("rendering")).toBeTruthy();
      expect(getByText("current: day 1 rendering")).toBeTruthy();
      const listen = getByRole("link", { name: /listen/i }) as HTMLAnchorElement;
      expect(listen.getAttribute("href")).toBe("/c/trip-1/l/l1");
    } finally {
      Object.defineProperty(llmActivityStore, "events", { value: [], configurable: true });
      Object.defineProperty(llmActivityStore, "currentLine", { value: "", configurable: true });
    }
  });
});
