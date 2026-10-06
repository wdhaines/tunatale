/**
 * Tests for wordGloss — which translation the reader shows for a word, and
 * when the card's own translation is worth a second line (bd tunatale-ceuc).
 */
import { describe, it, expect } from "vitest";
import { wordGloss } from "./wordGloss";
import { makeWordToken } from "$lib/../test/factories";

describe("wordGloss", () => {
  it("shows the lesson's gloss first and the disagreeing card second", () => {
    // Norwegian `gang`: the card says "hall", every lesson line means "time".
    const word = makeWordToken({
      surface: "gang",
      gloss: "time",
      translation: "hall",
      srs_item_id: 5,
    });
    expect(wordGloss(word)).toEqual({ shown: "time", card: "hall" });
  });

  it("has no card line when the card agrees with the gloss", () => {
    const word = makeWordToken({ gloss: "time", translation: "time", srs_item_id: 5 });
    expect(wordGloss(word)).toEqual({ shown: "time", card: null });
  });

  it("treats case and surrounding whitespace as agreement", () => {
    const word = makeWordToken({ gloss: " Time", translation: "time ", srs_item_id: 5 });
    expect(wordGloss(word)).toEqual({ shown: " Time", card: null });
  });

  it("falls back to the card's translation when the lesson has no gloss", () => {
    const word = makeWordToken({ gloss: null, translation: "hall", srs_item_id: 5 });
    expect(wordGloss(word)).toEqual({ shown: "hall", card: null });
  });

  it("has no card line for an untracked word, whose translation IS the gloss", () => {
    const word = makeWordToken({ gloss: "time", translation: "time", srs_item_id: null });
    expect(wordGloss(word)).toEqual({ shown: "time", card: null });
  });

  it("has no card line when the word has no card, even if the fields differ", () => {
    // Not a shape the backend emits today; the card line must still never
    // claim a card that is not there.
    const word = makeWordToken({ gloss: "time", translation: "hall", srs_item_id: null });
    expect(wordGloss(word)).toEqual({ shown: "time", card: null });
  });

  it("has no card line when the card has no translation", () => {
    const word = makeWordToken({ gloss: "time", translation: null, srs_item_id: 5 });
    expect(wordGloss(word)).toEqual({ shown: "time", card: null });
  });

  it("shows nothing when the word has neither", () => {
    const word = makeWordToken({ gloss: null, translation: null });
    expect(wordGloss(word)).toEqual({ shown: null, card: null });
  });

  it("reads a token with no gloss field as having no gloss", () => {
    // Offline-cached transcripts predate the field.
    const word = makeWordToken({ translation: "hall", srs_item_id: 5 });
    delete (word as { gloss?: unknown }).gloss;
    expect(wordGloss(word)).toEqual({ shown: "hall", card: null });
  });
});
