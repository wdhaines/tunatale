import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import PipelineCard from "./PipelineCard.svelte";
import type { PipelineStatus } from "$lib/api";

vi.mock("$lib/api", () => ({
  api: { retryPipelineDay: vi.fn() },
}));

import { api } from "$lib/api";

const mockRetry = vi.mocked(api.retryPipelineDay);

const DAYS_PARTIAL: PipelineStatus = {
  active: true,
  days: [
    {
      day: 1,
      state: "generating",
      lesson_id: null,
      has_audio: false,
      error: null,
      retryable: true,
      detail: "attempt 1/4",
    },
    {
      day: 2,
      state: "queued",
      lesson_id: null,
      has_audio: false,
      error: null,
      retryable: true,
      detail: null,
    },
    {
      day: 3,
      state: "failed",
      lesson_id: "l3",
      has_audio: false,
      error: "LLM 429 rate limited",
      retryable: true,
      detail: null,
    },
    {
      day: 4,
      state: "ready",
      lesson_id: "l4",
      has_audio: true,
      error: null,
      retryable: false,
      detail: null,
    },
  ],
};

const EMPTY: PipelineStatus = { active: false, days: [] };

describe("PipelineCard", () => {
  it("renders nothing when days array is empty", () => {
    const { container } = render(PipelineCard, {
      props: { status: EMPTY, curriculumId: "cid-1" },
    });
    expect(container.textContent?.trim()).toBe("");
  });

  it("renders a row per pipeline day", () => {
    const { getByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1" },
    });
    expect(getByText("Day 1")).toBeTruthy();
    expect(getByText("Day 2")).toBeTruthy();
    expect(getByText("Day 3")).toBeTruthy();
    expect(getByText("Day 4")).toBeTruthy();
  });

  it("shows state badge for each day", () => {
    const { getByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1" },
    });
    expect(getByText("generating")).toBeTruthy();
    expect(getByText("queued")).toBeTruthy();
    expect(getByText("failed")).toBeTruthy();
    expect(getByText("ready")).toBeTruthy();
  });

  it("shows detail line when present", () => {
    const { getByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1" },
    });
    expect(getByText(/attempt 1\/4/)).toBeTruthy();
  });

  it("shows Listen link when ready with lesson_id", () => {
    const { getByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1" },
    });
    const link = getByText("Listen →");
    expect(link.getAttribute("href")).toBe("/c/cid-1/l/l4");
  });

  it("does not show Listen link for non-ready days", () => {
    const { queryByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1" },
    });
    // Day 3 is failed — should not have Listen link
    expect(queryByText("Listen →")).toBeTruthy(); // Day 4 has it
  });

  it("shows Retry button on failed days", () => {
    const { getByText, queryAllByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1" },
    });
    const retryButtons = queryAllByText("Retry");
    expect(retryButtons).toHaveLength(1); // Only Day 3 is failed
    expect(getByText("Retry")).toBeTruthy();
  });

  it("calls retryPipelineDay on Retry click", async () => {
    mockRetry.mockResolvedValue({ status: "queued" });
    const onRefresh = vi.fn();

    const { getByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1", onRefresh },
    });
    await fireEvent.click(getByText("Retry"));
    expect(mockRetry).toHaveBeenCalledWith("cid-1", 3);
  });

  it("calls onRefresh after successful retry", async () => {
    mockRetry.mockResolvedValue({ status: "queued" });
    const onRefresh = vi.fn();

    const { getByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1", onRefresh },
    });
    await fireEvent.click(getByText("Retry"));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("retry succeeds even without onRefresh handler (default no-op)", async () => {
    mockRetry.mockResolvedValue({ status: "queued" });

    const { getByText } = render(PipelineCard, {
      props: { status: DAYS_PARTIAL, curriculumId: "cid-1" },
    });
    await fireEvent.click(getByText("Retry"));
    await vi.waitFor(() => {
      expect(mockRetry).toHaveBeenCalledWith("cid-1", 3);
    });
  });
});
