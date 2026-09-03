/**
 * ORACLE for F-5's frontend half — learning rows behave like known rows.
 * Issue: bd tunatale-aql.
 *
 * ⚠️ Authored ahead of the implementation on purpose. If an assertion here
 * looks wrong, STOP and report it — do not adjust it to match what you built.
 *
 * ── The decision ──────────────────────────────────────────────────────
 *
 * User, 2026-08-04, revising their own first answer the same day: *"maybe
 * learning should be treated like known words? I think they should be skipped
 * by default but I'd prefer that they are visible and auto-gradable opt-in like
 * known is."* The first answer would have dropped the rows from the preview
 * entirely; this one keeps them visible and merely un-rated.
 *
 * ── Why the backend sends a reason instead of a second boolean ────────
 *
 * `buildRatings` twice calls out its sharpest edge: for these rows, ABSENT from
 * word_ratings means *skip*, while for every other row it means *good*. There
 * are now two populations with that inverted polarity. One `deferred_reason`
 * field means one polarity rule — `if (c.deferred_reason) skip` — instead of a
 * second boolean and a second copy of the rule, which is how the third copy
 * gets written wrong. The next deferred category costs a value, not a branch.
 *
 * So these tests key off `deferred_reason`, never `well_known`: `well_known` is
 * derived from it server-side and exists only for compatibility.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, fireEvent } from "@testing-library/svelte";
import ListenPreviewModal from "$lib/components/ListenPreviewModal.svelte";
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
const mockMarkAsListened = vi.mocked(api.markAsListened);

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
  deferred_reason: null,
  well_known: false,
  due_at: null,
  will_create: true,
};

const dueRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "due",
  due_at: "2026-08-04T04:00:00+00:00",
});
const learningRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "learning",
  deferred_reason: "learning",
  due_at: "2026-08-04T10:20:00+00:00",
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
});
const createRow = (text: string): ListenPreviewCandidate => ({
  ...base,
  kind: "create",
  text,
  grade_class: "create",
  item_id: null,
  progress: null,
});

// Asymmetric on purpose: 3 now / 0 later / 2 learning / 1 known, so no two
// counts can be transposed without a test noticing.
const preview = () => ({
  candidates: [
    createRow("alle"),
    dueRow("helst"),
    dueRow("nabolag"),
    learningRow("hage"),
    learningRow("svinge"),
    knownRow("takk"),
  ],
});

// ── Helpers ───────────────────────────────────────────────────────────

async function open() {
  mockGetListenPreview.mockResolvedValue(preview());
  const rendered = render(ListenPreviewModal, {
    props: { lessonId: "l1", onDone: vi.fn() },
  });
  await waitFor(() => rendered.getByText("alle"));
  return rendered;
}

/** Word cells of the rows inside a named disclosure group, in render order. */
function groupTexts(container: HTMLElement, selector: string): string[] {
  const group = container.querySelector(selector);
  if (!group) return [];
  return [...group.querySelectorAll("li.candidate .text")].map((el) => el.textContent!.trim());
}

/** Word cells of the MAIN (always-visible) list. */
function mainTexts(container: HTMLElement): string[] {
  const list = container.querySelector(".list-head + ul.list");
  if (!list) throw new Error("no main list");
  return [...list.querySelectorAll("li.candidate .text")].map((el) => el.textContent!.trim());
}

const segments = (container: HTMLElement): [string, string][] => {
  const rowEl = container.querySelector(".partition");
  if (!rowEl) return [];
  return [...rowEl.querySelectorAll(":scope > *")].map((seg) => [
    seg.querySelector(".n")?.textContent?.trim() ?? "",
    seg.querySelector(".l")?.textContent?.trim() ?? "",
  ]);
};

const gradeBtn = (container: HTMLElement, key: string, grade: string) =>
  container.querySelector(
    `button[data-candidate='${key}'][data-grade='${grade}']`,
  ) as HTMLButtonElement;

const bulk = (container: HTMLElement, label: string) =>
  [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(label),
  ) as HTMLButtonElement;

const commit = async (container: HTMLElement) => {
  const btn = [...container.querySelectorAll("button")].find((b) =>
    /^Mark \d+ as listened$|^Mark as listened$/.test(b.textContent?.trim() ?? ""),
  ) as HTMLButtonElement;
  await fireEvent.click(btn);
};

/** The word_ratings map actually sent to the API — the only thing that decides
 *  what the backend stages. DOM state is a proxy; this is the real contract. */
const sentWordRatings = () =>
  (mockMarkAsListened.mock.calls.at(-1)![1] ?? {}) as Record<string, string>;

// ── Tests ─────────────────────────────────────────────────────────────

describe("F-5 — learning rows are deferred exactly like known rows", () => {
  it("keeps learning rows OUT of the main list", async () => {
    const { container } = await open();
    // The rejected answer dropped them from the response entirely; this one
    // only moves them out of the always-visible list.
    expect(mainTexts(container)).toEqual(["helst", "nabolag", "alle"]);
  });

  it("renders them in their own collapsed group, not folded into 'known'", async () => {
    const { container } = await open();
    expect(groupTexts(container, ".learning-group")).toEqual(["hage", "svinge"]);
    // Folding the two populations into one group would lose the distinction the
    // whole `deferred_reason` field exists to carry.
    expect(groupTexts(container, ".well-known-group")).toEqual(["takk"]);
  });

  it("uses the same disclosure idiom as every other group", async () => {
    const { container } = await open();
    const group = container.querySelector(".learning-group")!;
    expect(group.tagName).toBe("DETAILS");
    // F-16: the shared rules live on .disclosure-group, once.
    expect(group.classList.contains("disclosure-group")).toBe(true);
    expect(group.querySelector("summary")!.textContent!.trim()).toBe("2 learning words");
  });

  it("counts them in the summary, so the partition still sums to the row count", async () => {
    const { container } = await open();
    expect(segments(container)).toEqual([
      ["3", "now"],
      ["0", "later"],
      ["2", "learning"],
      ["1", "well recognized"],
    ]);
    const total = segments(container).reduce((n, [count]) => n + Number(count), 0);
    expect(total).toBe(preview().candidates.length);
  });

  it("rates them 'skip' by default — the whole point of the change", async () => {
    const { container } = await open();
    // A listen rated `hage` "good" eleven minutes after introducing it. The
    // learning step exists to test recall at a specific interval; a listen is
    // not that test.
    expect(gradeBtn(container, "word:hage", "skip").getAttribute("aria-pressed")).toBe("true");
    expect(gradeBtn(container, "word:hage", "good").getAttribute("aria-pressed")).toBe("false");
  });

  it("stages one only when it is explicitly graded", async () => {
    const { container } = await open();
    await fireEvent.click(gradeBtn(container, "word:hage", "good"));
    await commit(container);

    await waitFor(() => expect(mockMarkAsListened).toHaveBeenCalled());
    const sent = sentWordRatings();
    // Opted in. `hage` MUST appear explicitly: for a deferred row, absence
    // from word_ratings is what tells the backend to leave it alone, so
    // emitting nothing would silently drop the user's grade.
    expect(sent["hage"]).toBe("good");
    // svinge was left at its default — never staged.
    expect(sent["svinge"]).toBe("skip");
  });

  it("leaves them alone on Grade All — a bulk action is not an opt-in", async () => {
    const { container } = await open();
    await fireEvent.click(bulk(container, "Grade All"));

    expect(gradeBtn(container, "word:hage", "skip").getAttribute("aria-pressed")).toBe("true");
    // Known rows already behaved this way; the two populations must not drift.
    expect(gradeBtn(container, "word:takk", "skip").getAttribute("aria-pressed")).toBe("true");
    // ...while an ordinary row IS swept in.
    expect(gradeBtn(container, "word:helst", "good").getAttribute("aria-pressed")).toBe("true");
  });
});

describe("F-5 — the known population is unchanged", () => {
  it("still defers, still sits in its own group, still skips on Grade All", async () => {
    // Regression guard: `known` is being re-expressed through deferred_reason,
    // not redesigned. If this file's other tests pass and this one fails, the
    // learning case was added as a second special case rather than by
    // generalising the existing one.
    const { container } = await open();
    expect(gradeBtn(container, "word:takk", "skip").getAttribute("aria-pressed")).toBe("true");
    expect(mainTexts(container)).not.toContain("takk");
    await fireEvent.click(bulk(container, "Grade All"));
    expect(gradeBtn(container, "word:takk", "skip").getAttribute("aria-pressed")).toBe("true");
  });
});
