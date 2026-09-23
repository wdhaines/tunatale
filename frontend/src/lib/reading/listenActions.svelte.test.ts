/**
 * `onPreviewDone`'s cancel path (bd tunatale-69ou).
 *
 * An Ignore in the listen preview is committed server-side the moment it is
 * tapped — cancelling the modal does not undo it. The cancel path used to
 * return before re-reading the transcript, so read mode kept showing the word
 * as not-ignored until a page refresh. A cancel that carries ignores must
 * refetch; a plain cancel must stay a no-op (the control).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, type TranscriptData } from "$lib/api";
import { createListenActions } from "./listenActions.svelte";

vi.mock("$lib/api", () => ({
  api: {
    getTranscript: vi.fn(),
    fetchLessonReviewQueue: vi.fn(),
  },
}));

vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: { refresh: vi.fn(), has: vi.fn(() => false) },
}));

vi.mock("$lib/stores/queueStats.svelte", () => ({
  queueStatsStore: { refresh: vi.fn() },
}));

const mockGetTranscript = vi.mocked(api.getTranscript);
const transcript = { lines: [] } as unknown as TranscriptData;

function setup(contentId = "lesson-1") {
  const opts = {
    contentId,
    languageCode: "no",
    setTranscript: vi.fn(),
    setError: vi.fn(),
  };
  return { opts, actions: createListenActions(opts) };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetTranscript.mockResolvedValue(transcript);
});

describe("onPreviewDone — cancel", () => {
  it("re-reads the transcript when the cancelled preview ignored a word", async () => {
    const { opts, actions } = setup();

    await actions.onPreviewDone({ status: "cancelled", ignored: 1 });

    expect(mockGetTranscript).toHaveBeenCalledWith("lesson-1");
    expect(opts.setTranscript).toHaveBeenCalledWith(transcript);
  });

  it("does NOT re-read the transcript on a plain cancel (control)", async () => {
    const { opts, actions } = setup();

    await actions.onPreviewDone({ status: "cancelled", ignored: 0 });

    expect(mockGetTranscript).not.toHaveBeenCalled();
    expect(opts.setTranscript).not.toHaveBeenCalled();
  });

  it("drops the transcript if the page moved to other content meanwhile", async () => {
    const { opts, actions } = setup();
    mockGetTranscript.mockImplementation(async () => {
      opts.contentId = "lesson-2";
      return transcript;
    });

    await actions.onPreviewDone({ status: "cancelled", ignored: 1 });

    expect(opts.setTranscript).not.toHaveBeenCalled();
  });

  it("surfaces a failed re-read through setError instead of throwing", async () => {
    const { opts, actions } = setup();
    mockGetTranscript.mockRejectedValue(new Error("boom"));

    await actions.onPreviewDone({ status: "cancelled", ignored: 1 });

    expect(opts.setError).toHaveBeenCalledWith("boom");
  });

  it("stringifies a non-Error rejection", async () => {
    const { opts, actions } = setup();
    mockGetTranscript.mockRejectedValue("offline");

    await actions.onPreviewDone({ status: "cancelled", ignored: 1 });

    expect(opts.setError).toHaveBeenCalledWith("offline");
  });

  it("swallows a failed re-read for content the page has already left", async () => {
    const { opts, actions } = setup();
    mockGetTranscript.mockImplementation(async () => {
      opts.contentId = "lesson-2";
      throw new Error("boom");
    });

    await actions.onPreviewDone({ status: "cancelled", ignored: 1 });

    expect(opts.setError).not.toHaveBeenCalled();
  });

  it("closes the preview either way", async () => {
    const { actions } = setup();
    actions.open();

    await actions.onPreviewDone({ status: "cancelled", ignored: 1 });

    expect(actions.showPreview).toBe(false);
  });
});
