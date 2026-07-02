/**
 * Tests for ProposedBatch.svelte.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import ProposedBatch from "./ProposedBatch.svelte";
import type { ProposedBatch as Batch } from "$lib/api";

const proposed: Batch = {
  start_day: 3,
  days: [
    {
      day: 3,
      title: "At the market",
      focus: "buying produce",
      collocations: ["koliko stane", "eno kavo"],
      learning_objective: "ask prices",
      story_guidance: "haggling scene",
    },
    {
      day: 4,
      title: "Ordering lunch",
      focus: "restaurant phrases",
      collocations: ["jedilni list"],
      learning_objective: "order a meal",
      story_guidance: "",
    },
  ],
};

function setup(overrides: Record<string, unknown> = {}) {
  const onCommit = vi.fn().mockResolvedValue(undefined);
  const onRevise = vi.fn();
  const utils = render(ProposedBatch, {
    props: { proposed, pending: false, onCommit, onRevise, ...overrides },
  });
  return { onCommit, onRevise, ...utils };
}

describe("ProposedBatch", () => {
  it("renders the day range header", () => {
    const { getByText } = setup();
    expect(getByText(/proposed: days 3–4/i)).toBeTruthy();
  });

  it("singular header for a one-day batch", () => {
    const { getByText } = setup({ proposed: { start_day: 3, days: [proposed.days[0]] } });
    expect(getByText(/proposed: day 3/i)).toBeTruthy();
  });

  it("renders a card per day with title, focus, objective and collocation chips", () => {
    const { getByText, getAllByText, container } = setup();
    expect(getByText("At the market")).toBeTruthy();
    expect(getByText("buying produce")).toBeTruthy();
    expect(getByText("ask prices")).toBeTruthy();
    expect(getByText("koliko stane")).toBeTruthy();
    expect(getByText("eno kavo")).toBeTruthy();
    expect(getAllByText(/day \d/i).length).toBeGreaterThanOrEqual(2);
    expect(container.querySelectorAll(".day-card")).toHaveLength(2);
  });

  it("shows story guidance only when present", () => {
    const { getByText, container } = setup();
    expect(getByText("haggling scene")).toBeTruthy();
    expect(container.querySelectorAll(".guidance")).toHaveLength(1);
  });

  it("Commit batch calls onCommit", async () => {
    const { onCommit, getByRole } = setup();
    await fireEvent.click(getByRole("button", { name: /commit batch/i }));
    expect(onCommit).toHaveBeenCalled();
  });

  it("Revise calls onRevise", async () => {
    const { onRevise, getByRole } = setup();
    await fireEvent.click(getByRole("button", { name: /revise/i }));
    expect(onRevise).toHaveBeenCalled();
  });

  it("disables both buttons while pending", () => {
    const { getByRole } = setup({ pending: true });
    expect((getByRole("button", { name: /commit batch/i }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((getByRole("button", { name: /revise/i }) as HTMLButtonElement).disabled).toBe(true);
  });
});
