/**
 * Tests for ListenPreviewModal — preview of word candidates before listen commit.
 *
 * Uses real backend shapes:
 *   GET /api/srs/lesson/{id}/listen-preview → { candidates: [...] }
 *   Each candidate: { kind, text, item_id, grade_class, rating, translation, progress }
 *
 * The modal calls listenedStore.markListened(lessonId, wordRatings, kpRatings)
 * on commit — NOT api.commitPending.
 *
 * Payload contract (brief §Stage 5 / REDO): only NON-DEFAULT entries are sent.
 * A row left checked + "good" contributes nothing to either map (the backend
 * defaults an absent entry to "good"); an unchecked row sends "skip"; a
 * changed grade sends that grade.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "./ListenPreviewModal.svelte";
import { api } from "$lib/api";
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
  vi.useRealTimers();
  // Existing countdown tests assume a 10s auto-commit; set the pref to "10"
  // so every test that exercises countdown gets the old behavior by default.
  listenCountdownPref.set("10");
});

// ── Fixtures matching real backend shapes ──────────────────────────────

// due_at seeds for the dueness-tag tests must be relative to the CURRENT UTC day.
// Absolute literals are time-bombs: formatDueAt() buckets by UTC midnight, so a
// seed written as "today" on a local calendar date goes red the moment UTC rolls
// past it — 3 tests failed at 2026-07-26T00:00Z while it was still 2026-07-25 EDT.
// 04:00 UTC mirrors the backend's due_at convention.
const dueInDays = (days: number): string => {
  const d = new Date();
  d.setUTCHours(4, 0, 0, 0);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString();
};

const createCandidate = (text: string) => ({
  kind: "create" as const,
  text,
  item_id: null,
  grade_class: "create" as const,
  rating: "good" as const,
  translation: "",
  progress: null,
  well_known: false,
  due_at: null,
});

const wordCandidate = (
  text: string,
  opts?: {
    grade_class?: "create" | "learning" | "due" | "ahead";
    translation?: string;
    progress?: number;
    well_known?: boolean;
    due_at?: string | null;
  },
) => ({
  kind: "word" as const,
  text,
  item_id: 42,
  grade_class: opts?.grade_class ?? ("learning" as const),
  rating: "good" as const,
  translation: opts?.translation ?? "",
  progress: opts?.progress ?? 0.3,
  well_known: opts?.well_known ?? false,
  due_at: opts?.due_at ?? null,
});

const kpCandidate = (text: string, opts?: { translation?: string; progress?: number }) => ({
  kind: "kp" as const,
  text,
  item_id: 99,
  grade_class: "learning" as const,
  rating: "good" as const,
  translation: opts?.translation ?? "",
  progress: opts?.progress ?? 0.1,
  well_known: false,
  due_at: null,
});

// ── Tests ─────────────────────────────────────────────────────────────

describe("ListenPreviewModal", () => {
  it("shows loading state initially", () => {
    mockGetListenPreview.mockReturnValue(new Promise(() => {}));
    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });
    expect(getByText("Loading...")).toBeTruthy();
  });

  it("fetches and displays candidates from flat candidates array", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        createCandidate("kava"),
        createCandidate("mleko"),
        wordCandidate("prosim"),
        kpCandidate("na zdravje"),
      ],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    expect(await waitFor(() => getByText("kava"))).toBeTruthy();
    expect(getByText("mleko")).toBeTruthy();
    expect(getByText("prosim")).toBeTruthy();
    expect(getByText("na zdravje")).toBeTruthy();
  });

  it("shows error when getListenPreview fails", async () => {
    mockGetListenPreview.mockRejectedValue(new Error("preview boom"));

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    expect(await waitFor(() => getByText("preview boom"))).toBeTruthy();
  });

  it("shows empty state when no candidates", async () => {
    mockGetListenPreview.mockResolvedValue({ candidates: [] });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    expect(await waitFor(() => getByText("No new words to add."))).toBeTruthy();
  });

  it("default selection: ALL checked (creates and tracked alike)", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        createCandidate("kava"),
        createCandidate("mleko"),
        wordCandidate("prosim"),
        kpCandidate("hvala"),
      ],
    });

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      const checkboxes = container.querySelectorAll("input[type='checkbox']");
      expect(checkboxes.length).toBe(4);
    });

    const checkboxes = container.querySelectorAll(
      "input[type='checkbox']",
    ) as NodeListOf<HTMLInputElement>;
    expect(checkboxes[0].checked).toBe(true); // kava (create)
    expect(checkboxes[1].checked).toBe(true); // mleko (create)
    expect(checkboxes[2].checked).toBe(true); // prosim (word)
    expect(checkboxes[3].checked).toBe(true); // hvala (kp)
  });

  it("Mark button shows correct count based on selection", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), createCandidate("mleko"), wordCandidate("prosim")],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 3 as listened")).toBeTruthy();
    });
  });

  it("toggle checkbox updates selection and Mark count", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), createCandidate("mleko"), wordCandidate("prosim")],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 3 as listened")).toBeTruthy();
    });

    const checkboxes = container.querySelectorAll(
      "input[type='checkbox']",
    ) as NodeListOf<HTMLInputElement>;
    await fireEvent.click(checkboxes[0]);
    expect(getByText("Mark 2 as listened")).toBeTruthy();

    await fireEvent.click(checkboxes[0]);
    expect(getByText("Mark 3 as listened")).toBeTruthy();
  });

  it("Select All checks all candidates", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), wordCandidate("prosim"), kpCandidate("hvala")],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 3 as listened")).toBeTruthy();
    });

    // Uncheck one, then Select All
    const checkboxes = container.querySelectorAll(
      "input[type='checkbox']",
    ) as NodeListOf<HTMLInputElement>;
    await fireEvent.click(checkboxes[0]);

    await fireEvent.click(getByText("Select All"));

    const cbs = container.querySelectorAll(
      "input[type='checkbox']",
    ) as NodeListOf<HTMLInputElement>;
    for (const cb of cbs) {
      expect(cb.checked).toBe(true);
    }
    expect(getByText("Mark 3 as listened")).toBeTruthy();
  });

  it("Skip All unchecks all candidates and leaves the commit button enabled", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), wordCandidate("prosim")],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 2 as listened")).toBeTruthy();
    });

    await fireEvent.click(getByText("Skip All"));

    const checkboxes = container.querySelectorAll(
      "input[type='checkbox']",
    ) as NodeListOf<HTMLInputElement>;
    for (const cb of checkboxes) {
      expect(cb.checked).toBe(false);
    }
    // F4: a listen that stages nothing is still a legitimate listen — the
    // commit button is only disabled by loading/committing/error, never by
    // selectedCount.
    const markBtn = getByText("Mark as listened") as HTMLButtonElement;
    expect(markBtn.disabled).toBe(false);
  });

  it("Skip All then commit sends an all-skip payload for every candidate", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), wordCandidate("prosim")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 2,
      listen_count: 1,
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 2 as listened")).toBeTruthy();
    });

    await fireEvent.click(getByText("Skip All"));
    await fireEvent.click(getByText("Mark as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { kava: "skip", prosim: "skip" }, {});
    });
  });

  it("clicking a rating button marks it active in the rendered UI", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { getByText, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("kava")).toBeTruthy();
    });

    // Default rating is "good" — its button starts active.
    const goodBtns = await findAllByText("Good");
    expect(goodBtns[0].classList.contains("active")).toBe(true);

    const easyBtns = await findAllByText("Easy");
    await fireEvent.click(easyBtns[0]);

    expect(easyBtns[0].classList.contains("active")).toBe(true);
    expect(goodBtns[0].classList.contains("active")).toBe(false);
  });

  // The full grade domain — matching DrillCard.svelte's vocabulary and order
  // (Again, Hard, Good, Easy). The checkbox is the only way to express
  // "skip"; there is no per-row Skip button.

  it("renders the four real grades in Again, Hard, Good, Easy order with no per-row Skip button", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("kava"));

    const ratingBtns = container.querySelector(".rating-btns");
    expect(ratingBtns).toBeTruthy();
    const labels = Array.from(ratingBtns!.querySelectorAll("button")).map((b) => b.textContent);
    expect(labels).toEqual(["Again", "Hard", "Good", "Easy"]);
  });

  it("clicking Again or Hard marks it active and clears the previously active grade", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { getByText, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("kava"));

    const goodBtns = await findAllByText("Good");
    const hardBtns = await findAllByText("Hard");
    const againBtns = await findAllByText("Again");

    await fireEvent.click(hardBtns[0]);
    expect(hardBtns[0].classList.contains("active")).toBe(true);
    expect(goodBtns[0].classList.contains("active")).toBe(false);

    await fireEvent.click(againBtns[0]);
    expect(againBtns[0].classList.contains("active")).toBe(true);
    expect(hardBtns[0].classList.contains("active")).toBe(false);
  });

  it("a row left checked + good sends nothing (backend defaults absent entries to good)", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        createCandidate("kava"), // create → word_ratings, but default → omitted
        wordCandidate("mleko"), // word → word_ratings, but default → omitted
        kpCandidate("na zdravje"), // kp → kp_ratings, but default → omitted
      ],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 2,
      staged: 1,
      remaining_candidates: 0,
      listen_count: 3,
    });
    const onDone = vi.fn();

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    const btn = await waitFor(() => getByText("Mark 3 as listened"));
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {});
      expect(onDone).toHaveBeenCalledWith({
        status: "ok",
        created: 2,
        staged: 1,
        remaining_candidates: 0,
        listen_count: 3,
      });
    });
  });

  it("unchecked candidates get skip in their respective map; default siblings are omitted", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), kpCandidate("na zdravje")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 2 as listened")).toBeTruthy();
    });

    // Uncheck kava (first checkbox)
    const checkbox = container.querySelector("input[type='checkbox']") as HTMLInputElement;
    await fireEvent.click(checkbox);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { kava: "skip" }, {});
    });
  });

  it("commit error shows error message", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockRejectedValue(new Error("commit failed"));

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    const btn = await waitFor(() => getByText("Mark 1 as listened"));
    await fireEvent.click(btn);

    expect(await waitFor(() => getByText("commit failed"))).toBeTruthy();
  });

  it("commit shows non-Error stringified", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockRejectedValue("plain error");

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    const btn = await waitFor(() => getByText("Mark 1 as listened"));
    await fireEvent.click(btn);

    expect(await waitFor(() => getByText("plain error"))).toBeTruthy();
  });

  // F5 regression: commit must never mutate the $state rating map. After a
  // failed commit, re-checking a row must NOT still send "skip" for it.
  it("re-checking a row after unchecking it does not leave it stuck as skip", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 1,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));

    const checkbox = container.querySelector("input[type='checkbox']") as HTMLInputElement;
    await fireEvent.click(checkbox); // uncheck → skip
    expect(checkbox.checked).toBe(false);
    await fireEvent.click(checkbox); // re-check → restores good (default, omitted)
    expect(checkbox.checked).toBe(true);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {});
    });
  });

  it("shows key phrase tag for kind=kp candidates", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [kpCandidate("na zdravje")],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    expect(await waitFor(() => getByText("key phrase"))).toBeTruthy();
  });

  it("shows grade_class tag for tracked word candidates", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due", translation: "please" })],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    expect(await waitFor(() => getByText("due"))).toBeTruthy();
    expect(getByText("please")).toBeTruthy();
  });

  // ── F6: duplicate `text` across kinds must not collide ─────────────────

  it("word and kp candidates sharing the same text render independent rows and route to separate maps", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("voda"), kpCandidate("voda")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, container, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(container.querySelectorAll("input[type='checkbox']").length).toBe(2);
    });

    const checkboxes = container.querySelectorAll(
      "input[type='checkbox']",
    ) as NodeListOf<HTMLInputElement>;
    // Uncheck only the word row.
    await fireEvent.click(checkboxes[0]);
    expect(checkboxes[0].checked).toBe(false);
    expect(checkboxes[1].checked).toBe(true); // kp row unaffected

    // Give the kp row a non-default rating so it shows up in the payload,
    // proving it routes independently of the (skipped) word row.
    const easyBtns = await findAllByText("Easy");
    expect(easyBtns.length).toBe(2);
    await fireEvent.click(easyBtns[1]);

    await fireEvent.click(getByText(/Mark 1 as listened/));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { voda: "skip" }, { voda: "easy" });
    });
  });

  it("Good rating button leaves a word candidate's rating at the default (omitted from payload)", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 1,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));

    const goodBtns = await findAllByText("Good");
    await fireEvent.click(goodBtns[0]);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {});
    });
  });

  it("'again' on a tracked word survives into the commit payload", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));

    const againBtns = await findAllByText("Again");
    await fireEvent.click(againBtns[0]);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { prosim: "again" }, {});
    });
  });

  it("'hard' on a create candidate routes into word_ratings", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 1,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("kava"));

    const hardBtns = await findAllByText("Hard");
    await fireEvent.click(hardBtns[0]);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { kava: "hard" }, {});
    });
  });

  it("'again' on a kp candidate routes into kp_ratings", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [kpCandidate("na zdravje")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("na zdravje"));

    const againBtns = await findAllByText("Again");
    await fireEvent.click(againBtns[0]);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, { "na zdravje": "again" });
    });
  });

  // F5: unchecking always sends "skip" regardless of the prior grade;
  // re-checking always restores the DEFAULT "good" — not the grade that was
  // active before the row was unchecked.
  it("unchecking a row after picking 'again' sends skip; re-checking restores good, not again", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, findAllByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));

    const againBtns = await findAllByText("Again");
    await fireEvent.click(againBtns[0]);

    const checkbox = container.querySelector("input[type='checkbox']") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);

    await fireEvent.click(checkbox); // uncheck → skip, regardless of "again"
    expect(checkbox.checked).toBe(false);

    await fireEvent.click(checkbox); // re-check → restores default "good", not "again"
    expect(checkbox.checked).toBe(true);
    const goodBtns = await findAllByText("Good");
    expect(goodBtns[0].classList.contains("active")).toBe(true);
    expect(againBtns[0].classList.contains("active")).toBe(false);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      // Restored to the default (good) → omitted from the payload.
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {});
    });
  });

  it("rating changes are reflected in the commit call", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 1,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));

    const easyBtns = await findAllByText("Easy");
    await fireEvent.click(easyBtns[0]);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { prosim: "easy" }, {});
    });
  });

  // ── countdown: visibility, decrement, zero-candidates auto-commit ──────

  it("shows a decrementing 'Auto-marking' countdown while running", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await vi.advanceTimersByTimeAsync(0);

    const countdownText = () => container.querySelector(".countdown")?.textContent ?? "";
    expect(countdownText()).toContain("Auto-marking");
    expect(countdownText()).toContain("10");

    await vi.advanceTimersByTimeAsync(1000);
    expect(countdownText()).toContain("9");
  });

  it("F4: the countdown is visible even with zero candidates, and it auto-commits an empty listen", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({ candidates: [] });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });
    const onDone = vi.fn();

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelector(".countdown")?.textContent ?? "").toContain("Auto-marking");

    await vi.advanceTimersByTimeAsync(10_000);

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledTimes(1);
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {});
      expect(onDone).toHaveBeenCalledTimes(1);
    });
  });

  it("destroying the modal with the countdown still running commits nothing", async () => {
    // Navigating away (browser Back, a nav link) destroys the component without
    // any pointerdown/keydown reaching the overlay, so the countdown is still
    // armed. Without a destroy-time clearInterval the timer keeps ticking on a
    // dead component and fires a listen for a lesson the user has left.
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    const onDone = vi.fn();

    const { unmount } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(3000);
    unmount();
    await vi.advanceTimersByTimeAsync(15_000);

    expect(mockMarkAsListened).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("10-second countdown auto-commits the default (all-omitted) payload after 10s with no interaction", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 1,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });
    const onDone = vi.fn();

    render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    // Wait for candidates to load
    await vi.advanceTimersByTimeAsync(0);

    expect(await waitFor(() => mockGetListenPreview)).toHaveBeenCalled();

    // Advance 10 seconds — auto-commits
    await vi.advanceTimersByTimeAsync(10_000);

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledTimes(1);
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {});
      expect(onDone).toHaveBeenCalledTimes(1);
    });
  });

  it("a checkbox click cancels the countdown permanently", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 1,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });
    const onDone = vi.fn();

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(0);

    await vi.advanceTimersByTimeAsync(5_000);
    const checkbox = container.querySelector("input[type='checkbox']") as HTMLInputElement;
    await fireEvent.click(checkbox);

    await vi.advanceTimersByTimeAsync(15_000);

    expect(mockMarkAsListened).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  // F1/F8: pointerdown, focusin, and keydown must each independently cancel
  // the countdown — not just a checkbox click. A real Escape in a real
  // browser lands on document.activeElement, which must be inside the modal.

  it("a pointerdown on the overlay cancels the countdown permanently", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 1,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });
    const onDone = vi.fn();

    const { getByRole } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(0);

    const dialog = getByRole("dialog");
    await fireEvent.pointerDown(dialog);

    await vi.advanceTimersByTimeAsync(10_000);

    expect(mockMarkAsListened).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("a focusin on a child control cancels the countdown permanently", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 1,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });
    const onDone = vi.fn();

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(0);

    const checkbox = container.querySelector("input[type='checkbox']") as HTMLInputElement;
    await fireEvent.focusIn(checkbox);

    await vi.advanceTimersByTimeAsync(10_000);

    expect(mockMarkAsListened).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("a keydown (non-Escape) on the overlay cancels the countdown permanently", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 1,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });
    const onDone = vi.fn();

    const { getByRole } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(0);

    const dialog = getByRole("dialog");
    await fireEvent.keyDown(dialog, { key: "Tab" });

    await vi.advanceTimersByTimeAsync(10_000);

    expect(mockMarkAsListened).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("advancing past 10s after a manual cancel does not also auto-commit", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    const onDone = vi.fn();

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(0);
    await fireEvent.click(getByText("Cancel"));

    await vi.advanceTimersByTimeAsync(10_000);

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith({ status: "cancelled" });
    expect(mockMarkAsListened).not.toHaveBeenCalled();
  });

  // ── F1: focus management — the overlay must actually take focus so a real
  // Escape keypress (which targets document.activeElement) reaches it. ────

  it("focuses the modal on mount so document.activeElement is inside it", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { getByRole, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("kava"));

    const dialog = getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("a real Escape keypress on document.activeElement closes the modal with no commit", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    const onDone = vi.fn();

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await waitFor(() => getByText("kava"));

    // Dispatch on document.activeElement — exactly what a real browser does,
    // unlike dispatching directly on the dialog node regardless of focus.
    await fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });

    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith({ status: "cancelled" });
      expect(mockMarkAsListened).not.toHaveBeenCalled();
    });
  });

  it("Escape cancels modal without committing", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    const onDone = vi.fn();

    const { getByText, getByRole } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await waitFor(() => getByText("kava"));

    const dialog = getByRole("dialog");
    await fireEvent.keyDown(dialog, { key: "Escape" });

    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ status: "cancelled" }));
      expect(mockMarkAsListened).not.toHaveBeenCalled();
    });
  });

  it("Cancel button closes modal without committing", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    const onDone = vi.fn();

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await waitFor(() => getByText("kava"));

    await fireEvent.click(getByText("Cancel"));

    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ status: "cancelled" }));
      expect(mockMarkAsListened).not.toHaveBeenCalled();
    });
  });

  // ── Well-known: disclosure, buildRatings fix, selectedCount ─────────

  it("well-known rows render inside a collapsed disclosure, unchecked", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        wordCandidate("prosim", { grade_class: "due" }),
        wordCandidate("hvala", {
          grade_class: "ahead",
          well_known: true,
          due_at: "2126-01-01T04:00:00+00:00",
        }),
      ],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));

    // The well-known row is inside a <details> element
    const details = container.querySelector("details");
    expect(details).toBeTruthy();
    expect(getByText("1 well-known word")).toBeTruthy();

    // It is unchecked
    const wk = container.querySelector(
      "input[type='checkbox'][data-candidate='word:hvala']",
    ) as HTMLInputElement;
    expect(wk).toBeTruthy();
    expect(wk.checked).toBe(false);

    // The main row is checked
    const main = container.querySelector(
      "input[type='checkbox'][data-candidate='word:prosim']",
    ) as HTMLInputElement;
    expect(main.checked).toBe(true);
  });

  it("selectedCount excludes well-known rows until checked", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        wordCandidate("prosim", { grade_class: "due" }),
        wordCandidate("hvala", {
          grade_class: "ahead",
          well_known: true,
          due_at: "2126-01-01T04:00:00+00:00",
        }),
      ],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 1 as listened")).toBeTruthy();
    });

    // Check the well-known row
    const wk = container.querySelector(
      "input[type='checkbox'][data-candidate='word:hvala']",
    ) as HTMLInputElement;
    await fireEvent.click(wk);

    await waitFor(() => {
      expect(getByText("Mark 2 as listened")).toBeTruthy();
    });
  });

  it("well-known rows contribute nothing to payload when left alone", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        wordCandidate("prosim", { grade_class: "due" }),
        wordCandidate("hvala", {
          grade_class: "ahead",
          well_known: true,
          due_at: "2126-01-01T04:00:00+00:00",
        }),
      ],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("Mark 1 as listened"));
    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      // Only prosim is in wordRatings (checked+good → omitted as default);
      // hvala (unchecked well-known) sends "skip" so the backend won't stage it.
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { hvala: "skip" }, {});
    });
  });

  // ── Guardrail test (verbatim from brief §2) ────────────────────────

  it("a checked well-known row sends an explicit 'good'; an ordinary one is still omitted", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        wordCandidate("prosim", { grade_class: "due" }),
        wordCandidate("hvala", {
          grade_class: "ahead",
          well_known: true,
          due_at: "2126-01-01T04:00:00+00:00",
        }),
      ],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    // Only the ordinary row is selected on load — the well-known row is
    // collapsed, unchecked, and out of the count.
    await waitFor(() => {
      expect(getByText("Mark 1 as listened")).toBeTruthy();
    });

    const wellKnown = container.querySelector(
      "input[type='checkbox'][data-candidate='word:hvala']",
    ) as HTMLInputElement;
    expect(wellKnown).toBeTruthy();
    expect(wellKnown.checked).toBe(false);

    // Opt it in, leaving the rating at the default "good".
    await fireEvent.click(wellKnown);
    await fireEvent.click(getByText("Mark 2 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { hvala: "good" }, {});
    });
  });

  // ── Dueness tag ────────────────────────────────────────────────────

  it("shows a future dueness tag for a due card", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due", due_at: dueInDays(3) })],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("3d")).toBeTruthy();
    });
  });

  it("shows 'today' tag for a card due today", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due", due_at: dueInDays(0) })],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("today")).toBeTruthy();
    });
  });

  it("shows a negative dueness tag for an overdue card", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due", due_at: dueInDays(-2) })],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("-2d")).toBeTruthy();
    });
  });

  it("shows no dueness tag when due_at is null", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due", due_at: null })],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));
    expect(container.querySelector(".tag.due")).toBeNull();
  });

  it("shows no dueness tag for create rows", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("kava"));
    expect(container.querySelector(".tag.due")).toBeNull();
  });

  it("shows no dueness tag when due_at is an invalid date string", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due", due_at: "not-a-date" })],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));
    expect(container.querySelector(".tag.due")).toBeNull();
  });

  // ── Countdown pref ─────────────────────────────────────────────────

  it("pref 'off' shows no countdown and never auto-commits", async () => {
    listenCountdownPref.set("off");
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(container.querySelector(".countdown")).toBeNull();
    });

    // Advance well past any countdown — should never commit
    vi.useFakeTimers();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(mockMarkAsListened).not.toHaveBeenCalled();
  });

  it("pref '30' shows 30s countdown and auto-commits at 30s", async () => {
    listenCountdownPref.set("30");
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    vi.useFakeTimers();
    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(container.querySelector(".countdown")?.textContent).toContain("30");
    });

    await vi.advanceTimersByTimeAsync(30_000);

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalled();
    });
  });
});
