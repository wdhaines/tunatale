import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import PipelineCard from "./PipelineCard.svelte";
import type { PipelineDayState, PipelineStatus } from "$lib/api";

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
      position: 1,
      state: "generating",
      lesson_id: null,
      has_audio: false,
      error: null,
      retryable: true,
      detail: "attempt 1/4",
    },
    {
      day: 2,
      position: 2,
      state: "queued",
      lesson_id: null,
      has_audio: false,
      error: null,
      retryable: true,
      detail: null,
    },
    {
      day: 3,
      position: 3,
      state: "failed",
      lesson_id: "l3",
      has_audio: false,
      error: "LLM 429 rate limited",
      retryable: true,
      detail: null,
    },
    {
      day: 4,
      position: 4,
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

// tunatale-hbnd: a Cebuano render is minutes of throttled TTS, so a rendering
// day reports clips and (once there is enough of a sample) an ETA.
const RENDERING: PipelineDayState = {
  day: 1,
  position: 1,
  state: "rendering",
  lesson_id: "l1",
  has_audio: false,
  error: null,
  retryable: true,
  detail: "Rendering audio",
};

function oneDay(overrides: Partial<PipelineDayState>): PipelineStatus {
  return { active: true, days: [{ ...RENDERING, ...overrides }] };
}

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

  it("labels rows by position, matching the day picker after a deletion", () => {
    const gappy: PipelineStatus = {
      active: true,
      days: [{ ...DAYS_PARTIAL.days[0], day: 7, position: 2 }],
    };
    const { getByText, queryByText } = render(PipelineCard, {
      props: { status: gappy, curriculumId: "cid-1" },
    });
    expect(getByText("Day 2")).toBeTruthy();
    expect(queryByText("Day 7")).toBeNull();
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

describe("PipelineCard render progress", () => {
  it("shows the clip percentage and counts, and no ETA when none is known", () => {
    const { container, getByText, queryByText } = render(PipelineCard, {
      props: {
        status: oneDay({ clips_done: 116, clips_total: 171, eta_seconds: null }),
        curriculumId: "cid-1",
      },
    });
    expect(getByText("67% · 116/171")).toBeTruthy();
    expect(queryByText(/left/)).toBeNull();
    const bar = container.querySelector("progress");
    expect(bar?.getAttribute("value")).toBe("116");
    expect(bar?.getAttribute("max")).toBe("171");
  });

  it("rounds the percentage DOWN, so 170/171 never reads 100%", () => {
    const { getByText } = render(PipelineCard, {
      props: { status: oneDay({ clips_done: 170, clips_total: 171 }), curriculumId: "cid-1" },
    });
    expect(getByText("99% · 170/171")).toBeTruthy();
  });

  it("says 'under a minute left' below a minute", () => {
    const { getByText } = render(PipelineCard, {
      props: {
        status: oneDay({ clips_done: 116, clips_total: 171, eta_seconds: 26 }),
        curriculumId: "cid-1",
      },
    });
    expect(getByText("under a minute left")).toBeTruthy();
  });

  it("rounds minutes UP, so 125 s reads as 3 min rather than 2", () => {
    const { getByText } = render(PipelineCard, {
      props: {
        status: oneDay({ clips_done: 116, clips_total: 171, eta_seconds: 125 }),
        curriculumId: "cid-1",
      },
    });
    expect(getByText("about 3 min left")).toBeTruthy();
  });

  it("shows nothing for a day that is not rendering", () => {
    const { container, queryByText } = render(PipelineCard, {
      props: {
        status: oneDay({ state: "ready", clips_done: 116, clips_total: 171, eta_seconds: 26 }),
        curriculumId: "cid-1",
      },
    });
    expect(container.querySelector("progress")).toBeNull();
    expect(queryByText(/116\/171/)).toBeNull();
    expect(queryByText(/left/)).toBeNull();
  });

  it("shows nothing while rendering with no clip total — today's card, unchanged", () => {
    const { container, queryByText } = render(PipelineCard, {
      props: { status: oneDay({ clips_done: null, clips_total: null }), curriculumId: "cid-1" },
    });
    expect(container.querySelector("progress")).toBeNull();
    expect(queryByText(/%/)).toBeNull();
  });
});
