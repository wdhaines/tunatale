/**
 * The action row at the foot of the sticky card, and the one thing it gained
 * in bd tunatale-685k: with the player collapsed, the reader's Recall chip
 * sits ON this row beside Mark as Listened, the same height as the button
 * (the user: "keep only recall and do it inline with Mark as Listened").
 * The height is geometry and is pinned in tests/reader-chips-layout.spec.ts;
 * what is pinned here is that the chip is a direct child of the row, which is
 * what lets the row stretch it.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";

import ListenActions from "./ListenActions.svelte";
import { readerEnglishPref } from "$lib/stores/readerEnglishPref.svelte";
import { readerProductionPref } from "$lib/stores/readerProductionPref.svelte";

beforeEach(() => {
  localStorage.clear();
  readerEnglishPref.set("off");
  readerProductionPref.set(false);
  localStorage.clear();
});

const listen = (over: Record<string, unknown> = {}) =>
  ({
    listenResult: null,
    queueCount: 0,
    showPreview: false,
    isListened: false,
    showCheckWorkLink: false,
    open: vi.fn(),
    ...over,
  }) as never;

describe("ListenActions", () => {
  it("has no Recall chip unless asked", () => {
    const { container, queryByTestId } = render(ListenActions, {
      props: { listen: listen(), reviewHref: "/review" },
    });
    expect(queryByTestId("reader-recall-chip")).toBeNull();
    expect(container.querySelector(".listen-actions")!.classList.contains("with-recall")).toBe(
      false,
    );
  });

  it("recallInline puts the Recall chip on the row itself, and only Recall", async () => {
    const { container, getByTestId, queryByTestId } = render(ListenActions, {
      props: { listen: listen(), reviewHref: "/review", recallInline: true },
    });
    const row = container.querySelector(".listen-actions")!;
    const recall = getByTestId("reader-recall-chip");
    // A direct child, like the button: a wrapper would stop the row stretching
    // the chip to the button's height.
    expect(recall.parentElement).toBe(row);
    expect(container.querySelector(".listen-btn")!.parentElement).toBe(row);
    expect(row.classList.contains("with-recall")).toBe(true);
    expect(queryByTestId("reader-english-chip")).toBeNull();

    await fireEvent.click(recall);
    expect(readerProductionPref.enabled).toBe(true);
  });

  it("Mark as Listened still opens the preview with the chip beside it", async () => {
    const l = listen();
    const { getByText } = render(ListenActions, {
      props: { listen: l, reviewHref: "/review", recallInline: true },
    });
    await fireEvent.click(getByText("Mark as Listened"));
    expect((l as { open: ReturnType<typeof vi.fn> }).open).toHaveBeenCalledTimes(1);
  });
});
