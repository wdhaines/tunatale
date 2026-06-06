/**
 * Tests for Tooltip.svelte — interactive hover popover.
 */
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/svelte";
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
});
