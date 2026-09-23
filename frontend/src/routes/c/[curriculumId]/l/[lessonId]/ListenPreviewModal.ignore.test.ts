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
    unignoreLemma: vi.fn(),
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
const mockUnignoreLemma = vi.mocked(api.unignoreLemma);

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

// Ignoring is committed server-side the moment it is tapped, so the modal
// offers a timed UNDO instead of a rollback: `unignoreLemma` brings the lemma
// back the same way `ignoreLemma` took it away (bd tunatale-4p88). The bar is
// a live region that replaces itself on a second ignore (only the latest is
// undoable) and dismisses itself after 5 s. The 5 s timer MUST be a plain
// `setTimeout` — `SvelteDate` binds the real clock at module evaluation, so
// fake timers could not reach it, and the auto-dismiss test below would decay
// into a vacuous green.
describe("ListenPreviewModal — the Ignore Undo bar", () => {
  const undoBarEl = (container: HTMLElement) => container.querySelector(".undo-bar");
  const undoBtn = (container: HTMLElement) =>
    container.querySelector<HTMLButtonElement>(".undo-bar button.undo");
  const cancelBtn = (container: HTMLElement) =>
    container.querySelector<HTMLButtonElement>(".footer button.cancel")!;

  it("shows the undo bar after a successful ignore, naming the word", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);

    await waitFor(() => expect(undoBarEl(container)).not.toBeNull());
    // The bar is a status live region, so it names the word in its text.
    expect(getByText("Ignored “snømenn”")).toBeTruthy();
    // …and the Undo button carries an aria-label naming what it would bring back.
    expect(undoBtn(container)!.getAttribute("aria-label")).toBe("Undo ignore of snømenn");
  });

  it("a failed ignore shows the error and NO undo bar", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    mockIgnoreLemma.mockRejectedValue(new Error("ignore boom"));

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);

    await waitFor(() => expect(getByText(/couldn't ignore/i)).toBeTruthy());
    // Nothing was ignored, so there is nothing to undo — the bar must not appear.
    expect(undoBarEl(container)).toBeNull();
  });

  it("Undo sends the LEMMA, refetches, and the row comes back", async () => {
    mockGetListenPreview
      // First preview: snømenn is the junk the user ignores.
      .mockResolvedValueOnce({
        candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
      })
      // Post-ignore (the server dropped snømenn).
      .mockResolvedValueOnce({
        candidates: [createCandidate("kake", { willCreate: true, lemma: null })],
      })
      // Post-undo (it is back).
      .mockResolvedValueOnce({
        candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
      });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });
    mockUnignoreLemma.mockResolvedValue({ status: "ok" });

    const { getByText, queryByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    await waitFor(() => expect(queryByText("snømenn")).toBeNull());

    await fireEvent.click(undoBtn(container)!);

    await waitFor(() => {
      expect(mockUnignoreLemma).toHaveBeenCalledTimes(1);
    });
    // Same lemma-vs-text contract as the ignore: the backend matches on
    // `lemma` (`text: "snømenn"`, `lemma: "snøm"`).
    expect(mockUnignoreLemma).toHaveBeenCalledWith("snøm", "no");
    expect(mockUnignoreLemma).not.toHaveBeenCalledWith("snømenn", "no");
    // The undo REFETCHED like an ignore does — the returned row is the
    // server's reply, not a locally unhidden one.
    expect(mockGetListenPreview).toHaveBeenCalledTimes(3);
    await waitFor(() => getByText("snømenn"));
    // The bar is gone: this undo was consumed.
    expect(undoBarEl(container)).toBeNull();
  });

  it("a cancel after ignore-then-undo reports ignored: 0", async () => {
    mockGetListenPreview
      .mockResolvedValueOnce({
        candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
      })
      .mockResolvedValueOnce({
        candidates: [],
      })
      .mockResolvedValueOnce({
        candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
      });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });
    mockUnignoreLemma.mockResolvedValue({ status: "ok" });
    const onDone = vi.fn();

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone },
    });

    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    await waitFor(() => expect(mockGetListenPreview).toHaveBeenCalledTimes(2));
    await fireEvent.click(undoBtn(container)!);
    await waitFor(() => expect(mockUnignoreLemma).toHaveBeenCalledTimes(1));

    await fireEvent.click(cancelBtn(container));

    // The undo UNDID the one ignore that landed, so the page's transcript is
    // NOT stale — the count the cancel reports is the thing the page acts on.
    expect(onDone).toHaveBeenCalledWith({ status: "cancelled", ignored: 0 });
  });

  it("the undo bar auto-dismisses exactly 5 seconds after the ignore", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });

    const { container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });
    // Flush the onMount fetch; the ignore flies on microtasks below.
    await vi.advanceTimersByTimeAsync(0);

    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    await vi.advanceTimersByTimeAsync(0);

    expect(undoBarEl(container)).not.toBeNull();

    // Still offering the undo at 4999 ms…
    await vi.advanceTimersByTimeAsync(4999);
    expect(undoBarEl(container)).not.toBeNull();

    // …gone at 5000 (UNDO_MS = 5000).
    await vi.advanceTimersByTimeAsync(1);
    expect(undoBarEl(container)).toBeNull();
  });

  it("a second ignore replaces the bar and Undo only undoes the latest", async () => {
    mockGetListenPreview
      .mockResolvedValueOnce({
        candidates: [
          createCandidate("snømenn", { willCreate: true, lemma: "snøm" }),
          createCandidate("brød", { willCreate: true, lemma: "brød" }),
        ],
      })
      // Post-ignore of snømenn.
      .mockResolvedValueOnce({
        candidates: [createCandidate("brød", { willCreate: true, lemma: "brød" })],
      })
      // Post-ignore of brød.
      .mockResolvedValueOnce({
        candidates: [],
      })
      // Post-undo of brød.
      .mockResolvedValueOnce({
        candidates: [createCandidate("brød", { willCreate: true, lemma: "brød" })],
      });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });
    mockUnignoreLemma.mockResolvedValue({ status: "ok" });

    const { getByText, queryByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });

    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    await waitFor(() => expect(queryByText("snømenn")).toBeNull());
    expect(getByText("Ignored “snømenn”")).toBeTruthy();

    await fireEvent.click(ignoreBtn(container, "create:brød")!);
    await waitFor(() => expect(queryByText("brød")).toBeNull());
    // The bar was REPLACED, not stacked: only the latest ignore is named.
    await waitFor(() => expect(getByText("Ignored “brød”")).toBeTruthy());
    expect(container.querySelectorAll(".undo-bar")).toHaveLength(1);

    await fireEvent.click(undoBtn(container)!);
    await waitFor(() => {
      expect(mockUnignoreLemma).toHaveBeenCalledTimes(1);
    });
    // Only the SECOND ignore is undoable — the first never got an undo.
    expect(mockUnignoreLemma).toHaveBeenCalledWith("brød", "no");
    expect(mockUnignoreLemma).not.toHaveBeenCalledWith("snøm", "no");
    // snømenn stays absent (never undone); brød came back via the refetch.
    expect(queryByText("snømenn")).toBeNull();
    await waitFor(() => getByText("brød"));
  });

  it("a failed Undo is visible, the row stays ignored, and the bar comes back", async () => {
    mockGetListenPreview
      .mockResolvedValueOnce({
        candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
      })
      .mockResolvedValueOnce({
        candidates: [createCandidate("kake", { willCreate: true, lemma: null })],
      });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });
    mockUnignoreLemma.mockRejectedValue(new Error("undo boom"));
    const onDone = vi.fn();

    const { getByText, queryByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone },
    });

    await waitFor(() => getByText("snømenn"));
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    await waitFor(() => expect(queryByText("snømenn")).toBeNull());

    await fireEvent.click(undoBtn(container)!);

    // The failure is a visible, non-destructive error — like `ignoreError`,
    // NOT the body-replacing `error`.
    await waitFor(() => expect(getByText(/couldn't undo/i)).toBeTruthy());
    expect(mockUnignoreLemma).toHaveBeenCalledTimes(1);
    // The undo failed BEFORE the refetch — the server was never re-read.
    expect(mockGetListenPreview).toHaveBeenCalledTimes(2);
    // The row is still ignored, so the word is still absent…
    expect(queryByText("snømenn")).toBeNull();
    // …and the bar is back, still offering the undo.
    await waitFor(() => expect(undoBarEl(container)).not.toBeNull());

    // The ignore still counts as landed — an undone attempt is not an un-ignore.
    await fireEvent.click(cancelBtn(container));
    expect(onDone).toHaveBeenCalledWith({ status: "cancelled", ignored: 1 });
  });

  it("unmounting the modal clears the undo timer — nothing fires after destroy", async () => {
    vi.useFakeTimers();
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("snømenn", { willCreate: true, lemma: "snøm" })],
    });
    mockIgnoreLemma.mockResolvedValue({ status: "ok" });

    const { container, unmount } = render(ListenPreviewModal, {
      props: { lessonId: "l1", languageCode: "no", onDone: vi.fn() },
    });
    await vi.advanceTimersByTimeAsync(0);
    await fireEvent.click(ignoreBtn(container, "create:snømenn")!);
    await vi.advanceTimersByTimeAsync(0);

    expect(undoBarEl(container)).not.toBeNull();
    // The armed 5-second timer is the only pending timer.
    expect(vi.getTimerCount()).toBe(1);

    unmount();
    // onDestroy cleared the pending dismissal, so no timer survives…
    expect(vi.getTimerCount()).toBe(0);

    // …and advancing past the dismissal point changes nothing — without the
    // clear, this would fire on a dead component and write to $state after
    // destroy.
    await vi.advanceTimersByTimeAsync(6000);
    expect(vi.getTimerCount()).toBe(0);
  });
});
