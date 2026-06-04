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

  it("calls onWordClick with word and lineIndex on click", async () => {
    const onWordClick = vi.fn();
    const word = makeWordToken({ lemma: "zdravo", srs_item_id: 42 });
    const { getByRole } = render(WordSpan, {
      props: { word, onWordClick, lineIndex: 2 },
    });
    await fireEvent.click(getByRole("button", { name: /^zdravo$/ }));
    expect(onWordClick).toHaveBeenCalledWith(word, 2);
  });

  it("passes 0 as lineIndex when lineIndex prop is not provided", async () => {
    const onWordClick = vi.fn();
    const word = makeWordToken({ srs_item_id: null });
    const { getByRole } = render(WordSpan, {
      props: { word, onWordClick },
    });
    await fireEvent.click(getByRole("button"));
    expect(onWordClick).toHaveBeenCalledWith(word, 0);
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

    it("shows word-known class for known active_state", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ active_state: "known" }) },
      });
      expect(getByRole("button").className).toContain("word-known");
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
    expect(tooltip!.textContent).toContain("Learning");
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

    await rerender({ word: makeWordToken({ active_state: "known" }) });

    await waitFor(() => {
      expect(getByRole("button").className).toContain("word-known");
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

    it("Alt+click fires onWordClick when requireModifier is true", async () => {
      const onWordClick = vi.fn();
      const word = makeWordToken({ lemma: "zdravo", srs_item_id: 7 });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick, requireModifier: true },
      });
      await fireEvent.click(getByRole("button"), { altKey: true });
      expect(onWordClick).toHaveBeenCalledWith(word, 0);
    });

    it("Shift+click fires onWordClick when requireModifier is true", async () => {
      const onWordClick = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onWordClick, requireModifier: true },
      });
      await fireEvent.click(getByRole("button"), { shiftKey: true });
      expect(onWordClick).toHaveBeenCalled();
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

    it("mousedown on punct does not set drag anchor", async () => {
      const onWordClick = vi.fn();
      const { container } = render(WordSpan, {
        props: { word: makeWordToken({ suffix_punct: "," }), onWordClick },
      });
      const puncts = container.querySelectorAll(".punct");
      await fireEvent.mouseDown(puncts[1], { clientX: 0, clientY: 0 });
      await fireEvent.click(puncts[1], { clientX: 50, clientY: 0 });
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("still fires onWordClick when clicking the word surface with punct present", async () => {
      const onWordClick = vi.fn();
      const word = makeWordToken({ lemma: "zdravo", srs_item_id: 1, suffix_punct: "?" });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick },
      });
      await fireEvent.click(getByRole("button", { name: /^zdravo/ }));
      expect(onWordClick).toHaveBeenCalledWith(word, 0);
    });

    it("passes the given lineIndex to onWordClick", async () => {
      const onWordClick = vi.fn();
      const word = makeWordToken({ lemma: "zdravo" });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick, lineIndex: 7 },
      });
      await fireEvent.click(getByRole("button", { name: /^zdravo/ }));
      expect(onWordClick).toHaveBeenCalledWith(word, 7);
    });
  });

  describe("drag-to-select vs click", () => {
    it("does not fire onWordClick on a drag (pointer moved past threshold)", async () => {
      const onWordClick = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onWordClick },
      });
      const el = getByRole("button");
      await fireEvent.mouseDown(el, { clientX: 0, clientY: 0 });
      await fireEvent.click(el, { clientX: 50, clientY: 0 });
      expect(onWordClick).not.toHaveBeenCalled();
    });

    it("fires onWordClick on a click (pointer barely moved)", async () => {
      const onWordClick = vi.fn();
      const word = makeWordToken({ lemma: "zdravo", srs_item_id: 1 });
      const { getByRole } = render(WordSpan, {
        props: { word, onWordClick },
      });
      const el = getByRole("button", { name: /^zdravo$/ });
      await fireEvent.mouseDown(el, { clientX: 10, clientY: 10 });
      await fireEvent.click(el, { clientX: 12, clientY: 11 });
      expect(onWordClick).toHaveBeenCalledWith(word, 0);
    });

    it("still suppresses a drag when requireModifier and Alt are set", async () => {
      const onWordClick = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onWordClick, requireModifier: true },
      });
      const el = getByRole("button");
      await fireEvent.mouseDown(el, { clientX: 0, clientY: 0 });
      await fireEvent.click(el, { clientX: 50, clientY: 0, altKey: true });
      expect(onWordClick).not.toHaveBeenCalled();
    });
  });
});
