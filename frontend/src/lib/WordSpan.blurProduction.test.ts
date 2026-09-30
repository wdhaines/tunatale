/**
 * Blur-as-cloze reader (bd tunatale-dvdm.3): with the "practise production"
 * setting on, a word whose PRODUCTION direction is a due review renders
 * blurred instead of bold. Tapping reveals it; the popover then offers
 * Again / Good, which grade the PRODUCTION direction. A blur never touched
 * grades nothing.
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
    expect(queryByRole("button", { name: "Got it ✓" })).toBeNull();
    expect(queryByRole("button", { name: "Again" })).toBeNull();
    expect(queryByRole("button", { name: "Good" })).toBeNull();
    expect(onProductionGrade).not.toHaveBeenCalled();
    expect(onWordClick).not.toHaveBeenCalled();
  });

  it("tapping reveals the word and offers Again / Good, and the tap itself grades nothing", async () => {
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
    expect(getByRole("button", { name: "Again" })).toBeTruthy();
    expect(getByRole("button", { name: "Good" })).toBeTruthy();
    expect(onProductionGrade).not.toHaveBeenCalled();
    expect(onWordClick).not.toHaveBeenCalled();
  });

  it.each([
    ["Again", "again"],
    ["Good", "good"],
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
