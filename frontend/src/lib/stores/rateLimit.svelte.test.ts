import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("$lib/api", () => ({
  api: { getRateLimit: vi.fn(), probeRateLimit: vi.fn() },
}));

import { api } from "$lib/api";
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

const NO_SNAPSHOT_STATUS: RateLimitStatus = {
  provider: "groq",
  model: "openai/gpt-oss-120b",
  llm_mode: "live",
  snapshot: null,
  last_429: null,
  tokens_used_24h: 73,
  tokens_per_day_limit: 100000,
};

const MOCK_MODE_STATUS: RateLimitStatus = {
  provider: "groq",
  model: "openai/gpt-oss-120b",
  llm_mode: "mock",
  snapshot: null,
  last_429: null,
  tokens_used_24h: 73,
  tokens_per_day_limit: 100000,
};

interface RateLimitStore {
  status: RateLimitStatus | null;
  probeError: string;
  set: (next: RateLimitStatus | null) => void;
  refresh: () => Promise<void>;
  probe: () => Promise<void>;
  ensureFresh: () => Promise<void>;
}

let store: RateLimitStore;

beforeEach(async () => {
  vi.clearAllMocks();
  vi.resetModules();
  const mod = await import("./rateLimit.svelte");
  store = mod.rateLimitStore as RateLimitStore;
});

describe("rateLimitStore", () => {
  it("starts empty", () => {
    expect(store.status).toBeNull();
    expect(store.probeError).toBe("");
  });

  it("set() stores the value", () => {
    store.set(STATUS);
    expect(store.status).toEqual(STATUS);
  });

  it("refresh() pulls from getRateLimit", async () => {
    mockGetRateLimit.mockResolvedValue(STATUS);
    await store.refresh();
    expect(store.status).toEqual(STATUS);
  });

  it("refresh() keeps last-known value on error", async () => {
    store.set(STATUS);
    mockGetRateLimit.mockRejectedValue(new Error("offline"));
    await store.refresh();
    expect(store.status).toEqual(STATUS);
  });

  it("refresh() clears probeError", async () => {
    store.set(STATUS);
    mockGetRateLimit.mockResolvedValue(STATUS);
    await store.refresh();
    expect(store.probeError).toBe("");
  });

  it("probe() calls probeRateLimit and sets status", async () => {
    mockProbeRateLimit.mockResolvedValue(STATUS);
    await store.probe();
    expect(store.status).toEqual(STATUS);
  });

  it("probe() sets probeError on failure", async () => {
    mockProbeRateLimit.mockRejectedValue(new Error("API key missing"));
    await store.probe();
    expect(store.probeError).toBe("API key missing");
  });

  it("probe() stringifies a non-Error rejection", async () => {
    mockProbeRateLimit.mockRejectedValue("plain string failure");
    await store.probe();
    expect(store.probeError).toBe("plain string failure");
  });

  it("probe() does not clear status on failure", async () => {
    store.set(STATUS);
    mockProbeRateLimit.mockRejectedValue(new Error("offline"));
    await store.probe();
    expect(store.status).toEqual(STATUS);
  });

  // ── ensureFresh guardrail tests ──────────────────────────────────────

  it("ensureFresh probes exactly once when no snapshot", async () => {
    mockGetRateLimit.mockResolvedValue(NO_SNAPSHOT_STATUS);
    mockProbeRateLimit.mockResolvedValue(STATUS);
    await store.ensureFresh();
    expect(mockProbeRateLimit).toHaveBeenCalledTimes(1);
    expect(store.status).toEqual(STATUS);
  });

  it("second ensureFresh call does NOT probe again", async () => {
    mockGetRateLimit.mockResolvedValue(NO_SNAPSHOT_STATUS);
    mockProbeRateLimit.mockResolvedValue(STATUS);
    await store.ensureFresh();
    expect(mockProbeRateLimit).toHaveBeenCalledTimes(1);

    mockGetRateLimit.mockResolvedValue(STATUS);
    await store.ensureFresh();
    expect(mockProbeRateLimit).toHaveBeenCalledTimes(1);
  });

  it("no probe when refresh returns a populated snapshot", async () => {
    mockGetRateLimit.mockResolvedValue(STATUS);
    await store.ensureFresh();
    expect(mockProbeRateLimit).not.toHaveBeenCalled();
    expect(store.status).toEqual(STATUS);
  });

  it("no probe when llm_mode is mock", async () => {
    mockGetRateLimit.mockResolvedValue(MOCK_MODE_STATUS);
    await store.ensureFresh();
    expect(mockProbeRateLimit).not.toHaveBeenCalled();
    expect(store.status).toEqual(MOCK_MODE_STATUS);
  });

  it("probes when refresh throws (null status)", async () => {
    mockGetRateLimit.mockRejectedValue(new Error("offline"));
    mockProbeRateLimit.mockResolvedValue(STATUS);
    await store.ensureFresh();
    expect(mockProbeRateLimit).toHaveBeenCalledTimes(1);
    expect(store.status).toEqual(STATUS);
  });

  it("probe rejection sets probeError without throwing", async () => {
    mockGetRateLimit.mockResolvedValue(NO_SNAPSHOT_STATUS);
    mockProbeRateLimit.mockRejectedValue(new Error("no API key"));
    await expect(store.ensureFresh()).resolves.toBeUndefined();
    expect(store.probeError).toBe("no API key");
  });
});
