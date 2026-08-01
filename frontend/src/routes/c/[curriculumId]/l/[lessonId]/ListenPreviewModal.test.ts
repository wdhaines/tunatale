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
 * A row left on "good" contributes nothing to either map (the backend defaults
 * an absent entry to "good"); a row set to Skip sends "skip"; a changed grade
 * sends that grade.
 *
 * There is no checkbox: a row is skipped iff its rating is "skip", expressed by
 * the Skip segment of the per-row grade control. Tests address a specific row's
 * control via [data-candidate="<kind>:<text>"][data-grade="<rating>"].
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "./ListenPreviewModal.svelte";
import { api } from "$lib/api";
import { listenCountdownPref } from "$lib/stores/listenCountdownPref.svelte";
import { masteryBackgroundColor, masteryColor } from "$lib/mastery";

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
  will_create: true,
});

const wordCandidate = (
  text: string,
  opts?: {
    grade_class?: "create" | "new" | "learning" | "due" | "ahead";
    translation?: string;
    progress?: number | null;
    well_known?: boolean;
    due_at?: string | null;
    will_create?: boolean;
  },
) => ({
  kind: "word" as const,
  text,
  item_id: 42,
  grade_class: opts?.grade_class ?? ("learning" as const),
  rating: "good" as const,
  translation: opts?.translation ?? "",
  progress: opts?.progress === undefined ? 0.3 : opts.progress,
  well_known: opts?.well_known ?? false,
  due_at: opts?.due_at ?? null,
  will_create: opts?.will_create ?? true,
});

/** A carded-but-never-introduced word: the row this 2026-08 change surfaces. */
const newStateCandidate = (text: string, opts?: { will_create?: boolean }) =>
  wordCandidate(text, {
    grade_class: "new",
    progress: null,
    due_at: null,
    will_create: opts?.will_create ?? true,
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
  will_create: true,
});

const gradeBtn = (container: HTMLElement, key: string, grade: string) =>
  container.querySelector(
    `button[data-candidate='${key}'][data-grade='${grade}']`,
  ) as HTMLButtonElement;

const isActive = (b: HTMLButtonElement) => b.classList.contains("active");

// jsdom rewrites inline colours into rgb()/rgba() form, so a raw hsl() or hex
// string never appears in the serialized style attribute. Normalize the
// expected value the same way rather than hardcoding the converted output —
// that keeps the assertion tied to mastery.ts, not to a colour literal.
const asInlineColor = (css: string) => {
  const el = document.createElement("span");
  el.style.color = css;
  return el.style.color;
};
const asInlineBackground = (css: string) => {
  const el = document.createElement("span");
  el.style.background = css;
  return el.style.background;
};

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

  it("default selection: every row starts on Good (creates and tracked alike)", async () => {
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
      expect(container.querySelectorAll(".candidate").length).toBe(4);
    });

    for (const key of ["create:kava", "create:mleko", "word:prosim", "kp:hvala"]) {
      expect(isActive(gradeBtn(container, key, "good"))).toBe(true);
      expect(isActive(gradeBtn(container, key, "skip"))).toBe(false);
    }
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

  it("Skip then Good on a row updates the Mark count both ways", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), createCandidate("mleko"), wordCandidate("prosim")],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 3 as listened")).toBeTruthy();
    });

    await fireEvent.click(gradeBtn(container, "create:kava", "skip"));
    expect(getByText("Mark 2 as listened")).toBeTruthy();

    await fireEvent.click(gradeBtn(container, "create:kava", "good"));
    expect(getByText("Mark 3 as listened")).toBeTruthy();
  });

  it("Grade All returns every skipped row to a real grade", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), wordCandidate("prosim"), kpCandidate("hvala")],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 3 as listened")).toBeTruthy();
    });

    // Skip one, then Grade All
    await fireEvent.click(gradeBtn(container, "create:kava", "skip"));

    await fireEvent.click(getByText("Grade All"));

    for (const key of ["create:kava", "word:prosim", "kp:hvala"]) {
      expect(isActive(gradeBtn(container, key, "good"))).toBe(true);
      expect(isActive(gradeBtn(container, key, "skip"))).toBe(false);
    }
    expect(getByText("Mark 3 as listened")).toBeTruthy();
  });

  it("Skip All sets every row to Skip and leaves the commit button enabled", async () => {
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

    for (const key of ["create:kava", "word:prosim"]) {
      expect(isActive(gradeBtn(container, key, "skip"))).toBe(true);
      expect(isActive(gradeBtn(container, key, "good"))).toBe(false);
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith(
        "l1",
        { kava: "skip", prosim: "skip" },
        {},
        [],
        [],
      );
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

  it("renders Skip apart from the four welded grades, in Again/Hard/Good/Easy order", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("kava"));

    // The four real grades live in their own welded group; Skip is a sibling
    // of that group, not a member of it — that separation is the whole point.
    const grades = container.querySelector(".grades");
    expect(grades).toBeTruthy();
    const labels = Array.from(grades!.querySelectorAll("button")).map((b) => b.textContent);
    expect(labels).toEqual(["Again", "Hard", "Good", "Easy"]);

    const skip = gradeBtn(container, "create:kava", "skip");
    expect(skip).toBeTruthy();
    expect(grades!.contains(skip)).toBe(false);
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {}, [], []);
      expect(onDone).toHaveBeenCalledWith({
        status: "ok",
        created: 2,
        staged: 1,
        applied: 0,
        remaining_candidates: 0,
        listen_count: 3,
      });
    });
  });

  it("skipped candidates get skip in their respective map; default siblings are omitted", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava"), kpCandidate("na zdravje")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      applied: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 2 as listened")).toBeTruthy();
    });

    await fireEvent.click(gradeBtn(container, "create:kava", "skip"));

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { kava: "skip" }, {}, [], []);
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
  it("re-grading a row after skipping it does not leave it stuck as skip", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 1,
      applied: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));

    const skip = gradeBtn(container, "word:prosim", "skip");
    const good = gradeBtn(container, "word:prosim", "good");
    await fireEvent.click(skip);
    expect(isActive(skip)).toBe(true);
    await fireEvent.click(good); // back to good → default, omitted
    expect(isActive(good)).toBe(true);
    expect(isActive(skip)).toBe(false);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {}, ["prosim"], []);
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

  it("a tracked word with no due_at falls back to its grade_class in the Due cell", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due", translation: "please" })],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    expect(await waitFor(() => getByText("due"))).toBeTruthy();
    // The gloss is present in the DOM even while blurred — blur is visual only.
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
      applied: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, container, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(container.querySelectorAll(".candidate").length).toBe(2);
    });

    // Skip only the word row. The two rows share the literal text "voda", so
    // addressing them by kind-qualified key is exactly what F6 is about.
    await fireEvent.click(gradeBtn(container, "word:voda", "skip"));
    expect(isActive(gradeBtn(container, "word:voda", "skip"))).toBe(true);
    expect(isActive(gradeBtn(container, "kp:voda", "skip"))).toBe(false); // kp row unaffected

    // Give the kp row a non-default rating so it shows up in the payload,
    // proving it routes independently of the (skipped) word row.
    const easyBtns = await findAllByText("Easy");
    expect(easyBtns.length).toBe(2);
    await fireEvent.click(easyBtns[1]);

    await fireEvent.click(getByText(/Mark 1 as listened/));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith(
        "l1",
        { voda: "skip" },
        { voda: "easy" },
        [],
        ["voda"],
      );
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {}, ["prosim"], []);
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith(
        "l1",
        { prosim: "again" },
        {},
        ["prosim"],
        [],
      );
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { kava: "hard" }, {}, ["kava"], []);
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith(
        "l1",
        {},
        { "na zdravje": "again" },
        [],
        ["na zdravje"],
      );
    });
  });

  // F5: unchecking always sends "skip" regardless of the prior grade;
  // re-checking always restores the DEFAULT "good" — not the grade that was
  // active before the row was unchecked.
  it("skipping a row after picking 'again' sends skip; re-grading gives good, not again", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due" })],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 0,
      staged: 0,
      applied: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });

    const { getByText, findAllByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));

    const againBtns = await findAllByText("Again");
    await fireEvent.click(againBtns[0]);

    const skip = gradeBtn(container, "word:prosim", "skip");
    await fireEvent.click(skip); // → skip, regardless of "again"
    expect(isActive(skip)).toBe(true);
    expect(againBtns[0].classList.contains("active")).toBe(false);

    const goodBtns = await findAllByText("Good");
    await fireEvent.click(goodBtns[0]); // back to the default grade, not "again"
    expect(goodBtns[0].classList.contains("active")).toBe(true);
    expect(againBtns[0].classList.contains("active")).toBe(false);
    expect(isActive(skip)).toBe(false);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      // Restored to the default (good) → omitted from the payload.
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {}, ["prosim"], []);
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { prosim: "easy" }, {}, ["prosim"], []);
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {}, [], []);
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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {}, [], []);
      expect(onDone).toHaveBeenCalledTimes(1);
    });
  });

  it("a grade click cancels the countdown permanently", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      created: 1,
      staged: 0,
      applied: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });
    const onDone = vi.fn();

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(0);

    await vi.advanceTimersByTimeAsync(5_000);
    await fireEvent.click(gradeBtn(container, "create:kava", "hard"));

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
      applied: 0,
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
      applied: 0,
      remaining_candidates: 0,
      listen_count: 1,
    });
    const onDone = vi.fn();

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone },
    });

    await vi.advanceTimersByTimeAsync(0);

    await fireEvent.focusIn(gradeBtn(container, "create:kava", "good"));

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
      applied: 0,
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

  it("well-known rows render inside a collapsed disclosure, pre-set to Skip", async () => {
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
    // Terminology alignment (2026-08): the stats line's bucket is "known".
    // The API field stays `well_known`; only the label changed.
    expect(getByText("1 known word")).toBeTruthy();

    // It starts on Skip
    expect(isActive(gradeBtn(container, "word:hvala", "skip"))).toBe(true);
    expect(isActive(gradeBtn(container, "word:hvala", "good"))).toBe(false);

    // The main row starts on Good
    expect(isActive(gradeBtn(container, "word:prosim", "good"))).toBe(true);
  });

  it("selectedCount excludes well-known rows until they are graded", async () => {
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

    // Grade the well-known row
    await fireEvent.click(gradeBtn(container, "word:hvala", "good"));

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
      applied: 0,
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
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { hvala: "skip" }, {}, [], []);
    });
  });

  // ── Guardrail test (verbatim from brief §2) ────────────────────────

  it("a graded well-known row sends an explicit 'good'; an ordinary one is still omitted", async () => {
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
      applied: 0,
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

    const wellKnown = gradeBtn(container, "word:hvala", "good");
    expect(wellKnown).toBeTruthy();
    expect(isActive(wellKnown)).toBe(false);

    // Opt it in, leaving the rating at the default "good".
    await fireEvent.click(wellKnown);
    await fireEvent.click(getByText("Mark 2 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { hvala: "good" }, {}, ["hvala"], []);
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

  // The Due cell always renders exactly one thing. These three cases have no
  // day count to show, so they must fall back to a word rather than go blank —
  // asserting on the cell's text, not on the absence of a selector that no
  // longer exists (which would pass vacuously).
  const dueCellText = (container: HTMLElement) =>
    container.querySelector(".candidate .tag.day")?.textContent?.trim();

  it("falls back to the grade_class word when due_at is null", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "ahead", due_at: null })],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));
    expect(dueCellText(container)).toBe("ahead");
  });

  it("shows 'new' for create rows, never a day count", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("kava"));
    expect(dueCellText(container)).toBe("new");
  });

  // Terminology alignment (2026-08): the lesson stats line says
  // new / learning / due / review / known, so the preview says the same. This
  // assertion used to read "learn".
  it("shows 'learning' for a learning row", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "learning", due_at: dueInDays(2) })],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));
    expect(dueCellText(container)).toBe("learning");
  });

  it("shows 'new' for a NEW-state word row, identical to a create row", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [newStateCandidate("hansen")],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("hansen"));
    expect(dueCellText(container)).toBe("new");
  });

  it("renders a NEW-state row in the unknown colour, not red-for-0%", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [newStateCandidate("hansen"), createCandidate("kava")],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("hansen"));
    const cells = [...container.querySelectorAll(".tag.day")] as HTMLElement[];
    expect(cells).toHaveLength(2);
    // A NEW-state card has no schedule yet — progress is null, so it must not
    // be coloured as if it were 0% mastered.
    expect(cells[0].getAttribute("style")).toBe(cells[1].getAttribute("style"));
  });

  it("falls back to the grade_class word when due_at is an invalid date string", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [wordCandidate("prosim", { grade_class: "due", due_at: "not-a-date" })],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("prosim"));
    expect(dueCellText(container)).toBe("due");
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
      applied: 0,
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

  // ── Row layout: columns, gloss blur, dueness colour ─────────────────

  describe("row layout", () => {
    it("renders the three column headers", async () => {
      mockGetListenPreview.mockResolvedValue({ candidates: [createCandidate("kava")] });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("kava"));
      const head = container.querySelector(".list-head");
      expect(head).toBeTruthy();
      expect(Array.from(head!.children).map((c) => c.textContent)).toEqual([
        "Word",
        "Due",
        "Proposed grade",
      ]);
    });

    it("the header and every row share one grid template, so the columns line up", async () => {
      // The bug this pins: header and rows are separate grid containers, so an
      // `auto` track resolves per-container and a narrow "new" pill gives that
      // row different columns than a "today" one. jsdom does no layout, so the
      // offsets can't be measured here — the declared template is the thing
      // that must match, and it must contain no `auto` track.
      mockGetListenPreview.mockResolvedValue({
        candidates: [
          createCandidate("kava"),
          wordCandidate("prosim", { grade_class: "due", due_at: dueInDays(-5) }),
        ],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("kava"));

      const templates = [...container.querySelectorAll(".list-head, .candidate")].map(
        (el) => getComputedStyle(el).gridTemplateColumns,
      );
      expect(templates.length).toBe(3); // header + 2 rows
      expect(new Set(templates).size).toBe(1);
      expect(templates[0]).not.toContain("auto");
    });

    it("carries the lesson language on the word so hyphenation uses the right dictionary", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [createCandidate("etterforskningsteam")],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
      });

      await waitFor(() => getByText("etterforskningsteam"));
      expect(container.querySelector(".text")?.getAttribute("lang")).toBe("no");
    });
  });

  describe("gloss blur", () => {
    it("a gloss starts blurred and reveals on click", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [wordCandidate("prosim", { translation: "please" })],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      const gloss = container.querySelector(".gloss") as HTMLButtonElement;
      expect(gloss.classList.contains("blurred")).toBe(true);
      expect(gloss.getAttribute("aria-label")).toBe("Reveal gloss for prosim");

      await fireEvent.click(gloss);
      expect(gloss.classList.contains("blurred")).toBe(false);
      expect(gloss.getAttribute("aria-label")).toBe("please");
    });

    it("revealing one row's gloss does not reveal another's", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [
          wordCandidate("prosim", { translation: "please" }),
          wordCandidate("hvala", { translation: "thanks" }),
        ],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      const glosses = container.querySelectorAll(".gloss") as NodeListOf<HTMLButtonElement>;
      await fireEvent.click(glosses[0]);

      expect(glosses[0].classList.contains("blurred")).toBe(false);
      expect(glosses[1].classList.contains("blurred")).toBe(true);
    });

    it("a revealed gloss widens into the Due column", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [wordCandidate("prosim", { translation: "just (before, after)" })],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      const sub = container.querySelector(".sub") as HTMLElement;
      expect(sub.classList.contains("revealed")).toBe(false);

      await fireEvent.click(container.querySelector(".gloss") as HTMLButtonElement);
      expect(sub.classList.contains("revealed")).toBe(true);
    });

    it("a candidate with no gloss renders a visible placeholder, not an empty cell", async () => {
      // A create row from a lesson generated before token_glosses existed. An
      // absent gloss must look absent rather than look like a layout bug.
      mockGetListenPreview.mockResolvedValue({ candidates: [createCandidate("kava")] });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("kava"));
      const gloss = container.querySelector(".gloss") as HTMLElement;
      expect(gloss.classList.contains("empty")).toBe(true);
      expect(gloss.textContent).toBe("\u2014");
      expect(gloss.tagName).toBe("SPAN"); // not a button — nothing to reveal
    });

    it("a create row shows the gloss the backend resolved from the lesson", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [{ ...createCandidate("brygge"), translation: "wharf, quay" }],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("brygge"));
      const gloss = container.querySelector(".gloss") as HTMLButtonElement;
      expect(gloss.textContent).toBe("wharf, quay");
      expect(gloss.classList.contains("empty")).toBe(false);
    });
  });

  describe("dueness colour follows the dialogue", () => {
    it("an untracked create row uses WordSpan's unknown indigo", async () => {
      mockGetListenPreview.mockResolvedValue({ candidates: [createCandidate("kava")] });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("kava"));
      const style = (container.querySelector(".tag.day") as HTMLElement).getAttribute("style")!;
      expect(style).toContain(asInlineColor("#818cf8"));
    });

    it("a tracked row uses mastery.ts's ramp for its own progress", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [wordCandidate("prosim", { grade_class: "due", progress: 0.5 })],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      const style = (container.querySelector(".tag.day") as HTMLElement).getAttribute("style")!;
      // Compared against the shared helper, not a hardcoded hsl() string — if
      // the ramp is retuned, this test follows it instead of going red.
      expect(style).toContain(asInlineColor(masteryColor(0.5)));
      expect(style).toContain(asInlineBackground(masteryBackgroundColor(0.5)));
    });

    it("dueness is weight, not hue: overdue and due-today are bold, future is not", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [
          wordCandidate("a", { grade_class: "due", due_at: dueInDays(-2) }),
          wordCandidate("b", { grade_class: "due", due_at: dueInDays(0) }),
          wordCandidate("c", { grade_class: "ahead", due_at: dueInDays(5) }),
        ],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("a"));
      const tags = [...container.querySelectorAll(".candidate .tag.day")];
      expect(tags.map((t) => t.classList.contains("overdue"))).toEqual([true, true, false]);
      expect(tags.map((t) => t.textContent?.trim())).toEqual(["-2d", "today", "5d"]);
    });
  });

  // ── Bulk actions preserve per-row grades ────────────────────────────

  describe("Skip All / Grade All round trip", () => {
    const mixed = () => ({
      candidates: [
        wordCandidate("a", { grade_class: "due" }),
        wordCandidate("b", { grade_class: "due" }),
        wordCandidate("c", { grade_class: "due" }),
        wordCandidate("d", { grade_class: "due" }),
      ],
    });

    /** Set one distinct grade per row so a reset-to-good is unmistakable. */
    async function gradeEachDifferently(container: HTMLElement) {
      await fireEvent.click(gradeBtn(container, "word:a", "again"));
      await fireEvent.click(gradeBtn(container, "word:b", "hard"));
      await fireEvent.click(gradeBtn(container, "word:d", "easy"));
      // "c" is left on the default "good".
    }

    const activeGrades = (container: HTMLElement) =>
      ["a", "b", "c", "d"].map(
        (w) =>
          (
            container.querySelector(
              `button[data-candidate='word:${w}'].active`,
            ) as HTMLButtonElement
          )?.dataset.grade,
      );

    it("Grade All on its own leaves already-graded rows alone", async () => {
      mockGetListenPreview.mockResolvedValue(mixed());

      const { getByText, container } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("a"));
      await gradeEachDifferently(container);

      await fireEvent.click(getByText("Grade All"));

      expect(activeGrades(container)).toEqual(["again", "hard", "good", "easy"]);
    });

    it("Skip All then Grade All restores each row's own grade, not Good for all", async () => {
      mockGetListenPreview.mockResolvedValue(mixed());

      const { getByText, container } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("a"));
      await gradeEachDifferently(container);

      await fireEvent.click(getByText("Skip All"));
      expect(activeGrades(container)).toEqual(["skip", "skip", "skip", "skip"]);

      await fireEvent.click(getByText("Grade All"));
      expect(activeGrades(container)).toEqual(["again", "hard", "good", "easy"]);
    });

    it("toggling Skip All / Grade All repeatedly is stable", async () => {
      mockGetListenPreview.mockResolvedValue(mixed());

      const { getByText, container } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("a"));
      await gradeEachDifferently(container);

      for (let i = 0; i < 3; i++) {
        await fireEvent.click(getByText("Skip All"));
        await fireEvent.click(getByText("Grade All"));
      }

      expect(activeGrades(container)).toEqual(["again", "hard", "good", "easy"]);
    });

    it("a row skipped on its own is restored by Grade All", async () => {
      mockGetListenPreview.mockResolvedValue(mixed());

      const { getByText, container } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("a"));
      await fireEvent.click(gradeBtn(container, "word:a", "hard"));
      await fireEvent.click(gradeBtn(container, "word:a", "skip"));

      await fireEvent.click(getByText("Grade All"));

      expect(activeGrades(container)[0]).toBe("hard");
    });

    it("a row that was never graded still comes back as Good", async () => {
      // Well-known rows start on "skip" with no prior grade to remember.
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
      await fireEvent.click(getByText("Grade All"));

      expect(isActive(gradeBtn(container, "word:hvala", "good"))).toBe(true);
    });

    it("the restored grade is what actually gets committed", async () => {
      // The round trip must survive into the payload, not just the button state.
      mockGetListenPreview.mockResolvedValue(mixed());
      mockMarkAsListened.mockResolvedValue({
        status: "ok",
        created: 0,
        staged: 4,
        applied: 0,
        remaining_candidates: 0,
        listen_count: 1,
      });

      const { getByText, container } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("a"));
      await gradeEachDifferently(container);
      await fireEvent.click(getByText("Skip All"));
      await fireEvent.click(getByText("Grade All"));
      await fireEvent.click(getByText("Mark 4 as listened"));

      await waitFor(() => {
        // "c" is back on the default good → omitted from the ratings map, as
        // always. Confirmation survives the round trip too: a, b and d were
        // graded by hand, so they are applied rather than staged. "c" was
        // never touched, so it stays auto and keeps its safety net.
        expect(mockMarkAsListened).toHaveBeenCalledWith(
          "l1",
          { a: "again", b: "hard", d: "easy" },
          {},
          ["a", "b", "d"],
          [],
        );
      });
    });
  });

  // ── Auto-graded vs confirmed ────────────────────────────────────────

  describe("auto-grade indicator and confirmation", () => {
    it("an untouched row's Good reads as auto: provisional, not confirmed", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [wordCandidate("prosim", { grade_class: "due" })],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      const good = gradeBtn(container, "word:prosim", "good");
      expect(good.classList.contains("active")).toBe(true);
      expect(good.classList.contains("auto")).toBe(true);
      expect(good.getAttribute("aria-label")).toBe("Good for prosim — auto-graded, tap to confirm");
    });

    it("tapping Good on an auto row confirms it and drops the auto styling", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [wordCandidate("prosim", { grade_class: "due" })],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      const good = gradeBtn(container, "word:prosim", "good");
      await fireEvent.click(good);

      expect(good.classList.contains("active")).toBe(true);
      expect(good.classList.contains("auto")).toBe(false);
      expect(good.getAttribute("aria-label")).toBe("Good for prosim");
    });

    it("a confirmed row is sent in confirmed_words even at the default grade", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [
          wordCandidate("prosim", { grade_class: "due" }),
          wordCandidate("hvala", { grade_class: "due" }),
        ],
      });
      mockMarkAsListened.mockResolvedValue({
        status: "ok",
        created: 0,
        staged: 1,
        applied: 1,
        remaining_candidates: 0,
        listen_count: 1,
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      await fireEvent.click(gradeBtn(container, "word:prosim", "good"));
      await fireEvent.click(getByText("Mark 2 as listened"));

      await waitFor(() => {
        // "prosim" keeps the default rating (so it is still omitted from the
        // ratings map) but must appear in confirmed_words — that split is the
        // whole contract.
        expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {}, ["prosim"], []);
      });
    });

    it("a changed grade is both rated and confirmed", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [wordCandidate("prosim", { grade_class: "due" }), kpCandidate("dober dan")],
      });
      mockMarkAsListened.mockResolvedValue({
        status: "ok",
        created: 0,
        staged: 0,
        applied: 2,
        remaining_candidates: 0,
        listen_count: 1,
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      await fireEvent.click(gradeBtn(container, "word:prosim", "hard"));
      await fireEvent.click(gradeBtn(container, "kp:dober dan", "easy"));
      await fireEvent.click(getByText("Mark 2 as listened"));

      await waitFor(() => {
        expect(mockMarkAsListened).toHaveBeenCalledWith(
          "l1",
          { prosim: "hard" },
          { "dober dan": "easy" },
          ["prosim"],
          ["dober dan"],
        );
      });
    });

    it("Grade All does NOT confirm rows — it is about skip, not review", async () => {
      // One click must not commit reviews for every row, least of all the
      // well-known ones sitting behind a collapsed disclosure.
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
        staged: 2,
        applied: 0,
        remaining_candidates: 0,
        listen_count: 1,
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      await fireEvent.click(getByText("Grade All"));
      await fireEvent.click(getByText("Mark 2 as listened"));

      await waitFor(() => {
        const call = mockMarkAsListened.mock.calls.at(-1)!;
        expect(call[3]).toEqual([]); // confirmed_words
        expect(call[4]).toEqual([]); // confirmed_kps
      });
      // The auto styling survives Grade All, because nothing was reviewed.
      expect(gradeBtn(container, "word:prosim", "good").classList.contains("auto")).toBe(true);
    });

    it("confirmation survives a Skip All / Grade All round trip", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [wordCandidate("prosim", { grade_class: "due" })],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      await fireEvent.click(gradeBtn(container, "word:prosim", "hard"));

      await fireEvent.click(getByText("Skip All"));
      await fireEvent.click(getByText("Grade All"));

      const hard = gradeBtn(container, "word:prosim", "hard");
      expect(hard.classList.contains("active")).toBe(true);
      expect(hard.classList.contains("auto")).toBe(false);
    });

    it("Skip is never marked auto — it is not a grade", async () => {
      mockGetListenPreview.mockResolvedValue({
        candidates: [wordCandidate("prosim", { grade_class: "due" })],
      });

      const { container, getByText } = render(ListenPreviewModal, {
        props: { lessonId: "l1", onDone: vi.fn() },
      });

      await waitFor(() => getByText("prosim"));
      await fireEvent.click(gradeBtn(container, "word:prosim", "skip"));
      expect(gradeBtn(container, "word:prosim", "skip").classList.contains("auto")).toBe(false);
    });
  });
});

describe("NEW-state rows and the shared introduction budget", () => {
  it("puts an over-budget NEW-state row in the tail, not the gradeable list", async () => {
    // Releasing a staged grade on a NEW-state card INTRODUCES it, spending
    // Anki's daily new-card allowance — so rows past the budget must be
    // read-only, exactly like over-budget create rows.
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        newStateCandidate("hansen", { will_create: true }),
        newStateCandidate("lund", { will_create: false }),
      ],
    });

    const { container, getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("hansen"));
    expect(getByText("1 more — next listen")).toBeTruthy();
    expect(gradeBtn(container, "word:hansen", "good")).toBeTruthy();
    expect(gradeBtn(container, "word:lund", "good")).toBeNull();
  });

  it("never names an over-budget NEW-state row in the commit payload", async () => {
    // Same polarity trap as the create tail: naming a row the user was never
    // offered asserts a decision they never made.
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        newStateCandidate("hansen", { will_create: true }),
        newStateCandidate("lund", { will_create: false }),
      ],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("hansen"));
    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, {}, [], []);
    });
  });
});
