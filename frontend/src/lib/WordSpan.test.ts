/**
 * Tests for WordSpan.svelte — per-word SRS state widget.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
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

  it("calls onStateChange with lemma and srs_item_id on click", async () => {
    const onStateChange = vi.fn();
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ lemma: "zdravo", srs_item_id: 42 }), onStateChange },
    });
    await fireEvent.click(getByRole("button"));
    expect(onStateChange).toHaveBeenCalledWith("zdravo", 42);
  });

  it("passes null srs_item_id when word has no card", async () => {
    const onStateChange = vi.fn();
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_item_id: null }), onStateChange },
    });
    await fireEvent.click(getByRole("button"));
    expect(onStateChange).toHaveBeenCalledWith("zdravo", null);
  });

  it("handles Enter key the same as click", async () => {
    const onStateChange = vi.fn();
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken(), onStateChange },
    });
    await fireEvent.keyDown(getByRole("button"), { key: "Enter" });
    expect(onStateChange).toHaveBeenCalled();
  });

  it("handles Space key the same as click", async () => {
    const onStateChange = vi.fn();
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken(), onStateChange },
    });
    await fireEvent.keyDown(getByRole("button"), { key: " " });
    expect(onStateChange).toHaveBeenCalled();
  });

  it("ignores other keys", async () => {
    const onStateChange = vi.fn();
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken(), onStateChange },
    });
    await fireEvent.keyDown(getByRole("button"), { key: "Tab" });
    expect(onStateChange).not.toHaveBeenCalled();
  });

  it("does not throw when onStateChange is not provided", async () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken() },
    });
    await expect(fireEvent.click(getByRole("button"))).resolves.not.toThrow();
  });

  it("shows word-unknown class for unknown srs_state", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "unknown" }) },
    });
    expect(getByRole("button").className).toContain("word-unknown");
  });

  it("shows word-new class for new srs_state", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "new" }) },
    });
    expect(getByRole("button").className).toContain("word-new");
  });

  it("shows word-learning class for learning srs_state", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "learning" }) },
    });
    expect(getByRole("button").className).toContain("word-learning");
  });

  it("shows word-learning class for relearning srs_state", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "relearning" }) },
    });
    expect(getByRole("button").className).toContain("word-learning");
  });

  it("shows word-review class for review srs_state", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "review" }) },
    });
    expect(getByRole("button").className).toContain("word-review");
  });

  it("shows word-known class for known srs_state", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "known" }) },
    });
    expect(getByRole("button").className).toContain("word-known");
  });

  it("shows word-ignored class for suspended srs_state", () => {
    const { getByRole } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "suspended" }) },
    });
    expect(getByRole("button").className).toContain("word-ignored");
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

  it("updates colorClass reactively when srs_state changes", async () => {
    const { getByRole, rerender } = render(WordSpan, {
      props: { word: makeWordToken({ srs_state: "new" }) },
    });

    expect(getByRole("button").className).toContain("word-new");

    await rerender({ word: makeWordToken({ srs_state: "learning" }) });

    await waitFor(() => {
      expect(getByRole("button").className).toContain("word-learning");
    });
  });

  describe("requireModifier", () => {
    it("plain click does not fire onStateChange when requireModifier is true", async () => {
      const onStateChange = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onStateChange, requireModifier: true },
      });
      await fireEvent.click(getByRole("button"));
      expect(onStateChange).not.toHaveBeenCalled();
    });

    it("Alt+click fires onStateChange when requireModifier is true", async () => {
      const onStateChange = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: {
          word: makeWordToken({ lemma: "zdravo", srs_item_id: 7 }),
          onStateChange,
          requireModifier: true,
        },
      });
      await fireEvent.click(getByRole("button"), { altKey: true });
      expect(onStateChange).toHaveBeenCalledWith("zdravo", 7);
    });

    it("Shift+click fires onStateChange when requireModifier is true", async () => {
      const onStateChange = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onStateChange, requireModifier: true },
      });
      await fireEvent.click(getByRole("button"), { shiftKey: true });
      expect(onStateChange).toHaveBeenCalled();
    });

    it("plain Enter does not fire onStateChange when requireModifier is true", async () => {
      const onStateChange = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onStateChange, requireModifier: true },
      });
      await fireEvent.keyDown(getByRole("button"), { key: "Enter" });
      expect(onStateChange).not.toHaveBeenCalled();
    });

    it("Alt+Enter fires onStateChange when requireModifier is true", async () => {
      const onStateChange = vi.fn();
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onStateChange, requireModifier: true },
      });
      await fireEvent.keyDown(getByRole("button"), { key: "Enter", altKey: true });
      expect(onStateChange).toHaveBeenCalled();
    });

    it("applies word-selected class when selected=true and requireModifier=true", () => {
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), requireModifier: true, selected: true },
      });
      expect(getByRole("button").className).toContain("word-selected");
    });

    it("plain click bubbles to parent when requireModifier is true", async () => {
      const onStateChange = vi.fn();
      const parentHandler = vi.fn();
      const { getByRole, container } = render(WordSpan, {
        props: { word: makeWordToken(), onStateChange, requireModifier: true },
      });
      container.addEventListener("click", parentHandler);
      await fireEvent.click(getByRole("button"));
      expect(onStateChange).not.toHaveBeenCalled();
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

  describe("text selection", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("does not fire onStateChange when text is selected (drag-to-copy)", async () => {
      const onStateChange = vi.fn();
      vi.spyOn(window, "getSelection").mockReturnValue({
        toString: () => "dobro hvala",
      } as Selection);
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onStateChange },
      });
      await fireEvent.click(getByRole("button"));
      expect(onStateChange).not.toHaveBeenCalled();
    });

    it("does not fire on Alt+click either when text is selected", async () => {
      const onStateChange = vi.fn();
      vi.spyOn(window, "getSelection").mockReturnValue({
        toString: () => "dobro hvala",
      } as Selection);
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken(), onStateChange, requireModifier: true },
      });
      await fireEvent.click(getByRole("button"), { altKey: true });
      expect(onStateChange).not.toHaveBeenCalled();
    });

    it("fires normally when getSelection returns null", async () => {
      const onStateChange = vi.fn();
      vi.spyOn(window, "getSelection").mockReturnValue(null);
      const { getByRole } = render(WordSpan, {
        props: { word: makeWordToken({ lemma: "zdravo", srs_item_id: 1 }), onStateChange },
      });
      await fireEvent.click(getByRole("button"));
      expect(onStateChange).toHaveBeenCalledWith("zdravo", 1);
    });
  });
});
