import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("$lib/api", () => ({
  api: { getLlmActivity: vi.fn() },
}));

import { api } from "$lib/api";
import { llmActivityStore } from "./llmActivity.svelte";

const mockGetLlmActivity = vi.mocked(api.getLlmActivity);

const PIPELINE_EVENT = {
  seq: 1,
  timestamp: 1000,
  kind: "pipeline" as const,
  curriculum_id: "cid-1",
  day: 1,
  state: "queued",
  message: "enqueued",
};

const LLM_EVENT = {
  seq: 2,
  timestamp: 1001,
  kind: "llm_call" as const,
  provider: "groq",
  model: "llama",
  latency_ms: 500,
  status: "success",
  is_fallback: false,
  prompt_preview: "generate story for day 1",
  response_preview: "story content",
  rate_limits: null,
  reasoning_effort: null,
};

const FAIL_EVENT = {
  seq: 3,
  timestamp: 1002,
  kind: "llm_call" as const,
  provider: "groq",
  model: "llama",
  latency_ms: 0,
  status: "429",
  is_fallback: false,
  prompt_preview: "",
  response_preview: "",
  rate_limits: { tokens_remaining: 0 },
  reasoning_effort: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  llmActivityStore.reset();
});

describe("llmActivityStore", () => {
  it("starts empty", () => {
    expect(llmActivityStore.events).toEqual([]);
    expect(llmActivityStore.latestSeq).toBe(0);
    expect(llmActivityStore.currentLine).toBe("");
  });

  it("refresh() fetches and accumulates events", async () => {
    mockGetLlmActivity.mockResolvedValue({ latest: 2, events: [PIPELINE_EVENT, LLM_EVENT] });
    await llmActivityStore.refresh();
    expect(llmActivityStore.events).toHaveLength(2);
    expect(llmActivityStore.latestSeq).toBe(2);
  });

  it("passes since cursor on subsequent refreshes", async () => {
    mockGetLlmActivity
      .mockResolvedValueOnce({ latest: 2, events: [PIPELINE_EVENT, LLM_EVENT] })
      .mockResolvedValueOnce({ latest: 3, events: [FAIL_EVENT] });

    await llmActivityStore.refresh();
    expect(mockGetLlmActivity).toHaveBeenCalledWith(undefined);

    await llmActivityStore.refresh();
    expect(mockGetLlmActivity).toHaveBeenCalledWith(2);
    expect(llmActivityStore.events).toHaveLength(3);
  });

  it("caps retained events at 100", async () => {
    const manyEvents = Array.from({ length: 150 }, (_, i) => ({
      ...PIPELINE_EVENT,
      seq: i + 1,
      day: (i % 5) + 1,
      message: `event ${i + 1}`,
    }));
    mockGetLlmActivity.mockResolvedValue({ latest: 150, events: manyEvents });
    await llmActivityStore.refresh();
    expect(llmActivityStore.events).toHaveLength(100);
    expect(llmActivityStore.events[0].seq).toBe(51); // last 100
  });

  it("currentLine returns latest pipeline event as string", async () => {
    mockGetLlmActivity.mockResolvedValue({ latest: 1, events: [PIPELINE_EVENT] });
    await llmActivityStore.refresh();
    expect(llmActivityStore.currentLine).toContain("pipeline");
    expect(llmActivityStore.currentLine).toContain("day 1");
    expect(llmActivityStore.currentLine).toContain("queued");
  });

  it("currentLine returns latest llm_call event as string", async () => {
    mockGetLlmActivity.mockResolvedValue({ latest: 2, events: [LLM_EVENT] });
    await llmActivityStore.refresh();
    expect(llmActivityStore.currentLine).toContain("llm");
    expect(llmActivityStore.currentLine).toContain("groq/llama");
    expect(llmActivityStore.currentLine).toContain("success");
    expect(llmActivityStore.currentLine).toContain("500ms");
  });

  it("refresh silently degrades on error", async () => {
    mockGetLlmActivity.mockRejectedValue(new Error("offline"));
    await llmActivityStore.refresh();
    expect(llmActivityStore.events).toEqual([]);
  });

  it("reset() clears all state", async () => {
    mockGetLlmActivity.mockResolvedValue({ latest: 2, events: [PIPELINE_EVENT, LLM_EVENT] });
    await llmActivityStore.refresh();
    expect(llmActivityStore.events).toHaveLength(2);

    llmActivityStore.reset();
    expect(llmActivityStore.events).toEqual([]);
    expect(llmActivityStore.latestSeq).toBe(0);
    expect(llmActivityStore.currentLine).toBe("");
  });

  it("a re-sent seq is not appended twice (duplicate keys crash the keyed each)", async () => {
    mockGetLlmActivity.mockResolvedValue({ latest: 1, events: [PIPELINE_EVENT] });
    await llmActivityStore.refresh();
    await llmActivityStore.refresh(); // server replays the same event
    expect(llmActivityStore.events).toHaveLength(1);
    expect(llmActivityStore.latestSeq).toBe(1);
  });

  it("a server seq reset (backend restart) clears and re-accumulates", async () => {
    mockGetLlmActivity
      .mockResolvedValueOnce({ latest: 3, events: [FAIL_EVENT] })
      .mockResolvedValueOnce({ latest: 1, events: [PIPELINE_EVENT] });

    await llmActivityStore.refresh();
    expect(llmActivityStore.latestSeq).toBe(3);

    await llmActivityStore.refresh(); // latest went backwards → fresh seq space
    expect(llmActivityStore.events).toEqual([PIPELINE_EVENT]);
    expect(llmActivityStore.latestSeq).toBe(1);
  });
});
