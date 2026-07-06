import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";

const mocks = vi.hoisted(() => ({
  getRateLimit: vi.fn(),
  probeRateLimit: vi.fn(),
}));

vi.mock("$lib/api", () => ({
  api: {
    getRateLimit: mocks.getRateLimit,
    probeRateLimit: mocks.probeRateLimit,
  },
}));

import { rateLimitStore } from "$lib/stores/rateLimit.svelte";
import RateLimitWidget from "./RateLimitWidget.svelte";

const STATUS_WITH_SNAPSHOT = {
  provider: "groq",
  model: "openai/gpt-oss-120b",
  llm_mode: "live",
  snapshot: {
    age_s: 12.3,
    requests_limit: 1000,
    requests_remaining: 999,
    requests_reset_in_s: 86400,
    tokens_limit: 8000,
    tokens_remaining: 7927,
    tokens_reset_in_s: 30,
  },
  last_429: null,
  tokens_used_24h: 73,
  tokens_per_day_limit: 100000,
};

beforeEach(() => {
  vi.clearAllMocks();
  rateLimitStore.set(null);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("RateLimitWidget", () => {
  describe("no data (status null, no error)", () => {
    it("shows LLM — with title tooltip", () => {
      const { getByText } = render(RateLimitWidget);
      const chip = getByText("LLM —");
      expect(chip).toBeTruthy();
      expect(chip.getAttribute("title")).toBe("No LLM call yet this session — click to check");
    });

    it("calls probe() on click", async () => {
      mocks.probeRateLimit.mockResolvedValue(STATUS_WITH_SNAPSHOT);
      const { getByText } = render(RateLimitWidget);
      await fireEvent.click(getByText("LLM —"));
      expect(mocks.probeRateLimit).toHaveBeenCalled();
    });

    it("calls probe() on Enter key", async () => {
      mocks.probeRateLimit.mockResolvedValue(STATUS_WITH_SNAPSHOT);
      const { getByText } = render(RateLimitWidget);
      const chip = getByText("LLM —");
      await fireEvent.keyDown(chip, { key: "Enter" });
      expect(mocks.probeRateLimit).toHaveBeenCalled();
    });
  });

  describe("probing state (no data)", () => {
    it("shows busy indicator while probing", async () => {
      let resolveProbe: ((v: unknown) => void) | undefined;
      mocks.probeRateLimit.mockReturnValue(
        new Promise((r) => {
          resolveProbe = r;
        }),
      );
      const { getByText } = render(RateLimitWidget);
      await fireEvent.click(getByText("LLM —"));
      expect(getByText("LLM …")).toBeTruthy();
      resolveProbe!(STATUS_WITH_SNAPSHOT);
    });
  });

  describe("probe error (no data, probeError set)", () => {
    it("shows error indicator after failed probe", async () => {
      mocks.probeRateLimit.mockRejectedValue(new Error("API key missing"));
      const { getByText, findByText } = render(RateLimitWidget);
      await fireEvent.click(getByText("LLM —"));
      expect(await findByText("LLM !")).toBeTruthy();
    });
  });

  describe("mock mode", () => {
    beforeEach(() => {
      rateLimitStore.set({
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "mock",
        snapshot: null,
        last_429: null,
        tokens_used_24h: null,
        tokens_per_day_limit: 100000,
      });
    });

    it("shows LLM mock muted", () => {
      const { getByText } = render(RateLimitWidget);
      const chip = getByText("LLM mock");
      expect(chip).toBeTruthy();
      expect(chip.classList.contains("muted")).toBe(true);
    });
  });

  describe("snapshot with null tokens_reset_in_s", () => {
    beforeEach(() => {
      rateLimitStore.set({
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "live",
        snapshot: {
          age_s: 10,
          requests_limit: 1000,
          requests_remaining: 500,
          requests_reset_in_s: null,
          tokens_limit: 8000,
          tokens_remaining: 4000,
          tokens_reset_in_s: null,
        },
        last_429: null,
        tokens_used_24h: null,
        tokens_per_day_limit: 100000,
      });
    });

    it("shows ? placeholder when tokensResetIn is null", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).toContain("↻?s");
    });
  });

  describe("normal state (with snapshot)", () => {
    beforeEach(() => {
      rateLimitStore.set(STATUS_WITH_SNAPSHOT);
    });

    it("shows compact token display", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).toContain("LLM 7.9k/8.0k");
    });

    it("shows countdown seconds", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).toContain("↻30s");
    });

    it("countdown ticks down each second", async () => {
      const { container } = render(RateLimitWidget);
      await vi.advanceTimersByTimeAsync(5000);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).toContain("↻25s");
    });

    it("countdown clamps at 0", async () => {
      const { container } = render(RateLimitWidget);
      await vi.advanceTimersByTimeAsync(35000);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).toContain("↻0s");
    });

    it("has a detail title attribute", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      const title = chip?.getAttribute("title") ?? "";
      expect(title).toContain("Tokens/min:");
      expect(title).toContain("Requests/day:");
      expect(title).toContain("Model:");
      expect(title).toContain("As of");
    });
  });

  describe("warning state (low tokens)", () => {
    beforeEach(() => {
      rateLimitStore.set({
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "live",
        snapshot: {
          age_s: 10,
          requests_limit: 1000,
          requests_remaining: 999,
          requests_reset_in_s: 86400,
          tokens_limit: 8000,
          tokens_remaining: 1500,
          tokens_reset_in_s: 30,
        },
        last_429: null,
        tokens_used_24h: 73,
        tokens_per_day_limit: 100000,
      });
    });

    it("applies warning class when tokens < 20%", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.classList.contains("warning")).toBe(true);
      expect(chip?.classList.contains("danger")).toBe(false);
    });
  });

  describe("warning state (high daily usage)", () => {
    beforeEach(() => {
      rateLimitStore.set({
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "live",
        snapshot: {
          age_s: 10,
          requests_limit: 1000,
          requests_remaining: 999,
          requests_reset_in_s: 86400,
          tokens_limit: 8000,
          tokens_remaining: 7000,
          tokens_reset_in_s: 30,
        },
        last_429: null,
        tokens_used_24h: 85000,
        tokens_per_day_limit: 100000,
      });
    });

    it("applies warning class when daily usage > 80%", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.classList.contains("warning")).toBe(true);
    });
  });

  describe("danger state (active 429)", () => {
    beforeEach(() => {
      rateLimitStore.set({
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "live",
        snapshot: {
          age_s: 5,
          requests_limit: 1000,
          requests_remaining: 0,
          requests_reset_in_s: 86400,
          tokens_limit: 8000,
          tokens_remaining: 0,
          tokens_reset_in_s: 60,
        },
        last_429: { ago_s: 5, retry_in_s: 25 },
        tokens_used_24h: 73,
        tokens_per_day_limit: 100000,
      });
    });

    it("shows rate limited with retry countdown", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).toContain("Rate limited · 25s");
      expect(chip?.classList.contains("danger")).toBe(true);
    });

    it("switches back to normal display when retry countdown reaches 0", async () => {
      const { container } = render(RateLimitWidget);
      await vi.advanceTimersByTimeAsync(30000);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).not.toContain("Rate limited");
      expect(chip?.textContent).toContain("LLM");
    });
  });

  describe("snapshot null but status exists", () => {
    beforeEach(() => {
      rateLimitStore.set({
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "live",
        snapshot: null,
        last_429: null,
        tokens_used_24h: null,
        tokens_per_day_limit: 100000,
      });
    });

    it("shows placeholder when snapshot is null", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).toContain("LLM —/—");
    });
  });

  describe("with last_429 but retry expired", () => {
    beforeEach(() => {
      rateLimitStore.set({
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "live",
        snapshot: {
          age_s: 5,
          requests_limit: 1000,
          requests_remaining: 500,
          requests_reset_in_s: 43200,
          tokens_limit: 8000,
          tokens_remaining: 4000,
          tokens_reset_in_s: 30,
        },
        last_429: { ago_s: 30, retry_in_s: 0 },
        tokens_used_24h: 73,
        tokens_per_day_limit: 100000,
      });
    });

    it("shows normal display when retry_in_s is 0", () => {
      const { container } = render(RateLimitWidget);
      const chip = container.querySelector(".llm-chip");
      expect(chip?.textContent).toContain("LLM 4.0k/8.0k");
      expect(chip?.classList.contains("danger")).toBe(false);
    });
  });
});
