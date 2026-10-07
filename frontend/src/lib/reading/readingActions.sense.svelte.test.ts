/**
 * The reader's "different meaning" action (bd tunatale-ceuc, slice 4): a word
 * whose card means something else than the lesson's gloss gets a second card
 * for the gloss's meaning. The reader sends the card the word resolved to and
 * the gloss, then re-reads the transcript — where the word now resolves to the
 * new card, because the backend picks among a spelling's cards by the gloss.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, type TranscriptData } from "$lib/api";
import { queueStatsStore } from "$lib/stores/queueStats.svelte";
import { createReadingActions } from "./readingActions.svelte";
import { makeWordToken } from "../../test/factories";

vi.mock("$lib/api", () => ({
  api: {
    createSenseCard: vi.fn(),
    getTranscript: vi.fn(),
  },
}));

vi.mock("$lib/stores/queueStats.svelte", () => ({
  queueStatsStore: { refresh: vi.fn() },
}));

vi.mock("$lib/components/ConfirmDialog.svelte", () => ({
  confirmDialog: vi.fn(),
}));

const mockCreate = vi.mocked(api.createSenseCard);
const mockTranscript = vi.mocked(api.getTranscript);

const REREAD: TranscriptData = { lesson_id: "l1", key_phrases: [], dialogue_lines: [] };

function setup() {
  const setTranscript = vi.fn();
  const setError = vi.fn();
  const actions = createReadingActions({
    contentId: "l1",
    languageCode: "no",
    getTranscript: () => REREAD,
    setTranscript,
    setError,
  });
  return { actions, setTranscript, setError };
}

const word = makeWordToken({
  surface: "gang",
  lemma: "gang",
  srs_item_id: 3,
  translation: "hall",
  gloss: "time",
});

beforeEach(() => {
  vi.clearAllMocks();
  mockTranscript.mockResolvedValue(REREAD);
});

describe("onCreateSense", () => {
  it("asks for a card for the gloss's meaning, beside the card the word resolved to", async () => {
    mockCreate.mockResolvedValue({ id: 7, was_created: true, item: {} as never });
    const { actions, setTranscript, setError } = setup();

    await actions.tooltipActions.onCreateSense(word, "Jeg kommer med en gang.");

    expect(mockCreate).toHaveBeenCalledWith({
      item_id: 3,
      surface: "gang",
      sentence: "Jeg kommer med en gang.",
      language_code: "no",
      translation: "time",
    });
    // Re-read, so the word shows the card it now resolves to...
    expect(mockTranscript).toHaveBeenCalledWith("l1");
    expect(setTranscript).toHaveBeenCalledWith(REREAD);
    // ...and a new card is one more in the queue's count.
    expect(queueStatsStore.refresh).toHaveBeenCalled();
    expect(setError).toHaveBeenLastCalledWith("");
  });

  it("reports a refusal and leaves the transcript as it was", async () => {
    mockCreate.mockRejectedValue(
      new Error("Only a single-word vocab card can have a second sense card"),
    );
    const { actions, setTranscript, setError } = setup();

    await actions.tooltipActions.onCreateSense(word, "Jeg kommer med en gang.");

    expect(setError).toHaveBeenLastCalledWith(
      "Only a single-word vocab card can have a second sense card",
    );
    expect(setTranscript).not.toHaveBeenCalled();
    expect(queueStatsStore.refresh).not.toHaveBeenCalled();
  });
});
