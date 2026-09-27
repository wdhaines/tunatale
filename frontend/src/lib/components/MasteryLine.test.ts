/**
 * MasteryLine: while the transcript is loading it holds its space (tunatale-9k7j).
 *
 * The line sits above the lesson player. Rendering nothing until the transcript
 * arrived and then three lines at once shoved every player control down ~45px,
 * so a tap aimed at Repeat landed on Play. The geometry itself is asserted in
 * Playwright (lesson-header-layout.spec.ts); jsdom has no layout, so these
 * tests pin only WHAT renders in each state.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/svelte";
import MasteryLine from "./MasteryLine.svelte";
import { makeWordToken } from "$lib/../test/factories";
import type { TranscriptData } from "$lib/api";

function transcript(): TranscriptData {
  return {
    lesson_id: "l1",
    key_phrases: [],
    dialogue_lines: [
      { words: [makeWordToken({ lemma: "dan", active_state: "new", progress: 0 })] },
    ],
  } as unknown as TranscriptData;
}

describe("MasteryLine", () => {
  it("reserves its space with a hidden placeholder while loading", () => {
    const { container } = render(MasteryLine, { props: { transcript: null, loading: true } });
    const placeholder = container.querySelector(".mastery-placeholder");
    expect(placeholder).not.toBeNull();
    expect(placeholder!.getAttribute("aria-hidden")).toBe("true");
    // Same markup as the real line, so the same CSS gives the same height.
    expect(placeholder!.querySelector(".mastery-line")).not.toBeNull();
    expect(placeholder!.querySelectorAll(".mastery-sides .side-label")).toHaveLength(2);
  });

  it("renders nothing when not loading and there is no transcript (a failed load leaves no gap)", () => {
    const { container } = render(MasteryLine, { props: { transcript: null, loading: false } });
    expect(container.querySelector(".mastery-placeholder")).toBeNull();
    expect(container.querySelector(".mastery-line")).toBeNull();
  });

  it("loading defaults to false for callers that do not pass it", () => {
    const { container } = render(MasteryLine, { props: { transcript: null } });
    expect(container.querySelector(".mastery-placeholder")).toBeNull();
  });

  it("shows the real line, not the placeholder, once the transcript is there", () => {
    const { container } = render(MasteryLine, {
      props: { transcript: transcript(), loading: true },
    });
    expect(container.querySelector(".mastery-placeholder")).toBeNull();
    expect(container.querySelector(".mastery-line")?.textContent).toContain("known");
  });
});
