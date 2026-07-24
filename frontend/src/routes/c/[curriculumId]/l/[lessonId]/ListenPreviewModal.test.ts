/**
 * Tests for ListenPreviewModal — preview of word candidates before listen commit.
 *
 * Uses real backend shapes:
 *   GET /api/srs/lesson/{id}/listen-preview → { candidates: [...] }
 *   Each candidate: { kind, text, item_id, grade_class, rating, translation, progress }
 *
 * The modal calls listenedStore.markListened(lessonId, wordRatings, kpRatings)
 * on commit — NOT api.commitPending.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "./ListenPreviewModal.svelte";
import { api } from "$lib/api";

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
});

// ── Fixtures matching real backend shapes ──────────────────────────────

const createCandidate = (text: string) => ({
  kind: "create" as const,
  text,
  item_id: null,
  grade_class: "create",
  rating: "good" as const,
  translation: "",
  progress: null,
});

const wordCandidate = (
  text: string,
  opts?: { grade_class?: string; translation?: string; progress?: number },
) => ({
  kind: "word" as const,
  text,
  item_id: 42,
  grade_class: opts?.grade_class ?? "learning",
  rating: "good" as const,
  translation: opts?.translation ?? "",
  progress: opts?.progress ?? 0.3,
});

const kpCandidate = (text: string, opts?: { translation?: string; progress?: number }) => ({
  kind: "kp" as const,
  text,
  item_id: 99,
  grade_class: "learning",
  rating: "good" as const,
  translation: opts?.translation ?? "",
  progress: opts?.progress ?? 0.1,
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

  it("Skip All unchecks all candidates", async () => {
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
    expect((getByText("Mark 0 as listened") as HTMLButtonElement).disabled).toBe(true);
  });

  it("rating buttons update internal ratings state", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { getByText, findAllByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("kava")).toBeTruthy();
    });

    const easyBtns = await findAllByText("Easy");
    await fireEvent.click(easyBtns[0]);
    // No error = rating state updated. The actual routing (word vs kp) is tested
    // indirectly through the commit assertion below.
  });

  it("commit builds wordRatings for kind=create|word and kpRatings for kind=kp", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [
        createCandidate("kava"), // create → word_ratings
        wordCandidate("mleko"), // word → word_ratings
        kpCandidate("na zdravje"), // kp → kp_ratings
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
      expect(mockMarkAsListened).toHaveBeenCalledWith(
        "l1",
        { kava: "good", mleko: "good" },
        { "na zdravje": "good" },
      );
      expect(onDone).toHaveBeenCalledWith({
        status: "ok",
        created: 2,
        staged: 1,
        remaining_candidates: 0,
        listen_count: 3,
      });
    });
  });

  it("unchecked candidates get skip rating in their respective map", async () => {
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
      expect(mockMarkAsListened).toHaveBeenCalledWith(
        "l1",
        { kava: "skip" },
        { "na zdravje": "good" },
      );
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

  it("10-second countdown auto-commits after 10s with no interaction", async () => {
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
      expect(mockMarkAsListened).toHaveBeenCalled();
      expect(onDone).toHaveBeenCalled();
    });
  });

  it("any interaction cancels countdown permanently", async () => {
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

    // Interact: click checkbox at 5s
    await vi.advanceTimersByTimeAsync(5_000);
    const checkbox = container.querySelector("input[type='checkbox']") as HTMLInputElement;
    await fireEvent.click(checkbox);

    // Advance another 15s — countdown was cancelled, no auto-commit
    await vi.advanceTimersByTimeAsync(15_000);

    // Only the click, no auto-commit
    expect(mockMarkAsListened).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
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

  it("Skip All then Mark button is disabled", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kava")],
    });

    const { getByText } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    await waitFor(() => {
      expect(getByText("Mark 1 as listened")).toBeTruthy();
    });

    await fireEvent.click(getByText("Skip All"));

    const markBtn = getByText("Mark 0 as listened");
    expect((markBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it("Good rating button keeps a word candidate's rating good in the commit call", async () => {
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
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { prosim: "good" }, {});
    });
  });

  it("Skip rating button on a word candidate routes 'skip' into word_ratings", async () => {
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

    const skipBtns = await findAllByText("Skip");
    await fireEvent.click(skipBtns[0]);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", { prosim: "skip" }, {});
    });
  });

  it("Skip rating button on a kp candidate routes 'skip' into kp_ratings", async () => {
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

    const skipBtns = await findAllByText("Skip");
    await fireEvent.click(skipBtns[0]);

    await fireEvent.click(getByText("Mark 1 as listened"));

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {}, { "na zdravje": "skip" });
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
});
