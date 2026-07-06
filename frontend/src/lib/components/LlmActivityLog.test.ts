import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/svelte";
import LlmActivityLog from "./LlmActivityLog.svelte";
import type { ActivityEvent } from "$lib/api";

const PIPELINE_EVENTS: ActivityEvent[] = [
  {
    seq: 1,
    timestamp: 1000,
    kind: "pipeline",
    curriculum_id: "cid-1",
    day: 1,
    state: "queued",
    message: "enqueued",
  },
  {
    seq: 2,
    timestamp: 1001,
    kind: "pipeline",
    curriculum_id: "cid-1",
    day: 1,
    state: "generating",
    message: "generating story",
  },
];

const LLM_EVENTS: ActivityEvent[] = [
  {
    seq: 3,
    timestamp: 1002,
    kind: "llm_call",
    provider: "groq",
    model: "llama",
    latency_ms: 500,
    status: "success",
    is_fallback: false,
    prompt_preview: "generate story",
    response_preview: "story content",
    rate_limits: null,
    reasoning_effort: null,
  },
  {
    seq: 4,
    timestamp: 1003,
    kind: "llm_call",
    provider: "groq",
    model: "llama",
    latency_ms: 0,
    status: "429",
    is_fallback: false,
    prompt_preview: "",
    response_preview: "",
    rate_limits: { tokens_remaining: 0 },
    reasoning_effort: null,
  },
];

const MOCK = { llm_mode: "mock" };
const LIVE = { llm_mode: "live" };

describe("LlmActivityLog", () => {
  it("shows mock-mode message when llm_mode is mock and events empty", () => {
    const { getByText } = render(LlmActivityLog, {
      props: { events: [], currentLine: "", rateLimitStatus: MOCK as never },
    });
    expect(getByText(/Mock mode/)).toBeTruthy();
  });

  it("shows empty message when live but no events", () => {
    const { getByText } = render(LlmActivityLog, {
      props: { events: [], currentLine: "", rateLimitStatus: LIVE as never },
    });
    expect(getByText(/LLM activity/)).toBeTruthy();
  });

  it("shows currentLine when events exist", () => {
    const { container } = render(LlmActivityLog, {
      props: {
        events: PIPELINE_EVENTS,
        currentLine: "[pipeline] day 1: generating — generating story",
        rateLimitStatus: LIVE as never,
      },
    });
    const line = container.querySelector(".current-line");
    expect(line?.textContent).toContain("generating story");
  });

  it("expands to show event list in details", () => {
    const { container } = render(LlmActivityLog, {
      props: {
        events: PIPELINE_EVENTS,
        currentLine: "[pipeline] day 1: queued — enqueued",
        rateLimitStatus: LIVE as never,
      },
    });
    const details = container.querySelector("details");
    expect(details).toBeTruthy();
  });

  it("renders pipeline events in the log list with correct text", () => {
    const { container } = render(LlmActivityLog, {
      props: { events: PIPELINE_EVENTS, currentLine: "", rateLimitStatus: LIVE as never },
    });
    expect(container.textContent).toContain("generating story");
    expect(container.textContent).toContain("enqueued");
  });

  it("renders llm_call events with provider and status", () => {
    const { container } = render(LlmActivityLog, {
      props: { events: LLM_EVENTS, currentLine: "", rateLimitStatus: LIVE as never },
    });
    expect(container.textContent).toContain("groq");
    expect(container.textContent).toContain("429");
  });
});
