import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("$lib/api", () => ({
  api: { getRateLimit: vi.fn(), probeRateLimit: vi.fn() },
}));

import { api } from "$lib/api";
import { rateLimitStore } from "./rateLimit.svelte";
import type { RateLimitStatus } from "$lib/api";

const mockGetRateLimit = vi.mocked(api.getRateLimit);
const mockProbeRateLimit = vi.mocked(api.probeRateLimit);

const STATUS: RateLimitStatus = {
  provider: "groq",
  model: "openai/gpt-oss-120b",
  llm_mode: "live",
  snapshot: {
    age_s: 12.3,
    requests_limit: 1000,
    requests_remaining: 999,
    requests_reset_in_s: 86.4,
    tokens_limit: 8000,
    tokens_remaining: 7927,
    tokens_reset_in_s: 0.5,
  },
  last_429: null,
  tokens_used_24h: 73,
  tokens_per_day_limit: 100000,
};

beforeEach(() => {
  vi.clearAllMocks();
  rateLimitStore.set(null);
});

describe("rateLimitStore", () => {
  it("starts empty", () => {
    expect(rateLimitStore.status).toBeNull();
    expect(rateLimitStore.probeError).toBe("");
  });

  it("set() stores the value", () => {
    rateLimitStore.set(STATUS);
    expect(rateLimitStore.status).toEqual(STATUS);
  });

  it("refresh() pulls from getRateLimit", async () => {
    mockGetRateLimit.mockResolvedValue(STATUS);
    await rateLimitStore.refresh();
    expect(rateLimitStore.status).toEqual(STATUS);
  });

  it("refresh() keeps last-known value on error", async () => {
    rateLimitStore.set(STATUS);
    mockGetRateLimit.mockRejectedValue(new Error("offline"));
    await rateLimitStore.refresh();
    expect(rateLimitStore.status).toEqual(STATUS);
  });

  it("refresh() clears probeError", async () => {
    rateLimitStore.set(STATUS);
    mockGetRateLimit.mockResolvedValue(STATUS);
    await rateLimitStore.refresh();
    expect(rateLimitStore.probeError).toBe("");
  });

  it("probe() calls probeRateLimit and sets status", async () => {
    mockProbeRateLimit.mockResolvedValue(STATUS);
    await rateLimitStore.probe();
    expect(rateLimitStore.status).toEqual(STATUS);
  });

  it("probe() sets probeError on failure", async () => {
    mockProbeRateLimit.mockRejectedValue(new Error("API key missing"));
    await rateLimitStore.probe();
    expect(rateLimitStore.probeError).toBe("API key missing");
  });

  it("probe() stringifies a non-Error rejection", async () => {
    mockProbeRateLimit.mockRejectedValue("plain string failure");
    await rateLimitStore.probe();
    expect(rateLimitStore.probeError).toBe("plain string failure");
  });

  it("probe() does not clear status on failure", async () => {
    rateLimitStore.set(STATUS);
    mockProbeRateLimit.mockRejectedValue(new Error("offline"));
    await rateLimitStore.probe();
    expect(rateLimitStore.status).toEqual(STATUS);
  });
});
