import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("$lib/api", () => ({
  api: { fetchQueueStats: vi.fn() },
}));

import { api } from "$lib/api";
import { queueStatsStore } from "./queueStats.svelte";

const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);

const STATS = {
  new: 5,
  learning: 2,
  review: 3,
  daily_new_cap: 20,
  cap_source: "cache" as const,
  fsrs_source: "cache" as const,
};

beforeEach(() => {
  vi.clearAllMocks();
  queueStatsStore.set(null); // reset the singleton between tests
});

describe("queueStatsStore", () => {
  it("starts empty", () => {
    expect(queueStatsStore.stats).toBeNull();
  });

  it("set() stores the value", () => {
    queueStatsStore.set(STATS);
    expect(queueStatsStore.stats).toEqual(STATS);
  });

  it("refresh() pulls from the API", async () => {
    mockFetchQueueStats.mockResolvedValue(STATS);
    await queueStatsStore.refresh();
    expect(queueStatsStore.stats).toEqual(STATS);
  });

  it("refresh() keeps the last-known value when the API rejects", async () => {
    queueStatsStore.set(STATS);
    mockFetchQueueStats.mockRejectedValue(new Error("offline"));
    await queueStatsStore.refresh();
    expect(queueStatsStore.stats).toEqual(STATS);
  });
});
