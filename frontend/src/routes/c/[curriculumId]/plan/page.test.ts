import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  api: {
    planTurn: vi.fn(),
    commitPlan: vi.fn(),
    resetPlanChat: vi.fn(),
    getPipeline: vi.fn(),
    getLlmActivity: vi.fn().mockResolvedValue({ latest: 0, events: [] }),
    retryPipelineDay: vi.fn(),
    setGenerationMode: vi.fn(),
    getPlanTurnPrompt: vi.fn(),
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
    ensureFresh: vi.fn(),
  },
}));

// Keep RateLimitWidget mocked (deeply nested store)
vi.mock("$lib/components/RateLimitWidget.svelte", () => ({
  default: (_props: unknown) => "<span>LLM —</span>",
}));

import { api } from "$lib/api";
import { pipelineStore } from "$lib/stores/pipeline.svelte";
import { rateLimitStore } from "$lib/stores/rateLimit.svelte";
import { llmActivityStore } from "$lib/stores/llmActivity.svelte";
import type { CurriculumSummary, DayPlan, ProposedBatch } from "$lib/api";
import Page from "./+page.svelte";

const mockPlanTurn = vi.mocked(api.planTurn);
const mockCommitPlan = vi.mocked(api.commitPlan);
const mockResetPlanChat = vi.mocked(api.resetPlanChat);
const mockPipelineStoreStart = vi.mocked(pipelineStore.start);

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

  it("ensures rate-limit store is fresh on mount, starts pipeline store, and refreshes after each planner turn", async () => {
    const ensureFresh = vi.mocked(rateLimitStore.ensureFresh);
    const refresh = vi.mocked(rateLimitStore.refresh);
    const llmRefresh = vi.mocked(llmActivityStore.refresh);
    mockPlanTurn.mockResolvedValue({ reply: "ok", proposed: null });

    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    await waitFor(() => {
      expect(ensureFresh).toHaveBeenCalledTimes(1); // mount
      expect(mockPipelineStoreStart).toHaveBeenCalledWith("trip-1");
    });
    expect(refresh).not.toHaveBeenCalled(); // mount uses ensureFresh, not refresh

    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan my trip" },
    });
    await fireEvent.click(getByRole("button", { name: "Send" }));
    await waitFor(() => {
      expect(refresh).toHaveBeenCalledTimes(1); // after the turn
      expect(llmRefresh).toHaveBeenCalledTimes(1);
    });
    // ensureFresh still only once — we do NOT touch post-turn calls
    expect(ensureFresh).toHaveBeenCalledTimes(1);
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
    // Called from onMount + from handleCommit
    expect(mockPipelineStoreStart).toHaveBeenCalledTimes(2);
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
    // Called once from onMount; the failed commit does not add a second call
    expect(mockPipelineStoreStart).toHaveBeenCalledTimes(1);
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

  it("renders LlmActivityLog unconditionally (pipeline status null)", () => {
    // pipelineStore.status is null by default → showPipeline is false
    const { getByText } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    // LlmActivityLog renders its empty state
    expect(getByText("No LLM activity yet")).toBeTruthy();
    // PipelineCard is NOT rendered (no non-ready days)
    expect(getByText("LLM Activity")).toBeTruthy();
  });

  describe("reset chat", () => {
    it("first click shows Confirm reset, does not call the API", async () => {
      const { getByRole, queryByText } = render(Page, {
        props: { data: { curriculum: makeCurriculum() } },
      });

      const btn = getByRole("button", { name: "Reset chat" });
      await fireEvent.click(btn);

      expect(getByRole("button", { name: "Confirm reset" })).toBeTruthy();
      expect(mockResetPlanChat).not.toHaveBeenCalled();
      // Proposed area should not be rendered (no proposal)
      expect(queryByText(/proposed/i)).toBeNull();
    });

    it("second click calls the API, clears messages and proposed", async () => {
      mockResetPlanChat.mockResolvedValue({ reply_count_cleared: 2 });
      const proposed: ProposedBatch = { start_day: 1, days: [day(1)] };

      const { getByRole, queryByText, container } = render(Page, {
        props: {
          data: {
            curriculum: makeCurriculum({
              days: [day(1)],
              proposed,
            }),
          },
        },
      });

      // First click: arm
      await fireEvent.click(getByRole("button", { name: "Reset chat" }));
      // Second click: fire
      await fireEvent.click(getByRole("button", { name: "Confirm reset" }));

      await waitFor(() => {
        expect(mockResetPlanChat).toHaveBeenCalledWith("trip-1");
        // Proposed area gone
        expect(queryByText(/proposed/i)).toBeNull();
        // Messages cleared (no event line)
        expect(queryByText(/1 days committed so far/i)).toBeNull();
        // Error area is not present
        expect(container.querySelector(".error")).toBeNull();
      });
    });

    it("blur reverts the button text to Reset chat", async () => {
      const { getByRole } = render(Page, {
        props: { data: { curriculum: makeCurriculum() } },
      });

      await fireEvent.click(getByRole("button", { name: "Reset chat" }));
      expect(getByRole("button", { name: "Confirm reset" })).toBeTruthy();

      await fireEvent.blur(getByRole("button", { name: "Confirm reset" }));

      expect(getByRole("button", { name: "Reset chat" })).toBeTruthy();
    });

    it("API failure surfaces the error and reverts the button", async () => {
      mockResetPlanChat.mockRejectedValue(new Error("POST …/reset: Not found"));

      const { getByRole, getByText } = render(Page, {
        props: { data: { curriculum: makeCurriculum() } },
      });

      await fireEvent.click(getByRole("button", { name: "Reset chat" }));
      await fireEvent.click(getByRole("button", { name: "Confirm reset" }));

      await waitFor(() => {
        expect(getByText(/not found/i)).toBeTruthy();
      });
      // Button reverted
      expect(getByRole("button", { name: "Reset chat" })).toBeTruthy();
    });
  });
});

describe("manual mode", () => {
  const mockSetGenerationMode = vi.mocked(api.setGenerationMode);
  const mockGetPlanTurnPrompt = vi.mocked(api.getPlanTurnPrompt);
  const mockClipboard = { writeText: vi.fn().mockResolvedValue(undefined) };

  function makeManualCurriculum(overrides: Partial<CurriculumSummary> = {}): CurriculumSummary {
    return {
      id: "trip-1",
      topic: "Visiting Ljubljana",
      language_code: "sl",
      cefr_level: "A2",
      days: [],
      proposed: null,
      generation_mode: "manual",
      ...overrides,
    };
  }

  beforeEach(() => {
    vi.clearAllMocks();
    mockClipboard.writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: mockClipboard,
      configurable: true,
      writable: true,
    });
  });

  it("renders mode toggle showing Auto when in manual mode", () => {
    const { getByRole } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });
    // Toggle shows the target mode: in manual mode, button says "Auto" (click to switch)
    expect(getByRole("button", { name: /auto/i })).toBeTruthy();
  });

  it("mode toggle calls setGenerationMode and switches to auto", async () => {
    mockSetGenerationMode.mockResolvedValue({ mode: "auto" });

    const { getByRole } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    // Click the mode toggle to switch to auto
    const toggle = getByRole("button", { name: /auto/i });
    await fireEvent.click(toggle);

    await waitFor(() => {
      expect(mockSetGenerationMode).toHaveBeenCalledWith("trip-1", "auto");
    });
  });

  it("Copy prompt button calls getPlanTurnPrompt and copies to clipboard", async () => {
    mockGetPlanTurnPrompt.mockResolvedValue({
      system_prompt: "You are a planner",
      user_prompt: "Plan 5 days about coffee",
    });

    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    // Type a message
    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "Plan 5 days about coffee" },
    });

    // Click Copy prompt
    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));

    await waitFor(() => {
      expect(mockGetPlanTurnPrompt).toHaveBeenCalledWith("trip-1", "Plan 5 days about coffee", 5);
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining("You are a planner"),
      );
    });
  });

  it("paste textarea submits planTurn with pasted_response", async () => {
    vi.mocked(api.planTurn).mockResolvedValue({
      reply: "Here are the days",
      proposed: { start_day: 1, days: [day(1)] },
    });
    mockGetPlanTurnPrompt.mockResolvedValue({
      system_prompt: "sys",
      user_prompt: "plan 1 day",
    });

    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    // Type a message
    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan 1 day" },
    });

    // Click Copy prompt
    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));

    // Wait for paste textarea and type into it
    await waitFor(() => {
      expect(getByPlaceholderText(/paste claude/i)).toBeTruthy();
    });
    const pasteTextarea = getByPlaceholderText(/paste claude/i);
    await fireEvent.input(pasteTextarea, {
      target: { value: "Here are the days" },
    });

    // The submit button should now be enabled — click it
    const submitBtn = getByRole("button", { name: /submit reply/i });
    expect(submitBtn).not.toBeNull();
    await fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.planTurn).toHaveBeenCalled();
    });
  });

  it("freezes the message and batch-size inputs between Copy prompt and submit", async () => {
    mockGetPlanTurnPrompt.mockResolvedValue({
      system_prompt: "sys",
      user_prompt: "plan 5 days about coffee",
    });

    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    const messageBox = getByPlaceholderText(/message the planner/i);
    const batchInput = getByRole("spinbutton");
    await fireEvent.input(messageBox, { target: { value: "plan 5 days about coffee" } });

    // Before copy: both editable.
    expect((messageBox as HTMLTextAreaElement).disabled).toBe(false);
    expect((batchInput as HTMLInputElement).disabled).toBe(false);

    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));

    // After copy: frozen so the pasted reply can't diverge from the copied prompt.
    await waitFor(() => {
      expect((messageBox as HTMLTextAreaElement).disabled).toBe(true);
    });
    expect((batchInput as HTMLInputElement).disabled).toBe(true);
    expect((getByRole("button", { name: /copy prompt/i }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("Edit message re-opens the inputs and clears the paste area", async () => {
    mockGetPlanTurnPrompt.mockResolvedValue({
      system_prompt: "sys",
      user_prompt: "plan 5 days about coffee",
    });

    const { getByRole, getByPlaceholderText, queryByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    const messageBox = getByPlaceholderText(/message the planner/i);
    await fireEvent.input(messageBox, { target: { value: "plan 5 days about coffee" } });
    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));

    // Put text in the paste box, then bail out via Edit message.
    await waitFor(() => expect(getByPlaceholderText(/paste claude/i)).toBeTruthy());
    await fireEvent.input(getByPlaceholderText(/paste claude/i), {
      target: { value: "half-typed reply" },
    });
    await fireEvent.click(getByRole("button", { name: /edit message/i }));

    // Paste area is gone and the message input is editable again.
    await waitFor(() => {
      expect(queryByPlaceholderText(/paste claude/i)).toBeNull();
    });
    expect((messageBox as HTMLTextAreaElement).disabled).toBe(false);
  });

  it("mode toggle is absent for auto mode curriculum", () => {
    const { queryByRole } = render(Page, {
      props: { data: { curriculum: makeCurriculum() } },
    });
    // Auto mode should not show a mode toggle button
    expect(queryByRole("button", { name: /auto/i })).toBeNull();
  });

  it("batch size input changes batch size in manual mode", async () => {
    mockGetPlanTurnPrompt.mockResolvedValue({
      system_prompt: "sys",
      user_prompt: "plan 3 days",
    });

    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    // Change batch size
    const batchInput = getByRole("spinbutton");
    await fireEvent.input(batchInput, { target: { value: "3" } });

    // Type a message and copy prompt
    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan 3 days" },
    });
    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));

    await waitFor(() => {
      expect(mockGetPlanTurnPrompt).toHaveBeenCalledWith("trip-1", "plan 3 days", 3);
    });
  });

  it("setGenerationMode failure shows error", async () => {
    mockSetGenerationMode.mockRejectedValue(new Error("POST …/generation-mode: Not found"));

    const { getByText, getByRole } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    await fireEvent.click(getByRole("button", { name: /auto/i }));

    await waitFor(() => {
      expect(getByText(/not found/i)).toBeTruthy();
    });
  });

  it("getPlanTurnPrompt failure shows error", async () => {
    mockGetPlanTurnPrompt.mockRejectedValue(new Error("POST …/prompt: Not found"));

    const { getByText, getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan 1 day" },
    });
    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));

    await waitFor(() => {
      expect(getByText(/not found/i)).toBeTruthy();
    });
  });

  it("planTurn failure in paste mode shows error", async () => {
    mockGetPlanTurnPrompt.mockResolvedValue({
      system_prompt: "sys",
      user_prompt: "plan 1 day",
    });
    vi.mocked(api.planTurn).mockRejectedValue(new Error("POST …/turn: Expected 5 days, got 1"));

    const { getByText, getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan 1 day" },
    });
    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));

    await waitFor(() => {
      expect(getByPlaceholderText(/paste claude/i)).toBeTruthy();
    });
    const pasteTextarea = getByPlaceholderText(/paste claude/i);
    await fireEvent.input(pasteTextarea, {
      target: { value: "Here are the days" },
    });
    await fireEvent.click(getByRole("button", { name: /submit reply/i }));

    await waitFor(() => {
      expect(getByText(/expected 5 days/i)).toBeTruthy();
    });
    // The paste box lets you retry — its contents survive the error instead of
    // being cleared, unlike the success branch of handlePasteSubmit.
    expect((pasteTextarea as HTMLTextAreaElement).value).toBe("Here are the days");
  });

  it("manual mode shows the chat transcript after successful paste submit", async () => {
    mockGetPlanTurnPrompt.mockResolvedValue({
      system_prompt: "sys",
      user_prompt: "plan 1 day",
    });
    vi.mocked(api.planTurn).mockResolvedValue({
      reply: "Here is the plan",
      proposed: { start_day: 1, days: [day(1)] },
    });

    const { getByText, getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan 1 day" },
    });
    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));

    await waitFor(() => {
      expect(getByPlaceholderText(/paste claude/i)).toBeTruthy();
    });
    await fireEvent.input(getByPlaceholderText(/paste claude/i), {
      target: { value: "Here is the plan" },
    });
    await fireEvent.click(getByRole("button", { name: /submit reply/i }));

    await waitFor(() => {
      expect(getByText("plan 1 day")).toBeTruthy();
      expect(getByText("Here is the plan")).toBeTruthy();
    });
  });

  it("Revise in manual mode focuses the manual textarea", async () => {
    mockGetPlanTurnPrompt.mockResolvedValue({
      system_prompt: "sys",
      user_prompt: "plan 1 day",
    });
    vi.mocked(api.planTurn).mockResolvedValue({
      reply: "Here is the plan",
      proposed: { start_day: 1, days: [day(1)] },
    });

    const { getByRole, getByPlaceholderText } = render(Page, {
      props: { data: { curriculum: makeManualCurriculum() } },
    });

    // Submit a paste to get a proposal on screen
    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "plan 1 day" },
    });
    await fireEvent.click(getByRole("button", { name: /copy prompt/i }));
    await waitFor(() => {
      expect(getByPlaceholderText(/paste claude/i)).toBeTruthy();
    });
    await fireEvent.input(getByPlaceholderText(/paste claude/i), {
      target: { value: "Here is the plan" },
    });
    await fireEvent.click(getByRole("button", { name: /submit reply/i }));
    await waitFor(() => {
      expect(getByRole("button", { name: /revise/i })).toBeTruthy();
    });

    // Click Revise — should focus the manual message textarea
    await fireEvent.click(getByRole("button", { name: /revise/i }));
    expect(document.activeElement).toBe(getByPlaceholderText(/message the planner/i));
  });
});
