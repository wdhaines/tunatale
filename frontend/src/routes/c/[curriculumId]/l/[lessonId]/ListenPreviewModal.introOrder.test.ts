/**
 * ORACLE: the preview renders the server's introduction pool in ONE run.
 *
 * ⚠️ Authored against the BACKEND's stated contract, not against the
 * component. `get_listen_preview`'s docstring says, verbatim:
 *
 *     Array order (frontend contract): create rows come first, in rank order,
 *     live rows (`will_create` True) before tail rows (`will_create` False);
 *     then the tracked rows sorted as today. Do not reorder or interleave.
 *
 * and `_allocate_intro_pool` ranks creations and NEW-state cards in ONE pool by
 * corpus frequency (F-2), which is why `_GROUP_RANK["new"] = -1` sorts NEW-state
 * rows alongside the creations rather than with the reviews.
 *
 * The modal used to build its live list as
 *
 *     [...filter(kind !== 'create'), ...filter(kind === 'create')]
 *
 * which partitions on "does a card row already exist" — the same invisible
 * distinction the reader stopped drawing in colour and wording — and split that
 * single pool in half. Measured on the user's own deck (163-row preview,
 * 2026-09-13): six NEW-state cards at rendered positions 0-5, then 100
 * unrelated "ahead" rows, then four create rows at 106-109. The user's report
 * was "why are there some new at the top and some at the bottom".
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "$lib/components/ListenPreviewModal.svelte";
import { api, type ListenPreviewCandidate } from "$lib/api";
import { listenCountdownPref } from "$lib/stores/listenCountdownPref.svelte";

vi.mock("$lib/api", () => ({
  api: { getListenPreview: vi.fn(), markAsListened: vi.fn(), getListens: vi.fn() },
}));
vi.mock("$lib/stores/listened.svelte", async () => {
  const actual = await vi.importActual<typeof import("$lib/stores/listened.svelte")>(
    "$lib/stores/listened.svelte",
  );
  return { listenedStore: actual.listenedStore };
});

const mockGetListenPreview = vi.mocked(api.getListenPreview);

beforeEach(() => {
  vi.clearAllMocks();
  listenCountdownPref.set("off");
});

const base = {
  item_id: 42,
  rating: "good" as const,
  translation: "",
  progress: 0.3,
  deferred_reason: null,
  well_known: false,
  due_at: null,
  will_create: true,
};

const createRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "create",
  text,
  grade_class: "create",
  item_id: null,
  progress: null,
});
// A card that EXISTS but has never been studied. Shares the introduction
// budget with the creates and is ranked in the same pool.
const newCardRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "new",
  progress: null,
});
const aheadRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "ahead",
  due_at: "2027-01-01T04:00:00+00:00",
});

// The server's documented order: creates, then NEW-state cards, then reviews.
// Interleaved names on purpose — c2/n1 adjacency is what a kind-partition
// breaks.
const preview = () => ({
  candidates: [
    createRow("c1"),
    createRow("c2"),
    newCardRow("n1"),
    newCardRow("n2"),
    aheadRow("a1"),
    aheadRow("a2"),
    aheadRow("a3"),
  ],
});

function liveTexts(container: HTMLElement): string[] {
  // The FIRST ul.list is the live list; the tail and the deferred groups
  // render their own lists inside <details>.
  const list = container.querySelector("ul.list") as HTMLElement;
  return [...list.querySelectorAll("li.candidate")].map(
    (li) => li.querySelector(".text")?.textContent?.trim() ?? "",
  );
}

async function open() {
  mockGetListenPreview.mockResolvedValue(preview());
  const rendered = render(ListenPreviewModal, {
    props: { lessonId: "l1", onDone: vi.fn() },
  });
  await waitFor(() => rendered.getByText("c1"));
  return rendered;
}

describe("the listen preview renders the server's order", () => {
  it("preserves the server array order exactly", async () => {
    const { container } = await open();
    expect(liveTexts(container)).toEqual(["c1", "c2", "n1", "n2", "a1", "a2", "a3"]);
  });

  it("keeps every introduction row in ONE contiguous run", async () => {
    const { container } = await open();
    const texts = liveTexts(container);
    const intro = texts.filter((t) => t.startsWith("c") || t.startsWith("n"));
    const positions = intro.map((t) => texts.indexOf(t));
    // Contiguous from 0: no review row may fall between two intro rows. This
    // is the assertion the old kind-partition failed.
    expect(positions).toEqual(positions.map((_, i) => i));
    expect(intro).toHaveLength(4);
  });

  it("does not hoist existing NEW-state cards above the creates", async () => {
    const { container } = await open();
    const texts = liveTexts(container);
    // The specific inversion the old code produced: tracked rows first.
    expect(texts.indexOf("n1")).toBeGreaterThan(texts.indexOf("c2"));
  });
});
