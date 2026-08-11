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
 * The row's tag is the only thing carrying mastery: `dueStyle` paints it with
 * `masteryColor(progress)`, a continuous red→green ramp. Two cards at 41% and
 * 52% are visibly different and unreadably so — the hue is the only channel and
 * the eye cannot invert it. The hover names the number.
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
 * ── The label mirrors WordSpan's, deliberately including its carve-out ──
 *
 * The request was "like I have on the transcript", so the vocabulary is
 * WordSpan's: a well-known card reads "known" rather than a percentage. That is
 * not a rounding shortcut — WordSpan suppresses the number because a card
 * scheduled past the listen horizon is one this flow has already stopped asking
 * about, and quoting a work-in-progress percentage would contradict the row's
 * own "known" grouping. The two surfaces must not disagree about that word.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "./ListenPreviewModal.svelte";
import { api, type ListenPreviewCandidate } from "$lib/api";
import { listenCountdownPref } from "$lib/stores/listenCountdownPref.svelte";

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
};

// Today's UTC date at the 04:00 rollover convention, so `dueLabel` reads
// "today". A hardcoded date would drift the tag's text as the calendar moves
// and turn the last test in this file into a nightly failure.
const TODAY_DUE_AT = `${new Date().toISOString().slice(0, 10)}T04:00:00+00:00`;

// 0.374 and 0.416 round to 37% and 42% — deliberately close enough that the
// colour ramp cannot distinguish them, which is the whole reason for the hover.
const dueRow = (text: string, progress: number): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "due",
  progress,
  due_at: TODAY_DUE_AT,
});
const createRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "create",
  text,
  grade_class: "create",
  item_id: null,
  progress: null,
});
const newStateRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "new",
  progress: null,
});
const knownRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "ahead",
  well_known: true,
  progress: 0.95,
  due_at: "2126-01-01T04:00:00+00:00",
});
const tailRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "create",
  text,
  grade_class: "create",
  item_id: null,
  progress: null,
  will_create: false,
});

const preview = () => ({
  candidates: [
    createRow("alle"),
    newStateRow("innover"),
    dueRow("helst", 0.374),
    dueRow("nabolag", 0.416),
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

/** The mastery line inside the popover attached to this row's day tag.
 *  Deliberately reached THROUGH `.tt-wrap` → `.tt` → `.tt-mastery`: those are
 *  Tooltip.svelte's own internals, so a hand-rolled second overlay carrying the
 *  same text would not satisfy this path. */
function tagTooltipMastery(container: HTMLElement, text: string): string | null {
  const tag = row(container, text).querySelector(".tag.day");
  if (!tag) throw new Error(`no day tag on row "${text}"`);
  const wrap = tag.closest(".tt-wrap");
  if (!wrap) return null;
  return wrap.querySelector(".tt .tt-mastery")?.textContent?.trim() ?? null;
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
  it("reports the rounded percentage for a scheduled row", async () => {
    const { container } = await open();
    expect(tagTooltipMastery(container, "helst")).toBe("37%");
    expect(tagTooltipMastery(container, "nabolag")).toBe("42%");
  });

  it("mirrors WordSpan's vocabulary for the rows that have no percentage", async () => {
    const { container } = await open();
    // No card at all — the transcript's word for this is "not tracked".
    expect(tagTooltipMastery(container, "alle")).toBe("not tracked");
    // A card that exists but has never been introduced. The preview already
    // refuses to paint it red-for-0% (`dueStyle`'s NEW carve-out); the label
    // must not reintroduce that same lie in words.
    expect(tagTooltipMastery(container, "innover")).toBe("not started");
  });

  it("says 'known' — not a percentage — for a well-known row", async () => {
    const { container } = await open();
    // 0.95 would render as "95%"; asserting both halves is what makes this a
    // carve-out test rather than a rounding test.
    expect(tagTooltipMastery(container, "takk")).toBe("known");
    expect(tagTooltipMastery(container, "takk")).not.toContain("%");
  });

  it("covers the over-budget tail rows too — they carry the same tag", async () => {
    const { container } = await open();
    expect(tagTooltipMastery(container, "smelte")).toBe("not tracked");
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
});
