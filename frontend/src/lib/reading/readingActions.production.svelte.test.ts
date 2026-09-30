/**
 * The blur reader's grade (bd tunatale-dvdm.3) goes through the SAME endpoint
 * the review queue grades on — `api.submitDrill(id, "production", rating)` →
 * POST /api/srs/items/{id}/direction/production/feedback — never a second
 * grading implementation. So the revlog row, dirty flag and sync push are the
 * review queue's by construction; this pins that the reader actually calls it,
 * on the production direction, with the button's rating.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, type TranscriptData } from "$lib/api";
import { createReadingActions } from "./readingActions.svelte";
import { makeWordToken } from "../../test/factories";

vi.mock("$lib/api", () => ({
  api: {
    submitDrill: vi.fn(),
    undoGrade: vi.fn(),
    getTranscript: vi.fn(),
  },
}));

vi.mock("$lib/stores/queueStats.svelte", () => ({
  queueStatsStore: { refresh: vi.fn() },
}));

vi.mock("$lib/components/ConfirmDialog.svelte", () => ({
  confirmDialog: vi.fn(),
}));

const mockSubmit = vi.mocked(api.submitDrill);
const mockUndo = vi.mocked(api.undoGrade);
const mockTranscript = vi.mocked(api.getTranscript);

const EMPTY: TranscriptData = { lesson_id: "l1", key_phrases: [], dialogue_lines: [] };

function setup() {
  const setTranscript = vi.fn();
  const setError = vi.fn();
  const actions = createReadingActions({
    contentId: "l1",
    languageCode: "no",
    getTranscript: () => EMPTY,
    setTranscript,
    setError,
  });
  return { actions, setTranscript, setError };
}

const word = makeWordToken({
  surface: "huset",
  lemma: "hus",
  srs_item_id: 11,
  active_direction: "recognition",
  is_due: true,
  production_due: true,
});

beforeEach(() => {
  vi.clearAllMocks();
  mockSubmit.mockResolvedValue({ new_due_at: "2026-10-01T04:00:00+00:00", new_state: "review" });
  mockTranscript.mockResolvedValue(EMPTY);
});

describe("readingActions.onProductionGrade", () => {
  it.each(["again", "good"] as const)("grades the PRODUCTION direction with %s", async (rating) => {
    const { actions, setTranscript } = setup();
    await actions.tooltipActions.onProductionGrade(word, rating);
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    // lesson_review stays false: this is a real review, charged like the queue's.
    expect(mockSubmit).toHaveBeenCalledWith(11, "production", rating);
    expect(mockTranscript).toHaveBeenCalledWith("l1");
    expect(setTranscript).toHaveBeenCalledWith(EMPTY);
  });

  it("makes the production grade undoable, and the undo targets production", async () => {
    const { actions } = setup();
    await actions.tooltipActions.onProductionGrade(word, "good");
    expect(actions.tooltipActions.isGradeUndoable(word)).toBe(true);
    await actions.tooltipActions.onUndoGrade(word);
    expect(mockUndo).toHaveBeenCalledWith(11, "production");
  });

  it("a word with no card grades nothing", async () => {
    const { actions } = setup();
    await actions.tooltipActions.onProductionGrade(makeWordToken({ srs_item_id: null }), "good");
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("surfaces a failed grade as an error rather than throwing", async () => {
    mockSubmit.mockRejectedValueOnce(new Error("boom"));
    const { actions, setError } = setup();
    await actions.tooltipActions.onProductionGrade(word, "again");
    expect(setError).toHaveBeenCalledWith("boom");
  });
});
