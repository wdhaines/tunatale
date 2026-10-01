/**
 * Tests for RerenderAudio.svelte — the tools-menu re-render (bd tunatale-9paa).
 *
 * The user's calls, 2026-09-30: sections as checkboxes (all = the whole lesson),
 * and the cost shown before the click. The estimate and the render are props, so
 * the lesson page and the review-session page each pass their own API calls.
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import RerenderAudio from "./RerenderAudio.svelte";
import type { LessonAudio, RenderEstimate } from "$lib/api";

const SECTIONS = [
  { section_type: "key_phrases", title: "Key Phrases" },
  { section_type: "natural_speed", title: "Natural Speed" },
  { section_type: "slow_speed", title: "Enunciated" },
];

function est(chars: number, over: Partial<RenderEstimate> = {}): RenderEstimate {
  return {
    billable_chars: chars,
    new_clips: 12,
    cached_clips: 40,
    gemini_usd: 0,
    monthly_allowance: 500000,
    ...over,
  };
}

const AUDIO = { audio_id: "a2", lesson_id: "l1", sections: [], cues: [] } as unknown as LessonAudio;

function setup(overrides: Record<string, unknown> = {}) {
  const props = {
    sections: SECTIONS,
    estimate: vi.fn().mockResolvedValue(est(5660)),
    rerender: vi.fn().mockResolvedValue(AUDIO),
    onRendered: vi.fn(),
    ...overrides,
  };
  render(RerenderAudio, { props });
  return props;
}

const boxes = () => screen.getAllByRole("checkbox") as HTMLInputElement[];
const button = () => screen.getByRole("button", { name: /re-render/i }) as HTMLButtonElement;

describe("RerenderAudio", () => {
  it("offers every section, all ticked, and prices the whole lesson first", async () => {
    const p = setup();
    expect(boxes().map((b) => b.checked)).toEqual([true, true, true]);
    expect(screen.getByText("Enunciated")).toBeTruthy();
    await waitFor(() => expect(p.estimate).toHaveBeenCalledWith(null));
    await screen.findByText(/About 5,660 characters \(1\.1% of the monthly allowance\)/);
    expect(button().textContent).toMatch(/all sections/i);
  });

  it("re-prices when a section is unticked, naming the rest in lesson order", async () => {
    const p = setup();
    await waitFor(() => expect(p.estimate).toHaveBeenCalledTimes(1));
    p.estimate.mockResolvedValueOnce(est(900));
    await fireEvent.click(boxes()[1]);
    await waitFor(() => expect(p.estimate).toHaveBeenLastCalledWith(["key_phrases", "slow_speed"]));
    await screen.findByText(/About 900 characters/);
    expect(button().textContent).toMatch(/2 sections/);
  });

  it("with nothing ticked, says so and will not render or price an empty set", async () => {
    const p = setup();
    await waitFor(() => expect(p.estimate).toHaveBeenCalledTimes(1));
    for (const b of boxes()) await fireEvent.click(b);
    expect(button().disabled).toBe(true);
    screen.getByText(/pick at least one section/i);
    expect(p.estimate).not.toHaveBeenCalledWith([]);
  });

  it("an all-cached selection says it costs nothing", async () => {
    setup({ estimate: vi.fn().mockResolvedValue(est(0, { new_clips: 0 })) });
    await screen.findByText(/nothing new to synthesize/i);
  });

  it("names a Gemini cost when there is one", async () => {
    setup({ estimate: vi.fn().mockResolvedValue(est(300, { gemini_usd: 0.0234 })) });
    await screen.findByText(/about \$0\.02 for Gemini voices/i);
  });

  it("renders the selection, reports it, and hands the new audio to the page", async () => {
    let finish!: (a: LessonAudio) => void;
    const p = setup({
      rerender: vi.fn().mockReturnValue(new Promise<LessonAudio>((r) => (finish = r))),
    });
    await waitFor(() => expect(p.estimate).toHaveBeenCalledTimes(1));
    await fireEvent.click(boxes()[0]);
    await fireEvent.click(button());
    expect(p.rerender).toHaveBeenCalledWith(["natural_speed", "slow_speed"]);
    await screen.findByText(/rendering/i);
    expect(button().disabled).toBe(true);
    finish(AUDIO);
    await screen.findByText(/re-rendered/i);
    expect(p.onRendered).toHaveBeenCalledWith(AUDIO);
    expect(button().disabled).toBe(false);
  });

  it("all ticked renders the whole lesson (null), not a list of every section", async () => {
    const p = setup();
    await waitFor(() => expect(p.estimate).toHaveBeenCalledTimes(1));
    await fireEvent.click(button());
    expect(p.rerender).toHaveBeenCalledWith(null);
  });

  it("a failed render is shown, not swallowed", async () => {
    setup({
      rerender: vi
        .fn()
        .mockRejectedValue(new Error("A render is already in progress for this lesson")),
    });
    await fireEvent.click(await screen.findByRole("button", { name: /re-render/i }));
    await screen.findByText(/already in progress/i);
    expect(button().disabled).toBe(false);
  });

  it("two sections of one type are one box: the server selects by type", async () => {
    const p = setup({
      sections: [...SECTIONS, { section_type: "natural_speed", title: "Natural Speed" }],
    });
    expect(boxes()).toHaveLength(3);
    await waitFor(() => expect(p.estimate).toHaveBeenCalledWith(null));
  });

  it("a failed estimate is shown, not swallowed", async () => {
    setup({ estimate: vi.fn().mockRejectedValue(new Error("Lesson not found")) });
    await screen.findByText(/Re-render failed: Lesson not found/);
  });

  it("a non-Error render rejection is shown as text", async () => {
    setup({ rerender: vi.fn().mockRejectedValue("render string error") });
    await fireEvent.click(await screen.findByRole("button", { name: /re-render/i }));
    await screen.findByText(/render string error/);
  });

  it("a non-Error estimate rejection is shown as text", async () => {
    setup({ estimate: vi.fn().mockRejectedValue("estimate string error") });
    await screen.findByText(/Re-render failed: estimate string error/);
  });

  it("a stale estimate does not overwrite a newer one", async () => {
    let slow!: (e: RenderEstimate) => void;
    const estimate = vi
      .fn()
      .mockReturnValueOnce(new Promise<RenderEstimate>((r) => (slow = r)))
      .mockResolvedValueOnce(est(777));
    setup({ estimate });
    await fireEvent.click(boxes()[2]);
    await screen.findByText(/About 777 characters/);
    slow(est(99999));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/99,999/)).toBeNull();
  });
});
