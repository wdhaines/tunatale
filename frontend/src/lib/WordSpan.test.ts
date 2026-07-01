/**
 * Tests for WordSpan.svelte — per-word SRS state widget.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import WordSpan from "./WordSpan.svelte";
import { makeWordToken } from "../test/factories";

describe("WordSpan", () => {
  it("renders the word surface text", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ surface: "hvala" }) },
    });
    expect(getByRole("button").textContent).toBe("hvala");
  });

  it("clicking the word does NOT fire onWordClick (grading lives in the popover)", async () => {
    const onWordClick = vi.fn();
    const word = makeWordToken({
      lemma: "zdravo",
      srs_item_id: 42,
      is_due: true,
      active_direction: "recognition",
    });
    const { getByRole } = render(WordSpan, {
      props: { word, onWordClick, lineIndex: 2 },
    });
    await fireEvent.click(getByRole("button", { name: /^zdravo$/ }));
    expect(onWordClick).not.toHaveBeenCalled();
  });

  it("fires onWordClick with word and lineIndex via the popover grade button", async () => {
    const onWordClick = vi.fn();
    const word = makeWordToken({
      lemma: "zdravo",
      srs_item_id: 42,
      is_due: true,
      active_direction: "recognition",
    });
    const { getByRole } = render(WordSpan, {
      props: { word, onWordClick, lineIndex: 2 },
    });
    await fireEvent.click(getByRole("button", { name: "Got it ✓" }));
    expect(onWordClick).toHaveBeenCalledWith(word, 2);
  });

  it("passes 0 as lineIndex when lineIndex prop is not provided", async () => {
    const onWordClick = vi.fn();
    const word = makeWordToken({ active_state: "unknown", srs_item_id: null });
    const { getByRole } = render(WordSpan, {
      props: { word, onWordClick },
    });
    await fireEvent.click(getByRole("button", { name: "Start learning" }));
    expect(onWordClick).toHaveBeenCalledWith(word, 0);
  });

  describe("popover grade button", () => {
    it('shows "Start learning" for an unknown word', () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "unknown" }), onWordClick: vi.fn() },
      });
      expect(getByRole("button", { name: "Start learning" })).toBeTruthy();
    });

    it('shows "Got it ✓" for a due tracked word', () => {
      const word = makeWordToken({ is_due: true, active_direction: "production", srs_item_id: 7 });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn() },
      });
      expect(getByRole("button", { name: "Got it ✓" })).toBeTruthy();
    });

    it("shows no grade button for a tracked word that is not due (the old click no-op)", () => {
      const word = makeWordToken({ is_due: false, active_direction: "production", srs_item_id: 7 });
      const { queryByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn() },
      });
      expect(queryByRole("button", { name: /got it|start learning/i })).toBeNull();
    });

    it("shows no grade button for a due word missing active_direction", () => {
      const word = makeWordToken({ is_due: true, active_direction: null, srs_item_id: 7 });
      const { queryByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn() },
      });
      expect(queryByRole("button", { name: /got it|start learning/i })).toBeNull();
    });

    it("shows no grade button when onWordClick is not provided", () => {
      const { queryByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "unknown" }) },
      });
      expect(queryByRole("button", { name: /start learning/i })).toBeNull();
    });

    it("inner-phrase word (requireModifier) exposes the grade button when altHover reveals it", async () => {
      const onWordClick = vi.fn();
      const word = makeWordToken({ active_state: "unknown", lemma: "dober" });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick, requireModifier: true, altHover: true },
      });
      await fireEvent.click(getByRole("button", { name: "Start learning" }));
      expect(onWordClick).toHaveBeenCalledWith(word, 0);
    });
  });

  describe("read-ahead (Review ✓)", () => {
    it('shows "Review ✓" for a not-due word whose recognition is reviewable', () => {
      const word = makeWordToken({
        is_due: false,
        active_direction: "recognition",
        srs_item_id: 7,
        recognition_reviewable: true,
      });
      const { getByRole, queryByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn() },
      });
      expect(getByRole("button", { name: "Review ✓" })).toBeTruthy();
      expect(queryByRole("button", { name: "Got it ✓" })).toBeNull();
    });

    it('shows "Review ✓" for a graduated word whose active direction is production', () => {
      // Recognition graduated → active_direction flips to production, but reading
      // still evidences recognition, so the read-ahead affordance stays.
      const word = makeWordToken({
        is_due: false,
        active_direction: "production",
        srs_item_id: 7,
        recognition_reviewable: true,
      });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn() },
      });
      expect(getByRole("button", { name: "Review ✓" })).toBeTruthy();
    });

    it("fires onWordClick when the review-ahead button is clicked", async () => {
      const onWordClick = vi.fn();
      const word = makeWordToken({
        is_due: false,
        active_direction: "recognition",
        srs_item_id: 7,
        recognition_reviewable: true,
      });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick, lineIndex: 3 },
      });
      await fireEvent.click(getByRole("button", { name: "Review ✓" }));
      expect(onWordClick).toHaveBeenCalledWith(word, 3);
    });

    it('prefers the due "Got it ✓" over review-ahead when the active direction is due', () => {
      const word = makeWordToken({
        is_due: true,
        active_direction: "recognition",
        srs_item_id: 7,
        recognition_reviewable: true,
      });
      const { getByRole, queryByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn() },
      });
      expect(getByRole("button", { name: "Got it ✓" })).toBeTruthy();
      expect(queryByRole("button", { name: "Review ✓" })).toBeNull();
    });

    it("shows no review-ahead button when recognition_reviewable is false", () => {
      const word = makeWordToken({
        is_due: false,
        active_direction: "recognition",
        srs_item_id: 7,
        recognition_reviewable: false,
      });
      const { queryByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn() },
      });
      expect(queryByRole("button", { name: "Review ✓" })).toBeNull();
    });

    it("shows no review-ahead button when there is no srs_item_id", () => {
      const word = makeWordToken({
        is_due: false,
        active_direction: "recognition",
        srs_item_id: null,
        recognition_reviewable: true,
      });
      const { queryByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn() },
      });
      expect(queryByRole("button", { name: "Review ✓" })).toBeNull();
    });
  });

  describe("undo cycle (Got it ✓ → Undo ↩)", () => {
    it('shows "Undo ↩" when isGradeUndoable says this word was just graded — even though no longer due', () => {
      const word = makeWordToken({ srs_item_id: 42, is_due: false, active_direction: null });
      const tooltipActions = {
        isGradeUndoable: (w: typeof word) => w.srs_item_id === 42,
        onUndoGrade: vi.fn(),
      };
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn(), tooltipActions },
      });
      expect(getByRole("button", { name: "Undo ↩" })).toBeTruthy();
    });

    it('clicking "Undo ↩" calls onUndoGrade with the word, not onWordClick', async () => {
      const onWordClick = vi.fn();
      const onUndoGrade = vi.fn();
      const word = makeWordToken({ srs_item_id: 42 });
      const tooltipActions = {
        isGradeUndoable: () => true,
        onUndoGrade,
      };
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick, tooltipActions },
      });
      await fireEvent.click(getByRole("button", { name: "Undo ↩" }));
      expect(onUndoGrade).toHaveBeenCalledWith(word);
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("shows the normal grade label when isGradeUndoable returns false", () => {
      const word = makeWordToken({ is_due: true, active_direction: "recognition", srs_item_id: 7 });
      const tooltipActions = { isGradeUndoable: () => false, onUndoGrade: vi.fn() };
      const { getByRole, queryByRole } = render(WordSpan, {
        props: { word, onWordClick: vi.fn(), tooltipActions },
      });
      expect(getByRole("button", { name: "Got it ✓" })).toBeTruthy();
      expect(queryByRole("button", { name: "Undo ↩" })).toBeNull();
    });
  });

  it("handles Enter key the same as click", async () => {
    const onWordClick = vi.fn();
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken(), onWordClick },
    });
    await fireEvent.keyDown(getByRole("button"), { key: "Enter" });
    expect(onWordClick).toHaveBeenCalled();
  });

  it("handles Space key the same as click", async () => {
    const onWordClick = vi.fn();
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken(), onWordClick },
    });
    await fireEvent.keyDown(getByRole("button"), { key: " " });
    expect(onWordClick).toHaveBeenCalled();
  });

  it("ignores other keys", async () => {
    const onWordClick = vi.fn();
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken(), onWordClick },
    });
    await fireEvent.keyDown(getByRole("button"), { key: "Tab" });
    expect(onWordClick).not.toHaveBeenCalled();
  });

  it("does not throw when onWordClick is not provided", async () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken() },
    });
    await expect(fireEvent.click(getByRole("button"))).resolves.not.toThrow();
  });

  describe("active_state rendering", () => {
    it("shows word-unknown class for unknown active_state", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "unknown" }) },
      });
      expect(getByRole("button").className).toContain("word-unknown");
    });

    it("applies inline masteryColor style for known active_state (green, on the ramp)", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "known", progress: 1 }) },
      });
      const el = getByRole("button");
      // KNOWN is rendered on the green end of the mastery ramp, not the old static gray.
      expect(el.getAttribute("style")).toContain("color:");
      expect(el.className).not.toContain("word-known");
    });

    it("shows word-ignored class for suspended active_state", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "suspended" }) },
      });
      expect(getByRole("button").className).toContain("word-ignored");
    });

    it("shows word-ignored class for ignored active_state", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "ignored", srs_item_id: null }) },
      });
      expect(getByRole("button").className).toContain("word-ignored");
    });

    it("does not apply masteryColor for ignored active_state", () => {
      const { getByRole } = render(WordSpan, {
        props: {
          word: makeWordToken({ active_state: "ignored", progress: 0.5, srs_item_id: null }),
        },
      });
      expect(getByRole("button").getAttribute("style")).toBe("");
    });

    it("applies inline masteryColor style for new active_state (dynamic)", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "new", progress: 0.5 }) },
      });
      const el = getByRole("button");
      expect(el.getAttribute("style")).toContain("color:");
    });

    it("applies inline masteryColor style for learning active_state (dynamic)", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "learning", progress: 0.3 }) },
      });
      const el = getByRole("button");
      expect(el.getAttribute("style")).toContain("color:");
    });

    it("applies inline masteryColor style for review active_state (dynamic)", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "review", progress: 0.8 }) },
      });
      const el = getByRole("button");
      expect(el.getAttribute("style")).toContain("color:");
    });

    it("applies inline masteryColor style for relearning active_state (dynamic)", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "relearning", progress: 0.1 }) },
      });
      const el = getByRole("button");
      expect(el.getAttribute("style")).toContain("color:");
    });
  });

  describe("is_due cue", () => {
    it("adds word-due class when word.is_due is true", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ is_due: true }) },
      });
      expect(getByRole("button").className).toContain("word-due");
    });

    it("does not add word-due class when word.is_due is false", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ is_due: false }) },
      });
      expect(getByRole("button").className).not.toContain("word-due");
    });
  });

  it("has no title attribute on the word span", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "learning" }) },
    });
    expect(getByRole("button").getAttribute("title")).toBeNull();
  });

  describe("interlinear gloss (showGloss)", () => {
    it("does not render word-gloss by default", () => {
      const { container } = render(WordSpan, {
        props: { word: makeWordToken({ translation: "hello" }) },
      });
      expect(container.querySelector(".word-gloss")).toBeNull();
    });

    it("renders word-gloss when showGloss is true and word has translation", () => {
      const { container } = render(WordSpan, {
        props: { word: makeWordToken({ translation: "hello" }), showGloss: true },
      });
      const gloss = container.querySelector(".word-gloss");
      expect(gloss).not.toBeNull();
      expect(gloss!.textContent).toBe("hello");
    });

    it("does not render word-gloss when showGloss is true but translation is null", () => {
      const { container } = render(WordSpan, {
        props: { word: makeWordToken({ translation: null }), showGloss: true },
      });
      expect(container.querySelector(".word-gloss")).toBeNull();
    });
  });

  it("shows translation text in tooltip element", () => {
    const { container } = render(WordSpan, {
      props: { word: makeWordToken({ translation: "hello", srs_state: "new" }) },
    });
    const tooltip = container.querySelector('[role="tooltip"]');
    expect(tooltip).not.toBeNull();
    expect(tooltip!.textContent).toContain("hello");
  });

  it("shows readable state label in tooltip element", () => {
    const { container } = render(WordSpan, {
      props: { word: makeWordToken({ translation: null, srs_state: "learning" }) },
    });
    const tooltip = container.querySelector('[role="tooltip"]');
    expect(tooltip).not.toBeNull();
    expect(tooltip!.textContent).toContain("Not Due");
  });

  it("does not render tooltip when requireModifier=true and altHover=false", () => {
    const { container } = render(WordSpan, {
      props: {
        word: makeWordToken({ translation: "hello", srs_state: "new" }),
        requireModifier: true,
        altHover: false,
      },
    });
    expect(container.querySelector('[role="tooltip"]')).toBeNull();
  });

  it("renders tooltip when requireModifier=true and altHover=true", () => {
    const { container } = render(WordSpan, {
      props: {
        word: makeWordToken({ translation: "hello", srs_state: "new" }),
        requireModifier: true,
        altHover: true,
      },
    });
    expect(container.querySelector('[role="tooltip"]')).not.toBeNull();
  });

  it("updates color reactively when active_state changes", async () => {
    const { getByRole, rerender } = render(WordSpan, {
      props: { word: makeWordToken({ active_state: "new", progress: 0 }) },
    });

    expect(getByRole("button").getAttribute("style")).toContain("color:");

    await rerender({ word: makeWordToken({ active_state: "ignored", srs_item_id: null }) });

    await waitFor(() => {
      // flips off the ramp: inline color cleared, static class applied
      expect(getByRole("button").className).toContain("word-ignored");
      expect(getByRole("button").getAttribute("style")).toBe("");
    });
  });

  describe("requireModifier", () => {
    it("plain click does not fire onWordClick when requireModifier is true", async () => {
      const onWordClick = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onWordClick, requireModifier: true },
      });
      await fireEvent.click(getByRole("button"));
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("Alt+click no longer fires onWordClick (grading moved to the popover)", async () => {
      const onWordClick = vi.fn();
      const word = makeWordToken({ lemma: "zdravo", srs_item_id: 7 });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick, requireModifier: true },
      });
      await fireEvent.click(getByRole("button"), { altKey: true });
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("Shift+click no longer fires onWordClick (grading moved to the popover)", async () => {
      const onWordClick = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onWordClick, requireModifier: true },
      });
      await fireEvent.click(getByRole("button"), { shiftKey: true });
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("plain Enter does not fire onWordClick when requireModifier is true", async () => {
      const onWordClick = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onWordClick, requireModifier: true },
      });
      await fireEvent.keyDown(getByRole("button"), { key: "Enter" });
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("Alt+Enter fires onWordClick when requireModifier is true", async () => {
      const onWordClick = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onWordClick, requireModifier: true },
      });
      await fireEvent.keyDown(getByRole("button"), { key: "Enter", altKey: true });
      expect(onWordClick).toHaveBeenCalled();
    });

    it("applies word-selected class when selected=true and requireModifier=true", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), requireModifier: true, selected: true },
      });
      expect(getByRole("button").className).toContain("word-selected");
    });

    it("plain click bubbles to parent when requireModifier is true", async () => {
      const onWordClick = vi.fn();
      const parentHandler = vi.fn();
      const { getByRole, container } = render(WordSpan, {
        props: { word: makeWordToken(), onWordClick, requireModifier: true },
      });
      container.addEventListener("click", parentHandler);
      await fireEvent.click(getByRole("button"));
      expect(onWordClick).not.toHaveBeenCalled();
      expect(parentHandler).toHaveBeenCalled();
    });
  });

  describe("lineIndex / wordIndex props", () => {
    it("renders data-line-index when lineIndex prop is provided", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), lineIndex: 2, wordIndex: 3 },
      });
      expect(getByRole("button").getAttribute("data-line-index")).toBe("2");
    });

    it("renders data-word-index when wordIndex prop is provided", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), lineIndex: 0, wordIndex: 5 },
      });
      expect(getByRole("button").getAttribute("data-word-index")).toBe("5");
    });

    it("does not render data-line-index when lineIndex is not provided", () => {
      const { getByRole } = render(WordSpan, { props: { word: makeWordToken() } });
      expect(getByRole("button").getAttribute("data-line-index")).toBeNull();
    });
  });

  describe("selected prop", () => {
    it("applies word-selected class when selected={true}", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), selected: true },
      });
      expect(getByRole("button").className).toContain("word-selected");
    });

    it("does not apply word-selected class by default", () => {
      const { getByRole } = render(WordSpan, { props: { word: makeWordToken() } });
      expect(getByRole("button").className).not.toContain("word-selected");
    });

    it("does not apply word-selected class when selected={false}", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), selected: false },
      });
      expect(getByRole("button").className).not.toContain("word-selected");
    });
  });

  describe("punctuation guarding", () => {
    it("renders prefix and suffix punctuation spans inside the button", () => {
      const { container } = render(WordSpan, {
        props: { word: makeWordToken({ prefix_punct: '"', suffix_punct: "," }) },
      });
      const puncts = container.querySelectorAll(".punct");
      expect(puncts.length).toBe(2);
      expect(puncts[0].textContent).toBe('"');
      expect(puncts[1].textContent).toBe(",");
    });

    it("does not fire onWordClick when clicking on prefix punctuation", async () => {
      const onWordClick = vi.fn();
      const { container } = render(WordSpan, {
        props: { word: makeWordToken({ prefix_punct: '"' }), onWordClick },
      });
      const puncts = container.querySelectorAll(".punct");
      await fireEvent.click(puncts[0]);
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("does not fire onWordClick when clicking on suffix punctuation", async () => {
      const onWordClick = vi.fn();
      const { container } = render(WordSpan, {
        props: { word: makeWordToken({ suffix_punct: "," }), onWordClick },
      });
      const puncts = container.querySelectorAll(".punct");
      await fireEvent.click(puncts[1]);
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("passes the given lineIndex to onWordClick via the grade button", async () => {
      const onWordClick = vi.fn();
      const word = makeWordToken({ lemma: "zdravo", active_state: "unknown", suffix_punct: "?" });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick, lineIndex: 7 },
      });
      await fireEvent.click(getByRole("button", { name: "Start learning" }));
      expect(onWordClick).toHaveBeenCalledWith(word, 7);
    });
  });
});
