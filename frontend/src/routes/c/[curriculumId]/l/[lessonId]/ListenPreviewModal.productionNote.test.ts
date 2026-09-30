/**
 * ORACLE for bd tunatale-dvdm.2 — a deferred row says where production is
 * practised.
 *
 * A listen defers a word whose RECOGNITION is well known. Whether its
 * PRODUCTION was ever practised is a separate direction with a separate
 * schedule, so "well recognized" reads on the row as "nothing left here" for a
 * word the user cannot yet produce. The preview therefore has to say so — and
 * only where that is true: a row that is not deferred carries no note, however
 * unpractised its production is.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "$lib/components/ListenPreviewModal.svelte";
import { api, type ListenPreviewCandidate } from "$lib/api";
import { listenCountdownPref } from "$lib/stores/listenCountdownPref.svelte";
import { candidateId } from "$lib/../test/factories";

vi.mock("$lib/api", () => ({
  api: {
    getListenPreview: vi.fn(),
    markAsListened: vi.fn(),
    getListens: vi.fn(),
  },
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

// ── Fixtures ──────────────────────────────────────────────────────────

const NOTE = "recognition solid — production is practised in the review queue";

const base = {
  rating: "good" as const,
  translation: "a gloss",
  progress: 0.95,
  deferred_reason: "known" as const,
  well_known: true,
  production_unpractised: false,
  due_at: "2126-01-01T04:00:00+00:00",
  will_create: true,
};

/** Deferred (recognition known) AND production never practised — the only
 *  population the note is allowed to appear on. */
const unpractisedRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  item_id: candidateId(text),
  kind: "word",
  text,
  grade_class: "ahead",
  production_unpractised: true,
});

/** Deferred with production already solid: recognition-only news is nothing. */
const practisedRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  item_id: candidateId(text),
  kind: "word",
  text,
  grade_class: "ahead",
});

/** Not deferred at all, so nothing about it is suppressed — the note would be
 *  a lie on this row. */
const dueRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  item_id: candidateId(text),
  kind: "word",
  text,
  grade_class: "due",
  deferred_reason: null,
  well_known: false,
  progress: 0.4,
  due_at: "2026-08-04T04:00:00+00:00",
});

const preview = () => ({
  candidates: [unpractisedRow("fisk"), practisedRow("takk"), dueRow("hage")],
});

// ── Helpers ───────────────────────────────────────────────────────────

async function open() {
  mockGetListenPreview.mockResolvedValue(preview());
  const rendered = render(ListenPreviewModal, {
    props: { lessonId: "l1", onDone: vi.fn() },
  });
  await waitFor(() => rendered.getByText("fisk"));
  return rendered;
}

/** The `li.candidate` row whose word cell reads `text`. */
function rowFor(container: HTMLElement, text: string): HTMLElement {
  const row = [...container.querySelectorAll("li.candidate")].find(
    (li) => li.querySelector(".text")?.textContent?.trim() === text,
  );
  if (!row) throw new Error(`no row for ${text}`);
  return row as HTMLElement;
}

// ── Tests ─────────────────────────────────────────────────────────────

describe("dvdm.2 — the production note", () => {
  it("appears exactly once, on the row whose production is unpractised", async () => {
    const { container } = await open();
    expect(container.querySelectorAll(".production-note")).toHaveLength(1);
  });

  it("sits inside the row that carries the word it is about", async () => {
    const { container } = await open();
    // Placement is the whole assertion: a page-wide text match would pass with
    // the note rendered once on the WRONG row, which is the bug this guards.
    const note = rowFor(container, "fisk").querySelector(".production-note");
    expect(note?.textContent?.trim()).toBe(NOTE);
    expect(rowFor(container, "takk").querySelector(".production-note")).toBeNull();
  });

  it("never appears on a row that is not deferred", async () => {
    const { container } = await open();
    expect(rowFor(container, "hage").querySelector(".production-note")).toBeNull();
  });
});
