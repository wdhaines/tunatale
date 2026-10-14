/**
 * The reader's four ratings (bd tunatale-8jgb): a word or phrase graded from
 * its popover records the rating that was tapped, where it used to record Good
 * whatever the learner thought. Every rating goes through the review queue's own
 * endpoint (`api.submitDrill`), so there is still exactly one grading path.
 *
 * What these pin is WHICH CARD takes the rating. A read-ahead review always
 * grades recognition, never `active_direction`, which flips to production once
 * recognition graduates; rating the wrong card there would be silent.
 *
 * LOCKED: written by the orchestrator before the implementation. Do not edit.
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
    createBaseCard: vi.fn(),
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
const mockCreateBase = vi.mocked(api.createBaseCard);

const EMPTY: TranscriptData = { lesson_id: "l1", key_phrases: [], dialogue_lines: [] };
const RATINGS = ["again", "hard", "good", "easy"] as const;

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

const dueRecognition = makeWordToken({
  surface: "huset",
  lemma: "hus",
  srs_item_id: 11,
  active_state: "learning",
  active_direction: "recognition",
  is_due: true,
});

const dueProduction = makeWordToken({
  surface: "huset",
  lemma: "hus",
  srs_item_id: 12,
  active_state: "review",
  active_direction: "production",
  is_due: true,
});

// Not due, recognition still on the ramp, and the ACTIVE direction has already
// flipped to production: the case where grading active_direction is wrong.
const readAhead = makeWordToken({
  surface: "boka",
  lemma: "bok",
  srs_item_id: 13,
  active_state: "review",
  active_direction: "production",
  is_due: false,
  recognition_reviewable: true,
});

beforeEach(() => {
  vi.clearAllMocks();
  mockSubmit.mockResolvedValue({ new_due_at: "2026-10-01T04:00:00+00:00", new_state: "review" });
  mockTranscript.mockResolvedValue(EMPTY);
});

describe("readingActions.onWordClick with a rating", () => {
  it.each(RATINGS)("a due word records %s on its active recognition direction", async (rating) => {
    const { actions, setTranscript } = setup();
    await actions.onWordClick(dueRecognition, 0, rating);
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(mockSubmit).toHaveBeenCalledWith(11, "recognition", rating);
    expect(setTranscript).toHaveBeenCalledWith(EMPTY);
  });

  it.each(RATINGS)("a due word records %s on its active production direction", async (rating) => {
    const { actions } = setup();
    await actions.onWordClick(dueProduction, 0, rating);
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(mockSubmit).toHaveBeenCalledWith(12, "production", rating);
  });

  it.each(RATINGS)(
    "a read-ahead word records %s on RECOGNITION, never the active direction",
    async (rating) => {
      const { actions } = setup();
      await actions.onWordClick(readAhead, 0, rating);
      expect(mockSubmit).toHaveBeenCalledTimes(1);
      expect(mockSubmit).toHaveBeenCalledWith(13, "recognition", rating);
    },
  );

  it("records Good when no rating is passed (the keyboard path)", async () => {
    const { actions } = setup();
    await actions.onWordClick(dueRecognition, 0);
    expect(mockSubmit).toHaveBeenCalledWith(11, "recognition", "good");
  });

  it("the undo after a read-ahead rating targets recognition", async () => {
    const { actions } = setup();
    await actions.onWordClick(readAhead, 0, "hard");
    expect(actions.tooltipActions.isGradeUndoable(readAhead)).toBe(true);
    await actions.tooltipActions.onUndoGrade(readAhead);
    expect(mockUndo).toHaveBeenCalledWith(13, "recognition");
  });

  it("the undo after an Again on a due production word targets production", async () => {
    const { actions } = setup();
    await actions.onWordClick(dueProduction, 0, "again");
    await actions.tooltipActions.onUndoGrade(dueProduction);
    expect(mockUndo).toHaveBeenCalledWith(12, "production");
  });

  it("starting a word that has no card still records Good", async () => {
    mockCreateBase.mockResolvedValue({
      id: 77,
      item: { directions: { recognition: {}, production: null } },
    } as never);
    const unknown = makeWordToken({ surface: "haya", lemma: "haya", active_state: "unknown" });
    const { actions } = setup();
    await actions.onWordClick(unknown, 0);
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(mockSubmit).toHaveBeenCalledWith(77, "recognition", "good");
  });

  it("starting a card that exists but was never studied still records Good", async () => {
    const neverStarted = makeWordToken({
      surface: "ang",
      lemma: "ang",
      srs_item_id: 14,
      active_state: "new",
      active_direction: "production",
      is_due: false,
    });
    const { actions } = setup();
    await actions.onWordClick(neverStarted, 0);
    expect(mockSubmit).toHaveBeenCalledWith(14, "production", "good");
  });
});

describe("readingActions.onCollocationStateChange with a rating", () => {
  it.each(RATINGS)("a phrase records %s on recognition", async (rating) => {
    const { actions, setTranscript } = setup();
    await actions.onCollocationStateChange(99, rating);
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(mockSubmit).toHaveBeenCalledWith(99, "recognition", rating);
    expect(setTranscript).toHaveBeenCalledWith(EMPTY);
  });

  it("records Good when no rating is passed (the keyboard path)", async () => {
    const { actions } = setup();
    await actions.onCollocationStateChange(99);
    expect(mockSubmit).toHaveBeenCalledWith(99, "recognition", "good");
  });

  it("the undo after a phrase rating targets recognition", async () => {
    const { actions } = setup();
    await actions.onCollocationStateChange(99, "easy");
    await actions.onCollocationUndo(99);
    expect(mockUndo).toHaveBeenCalledWith(99, "recognition");
  });
});
