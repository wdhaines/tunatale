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
  offeredSuggestions,
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
  in_deck: false,
  suggested: false,
  ...over,
});

/** "Bra, takk, og du?": the lesson blanks takk and du, and makes no whole card. */
const line = (over: Partial<SentenceLine> = {}): SentenceLine => ({
  line_key: "sent:test-01:3",
  speaker: "Kari",
  text: "Bra, takk, og du?",
  english: "Fine, thanks, and you?",
  linked: false,
  dialogue_index: 2,
  spoken_map: [0, 1, 2, 3],
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
    expect(stage.cards["11-13"]).toEqual({
      on: true,
      origin: "mine",
      reviewed: false,
      inDeck: false,
      initiallyOn: false,
    });
    expect(lineEdit(l, stage)).toEqual({
      line_key: l.line_key,
      on: [[11, 13]],
      off: [],
      confirm: false,
    });
    expect(newCards(l, stage, true)).toBe(1);
    expect(addedBlanks(l, stage)).toEqual([{ key: "11-13", word: "og", gloss: "" }]);
  });

  it("a blank of mine tapped again is gone, and nothing is sent", () => {
    const l = line();
    const stage = initStage(l);
    toggle(l, stage, "0-3");
    expect(addedBlanks(l, stage)).toEqual([{ key: "0-3", word: "Bra", gloss: "fine" }]);
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
      const l = line({
        linked,
        tokens: [tok(5, 9, { state: "on", origin: "lesson", in_deck: true })],
      });
      const stage = initStage(l);
      toggle(l, stage, "5-9");
      expect(stage.refuse).toBe(linked);
      expect(countOn(stage)).toBe(linked ? 1 : 0);
    }
  });

  it("the next tap clears a refusal", () => {
    const l = line({
      linked: true,
      tokens: [tok(5, 9, { state: "on", origin: "lesson", in_deck: true }), tok(11, 13)],
    });
    const stage = initStage(l);
    toggle(l, stage, "5-9");
    expect(stage.refuse).toBe(true);
    toggle(l, stage, "11-13");
    expect(stage.refuse).toBe(false);
    toggle(l, stage, "5-9");
    expect(stage.cards["5-9"].on).toBe(false);
  });

  it("a listed card the deck lacks keeps nothing in Anki: it goes freely, and does not save the last real card", () => {
    // Review F4: du's row was deleted on the Cards page; it reads on (it mints at study), the server counts rows.
    const l = line({
      linked: true,
      tokens: [
        tok(5, 9, { state: "on", origin: "lesson", in_deck: true }),
        tok(14, 16, { state: "on", origin: "lesson" }),
      ],
    });
    const stage = initStage(l);
    toggle(l, stage, "5-9");
    expect(stage.refuse).toBe(true);
    toggle(l, stage, "14-16");
    expect(stage.refuse).toBe(false);
    expect(stage.cards["14-16"].on).toBe(false);
  });

  it("a card this Save adds keeps the note, so the last held card may then go", () => {
    const l = line({
      linked: true,
      tokens: [tok(5, 9, { state: "on", origin: "lesson", in_deck: true }), tok(11, 13)],
    });
    const stage = initStage(l);
    toggle(l, stage, "11-13");
    toggle(l, stage, "5-9");
    expect(stage.refuse).toBe(false);
    expect(lineEdit(l, stage)).toEqual({
      line_key: l.line_key,
      on: [[11, 13]],
      off: [[5, 9]],
      confirm: false,
    });
  });
});

// The day's pattern forms as suggested blanks (tunatale-ve4p.37, design § 8): Grade offers
// the ones its budget reaches; a Save keeps them, and one turned off is declined.
describe("suggested blanks", () => {
  /** "Molakaw ko. Moinom siya.": both verbs say mo-/mi-; "ko" says nothing. */
  const verbs = (over: Partial<SentenceLine> = {}): SentenceLine =>
    line({
      line_key: "sent:lesson-1:1",
      text: "Molakaw ko. Moinom siya.",
      tokens: [
        tok(0, 7, { suggested: true, gloss: "will go" }),
        tok(8, 10),
        tok(12, 18, { suggested: true }),
        tok(19, 23),
      ],
      whole: tok(0, 24),
      ...over,
    });
  const offer = (...keys: string[]) => new Set(keys);

  it("offeredSuggestions takes the budget's worth in story order, across lines", () => {
    const second = verbs({ line_key: "sent:lesson-1:2" });
    expect(offeredSuggestions([verbs(), second], 3)).toEqual(
      new Map([
        ["sent:lesson-1:1", new Set(["0-7", "12-18"])],
        ["sent:lesson-1:2", new Set(["0-7"])],
      ]),
    );
    expect(offeredSuggestions([verbs()], 0)).toEqual(new Map());
  });

  it("an offered suggestion starts on and a Save makes it", () => {
    const l = verbs();
    const stage = initStage(l, offer("0-7"));
    expect(countOn(stage)).toBe(1);
    expect(stage.cards["12-18"]).toBeUndefined(); // past the budget: not staged
    expect(lineEdit(l, stage)).toEqual({
      line_key: l.line_key,
      on: [[0, 7]],
      off: [],
      confirm: false,
    });
    expect(newCards(l, stage, true)).toBe(1);
    expect(addedBlanks(l, stage)).toEqual([{ key: "0-7", word: "Molakaw", gloss: "will go" }]);
  });

  it("an offered suggestion turned off is declined, and on again is a card again", () => {
    const l = verbs();
    const stage = initStage(l, offer("0-7"));
    toggle(l, stage, "0-7");
    expect(stage.cards["0-7"].on).toBe(false); // kept, so the Save can decline it
    expect(lineEdit(l, stage)).toEqual({
      line_key: l.line_key,
      on: [],
      off: [[0, 7]],
      confirm: false,
    });
    expect(turnedOff(l, stage)).toBe(1);
    toggle(l, stage, "0-7");
    expect(lineEdit(l, stage)?.on).toEqual([[0, 7]]);
  });

  it("a suggestion not offered (Read, or past the budget) is a plain word: tapped on, it is a mined blank", () => {
    const l = verbs();
    const stage = initStage(l);
    expect(lineEdit(l, stage)).toBeNull(); // nothing offered, nothing declined
    toggle(l, stage, "12-18");
    expect(stage.cards["12-18"].origin).toBe("mine");
    toggle(l, stage, "12-18");
    expect(stage.cards["12-18"]).toBeUndefined(); // and off again: nothing to decline
    expect(lineEdit(l, stage)).toBeNull();
  });

  it("an offered suggestion keeps a linked line's note, as any card this Save adds", () => {
    const l = verbs({
      linked: true,
      tokens: [tok(0, 7, { suggested: true }), tok(8, 10, { state: "on", in_deck: true })],
    });
    const stage = initStage(l, offer("0-7"));
    toggle(l, stage, "8-10");
    expect(stage.refuse).toBe(false);
  });
});
