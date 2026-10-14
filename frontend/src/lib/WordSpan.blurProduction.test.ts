/**
 * Blur-as-cloze reader (bd tunatale-dvdm.3): with the "practise production"
 * setting on, a word whose PRODUCTION direction is a due review renders
 * blurred instead of bold. Tapping reveals it; the popover then offers the
 * review queue's four ratings (Again / Hard / Good / Easy, bd tunatale-dvdm.5),
 * which grade the PRODUCTION direction. A blur never touched grades nothing.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import WordSpan from "./WordSpan.svelte";
import { makeWordToken } from "../test/factories";
import type { WordToken } from "./api";

function prodDue(overrides: Partial<WordToken> = {}): WordToken {
  return makeWordToken({
    surface: "huset",
    lemma: "hus",
    srs_state: "review",
    srs_item_id: 11,
    translation: "the house",
    active_state: "review",
    active_direction: "recognition",
    is_due: true,
    production_due: true,
    ...overrides,
  });
}

const RATING_LABELS = ["Again", "Hard", "Good", "Easy"];

function wordEl(container: HTMLElement): HTMLElement {
  return container.querySelector(".word") as HTMLElement;
}

describe("WordSpan blur-as-cloze", () => {
  it("setting OFF: a production-due word renders byte-identical to one without the flag", () => {
    const a = render(WordSpan, { props: { word: prodDue(), onWordClick: vi.fn() } });
    const b = render(WordSpan, {
      props: { word: prodDue({ production_due: false }), onWordClick: vi.fn() },
    });
    expect(a.container.innerHTML).toBe(b.container.innerHTML);
    expect(wordEl(a.container).classList.contains("word-due")).toBe(true);
    expect(wordEl(a.container).classList.contains("word-blurred")).toBe(false);
  });

  it("setting ON: a production-due word is blurred and NOT bold", () => {
    const { container } = render(WordSpan, {
      props: { word: prodDue(), onWordClick: vi.fn(), blurProduction: true },
    });
    const el = wordEl(container);
    expect(el.classList.contains("word-blurred")).toBe(true);
    expect(el.classList.contains("word-due")).toBe(false);
  });

  it("setting ON: a word that is not production-due is unchanged (recognition bold kept)", () => {
    const on = render(WordSpan, {
      props: {
        word: prodDue({ production_due: false }),
        onWordClick: vi.fn(),
        blurProduction: true,
      },
    });
    const off = render(WordSpan, {
      props: { word: prodDue({ production_due: false }), onWordClick: vi.fn() },
    });
    expect(on.container.innerHTML).toBe(off.container.innerHTML);
  });

  it("a blurred word offers no grade at all until it is revealed", () => {
    const onProductionGrade = vi.fn();
    const onWordClick = vi.fn();
    const { queryByRole } = render(WordSpan, {
      props: {
        word: prodDue(),
        onWordClick,
        blurProduction: true,
        tooltipActions: { onProductionGrade },
      },
    });
    expect(queryByRole("button", { name: "Undo ↩" })).toBeNull();
    for (const name of RATING_LABELS) expect(queryByRole("button", { name })).toBeNull();
    expect(onProductionGrade).not.toHaveBeenCalled();
    expect(onWordClick).not.toHaveBeenCalled();
  });

  it("tapping reveals the word and offers all four ratings, and the tap itself grades nothing", async () => {
    const onProductionGrade = vi.fn();
    const onWordClick = vi.fn();
    const { container, getByRole } = render(WordSpan, {
      props: {
        word: prodDue(),
        onWordClick,
        blurProduction: true,
        tooltipActions: { onProductionGrade },
      },
    });
    await fireEvent.click(wordEl(container));
    expect(wordEl(container).classList.contains("word-blurred")).toBe(false);
    for (const name of RATING_LABELS) expect(getByRole("button", { name })).toBeTruthy();
    expect(onProductionGrade).not.toHaveBeenCalled();
    expect(onWordClick).not.toHaveBeenCalled();
  });

  it.each([
    ["Again", "again"],
    ["Hard", "hard"],
    ["Good", "good"],
    ["Easy", "easy"],
  ])("%s grades the PRODUCTION direction with %s", async (label, rating) => {
    const onProductionGrade = vi.fn();
    const onWordClick = vi.fn();
    const word = prodDue();
    const { container, getByRole } = render(WordSpan, {
      props: { word, onWordClick, blurProduction: true, tooltipActions: { onProductionGrade } },
    });
    await fireEvent.click(wordEl(container));
    await fireEvent.click(getByRole("button", { name: label }));
    expect(onProductionGrade).toHaveBeenCalledTimes(1);
    expect(onProductionGrade).toHaveBeenCalledWith(word, rating);
    // Never the recognition path.
    expect(onWordClick).not.toHaveBeenCalled();
  });

  it("the four ratings render in their own row, in the review queue's order", async () => {
    const { container } = render(WordSpan, {
      props: {
        word: prodDue(),
        onWordClick: vi.fn(),
        blurProduction: true,
        tooltipActions: { onProductionGrade: vi.fn() },
      },
    });
    await fireEvent.click(wordEl(container));
    const rate = container.querySelector(".tt-rate") as HTMLElement;
    const labels = [...rate.querySelectorAll("button")].map((b) => b.textContent?.trim());
    expect(labels).toEqual(RATING_LABELS);
    // The ratings are a row of their own; the utilities live in `.tt-actions`.
    const actions = container.querySelector(".tt-actions") as HTMLElement;
    expect([...actions.querySelectorAll("button")].map((b) => b.textContent?.trim())).not.toContain(
      "Good",
    );
  });

  it("each rating carries its own colour class, as on the review card", async () => {
    const { container, getByRole } = render(WordSpan, {
      props: {
        word: prodDue(),
        onWordClick: vi.fn(),
        blurProduction: true,
        tooltipActions: { onProductionGrade: vi.fn() },
      },
    });
    await fireEvent.click(wordEl(container));
    for (const label of RATING_LABELS) {
      const classes = [...getByRole("button", { name: label }).classList];
      const rating = label.toLowerCase();
      // Exactly its own colour: Good used to be the one accent-blue button,
      // which is Easy's colour in the review queue.
      expect(classes.filter((c) => /^tt-btn-(again|hard|good|easy|grade)$/.test(c))).toEqual([
        `tt-btn-${rating}`,
      ]);
    }
  });

  it("Enter on a blurred word reveals it instead of grading", async () => {
    const onWordClick = vi.fn();
    const onProductionGrade = vi.fn();
    const { container } = render(WordSpan, {
      props: {
        word: prodDue(),
        onWordClick,
        blurProduction: true,
        tooltipActions: { onProductionGrade },
      },
    });
    await fireEvent.keyDown(wordEl(container), { key: "Enter" });
    expect(wordEl(container).classList.contains("word-blurred")).toBe(false);
    expect(onWordClick).not.toHaveBeenCalled();
    expect(onProductionGrade).not.toHaveBeenCalled();
  });

  it("a blurred word does not expose its surface to assistive tech", () => {
    const { container } = render(WordSpan, {
      props: { word: prodDue(), onWordClick: vi.fn(), blurProduction: true },
    });
    const label = wordEl(container).getAttribute("aria-label") ?? "";
    expect(label).not.toContain("huset");
    expect(label.length).toBeGreaterThan(0);
  });

  it("an untracked production_due word (no item id) never blurs", () => {
    const { container } = render(WordSpan, {
      props: { word: prodDue({ srs_item_id: null }), onWordClick: vi.fn(), blurProduction: true },
    });
    expect(wordEl(container).classList.contains("word-blurred")).toBe(false);
  });
});
