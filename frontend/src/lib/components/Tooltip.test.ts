/**
 * Tests for Tooltip.svelte — interactive hover popover.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";
import TooltipTest from "./TooltipTest.svelte";
import { makeWordToken } from "$lib/../test/factories";

describe("Tooltip", () => {
  it("renders the child content", () => {
    const { getByText } = render(TooltipTest, {
      props: { translation: null, childText: "zdravo" },
    });
    expect(getByText("zdravo")).toBeTruthy();
  });

  it("renders translation text when provided", () => {
    const { getByRole } = render(TooltipTest, {
      props: { translation: "hello", childText: "zdravo" },
    });
    const tooltip = getByRole("tooltip");
    expect(tooltip.textContent).toContain("hello");
  });

  it('renders "Due" when word is due', () => {
    const word = makeWordToken({ is_due: true, srs_item_id: 1 });
    const { getByRole } = render(TooltipTest, {
      props: { translation: null, word, childText: "zdravo" },
    });
    expect(getByRole("tooltip").textContent).toContain("Due");
  });

  it('renders "Not Due" when word is not due', () => {
    const word = makeWordToken({ is_due: false, srs_item_id: 1 });
    const { getByRole } = render(TooltipTest, {
      props: { translation: null, word, childText: "zdravo" },
    });
    expect(getByRole("tooltip").textContent).toContain("Not Due");
  });

  it("does not render due label when word is not provided", () => {
    const { queryByRole } = render(TooltipTest, {
      props: { translation: "hello", childText: "zdravo" },
    });
    const tooltip = queryByRole("tooltip");
    expect(tooltip?.textContent).not.toContain("Due");
  });

  it("renders no tooltip when both translation and state are null and no actions apply", () => {
    const { queryByRole } = render(TooltipTest, {
      props: { translation: null, childText: "zdravo" },
    });
    expect(queryByRole("tooltip")).toBeNull();
  });

  it("renders no tooltip when suppressed, even though content would otherwise show", () => {
    const word = makeWordToken({ is_due: true, srs_item_id: 1 });
    const { queryByRole, getByText } = render(TooltipTest, {
      props: { translation: "hello", word, childText: "zdravo", suppressed: true },
    });
    // Child still renders (the word itself), but the popover is fully suppressed.
    expect(getByText("zdravo")).toBeTruthy();
    expect(queryByRole("tooltip")).toBeNull();
  });

  it("renders both translation and due label when both provided", () => {
    const word = makeWordToken({ is_due: true, srs_item_id: 1 });
    const { getByRole } = render(TooltipTest, {
      props: { translation: "hello", word, childText: "zdravo" },
    });
    const tooltip = getByRole("tooltip");
    expect(tooltip.textContent).toContain("hello");
    expect(tooltip.textContent).toContain("Due");
  });

  it('has role="tooltip" on the tooltip element', () => {
    const { getByRole } = render(TooltipTest, {
      props: { translation: "hello", childText: "zdravo" },
    });
    expect(getByRole("tooltip")).toBeTruthy();
  });

  // --- Action buttons ---

  it('shows "Create inflection card" button when word is inflectable', () => {
    const word = makeWordToken({ inflectable: true, active_state: "new", srs_item_id: 1 });
    const actions = { onCreateInflection: vi.fn() };
    const { getByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(getByRole("button", { name: /create inflection card/i })).toBeTruthy();
  });

  it('does not show "Create inflection card" when word is not inflectable', () => {
    const word = makeWordToken({ inflectable: false, active_state: "new", srs_item_id: 1 });
    const actions = { onCreateInflection: vi.fn() };
    const { queryByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(queryByRole("button", { name: /create inflection card/i })).toBeNull();
  });

  it("shows Ignore button for tracked states", () => {
    const word = makeWordToken({ active_state: "learning", srs_item_id: 5 });
    const actions = { onUntrack: vi.fn() };
    const { getByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(getByRole("button", { name: /ignore/i })).toBeTruthy();
  });

  it("does not show Ignore for unknown words without srs_item_id", () => {
    const word = makeWordToken({ active_state: "unknown", srs_item_id: null });
    const actions = { onUntrack: vi.fn() };
    const { queryByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(queryByRole("button", { name: /ignore/i })).toBeNull();
  });

  it('shows "Un-ignore" for suspended state', () => {
    const word = makeWordToken({ active_state: "suspended", srs_item_id: 5 });
    const actions = { onSetState: vi.fn() };
    const { getByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(getByRole("button", { name: /un-ignore/i })).toBeTruthy();
  });

  it("shows Known button for learning/review/relearning", () => {
    const word = makeWordToken({ active_state: "learning", srs_item_id: 5 });
    const actions = { onSetState: vi.fn() };
    const { getByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(getByRole("button", { name: /^known$/i })).toBeTruthy();
  });

  it("does not show Known button for known state", () => {
    const word = makeWordToken({ active_state: "known", srs_item_id: 5 });
    const actions = { onSetState: vi.fn() };
    const { queryByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(queryByRole("button", { name: /^known$/i })).toBeNull();
  });

  it('shows "Un-mark known" (not "Known") when known_marked is true', () => {
    const word = makeWordToken({ active_state: "learning", srs_item_id: 5, known_marked: true });
    const actions = { onRestoreKnown: vi.fn() };
    const { getByRole, queryByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(getByRole("button", { name: /un-mark known/i })).toBeTruthy();
    expect(queryByRole("button", { name: /^known$/i })).toBeNull();
  });

  it('does NOT show "Un-mark known" when known_marked is false', () => {
    const word = makeWordToken({ active_state: "learning", srs_item_id: 5, known_marked: false });
    const actions = { onRestoreKnown: vi.fn() };
    const { queryByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(queryByRole("button", { name: /un-mark known/i })).toBeNull();
  });

  it('calls onRestoreKnown with srs_item_id when "Un-mark known" clicked', async () => {
    const word = makeWordToken({ active_state: "learning", srs_item_id: 10, known_marked: true });
    const onRestoreKnown = vi.fn();
    const { getByRole } = render(TooltipTest, {
      props: { word, actions: { onRestoreKnown }, childText: "test" },
    });
    await getByRole("button", { name: /un-mark known/i }).click();
    expect(onRestoreKnown).toHaveBeenCalledWith(10);
  });

  it("shows Reset button for learning/review/relearning/known", () => {
    const word = makeWordToken({ active_state: "review", srs_item_id: 5 });
    const actions = { onSetState: vi.fn() };
    const { getByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(getByRole("button", { name: /^reset$/i })).toBeTruthy();
  });

  it("does not show Reset for new state", () => {
    const word = makeWordToken({ active_state: "new", srs_item_id: 5 });
    const actions = { onSetState: vi.fn() };
    const { queryByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(queryByRole("button", { name: /^reset$/i })).toBeNull();
  });

  it("calls onCreateInflection with word and sentence when button clicked", async () => {
    const word = makeWordToken({ inflectable: true, active_state: "new", srs_item_id: 1 });
    const onCreateInflection = vi.fn();
    const { getByRole } = render(TooltipTest, {
      props: {
        word,
        sentence: "to be or not to be",
        actions: { onCreateInflection },
        childText: "test",
      },
    });
    await getByRole("button", { name: /create inflection card/i }).click();
    expect(onCreateInflection).toHaveBeenCalledWith(word, "to be or not to be");
  });

  it("calls onUntrack with srs_item_id when Ignore clicked", async () => {
    const word = makeWordToken({ active_state: "learning", srs_item_id: 42 });
    const onUntrack = vi.fn();
    const { getByRole } = render(TooltipTest, {
      props: { word, actions: { onUntrack }, childText: "test" },
    });
    await getByRole("button", { name: /ignore/i }).click();
    expect(onUntrack).toHaveBeenCalledWith(42);
  });

  it("calls onUnignore with id when Un-ignore clicked", async () => {
    const word = makeWordToken({ active_state: "suspended", srs_item_id: 7 });
    const onUnignore = vi.fn();
    const { getByRole } = render(TooltipTest, {
      props: { word, actions: { onUnignore }, childText: "test" },
    });
    await getByRole("button", { name: /un-ignore/i }).click();
    expect(onUnignore).toHaveBeenCalledWith(7);
  });

  it("calls onSetState with id and 'known' when Known clicked", async () => {
    const word = makeWordToken({ active_state: "learning", srs_item_id: 10 });
    const onSetState = vi.fn();
    const { getByRole } = render(TooltipTest, {
      props: { word, actions: { onSetState }, childText: "test" },
    });
    await getByRole("button", { name: /^known$/i }).click();
    expect(onSetState).toHaveBeenCalledWith(10, "known");
  });

  it('shows "Ignore" button for unknown word when onIgnoreLemma is provided', () => {
    const word = makeWordToken({ active_state: "unknown", srs_item_id: null });
    const actions = { onIgnoreLemma: vi.fn() };
    const { getByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(getByRole("button", { name: /ignore/i })).toBeTruthy();
  });

  it("calls onIgnoreLemma with lemma when Ignore clicked on unknown word", async () => {
    const word = makeWordToken({ active_state: "unknown", srs_item_id: null, lemma: "banka" });
    const onIgnoreLemma = vi.fn();
    const { getByRole } = render(TooltipTest, {
      props: { word, actions: { onIgnoreLemma }, childText: "test" },
    });
    await getByRole("button", { name: /ignore/i }).click();
    expect(onIgnoreLemma).toHaveBeenCalledWith("banka");
  });

  it('shows "Un-ignore" button for card-less ignored word when onUnignoreLemma is provided', () => {
    const word = makeWordToken({ active_state: "ignored", srs_item_id: null });
    const actions = { onUnignoreLemma: vi.fn() };
    const { getByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(getByRole("button", { name: /un-ignore/i })).toBeTruthy();
  });

  it("calls onUnignoreLemma with lemma when Un-ignore clicked on card-less ignored word", async () => {
    const word = makeWordToken({ active_state: "ignored", srs_item_id: null, lemma: "banka" });
    const onUnignoreLemma = vi.fn();
    const { getByRole } = render(TooltipTest, {
      props: { word, actions: { onUnignoreLemma }, childText: "test" },
    });
    await getByRole("button", { name: /un-ignore/i }).click();
    expect(onUnignoreLemma).toHaveBeenCalledWith("banka");
  });

  it('does NOT show "Un-ignore" for unknown word', () => {
    const word = makeWordToken({ active_state: "unknown", srs_item_id: null });
    const actions = { onUnignoreLemma: vi.fn() };
    const { queryByRole } = render(TooltipTest, {
      props: { word, actions, childText: "test" },
    });
    expect(queryByRole("button", { name: /un-ignore/i })).toBeNull();
  });

  it("calls onSetState with id and 'new' when Reset clicked", async () => {
    const word = makeWordToken({ active_state: "review", srs_item_id: 15 });
    const onSetState = vi.fn();
    const { getByRole } = render(TooltipTest, {
      props: { word, actions: { onSetState }, childText: "test" },
    });
    await getByRole("button", { name: /^reset$/i }).click();
    expect(onSetState).toHaveBeenCalledWith(15, "new");
  });

  // --- Grade button (all grading lives in the popover) ---

  describe("grade button", () => {
    it("renders the grade button with the given label when gradeLabel and onGrade are set", () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { getByRole } = render(TooltipTest, {
        props: { word, childText: "test", gradeLabel: "Got it ✓", onGrade: vi.fn() },
      });
      expect(getByRole("button", { name: "Got it ✓" })).toBeTruthy();
    });

    it("calls onGrade when the grade button is clicked, and keeps the popover open", async () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const onGrade = vi.fn();
      const { getByRole, getByText, container } = render(TooltipTest, {
        props: { word, childText: "test", gradeLabel: "Got it ✓", onGrade },
      });
      const wrap = container.querySelector(".tt-wrap")!;

      await fireEvent.click(getByText("test"));
      expect(wrap.className).toContain("open");

      await fireEvent.click(getByRole("button", { name: "Got it ✓" }));
      expect(onGrade).toHaveBeenCalledTimes(1);
      // Stays open so the user can watch the state advance (cycle) and keep acting.
      expect(wrap.className).toContain("open");
    });

    it("does not render a grade button when gradeLabel is null", () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { queryByRole } = render(TooltipTest, {
        props: { word, childText: "test", gradeLabel: null, onGrade: vi.fn() },
      });
      expect(queryByRole("button", { name: /got it|start learning/i })).toBeNull();
    });

    it("does not render a grade button when onGrade is null", () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { queryByRole } = render(TooltipTest, {
        props: { word, childText: "test", gradeLabel: "Got it ✓", onGrade: null },
      });
      expect(queryByRole("button", { name: /got it/i })).toBeNull();
    });

    it("applies the review-ahead variant class when gradeVariant is 'ahead'", () => {
      const word = makeWordToken({ is_due: false, srs_item_id: 1, recognition_reviewable: true });
      const { getByRole } = render(TooltipTest, {
        props: {
          word,
          childText: "test",
          gradeLabel: "Review ✓",
          gradeVariant: "ahead",
          onGrade: vi.fn(),
        },
      });
      const btn = getByRole("button", { name: "Review ✓" });
      expect(btn.className).toContain("tt-btn-review-ahead");
    });

    it("does not apply the review-ahead class for the default (primary) grade", () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { getByRole } = render(TooltipTest, {
        props: { word, childText: "test", gradeLabel: "Got it ✓", onGrade: vi.fn() },
      });
      expect(getByRole("button", { name: "Got it ✓" }).className).not.toContain(
        "tt-btn-review-ahead",
      );
    });

    it("grade button alone makes the popover renderable (counts as content)", () => {
      const { getByRole } = render(TooltipTest, {
        props: {
          translation: null,
          childText: "test",
          gradeLabel: "Start learning",
          onGrade: vi.fn(),
        },
      });
      expect(getByRole("tooltip")).toBeTruthy();
      expect(getByRole("button", { name: "Start learning" })).toBeTruthy();
    });
  });

  // --- Drill-in button (touch path into a phrase's individual words) ---

  describe("drill-in button", () => {
    it('renders "Words…" when onDrillIn is provided and calls it on click', async () => {
      const onDrillIn = vi.fn();
      const { getByRole } = render(TooltipTest, {
        props: { translation: "good day", childText: "phrase", onDrillIn },
      });
      await fireEvent.click(getByRole("button", { name: /words…/i }));
      expect(onDrillIn).toHaveBeenCalledTimes(1);
    });

    it('does not render "Words…" when onDrillIn is absent', () => {
      const { queryByRole } = render(TooltipTest, {
        props: { translation: "good day", childText: "phrase" },
      });
      expect(queryByRole("button", { name: /words…/i })).toBeNull();
    });
  });

  describe("long-press to open / tap to grade", () => {
    it("tooltip is hidden by default (no hover/press)", () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { container } = render(TooltipTest, {
        props: { translation: "hello", word, childText: "zdravo" },
      });
      const wrap = container.querySelector(".tt-wrap")!;
      expect(wrap.className).not.toContain("open");
    });

    it("a plain tap OPENS the popover (click-to-open) without suppressing child handlers", async () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const onChildClick = vi.fn();
      const { getByText, container } = render(TooltipTest, {
        props: { translation: "hello", word, childText: "tap-me", onChildClick },
      });
      const child = getByText("tap-me");
      const wrap = container.querySelector(".tt-wrap")!;

      await fireEvent.pointerDown(child);
      await fireEvent.pointerUp(child);
      await fireEvent.click(child);

      expect(wrap.className).toContain("open");
      expect(onChildClick).toHaveBeenCalledTimes(1);
    });

    it("a second tap closes the popover (toggle)", async () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { getByText, container } = render(TooltipTest, {
        props: { translation: "hello", word, childText: "tap-me" },
      });
      const child = getByText("tap-me");
      const wrap = container.querySelector(".tt-wrap")!;

      await fireEvent.click(child);
      expect(wrap.className).toContain("open");

      await fireEvent.click(child);
      expect(wrap.className).not.toContain("open");
    });

    it("a click inside the popover body does not toggle it closed", async () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { getByText, getByRole, container } = render(TooltipTest, {
        props: { translation: "hello", word, childText: "tap-me" },
      });
      const wrap = container.querySelector(".tt-wrap")!;

      await fireEvent.click(getByText("tap-me"));
      expect(wrap.className).toContain("open");

      await fireEvent.click(getByRole("tooltip"));
      expect(wrap.className).toContain("open");
    });

    it("a long-press opens the tooltip and suppresses the grade click", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const onChildClick = vi.fn();
        const { getByText, container } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "hold-me", onChildClick },
        });
        const child = getByText("hold-me");
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(child);
        vi.advanceTimersByTime(500);
        await tick();
        expect(wrap.className).toContain("open");

        // The click a long-press fires on release must be swallowed (no grade).
        await fireEvent.pointerUp(child);
        await fireEvent.click(child);
        expect(onChildClick).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });

    it("pointer movement beyond the jitter threshold cancels the long-press (no open)", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText, container } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "drag-me" },
        });
        const child = getByText("drag-me");
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(child, { clientX: 10, clientY: 10 });
        await fireEvent.pointerMove(child, { clientX: 40, clientY: 10 });
        vi.advanceTimersByTime(500);

        expect(wrap.className).not.toContain("open");
      } finally {
        vi.useRealTimers();
      }
    });

    it("small finger jitter does NOT cancel the long-press (touch tremor)", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText, container } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "hold-me" },
        });
        const child = getByText("hold-me");
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(child, { clientX: 10, clientY: 10 });
        await fireEvent.pointerMove(child, { clientX: 14, clientY: 12 });
        vi.advanceTimersByTime(500);
        await tick();

        expect(wrap.className).toContain("open");
      } finally {
        vi.useRealTimers();
      }
    });

    it("pointer movement with no active press is a no-op", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText, container } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "move-me" },
        });
        const child = getByText("move-me");
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerMove(child, { clientX: 100, clientY: 100 });
        vi.advanceTimersByTime(500);

        expect(wrap.className).not.toContain("open");
      } finally {
        vi.useRealTimers();
      }
    });

    it("pointercancel (browser takes over for scrolling) cancels the long-press", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText, container } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "scroll-me" },
        });
        const child = getByText("scroll-me");
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(child);
        await fireEvent.pointerCancel(child);
        vi.advanceTimersByTime(500);

        expect(wrap.className).not.toContain("open");
      } finally {
        vi.useRealTimers();
      }
    });

    it("a non-primary-button press (right-click) never starts a long-press", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText, container } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "right-click-me" },
        });
        const child = getByText("right-click-me");
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(child, { button: 2 });
        vi.advanceTimersByTime(500);

        expect(wrap.className).not.toContain("open");
      } finally {
        vi.useRealTimers();
      }
    });

    it("suppresses the OS context menu while a press is pending (Android long-press)", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "hold-me" },
        });
        const child = getByText("hold-me");

        await fireEvent.pointerDown(child);
        const notPrevented = await fireEvent.contextMenu(child);
        expect(notPrevented).toBe(false);
      } finally {
        vi.useRealTimers();
      }
    });

    it("suppresses the OS context menu after the long-press completes", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "hold-me" },
        });
        const child = getByText("hold-me");

        await fireEvent.pointerDown(child);
        vi.advanceTimersByTime(500);
        await tick();

        const notPrevented = await fireEvent.contextMenu(child);
        expect(notPrevented).toBe(false);
      } finally {
        vi.useRealTimers();
      }
    });

    it("does NOT suppress the context menu when no press is active (desktop right-click)", async () => {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { getByText } = render(TooltipTest, {
        props: { translation: "hello", word, childText: "right-click-me" },
      });
      const child = getByText("right-click-me");

      const notPrevented = await fireEvent.contextMenu(child);
      expect(notPrevented).toBe(true);
    });

    it("tap-outside (pointerdown) closes an open tooltip", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText, container } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "hold-me" },
        });
        const child = getByText("hold-me");
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(child);
        vi.advanceTimersByTime(500);
        await tick();
        expect(wrap.className).toContain("open");

        await fireEvent.pointerDown(document.body);
        expect(wrap.className).not.toContain("open");
      } finally {
        vi.useRealTimers();
      }
    });

    it("pointerdown inside the open tooltip does NOT close it", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const { getByText, container } = render(TooltipTest, {
          props: { translation: "hello", word, childText: "hold-me" },
        });
        const child = getByText("hold-me");
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(child);
        vi.advanceTimersByTime(500);
        await tick();
        expect(wrap.className).toContain("open");

        await fireEvent.pointerDown(child);
        expect(wrap.className).toContain("open");
      } finally {
        vi.useRealTimers();
      }
    });

    it("tapping a DIFFERENT word's wrapper closes this tooltip (scoped outside-check)", async () => {
      vi.useFakeTimers();
      try {
        const word = makeWordToken({ is_due: true, srs_item_id: 1 });
        const first = render(TooltipTest, {
          props: { translation: "hello", word, childText: "first-word" },
        });
        const second = render(TooltipTest, {
          props: { translation: "world", word, childText: "second-word" },
        });
        const firstWrap = first.container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(first.getByText("first-word"));
        vi.advanceTimersByTime(500);
        await tick();
        expect(firstWrap.className).toContain("open");

        // The old closest('.tt-wrap') check matched ANY wrapper, leaving stale
        // popovers open when the user tapped the next word on touch.
        await fireEvent.pointerDown(second.getByText("second-word"));
        expect(firstWrap.className).not.toContain("open");
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("viewport edge clamping", () => {
    /** Long-press open with a mocked tooltip rect, then return the tooltip el. */
    async function openWithRect(rect: { left: number; right: number } | null) {
      const word = makeWordToken({ is_due: true, srs_item_id: 1 });
      const { getByText, container } = render(TooltipTest, {
        props: { translation: "hello", word, childText: "clamp-me" },
      });
      const tt = container.querySelector<HTMLElement>(".tt")!;
      if (rect) {
        tt.getBoundingClientRect = () =>
          ({
            ...rect,
            top: 0,
            bottom: 20,
            width: rect.right - rect.left,
            height: 20,
            x: rect.left,
            y: 0,
            toJSON: () => ({}),
          }) as DOMRect;
      }
      await fireEvent.pointerDown(getByText("clamp-me"));
      vi.advanceTimersByTime(500);
      await tick();
      return { tt, container };
    }

    it("shifts right when the popover would clip the left viewport edge", async () => {
      vi.useFakeTimers();
      try {
        const { tt } = await openWithRect({ left: -20, right: 100 });
        expect(tt.style.transform).toBe("translateX(calc(-50% + 28px))");
      } finally {
        vi.useRealTimers();
      }
    });

    it("shifts left when the popover would clip the right viewport edge", async () => {
      vi.useFakeTimers();
      try {
        // jsdom window.innerWidth is 1024; margin 8 → max right edge 1016.
        const { tt } = await openWithRect({ left: 900, right: 1020 });
        expect(tt.style.transform).toBe("translateX(calc(-50% + -4px))");
      } finally {
        vi.useRealTimers();
      }
    });

    it("applies no shift when the popover fits", async () => {
      vi.useFakeTimers();
      try {
        const { tt } = await openWithRect({ left: 100, right: 200 });
        expect(tt.style.transform).toBe("translateX(calc(-50% + 0px))");
      } finally {
        vi.useRealTimers();
      }
    });

    it("opening with no tooltip content (suppressed/empty) does not crash the clamp", async () => {
      vi.useFakeTimers();
      try {
        const { getByText, container, queryByRole } = render(TooltipTest, {
          props: { translation: null, childText: "empty-me" },
        });
        const wrap = container.querySelector(".tt-wrap")!;

        await fireEvent.pointerDown(getByText("empty-me"));
        vi.advanceTimersByTime(500);
        await tick();

        expect(wrap.className).toContain("open");
        expect(queryByRole("tooltip")).toBeNull();
      } finally {
        vi.useRealTimers();
      }
    });
  });
});
