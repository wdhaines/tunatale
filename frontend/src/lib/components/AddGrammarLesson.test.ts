/**
 * Tests for AddGrammarLesson.svelte — the curriculum page's "Add a grammar lesson".
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  api: { listGrammarPatterns: vi.fn(), createGrammarLesson: vi.fn() },
}));

import { api } from "$lib/api";
import type { CreatedGrammarLesson, GrammarPatternOption } from "$lib/api";
import AddGrammarLesson from "./AddGrammarLesson.svelte";

const mockList = vi.mocked(api.listGrammarPatterns);
const mockCreate = vi.mocked(api.createGrammarLesson);

// The owner's deck on 2026-10-07, plus a pattern with one root to show the
// "too few" wording.
const patterns: GrammarPatternOption[] = [
  {
    key: "mo-mi",
    title: "Affix drill: mo- / mi-",
    roots: ["lakaw", "inom", "adto", "anhi", "uban"],
    ready: true,
  },
  { key: "mag-nag", title: "Affix drill: mag- / nag-", roots: ["uban"], ready: false },
  { key: "ma-na", title: "Affix drill: ma- / na-", roots: [], ready: false },
];

const created = { day: 3 } as CreatedGrammarLesson;

async function opened(onAdded = vi.fn()) {
  const result = render(AddGrammarLesson, { props: { curriculumId: "cid-1", onAdded } });
  const toggle = await result.findByRole("button", { name: /Add a grammar lesson/ });
  await fireEvent.click(toggle);
  return { ...result, toggle, onAdded };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue(patterns);
});

describe("AddGrammarLesson", () => {
  it("draws nothing for a language with no affix patterns", async () => {
    mockList.mockResolvedValue([]);
    const { container } = render(AddGrammarLesson, {
      props: { curriculumId: "cid-1", onAdded: vi.fn() },
    });

    await waitFor(() => expect(mockList).toHaveBeenCalledWith("cid-1"));
    expect(container.querySelector(".grammar")).toBeNull();
  });

  it("draws nothing when the patterns cannot be fetched", async () => {
    mockList.mockRejectedValue(new Error("boom"));
    const { container } = render(AddGrammarLesson, {
      props: { curriculumId: "cid-1", onAdded: vi.fn() },
    });

    await waitFor(() => expect(mockList).toHaveBeenCalled());
    expect(container.querySelector(".grammar")).toBeNull();
  });

  it("starts closed, and the toggle opens and closes the list", async () => {
    const { container, toggle } = await opened();

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelectorAll(".grammar-pattern")).toHaveLength(3);

    await fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".grammar-patterns")).toBeNull();
  });

  it("says which roots carry each pattern, and why one cannot be drilled yet", async () => {
    const { container } = await opened();

    expect([...container.querySelectorAll(".pattern-roots")].map((e) => e.textContent)).toEqual([
      "5 roots you understand: lakaw, inom, adto, anhi, uban.",
      "1 root you understand: uban. A drill needs two.",
      "No roots yet. A drill needs two you understand.",
    ]);
  });

  it("only a pattern the learner is ready for can be added", async () => {
    const { container } = await opened();

    expect(
      [...container.querySelectorAll<HTMLButtonElement>(".pattern-add")].map((b) => b.disabled),
    ).toEqual([false, true, true]);
  });

  it("adding a pattern creates the lesson, closes the list and tells the page", async () => {
    mockCreate.mockResolvedValue(created);
    const { container, getByRole, onAdded } = await opened();

    await fireEvent.click(getByRole("button", { name: "Add Affix drill: mo- / mi-" }));

    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith("cid-1", "mo-mi");
    expect(container.querySelector(".grammar-patterns")).toBeNull();
    expect(container.querySelector(".grammar-error")).toBeNull();
  });

  it("while one is being added the button says so and nothing else can be started", async () => {
    let finish!: (value: CreatedGrammarLesson) => void;
    mockCreate.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { container, getByRole, onAdded } = await opened();

    await fireEvent.click(getByRole("button", { name: "Add Affix drill: mo- / mi-" }));

    const buttons = [...container.querySelectorAll<HTMLButtonElement>(".pattern-add")];
    expect(buttons[0].textContent?.trim()).toBe("Adding…");
    expect(buttons.every((b) => b.disabled)).toBe(true);
    expect(onAdded).not.toHaveBeenCalled();

    finish(created);
    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1));
  });

  it.each([
    [
      new Error("An affix drill needs at least 2 roots you understand, and mo-mi has 1: inom"),
      /needs at least 2 roots/,
    ],
    ["offline", /offline/],
  ])(
    "a refusal is shown, the list stays open, and the page is not told",
    async (failure, shown) => {
      mockCreate.mockRejectedValue(failure);
      const { container, getByRole, onAdded } = await opened();

      await fireEvent.click(getByRole("button", { name: "Add Affix drill: mo- / mi-" }));

      await waitFor(() =>
        expect(container.querySelector(".grammar-error")?.textContent).toMatch(shown),
      );
      expect(onAdded).not.toHaveBeenCalled();
      expect(container.querySelectorAll(".grammar-pattern")).toHaveLength(3);
      expect(container.querySelector<HTMLButtonElement>(".pattern-add")!.disabled).toBe(false);
    },
  );
});
