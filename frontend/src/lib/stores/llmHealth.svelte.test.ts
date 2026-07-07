import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("$lib/api", () => ({
  api: { getLlmHealth: vi.fn() },
}));

import { api } from "$lib/api";
import type { LlmHealthStatus } from "$lib/api";

const mockGetLlmHealth = vi.mocked(api.getLlmHealth);

const HEALTHY: LlmHealthStatus = {
  healthy: true,
  consecutive_failures: 0,
  last_error: null,
  fallback_allowed: false,
  llm_mode: "live",
};

const UNHEALTHY: LlmHealthStatus = {
  healthy: false,
  consecutive_failures: 3,
  last_error: { status: 401, message: "Groq returned HTTP 401", ago_s: 15.2 },
  fallback_allowed: false,
  llm_mode: "live",
};

interface LlmHealthStore {
  status: LlmHealthStatus | null;
  set: (next: LlmHealthStatus | null) => void;
  refresh: () => Promise<void>;
}

let store: LlmHealthStore;

beforeEach(async () => {
  vi.clearAllMocks();
  vi.resetModules();
  const mod = await import("./llmHealth.svelte");
  store = mod.llmHealthStore as LlmHealthStore;
});

describe("llmHealthStore", () => {
  it("starts empty", () => {
    expect(store.status).toBeNull();
  });

  it("set() stores the value", () => {
    store.set(HEALTHY);
    expect(store.status).toEqual(HEALTHY);
  });

  it("refresh() pulls from getLlmHealth", async () => {
    mockGetLlmHealth.mockResolvedValue(HEALTHY);
    await store.refresh();
    expect(store.status).toEqual(HEALTHY);
  });

  it("refresh() keeps last-known value on error", async () => {
    store.set(HEALTHY);
    mockGetLlmHealth.mockRejectedValue(new Error("offline"));
    await store.refresh();
    expect(store.status).toEqual(HEALTHY);
  });

  it("refresh() stores unhealthy status", async () => {
    mockGetLlmHealth.mockResolvedValue(UNHEALTHY);
    await store.refresh();
    expect(store.status).toEqual(UNHEALTHY);
  });
});
