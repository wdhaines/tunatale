/**
 * The word popover's four ratings and its "More…" disclosure (bd tunatale-8jgb).
 *
 * Decided by the user on 2026-10-13 from the mockup page:
 * - a due word and a read-ahead word both offer Again / Hard / Good / Easy, in
 *   the review card's order, in place of "Got it ✓" and "Review ✓";
 * - "Start learning" stays one tap, and after any grade the row is "Undo ↩";
 * - the utility actions (Ignore, Known, Reset, …) sit behind "More…", because
 *   the popover already holds a lot of buttons and more are coming.
 *
 * The disclosure rule these pin: utilities collapse behind "More…" only when
 * the popover has a primary action (ratings, a grade button, or "Words…") AND
 * two or more utilities. One utility costs the same room as "More…" would, and
 * a popover holding nothing but utilities has nothing to make room for.
 *
 * LOCKED: written by the orchestrator before the implementation. Do not edit.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import WordSpan from "./WordSpan.svelte";
import { makeWordToken } from "../test/factories";
import type { WordToken } from "./api";
import type { TooltipActions } from "./components/Tooltip.svelte";

const RATING_LABELS = ["Again", "Hard", "Good", "Easy"];

function due(overrides: Partial<WordToken> = {}): WordToken {
  return makeWordToken({
    surface: "bilar",
    lemma: "bilar",
    srs_state: "learning",
    srs_item_id: 21,
    translation: "vigil",
    active_state: "learning",
    active_direction: "recognition",
    is_due: true,
    ...overrides,
  });
}

function readAhead(overrides: Partial<WordToken> = {}): WordToken {
  return due({
    surface: "gahapon",
    lemma: "gahapon",
    srs_item_id: 22,
    translation: "yesterday",
    active_state: "review",
    is_due: false,
    recognition_reviewable: true,
    ...overrides,
  });
}

function actions(overrides: Partial<TooltipActions> = {}): TooltipActions {
  return {
    onUntrack: vi.fn(),
    onSetState: vi.fn(),
    onIgnoreLemma: vi.fn(),
    onUnignoreLemma: vi.fn(),
    ...overrides,
  } as TooltipActions;
}

function labels(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("[role='tooltip'] button")).map((b) =>
    (b.textContent ?? "").trim(),
  );
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("[role='tooltip'] button")).find(
    (b) => (b.textContent ?? "").trim() === label,
  );
  if (!found) throw new Error(`no "${label}" button; have: ${labels(container).join(" | ")}`);
  return found as HTMLButtonElement;
}

describe("WordSpan ratings", () => {
  it("a due word offers the four ratings in the review card's order, not 'Got it ✓'", () => {
    const { container } = render(WordSpan, { props: { word: due(), onWordClick: vi.fn() } });
    const got = labels(container);
    expect(got.slice(0, 4)).toEqual(RATING_LABELS);
    expect(got).not.toContain("Got it ✓");
  });

  it.each([
    ["Again", "again"],
    ["Hard", "hard"],
    ["Good", "good"],
    ["Easy", "easy"],
  ])("tapping %s on a due word passes that rating to the click handler", async (label, rating) => {
    const onWordClick = vi.fn();
    const word = due();
    const { container } = render(WordSpan, { props: { word, onWordClick, lineIndex: 3 } });
    await fireEvent.click(button(container, label));
    expect(onWordClick).toHaveBeenCalledTimes(1);
    expect(onWordClick).toHaveBeenCalledWith(word, 3, rating);
  });

  it("a read-ahead word offers the same four ratings, not 'Review ✓'", async () => {
    const onWordClick = vi.fn();
    const word = readAhead();
    const { container } = render(WordSpan, { props: { word, onWordClick } });
    const got = labels(container);
    expect(got.slice(0, 4)).toEqual(RATING_LABELS);
    expect(got).not.toContain("Review ✓");
    await fireEvent.click(button(container, "Easy"));
    expect(onWordClick).toHaveBeenCalledWith(word, 0, "easy");
  });

  it("a word with no card keeps the one-tap 'Start learning' and passes no rating", async () => {
    const onWordClick = vi.fn();
    const word = makeWordToken({ surface: "haya", lemma: "haya", active_state: "unknown" });
    const { container } = render(WordSpan, { props: { word, onWordClick } });
    const got = labels(container);
    expect(got).toContain("Start learning");
    for (const rating of RATING_LABELS) expect(got).not.toContain(rating);
    await fireEvent.click(button(container, "Start learning"));
    // Exactly two arguments: the start path never forwards a rating.
    expect(onWordClick).toHaveBeenCalledWith(word, 0);
  });

  it("after a grade the ratings give way to 'Undo ↩'", async () => {
    const onUndoGrade = vi.fn();
    const word = due();
    const { container } = render(WordSpan, {
      props: {
        word,
        onWordClick: vi.fn(),
        tooltipActions: actions({ isGradeUndoable: () => true, onUndoGrade }),
      },
    });
    const got = labels(container);
    expect(got).toContain("Undo ↩");
    for (const rating of RATING_LABELS) expect(got).not.toContain(rating);
    await fireEvent.click(button(container, "Undo ↩"));
    expect(onUndoGrade).toHaveBeenCalledWith(word);
  });

  it("Enter on a due word still grades without a rating (the handler defaults to Good)", async () => {
    const onWordClick = vi.fn();
    const word = due();
    const { container } = render(WordSpan, { props: { word, onWordClick } });
    await fireEvent.keyDown(container.querySelector(".word") as HTMLElement, { key: "Enter" });
    expect(onWordClick).toHaveBeenCalledWith(word, 0);
  });

  it("with no click handler a due word offers no ratings", () => {
    const { container } = render(WordSpan, { props: { word: due() } });
    for (const rating of RATING_LABELS) expect(labels(container)).not.toContain(rating);
  });
});

describe("WordSpan 'More…' disclosure", () => {
  it("a due learning word shows the ratings and 'More…', with the utilities out of the DOM", () => {
    const { container } = render(WordSpan, {
      props: { word: due(), onWordClick: vi.fn(), tooltipActions: actions() },
    });
    expect(labels(container)).toEqual([...RATING_LABELS, "More…"]);
  });

  it("tapping 'More…' reveals the utilities, keeps the ratings, and removes itself", async () => {
    const { container } = render(WordSpan, {
      props: { word: due(), onWordClick: vi.fn(), tooltipActions: actions() },
    });
    const more = button(container, "More…");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    await fireEvent.click(more);
    expect(labels(container)).toEqual([...RATING_LABELS, "Ignore", "Known", "Reset"]);
  });

  it("a revealed utility still does its job", async () => {
    const onUntrack = vi.fn();
    const { container } = render(WordSpan, {
      props: { word: due(), onWordClick: vi.fn(), tooltipActions: actions({ onUntrack }) },
    });
    await fireEvent.click(button(container, "More…"));
    await fireEvent.click(button(container, "Ignore"));
    expect(onUntrack).toHaveBeenCalledWith(21);
  });

  it("a read-ahead word collapses its utilities the same way", () => {
    const { container } = render(WordSpan, {
      props: { word: readAhead(), onWordClick: vi.fn(), tooltipActions: actions() },
    });
    expect(labels(container)).toEqual([...RATING_LABELS, "More…"]);
  });

  it("one utility beside a primary action is shown as itself, not behind 'More…'", () => {
    const word = makeWordToken({ surface: "haya", lemma: "haya", active_state: "unknown" });
    const { container } = render(WordSpan, {
      props: { word, onWordClick: vi.fn(), tooltipActions: actions() },
    });
    expect(labels(container)).toEqual(["Start learning", "Ignore"]);
  });

  it("a popover with no primary action shows its utilities directly", () => {
    // Not due and not readable ahead: nothing to grade, only utilities.
    const word = due({ is_due: false, active_state: "review" });
    const { container } = render(WordSpan, {
      props: { word, onWordClick: vi.fn(), tooltipActions: actions() },
    });
    expect(labels(container)).toEqual(["Ignore", "Known", "Reset"]);
  });
});
