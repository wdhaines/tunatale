/**
 * ORACLE for F-4's second half — the hover on the dueness/mastery tag.
 * Issue: bd tunatale-dnj.
 *
 * ⚠️ Authored ahead of the implementation on purpose. If an assertion here
 * looks wrong, STOP and report it — do not adjust it to match what you built.
 *
 * ── What the user asked for ───────────────────────────────────────────
 *
 * "would be ideal to go from most red down and have a hover maybe so I can see
 * the exact redness like I have on the hover on the transcript."
 *
 * Superseded in part by two-sided mastery (bd tunatale-yh47.6, 2026-09-10):
 * the tag no longer carries a blended red→green hue. It has the reader's twin
 * rails painted under it, and its hover names BOTH sides in two lines
 * ("Understand: … / Produce: …") from `masteryBands.ts::masterySides`, the same
 * function WordSpan's popover calls.
 *
 * ── Why it reuses Tooltip.svelte rather than adding a second overlay ──
 *
 * `5c794d6` and then `ef74a33` fixed popover overflow — a popover near the
 * right edge running off the document — INSIDE `Tooltip.svelte`, gated on
 * "displayed", which covers the hover-revealed case. A second overlay would not
 * inherit either fix and would rediscover the bug on a second surface. So this
 * file pins the component, not just the text: the popover must be Tooltip's own
 * `.tt` / `.tt-mastery` structure, nested inside Tooltip's `.tt-wrap`.
 *
 * ── The label is WordSpan's, by construction ──
 *
 * The request was "like I have on the transcript", so both surfaces call one
 * function; the agreement test below pins that they print identical lines for
 * identical bands. The old "well recognized" carve-out is gone: the understand
 * line's band ("Half a year +") already says it, without hiding produce.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "$lib/components/ListenPreviewModal.svelte";
import WordSpan from "$lib/WordSpan.svelte";
import { api, type ListenPreviewCandidate } from "$lib/api";
import { listenCountdownPref } from "$lib/stores/listenCountdownPref.svelte";
import { makeWordToken } from "$lib/../test/factories";

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

const base = {
  item_id: 42,
  rating: "good" as const,
  translation: "",
  progress: 0.3,
  well_known: false,
  due_at: null,
  will_create: true,
  understand_band: null as string | null,
  understand_stability: null as number | null,
  produce_band: null as string | null,
  produce_stability: null as number | null,
};

// Today's UTC date at the 04:00 rollover convention, so `dueLabel` reads
// "today". A hardcoded date would drift the tag's text as the calendar moves
// and turn the last test in this file into a nightly failure.
const TODAY_DUE_AT = `${new Date().toISOString().slice(0, 10)}T04:00:00+00:00`;

const dueBands: {
  understand_band: string;
  understand_stability: number;
  produce_band: string;
  produce_stability: number | null;
} = {
  understand_band: "weeks",
  understand_stability: 10,
  produce_band: "months",
  produce_stability: 45,
};

// 0.374 and 0.416 round to 37% and 42% — deliberately close enough that the
// colour ramp cannot distinguish them, which is the whole reason for the hover.
const dueRow = (
  text: string,
  progress: number,
  bands?: Partial<typeof dueBands>,
): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "due",
  progress,
  due_at: TODAY_DUE_AT,
  ...dueBands,
  ...bands,
});
const createRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "create",
  text,
  grade_class: "create",
  item_id: null,
  progress: null,
  understand_band: "none",
  produce_band: "none",
});
const newStateRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "new",
  progress: null,
  understand_band: "new",
  produce_band: "new",
});
const knownRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "ahead",
  deferred_reason: "known",
  well_known: true,
  progress: 0.95,
  due_at: "2126-01-01T04:00:00+00:00",
  understand_band: "solid",
  understand_stability: 400,
  produce_band: "new",
});
const tailRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "create",
  text,
  grade_class: "create",
  item_id: null,
  progress: null,
  will_create: false,
  understand_band: "none",
  produce_band: "none",
});

const preview = () => ({
  candidates: [
    createRow("alle"),
    newStateRow("innover"),
    dueRow("helst", 0.374),
    dueRow("nabolag", 0.416, {
      understand_band: "months",
      understand_stability: 45,
      produce_band: "solid",
      produce_stability: 200,
    }),
    tailRow("smelte"),
    knownRow("takk"),
  ],
});

// ── Helpers ───────────────────────────────────────────────────────────

/** The `<li>` whose word cell reads `text`. Searches every list in the modal,
 *  so it reaches rows inside the collapsed disclosures too — a `<details>`
 *  hides its content visually but keeps it in the DOM. */
function row(container: HTMLElement, text: string): HTMLElement {
  const found = [...container.querySelectorAll("li.candidate")].find(
    (li) => li.querySelector(".text")?.textContent?.trim() === text,
  );
  if (!found) throw new Error(`no candidate row for "${text}"`);
  return found as HTMLElement;
}

/** The mastery lines inside the popover attached to this row's day tag.
 *  A create row's single "not tracked" label comes back as a one-element list;
 *  a tracked row's two-side label comes back as its two elements; absent → null.
 *  Deliberately reached THROUGH `.tt-wrap` → `.tt` → `.tt-mastery` / `.tt-side`:
 *  those are Tooltip.svelte's own internals, so a hand-rolled second overlay
 *  carrying the same text would not satisfy this path. */
function tagTooltipLines(container: HTMLElement, text: string): string[] | null {
  const tag = row(container, text).querySelector(".tag.day");
  if (!tag) throw new Error(`no day tag on row "${text}"`);
  const wrap = tag.closest(".tt-wrap");
  if (!wrap) return null;
  const mastery = wrap.querySelector(".tt .tt-mastery")?.textContent?.trim();
  if (mastery != null) return [mastery];
  const sides = [...wrap.querySelectorAll(".tt .tt-side")];
  if (sides.length > 0) return sides.map((s) => s.textContent?.trim() ?? "");
  return null;
}

async function open() {
  mockGetListenPreview.mockResolvedValue(preview());
  const rendered = render(ListenPreviewModal, {
    props: { lessonId: "l1", onDone: vi.fn() },
  });
  await waitFor(() => rendered.getByText("alle"));
  return rendered;
}

// ── Tests ─────────────────────────────────────────────────────────────

describe("F-4 — the day tag names its exact mastery on hover", () => {
  it("reports both sides for a scheduled row", async () => {
    const { container } = await open();
    expect(tagTooltipLines(container, "helst")).toEqual([
      "Understand: Weeks · holds ~1 week",
      "Produce: Months · holds ~6 weeks",
    ]);
    expect(tagTooltipLines(container, "nabolag")).toEqual([
      "Understand: Months · holds ~6 weeks",
      "Produce: Half a year + · holds ~7 months",
    ]);
  });

  it("mirrors WordSpan's vocabulary for the rows that have no percentage", async () => {
    const { container } = await open();
    // No card at all — the transcript's word for this is "not tracked".
    expect(tagTooltipLines(container, "alle")).toEqual(["not tracked"]);
    // A card that exists but has never been introduced — every direction reads
    // "Not started", exactly as the reader's rails describe it.
    expect(tagTooltipLines(container, "innover")).toEqual([
      "Understand: Not started",
      "Produce: Not started",
    ]);
  });

  it("shows the two-line sides — not a percentage — for a well-known row", async () => {
    const { container } = await open();
    // 0.95 would render as "95%"; asserting both halves is what makes this a
    // carve-out test rather than a rounding test.
    const lines = tagTooltipLines(container, "takk");
    expect(lines).toEqual(["Understand: Half a year + · holds ~1.1 years", "Produce: Not started"]);
    expect(lines!.join(" ")).not.toContain("%");
  });

  it("covers the over-budget tail rows too — they carry the same tag", async () => {
    const { container } = await open();
    expect(tagTooltipLines(container, "smelte")).toEqual(["not tracked"]);
  });

  it("uses Tooltip.svelte's own popover, never a second overlay", async () => {
    const { container } = await open();
    const tag = row(container, "helst").querySelector(".tag.day")!;
    const wrap = tag.closest(".tt-wrap");
    expect(wrap, "the day tag must be wrapped by Tooltip.svelte").not.toBeNull();
    const popover = wrap!.querySelector(".tt");
    expect(popover).not.toBeNull();
    // Tooltip's popover announces itself; a bare styled <div> would not.
    expect(popover!.getAttribute("role")).toBe("tooltip");
  });

  it("leaves the tag itself — its classes and its text — untouched", async () => {
    // The tag is a fixed-width grid cell measured to the pixel by
    // `listen-preview-layout.spec.ts`. Wrapping it must not rename it, restyle
    // it, or move the dueness label off it.
    const { container } = await open();
    const tag = row(container, "helst").querySelector(".tag.day")!;
    expect(tag.textContent?.trim()).toBe("today");
    expect(row(container, "innover").querySelector(".tag.day")!.textContent?.trim()).toBe("new");
  });

  it("marks every 'new' pill — create and NEW-state rows alike — for the untracked blue", async () => {
    // User, 2026-09-10: "new" reads in the reader's untracked-word blue. Both
    // populations print "new", so both carry the class; a dated pill does not.
    const { container } = await open();
    const dayTag = (text: string) => row(container, text).querySelector(".tag.day")!;
    expect(dayTag("alle").classList.contains("is-new")).toBe(true);
    expect(dayTag("innover").classList.contains("is-new")).toBe(true);
    expect(dayTag("helst").classList.contains("is-new")).toBe(false);
  });
});

describe("F-4 — WordSpan and the preview describe the same card identically", () => {
  it("produces identical advances side lists for identical band values", async () => {
    const BANDS = {
      understand_band: "months",
      understand_stability: 90,
      produce_band: "none",
      produce_stability: null,
    };
    const rendered = render(WordSpan, {
      props: { word: makeWordToken({ active_state: "review", ...BANDS }) },
    });
    const wordSides = [...rendered.container.querySelectorAll(".tt-side")].map(
      (el) => el.textContent?.trim() ?? "",
    );
    expect(wordSides).toEqual(["Understand: Months · holds ~3 months", "Produce: No card"]);
    rendered.unmount();

    mockGetListenPreview.mockResolvedValue({
      candidates: [dueRow("mesec", 0.5, BANDS)],
    });
    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });
    await waitFor(() => getByText("mesec"));
    const previewSides = [...row(container, "mesec").querySelectorAll(".tt-side")].map(
      (el) => el.textContent?.trim() ?? "",
    );
    expect(previewSides).toEqual(wordSides);
  });
});
