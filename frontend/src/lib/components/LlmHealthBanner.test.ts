import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

const mocks = vi.hoisted(() => ({
  getLlmHealth: vi.fn(),
  getRateLimit: vi.fn(),
  probeRateLimit: vi.fn(),
}));

vi.mock("$lib/api", () => ({
  api: {
    getLlmHealth: mocks.getLlmHealth,
    getRateLimit: mocks.getRateLimit,
    probeRateLimit: mocks.probeRateLimit,
  },
}));

import { llmHealthStore } from "$lib/stores/llmHealth.svelte";
import { rateLimitStore } from "$lib/stores/rateLimit.svelte";
import LlmHealthBanner from "./LlmHealthBanner.svelte";

const STATUS = {
  provider: "groq",
  model: "openai/gpt-oss-120b",
  llm_mode: "live",
  snapshot: null,
  last_429: null,
  tokens_used_24h: null,
  tokens_per_day_limit: 100000,
};

beforeEach(() => {
  vi.clearAllMocks();
  llmHealthStore.set(null);
  rateLimitStore.set(null);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("LlmHealthBanner", () => {
  it("renders nothing when status is null", () => {
    const { container } = render(LlmHealthBanner);
    expect(container.querySelector(".health-banner")).toBeNull();
  });

  it("renders nothing when healthy is true", () => {
    llmHealthStore.set({
      healthy: true,
      consecutive_failures: 0,
      last_error: null,
      fallback_allowed: false,
      llm_mode: "live",
    });
    const { container } = render(LlmHealthBanner);
    expect(container.querySelector(".health-banner")).toBeNull();
  });

  it("renders nothing in mock mode even when unhealthy", () => {
    llmHealthStore.set({
      healthy: false,
      consecutive_failures: 99,
      last_error: { status: 401, message: "Groq returned HTTP 401", ago_s: 10 },
      fallback_allowed: false,
      llm_mode: "mock",
    });
    const { container } = render(LlmHealthBanner);
    expect(container.querySelector(".health-banner")).toBeNull();
  });

  it("shows banner with last_error message and ago_s", () => {
    llmHealthStore.set({
      healthy: false,
      consecutive_failures: 3,
      last_error: { status: 401, message: "Groq returned HTTP 401", ago_s: 15.2 },
      fallback_allowed: false,
      llm_mode: "live",
    });
    const { getByText } = render(LlmHealthBanner);
    expect(getByText(/Groq returned HTTP 401/)).toBeTruthy();
    expect(getByText(/15s ago/)).toBeTruthy();
    expect(getByText(/Check now/)).toBeTruthy();
  });

  it("shows fallback suffix when fallback_allowed is true", () => {
    llmHealthStore.set({
      healthy: false,
      consecutive_failures: 1,
      last_error: { status: 500, message: "Groq returned HTTP 500", ago_s: 30 },
      fallback_allowed: true,
      llm_mode: "live",
    });
    const { getByText } = render(LlmHealthBanner);
    expect(getByText(/using local fallback/)).toBeTruthy();
  });

  it("shows message without error detail when last_error is null", () => {
    llmHealthStore.set({
      healthy: false,
      consecutive_failures: 1,
      last_error: null,
      fallback_allowed: false,
      llm_mode: "live",
    });
    const { getByText } = render(LlmHealthBanner);
    expect(getByText(/LLM provider failing/)).toBeTruthy();
  });

  it("Check now button calls probe then refreshes health", async () => {
    llmHealthStore.set({
      healthy: false,
      consecutive_failures: 3,
      last_error: { status: 401, message: "Groq returned HTTP 401", ago_s: 15 },
      fallback_allowed: false,
      llm_mode: "live",
    });
    mocks.probeRateLimit.mockResolvedValue(STATUS);
    mocks.getLlmHealth.mockResolvedValue({
      healthy: true,
      consecutive_failures: 0,
      last_error: null,
      fallback_allowed: false,
      llm_mode: "live",
    });

    const { getByText } = render(LlmHealthBanner);
    await fireEvent.click(getByText("Check now"));
    await waitFor(() => {
      expect(mocks.probeRateLimit).toHaveBeenCalled();
      expect(mocks.getLlmHealth).toHaveBeenCalled();
    });
  });

  it("shows disabled button while probing", async () => {
    llmHealthStore.set({
      healthy: false,
      consecutive_failures: 3,
      last_error: { status: 401, message: "Groq returned HTTP 401", ago_s: 15 },
      fallback_allowed: false,
      llm_mode: "live",
    });
    let resolveProbe: ((v: unknown) => void) | undefined;
    mocks.probeRateLimit.mockReturnValue(
      new Promise((r) => {
        resolveProbe = r;
      }),
    );
    mocks.getLlmHealth.mockResolvedValue({
      healthy: true,
      consecutive_failures: 0,
      last_error: null,
      fallback_allowed: false,
      llm_mode: "live",
    });

    const { getByText, findByText } = render(LlmHealthBanner);
    await fireEvent.click(getByText("Check now"));
    const btn = (await findByText("Checking…")) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    resolveProbe!(STATUS);
  });
});
