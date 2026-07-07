import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("$lib/api", () => ({
  api: { getPipeline: vi.fn() },
}));

vi.mock("$lib/stores/rateLimit.svelte", () => ({
  rateLimitStore: { refresh: vi.fn() },
}));

vi.mock("$lib/stores/llmActivity.svelte", () => ({
  llmActivityStore: { refresh: vi.fn() },
}));

import { api } from "$lib/api";
import { pipelineStore } from "./pipeline.svelte";
import { rateLimitStore } from "$lib/stores/rateLimit.svelte";
import { llmActivityStore } from "$lib/stores/llmActivity.svelte";

const mockGetPipeline = vi.mocked(api.getPipeline);

const ACTIVE_STATUS = {
  active: true,
  days: [
    {
      day: 1,
      state: "generating" as const,
      lesson_id: null,
      has_audio: false,
      error: null,
      retryable: true,
      detail: "attempt 1/4",
    },
    {
      day: 2,
      state: "queued" as const,
      lesson_id: null,
      has_audio: false,
      error: null,
      retryable: true,
      detail: null,
    },
  ],
};

const IDLE_STATUS = {
  active: false,
  days: [
    {
      day: 1,
      state: "ready" as const,
      lesson_id: "l1",
      has_audio: true,
      error: null,
      retryable: false,
      detail: null,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  pipelineStore.stop();
});

afterEach(() => {
  pipelineStore.stop();
});

describe("pipelineStore", () => {
  it("starts with null status and empty error", () => {
    expect(pipelineStore.status).toBeNull();
    expect(pipelineStore.error).toBe("");
  });

  it("start() fetches pipeline immediately", async () => {
    mockGetPipeline.mockResolvedValue(ACTIVE_STATUS);
    pipelineStore.start("cid-1");
    await vi.waitFor(() => {
      expect(pipelineStore.status).toEqual(ACTIVE_STATUS);
    });
  });

  it("start() sets error on fetch failure", async () => {
    mockGetPipeline.mockRejectedValue(new Error("network error"));
    pipelineStore.start("cid-1");
    await vi.waitFor(() => {
      expect(pipelineStore.error).toBe("network error");
    });
  });

  it("stop() clears status and error", () => {
    pipelineStore.stop();
    expect(pipelineStore.status).toBeNull();
    expect(pipelineStore.error).toBe("");
  });

  it("polls every 2s while active using fake timers", async () => {
    vi.useFakeTimers();
    try {
      mockGetPipeline.mockResolvedValue(ACTIVE_STATUS);
      pipelineStore.start("cid-1");
      await vi.advanceTimersByTimeAsync(0); // initial poll

      expect(mockGetPipeline).toHaveBeenCalledTimes(1);

      await vi.advanceTimersByTimeAsync(2000);
      expect(mockGetPipeline).toHaveBeenCalledTimes(2);

      await vi.advanceTimersByTimeAsync(2000);
      expect(mockGetPipeline).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refreshes rateLimit and activity stores every poll (active or idle)", async () => {
    mockGetPipeline.mockResolvedValue(ACTIVE_STATUS);
    pipelineStore.start("cid-1");
    await vi.waitFor(() => {
      expect(rateLimitStore.refresh).toHaveBeenCalled();
      expect(llmActivityStore.refresh).toHaveBeenCalled();
    });
  });

  it("idle poll still refreshes both stores", async () => {
    mockGetPipeline.mockResolvedValue(IDLE_STATUS);
    pipelineStore.start("cid-1");
    await vi.waitFor(() => {
      expect(rateLimitStore.refresh).toHaveBeenCalled();
      expect(llmActivityStore.refresh).toHaveBeenCalled();
    });
  });

  it("decays to 10s when idle", async () => {
    vi.useFakeTimers();
    try {
      mockGetPipeline.mockResolvedValueOnce(ACTIVE_STATUS).mockResolvedValueOnce(IDLE_STATUS);
      pipelineStore.start("cid-1");
      await vi.advanceTimersByTimeAsync(0); // initial poll

      expect(mockGetPipeline).toHaveBeenCalledTimes(1);

      // 2s later, poll returns idle status
      await vi.advanceTimersByTimeAsync(2000);
      expect(mockGetPipeline).toHaveBeenCalledTimes(2);

      // Next poll should be after 10s
      await vi.advanceTimersByTimeAsync(10000);
      expect(mockGetPipeline).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("cleanup: stop() prevents further polling", async () => {
    vi.useFakeTimers();
    try {
      mockGetPipeline.mockResolvedValue(ACTIVE_STATUS);
      pipelineStore.start("cid-1");
      await vi.advanceTimersByTimeAsync(0);
      expect(mockGetPipeline).toHaveBeenCalledTimes(1);

      pipelineStore.stop();
      await vi.advanceTimersByTimeAsync(10000);
      expect(mockGetPipeline).toHaveBeenCalledTimes(1); // no more calls
    } finally {
      vi.useRealTimers();
    }
  });

  it("start() while already running replaces the old polling loop (no leak)", async () => {
    // Regression: start() used to call bare stop(), which resolved to
    // window.stop() instead of the store's stop — the old timer survived a
    // restart and two polling loops ran side by side.
    vi.useFakeTimers();
    try {
      mockGetPipeline.mockResolvedValue(ACTIVE_STATUS);
      pipelineStore.start("cid-old");
      await vi.advanceTimersByTimeAsync(0); // initial poll for cid-old, 2s timer armed
      expect(mockGetPipeline).toHaveBeenCalledTimes(1);

      pipelineStore.start("cid-new"); // restart WITHOUT an explicit stop()
      await vi.advanceTimersByTimeAsync(0); // initial poll for cid-new
      expect(mockGetPipeline).toHaveBeenCalledTimes(2);

      await vi.advanceTimersByTimeAsync(2000);
      // Exactly one scheduled poll fired, and only for the new id.
      expect(mockGetPipeline).toHaveBeenCalledTimes(3);
      expect(mockGetPipeline.mock.calls.slice(1).every(([id]) => id === "cid-new")).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a poll error landing after stop() does not set error state", async () => {
    vi.useFakeTimers();
    try {
      let rejectPoll!: (e: Error) => void;
      mockGetPipeline.mockReturnValue(
        new Promise((_r, rej) => {
          rejectPoll = rej;
        }),
      );
      pipelineStore.start("cid-1");

      pipelineStore.stop();
      rejectPoll(new Error("late failure")); // in-flight rejection lands after stop
      await vi.advanceTimersByTimeAsync(0);

      expect(pipelineStore.error).toBe("");
      expect(pipelineStore.status).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a poll response landing after stop() does not resurrect status", async () => {
    vi.useFakeTimers();
    try {
      let resolvePoll!: (v: typeof ACTIVE_STATUS) => void;
      mockGetPipeline.mockReturnValue(
        new Promise((r) => {
          resolvePoll = r;
        }),
      );
      pipelineStore.start("cid-1");

      pipelineStore.stop();
      resolvePoll(ACTIVE_STATUS); // in-flight response lands after stop
      await vi.advanceTimersByTimeAsync(0);

      expect(pipelineStore.status).toBeNull();
      // And the .then(scheduleNext) chain must not re-arm polling.
      await vi.advanceTimersByTimeAsync(20000);
      expect(mockGetPipeline).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("can be started again after stop", async () => {
    mockGetPipeline.mockResolvedValue(ACTIVE_STATUS);
    pipelineStore.start("cid-1");
    await vi.waitFor(() => expect(pipelineStore.status).toEqual(ACTIVE_STATUS));

    pipelineStore.stop();
    expect(pipelineStore.status).toBeNull();

    const newStatus = { ...ACTIVE_STATUS, days: [{ ...ACTIVE_STATUS.days[0], day: 3 }] };
    mockGetPipeline.mockResolvedValue(newStatus);
    pipelineStore.start("cid-2");
    await vi.waitFor(() => {
      expect(pipelineStore.status?.days[0].day).toBe(3);
    });
  });
});
