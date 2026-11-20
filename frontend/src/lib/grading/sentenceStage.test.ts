/**
 * A lesson line's staged cards (tunatale-h0q3.67): the toggle rule both screens share,
 * and what a Save sends. Design: design-sentence-mining-2026-11.md § 4-6.
 */
import { describe, expect, it } from "vitest";
import {
  addedBlanks,
  confirmOff,
  countOn,
  initStage,
  keep,
  lineEdit,
  newCards,
  toggle,
  turnedOff,
  WHOLE,
  type SentenceLine,
  type SentenceLineToken,
} from "./sentenceStage";

const tok = (
  start: number,
  end: number,
  over: Partial<SentenceLineToken> = {},
): SentenceLineToken => ({
  start,
  end,
  part: null,
  state: "off",
  origin: "mine",
  reviewed: false,
  gloss: "",
  ...over,
});

/** "Bra, takk, og du?": the lesson blanks takk and du, and makes no whole card. */
const line = (over: Partial<SentenceLine> = {}): SentenceLine => ({
  line_key: "sent:test-01:3",
  speaker: "Kari",
  text: "Bra, takk, og du?",
  english: "Fine, thanks, and you?",
  linked: false,
  tokens: [
    tok(0, 3, { gloss: "fine" }),
    tok(5, 9, { state: "on", origin: "lesson" }),
    tok(11, 13),
    tok(14, 16, { state: "on", origin: "lesson" }),
  ],
  whole: tok(0, 17),
  ...over,
});

describe("initStage", () => {
  it("stages the cards that are on and the lesson's cards turned off", () => {
    const l = line({
      tokens: [...line().tokens.slice(0, 3), tok(14, 16, { state: "declined", origin: "lesson" })],
    });
    const stage = initStage(l);
    expect(Object.keys(stage.cards).sort()).toEqual(["14-16", "5-9"]);
    expect(stage.cards["14-16"].on).toBe(false);
    expect(countOn(stage)).toBe(1);
  });

  it("keys a token by the part of the word the lesson blanks", () => {
    const l = line({
      text: "Bilen er rød.",
      tokens: [tok(0, 5, { part: [3, 5], state: "on", origin: "lesson" })],
    });
    expect(Object.keys(initStage(l).cards)).toEqual(["3-5"]);
  });

  it("stages the whole-line card when it is on", () => {
    expect(
      initStage(line({ whole: tok(0, 17, { state: "on", origin: "lesson" }) })).cards[WHOLE].on,
    ).toBe(true);
  });
});

describe("toggle and lineEdit", () => {
  it("a tapped word is a new blank of mine, sent as on", () => {
    const l = line();
    const stage = initStage(l);
    toggle(l, stage, "11-13");
    expect(stage.cards["11-13"]).toEqual({ on: true, origin: "mine", reviewed: false });
    expect(lineEdit(l, stage)).toEqual({
      line_key: l.line_key,
      on: [[11, 13]],
      off: [],
      confirm: false,
    });
    expect(newCards(l, stage, true)).toBe(1);
    expect(addedBlanks(l, stage)).toEqual([{ word: "og", gloss: "" }]);
  });

  it("a blank of mine tapped again is gone, and nothing is sent", () => {
    const l = line();
    const stage = initStage(l);
    toggle(l, stage, "0-3");
    expect(addedBlanks(l, stage)).toEqual([{ word: "Bra", gloss: "fine" }]);
    toggle(l, stage, "0-3");
    expect(stage.cards["0-3"]).toBeUndefined();
    expect(lineEdit(l, stage)).toBeNull();
  });

  it("the lesson's card turned off stays staged, declined, and is sent as off", () => {
    const l = line();
    const stage = initStage(l);
    toggle(l, stage, "5-9");
    expect(stage.cards["5-9"].on).toBe(false);
    expect(lineEdit(l, stage)?.off).toEqual([[5, 9]]);
    expect(turnedOff(l, stage)).toBe(1);
    toggle(l, stage, "5-9");
    expect(lineEdit(l, stage)).toBeNull();
  });

  it("a mined card that exists, turned off, is sent as off", () => {
    const l = line({ tokens: [tok(0, 3, { state: "on", origin: "mine" })] });
    const stage = initStage(l);
    toggle(l, stage, "0-3");
    expect(lineEdit(l, stage)?.off).toEqual([[0, 3]]);
  });

  it("the whole-line card toggles like a blank", () => {
    const l = line();
    const stage = initStage(l);
    toggle(l, stage, WHOLE);
    expect(lineEdit(l, stage)?.on).toEqual([WHOLE]);
  });

  it("before the study, the lesson's own cards still on count as new; after it, they do not", () => {
    const l = line();
    expect(newCards(l, initStage(l), false)).toBe(2);
    expect(newCards(l, initStage(l), true)).toBe(0);
  });
});

describe("the two refusals", () => {
  it("a reviewed card asks first, and Keep it leaves it on", () => {
    const l = line({
      tokens: [
        tok(5, 9, { state: "on", origin: "lesson", reviewed: true }),
        tok(14, 16, { state: "on", origin: "lesson" }),
      ],
    });
    const stage = initStage(l);
    toggle(l, stage, "5-9");
    expect(stage.ask).toBe("5-9");
    expect(stage.cards["5-9"].on).toBe(true);
    keep(stage);
    expect(stage.ask).toBeNull();
    expect(lineEdit(l, stage)).toBeNull();
  });

  it("Turn it off sends it with the confirm, and a second tap does not ask again", () => {
    const l = line({
      tokens: [
        tok(0, 3, { state: "on", origin: "mine", reviewed: true }),
        tok(14, 16, { state: "on", origin: "lesson" }),
      ],
    });
    const stage = initStage(l);
    toggle(l, stage, "0-3");
    confirmOff(stage);
    expect(stage.cards["0-3"]).toBeUndefined();
    expect(lineEdit(l, stage)).toEqual({
      line_key: l.line_key,
      on: [],
      off: [[0, 3]],
      confirm: true,
    });
    toggle(l, stage, "0-3");
    toggle(l, stage, "0-3");
    expect(stage.ask).toBeNull();
  });

  it("confirmOff with nothing asked does nothing", () => {
    const stage = initStage(line());
    confirmOff(stage);
    expect(countOn(stage)).toBe(2);
  });

  it("a linked line's last card is refused; an unlinked line's goes", () => {
    for (const linked of [true, false]) {
      const l = line({ linked, tokens: [tok(5, 9, { state: "on", origin: "lesson" })] });
      const stage = initStage(l);
      toggle(l, stage, "5-9");
      expect(stage.refuse).toBe(linked);
      expect(countOn(stage)).toBe(linked ? 1 : 0);
    }
  });

  it("the next tap clears a refusal", () => {
    const l = line({
      linked: true,
      tokens: [tok(5, 9, { state: "on", origin: "lesson" }), tok(11, 13)],
    });
    const stage = initStage(l);
    toggle(l, stage, "5-9");
    expect(stage.refuse).toBe(true);
    toggle(l, stage, "11-13");
    expect(stage.refuse).toBe(false);
    toggle(l, stage, "5-9");
    expect(stage.cards["5-9"].on).toBe(false);
  });
});
