/**
 * The Ignore action on the listen preview's CREATE rows (bd tunatale-qfoa,
 * frontend half — brief-2026-09-22-qfoa-preview-ignore-frontend.md).
 *
 * An ignored lemma disappears from the NEXT getListenPreview, so the modal
 * REFETCHES instead of hiding the row: `will_create` is a server-side budget
 * allocation, dropping a live row can promote a tail row to live, and only
 * the server knows which. The second mocked response below omits the ignored
 * row and promotes a former tail row to pin that the refetch is a real one.
 *
 * The lemma-vs-text contract is backend-verified in
 * `backend/tests/test_api_listen_preview_ignore.py::test_ignoring_by_the_rows_text_would_not_work`:
 * an ignore sent with `text` returns 200 and does NOTHING.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "$lib/components/ListenPreviewModal.svelte";
import { api, type ListenPreviewCandidate } from "$lib/api";
import { listenCountdownPref } from "$lib/stores/listenCountdownPref.svelte";

vi.mock("$lib/api", () => ({
  api: {
    getListenPreview: vi.fn(),
    markAsListened: vi.fn(),
    getListens: vi.fn(),
    ignoreLemma: vi.fn(),
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
const mockIgnoreLemma = vi.mocked(api.ignoreLemma);

const listenResult = {
  status: "ok",
  created: 0,
  staged: 0,
  applied: 0,
  remaining_candidates: 0,
  listen_count: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  // The countdown auto-commits; only the countdown test opts back in.
  listenCountdownPref.set("off");
});

afterEach(() => {
  vi.useRealTimers();
});

// ── Fixtures ──────────────────────────────────────────────────────────
// `lemma` is the key the ignore list is matched on — NOT `text` (the card
// headword). They differ in real data (`text: "snømenn"`, `lemma: "snøm"`).

const createCandidate = (
  text: string,
  opts: { willCreate: boolean; lemma?: string | null },
): ListenPreviewCandidate => ({
  kind: "create" as const,
  text,
  item_id: null,
  grade_class: "create" as const,
  rating: "good" as const,
  translation: "",
  progress: null,
  well_known: false,
  due_at: null,
  will_create: opts.willCreate,
  lemma: opts.lemma ?? null,
});

const wordCandidate = (text: string): ListenPreviewCandidate => ({
  kind: "word" as const,
  text,
  item_id: 7,
  grade_class: "due" as const,
  rating: "good" as const,
  translation: "",
  progress: null,
  well_known: false,
  due_at: null,
  will_create: true,
  lemma: null,
});

const ignoreBtn = (container: HTMLElement, key: string) =>
  container.querySelector<HTMLButtonElement>(`button.ignore[data-candidate='${key}']`);

const gradeBtn = (container: HTMLElement, key: string, grade: string) =>
  container.querySelector(
    `button[data-candidate='${key}'][data-grade='${grade}']`,
  ) as HTMLButtonElement | null;

const isActive = (b: HTMLButtonElement) => b.classList.contains("active");

const isAboveDivider = (container: HTMLElement, text: string) =>
  [...container.querySelectorAll("li.candidate")].some(
    (li) => li.querySelector(".text")?.textContent === text && !li.classList.contains("tail"),
  );

// ── Tests ─────────────────────────────────────────────────────────────

describe("ListenPreviewModal — Ignore on CREATE rows", () => {
  it("renders Ignore on create rows with a lemma and a languageCode, and nowhere else", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        createCandidate("snømenn", { willCreate: true, lemma: "snøm" }),
        createCandidate("kake", { willCreate: true, lemma: null }),
        wordCandidate("Anders"),
        createCandidate("brød", { willCreate: false, lemma: "brød" }),
      ],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));

    // A live create with a lemma gets the button.
    expect(ignoreBtn(container, "create:snømenn")).not.toBeNull();
    // A create row with lemma null/absent gets nothing.
    expect(ignoreBtn(container, "create:kake")).toBeNull();
    // A tracked row (kind !== 'create') gets nothing.
    expect(ignoreBtn(container, "word:Anders")).toBeNull();
    // …and an over-budget tail create with a lemma gets the button too —
    // junk can sit in the tail, and `will_create` says nothing about whether
    // the word is worth creating.
    expect(ignoreBtn(container, "create:brød")).not.toBeNull();
  });

  it("renders no Ignore button anywhere when languageCode is undefined", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));
    expect(container.querySelectorAll("button.ignore")).toHaveLength(0);
  });

  it("sends the LEMMA, not the row's text — and exactly once", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);

    await waitFor(() => expect(mockIgnoreLemma).toHaveBeenCalledTimes(1));
    expect(mockIgnoreLemma).toHaveBeenCalledWith("snøm", "no");
    // The backend proves a `text`-keyed ignore is a silent no-op
    // (test_ignoring_by_the_rows_text_would_not_work) — pin the negative here.
    expect(mockIgnoreLemma).not.toHaveBeenCalledWith("snømenn", "no");
  });

  it("refetches after an ignore — the server drops the row and a tail row is promoted", async () => {
    // First preview: snømenn is the junk the user will ignore; brød is over
    // budget (the tail).
    mockGetListenPreview
      .mockResolvedValueOnce({
        candidates: [
          createCandidate("snømenn", { willCreate: true, lemma: "snøm" }),
          createCandidate("kake", { willCreate: true, lemma: null }),
          createCandidate("brød", { willCreate: false, lemma: "brød" }),
        ],
      })
      // Second preview (post-ignore): snømenn is gone, and the freed budget
      // promoted brød above the divider.
      .mockResolvedValueOnce({
        candidates: [
          createCandidate("kake", { willCreate: true, lemma: null }),
          createCandidate("brød", { willCreate: true, lemma: "brød" }),
        ],
      });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });

    const { getByText, queryByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));
    // brød starts in the tail: not live, not graded.
    expect(isAboveDivider(container, "brød")).toBe(false);

    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);

    // The ignored row is gone because the SERVER dropped it — the modal
    // refetched (a second getListenPreview call), it did not just hide a row.
    await waitFor(() => expect(queryByText("snømenn")).toBeNull());
    expect(mockGetListenPreview).toHaveBeenCalledTimes(2);
    expect(mockGetListenPreview).toHaveBeenLastCalledWith("l1");

    // The promoted tail row is now live and graded with the ordinary seed.
    expect(isAboveDivider(container, "brød")).toBe(true);
    await waitFor(() => {
      expect(isActive(gradeBtn(container, "create:brød", "good")!)).toBe(true);
    });
    // kake + brød are both "good" now — the footer count reflects the
    // promotion, i.e. the refetched list replaced the first one wholesale.
    expect(getByText("Mark 2 as listened")).toBeTruthy();
  });

  it("the user's own grades survive the refetch", async () => {
    mockGetListenPreview
      .mockResolvedValueOnce({
        candidates: [
          createCandidate("snømenn", { willCreate: true, lemma: "snøm" }),
          createCandidate("kake", { willCreate: true, lemma: null }),
          createCandidate("brød", { willCreate: false, lemma: "brød" }),
        ],
      })
      .mockResolvedValueOnce({
        candidates: [
          createCandidate("kake", { willCreate: true, lemma: null }),
          createCandidate("brød", { willCreate: true, lemma: "brød" }),
        ],
      });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));

    // A DIFFERENT live row is graded by hand before the ignore.
    await fireEvent.click(gradeBtn(container, "create:kake", "hard")!);
    await waitFor(() => {
      expect(isActive(gradeBtn(container, "create:kake", "hard")!)).toBe(true);
    });

    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);

    // The hand grade survives the refetch; the promoted row gets the seed.
    await waitFor(() => {
      expect(isActive(gradeBtn(container, "create:kake", "hard")!)).toBe(true);
    });
    expect(isActive(gradeBtn(container, "create:brød", "good")!)).toBe(true);
  });

  it("an ignore cancels the auto-commit countdown", async () => {
    vi.useFakeTimers();
    listenCountdownPref.set("10");
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        createCandidate("snømenn", { willCreate: true, lemma: "snøm" }),
        createCandidate("kake", { willCreate: true, lemma: null }),
      ],
    });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });
    mockMarkAsListened.mockResolvedValue(listenResult);

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    // Flush the onMount fetch; the countdown is armed.
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelector(".grade-all")?.getAttribute("data-countdown")).toBe("running");

    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    // Flush the ignore + refetch microtasks.
    await vi.advanceTimersByTimeAsync(0);

    // Ignoring is an interaction: the countdown is cancelled, so no amount of
    // waiting can auto-commit.
    expect(container.querySelector(".grade-all")?.getAttribute("data-countdown")).toBe("idle");
    await vi.advanceTimersByTimeAsync(15_000);
    expect(mockMarkAsListened).not.toHaveBeenCalled();
  });

  it("a failed ignore is visible and harmless — the row stays, no refetch, the modal keeps working", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        createCandidate("snømenn", { willCreate: true, lemma: "snøm" }),
        createCandidate("kake", { willCreate: true, lemma: null }),
      ],
    });
    mockIgnoreLemma.mockRejectedValue(new Error("ignore boom"));
    mockMarkAsListened.mockResolvedValue(listenResult);

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);

    // An error message is shown…
    await waitFor(() => expect(getByText(/couldn't ignore/i)).toBeTruthy());
    // …the row stays…
    expect(getByText("snømenn")).toBeTruthy();
    // …and the failure did NOT trigger the refetch.
    expect(mockGetListenPreview).toHaveBeenCalledTimes(1);
    expect(mockIgnoreLemma).toHaveBeenCalledTimes(1);

    // The rest of the modal keeps working: grading a row still works…
    await fireEvent.click(gradeBtn(container, "create:kake", "hard")!);
    await waitFor(() => {
      expect(isActive(gradeBtn(container, "create:kake", "hard")!)).toBe(true);
    });
    // …and so does committing.
    await fireEvent.click(getByText("Mark 2 as listened"));
    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", expect.anything());
    });
  });

  it("no double-submit: the row's Ignore is disabled while in flight, and the handler guards too", async () => {
    let resolveIgnore!: (v: { status: string }) => void;
    mockIgnoreLemma.mockImplementation(
      () =>
        new Promise<{ status: string }>((res) => {
          resolveIgnore = res;
        }),
    );
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    mockMarkAsListened.mockResolvedValue(listenResult);

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));

    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    // In flight: the button is disabled…
    await waitFor(() => {
      expect(ignoreBtn(container, "create:snømenn")!.disabled).toBe(true);
    });
    // …and a second click cannot fire a second call. (`disabled` suppresses
    // the native activation, but a synthetic click still reaches the Svelte
    // listener — the handler's in-flight guard is what actually holds.)
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    expect(mockIgnoreLemma).toHaveBeenCalledTimes(1);

    // The refetch still happens when the request resolves, and the row's
    // button re-enables.
    resolveIgnore({ status: "ok" });
    await waitFor(() => {
      expect(mockGetListenPreview).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(ignoreBtn(container, "create:snømenn")!.disabled).toBe(false);
    });
  });
});

// An ignore is committed server-side the moment it is tapped, so cancelling
// the modal does not undo it. The cancel result reports how many ignores
// landed, so the page knows its transcript is stale and re-reads it
// (bd tunatale-69ou — read mode kept the old state until a refresh).
describe("ListenPreviewModal — cancel reports the ignores that landed", () => {
  const cancelBtn = (container: HTMLElement) =>
    container.querySelector<HTMLButtonElement>(".footer button.cancel")!;

  it("a cancel after a successful ignore reports it", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });
    const onDone = vi.fn();

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone },
    });
    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    await waitFor(() => expect(mockGetListenPreview).toHaveBeenCalledTimes(2));

    await fireEvent.click(cancelBtn(container));

    expect(onDone).toHaveBeenCalledWith({ status: "cancelled", ignored: 1 });
  });

  it("a FAILED ignore is not counted", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    mockIgnoreLemma.mockRejectedValue(new Error("ignore boom"));
    const onDone = vi.fn();

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone },
    });
    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    await waitFor(() => expect(getByText(/couldn't ignore/i)).toBeTruthy());

    await fireEvent.click(cancelBtn(container));

    expect(onDone).toHaveBeenCalledWith({ status: "cancelled", ignored: 0 });
  });

  it("a plain cancel reports zero (control)", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    const onDone = vi.fn();

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone },
    });
    await waitFor(() => getByText("snømenn"));

    await fireEvent.click(cancelBtn(container));

    expect(onDone).toHaveBeenCalledWith({ status: "cancelled", ignored: 0 });
  });
});
