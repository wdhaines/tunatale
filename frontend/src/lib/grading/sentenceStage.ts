/**
 * A lesson line's cards, staged on the page until Save (tunatale-h0q3.67).
 *
 * The server sends each line's tokens (`sentence_lines`, never split here); a
 * stage is the learner's copy of which cards are on. A token toggles its blank,
 * or the part of the word the lesson blanks (a linker suffix); the whole-line
 * card toggles like a blank (owner, 2026-11-20). Two refusals happen on the page
 * before Save, and the server enforces both again:
 *
 * - a card that has been reviewed asks first, because its history goes at the
 *   next sync (`ask`);
 * - a line whose note is in Anki keeps at least one card (`refuse`), counted as the
 *   server counts it: a card the deck holds, or one this Save adds. A listed card the
 *   deck lacks reads on but keeps nothing in Anki (review F4).
 *
 * Mockup and rules: .beads-tasks/briefs/design-sentence-mining-2026-11.md § 5-6.
 */
import type { components } from "#lib/api-types.js";

export type SentenceLine = components["schemas"]["SentenceLine"];
export type SentenceLineToken = components["schemas"]["SentenceLineToken"];
export type SentenceEdit = components["schemas"]["SentenceEdit"];

export const WHOLE = "whole";

export type StagedCard = {
  on: boolean;
  origin: string;
  reviewed: boolean;
  /** A row holds it. A listed card the deck lacks is on and not in it (review F4). */
  inDeck: boolean;
  /** On when the page loaded. */
  initiallyOn: boolean;
};
export type LineStage = {
  cards: Record<string, StagedCard>;
  /** The reviewed card waiting for "Turn it off". */
  ask: string | null;
  /** The last card of a linked line was tapped off. */
  refuse: boolean;
  /** Reviewed cards the learner confirmed off. */
  confirmed: string[];
};

/** The span a token toggles: the lesson's part of the word, else the whole word. */
export function tokenSpan(token: SentenceLineToken): [number, number] {
  return token.part ?? [token.start, token.end];
}

export function tokenKey(token: SentenceLineToken): string {
  const [s, e] = tokenSpan(token);
  return `${s}-${e}`;
}

function staged(token: SentenceLineToken): StagedCard | null {
  if (token.state === "off") return null;
  const on = token.state === "on";
  return {
    on,
    origin: token.origin,
    reviewed: token.reviewed,
    inDeck: token.in_deck,
    initiallyOn: on,
  };
}

/** Whether *card* will be a card of the line's note after Save: held by a row, or added by it. */
function keepsTheNote(card: StagedCard): boolean {
  return card.on && (card.inDeck || !card.initiallyOn);
}

export function initStage(line: SentenceLine): LineStage {
  const cards: Record<string, StagedCard> = {};
  for (const token of line.tokens) {
    const card = staged(token);
    if (card) cards[tokenKey(token)] = card;
  }
  const whole = staged(line.whole);
  if (whole) cards[WHOLE] = whole;
  return { cards, ask: null, refuse: false, confirmed: [] };
}

export function countOn(stage: LineStage): number {
  return Object.values(stage.cards).filter((c) => c.on).length;
}

function turnOff(stage: LineStage, key: string) {
  const card = stage.cards[key];
  if (card.origin === "lesson") card.on = false;
  else delete stage.cards[key];
}

/** Tap the card *key* (a token key or `WHOLE`): the one toggle rule of both screens. */
export function toggle(line: SentenceLine, stage: LineStage, key: string): void {
  stage.ask = null;
  stage.refuse = false;
  const card = stage.cards[key];
  if (card?.on) {
    const kept = Object.values(stage.cards).filter(keepsTheNote).length;
    if (line.linked && keepsTheNote(card) && kept === 1) {
      stage.refuse = true;
      return;
    }
    if (card.reviewed && !stage.confirmed.includes(key)) {
      stage.ask = key;
      return;
    }
    turnOff(stage, key);
  } else if (card) {
    card.on = true;
  } else {
    stage.cards[key] = {
      on: true,
      origin: "mine",
      reviewed: false,
      inDeck: false,
      initiallyOn: false,
    };
  }
}

/** "Turn it off" on the reviewed card the stage asked about. */
export function confirmOff(stage: LineStage): void {
  const key = stage.ask;
  if (key === null) return;
  stage.confirmed.push(key);
  turnOff(stage, key);
  stage.ask = null;
}

/** "Keep it". */
export function keep(stage: LineStage): void {
  stage.ask = null;
}

function spanOf(key: string): [number, number] | typeof WHOLE {
  if (key === WHOLE) return WHOLE;
  const [s, e] = key.split("-").map(Number);
  return [s, e];
}

/** What a Save sends for the line: the cards turned on and off since the page loaded, or null for none. */
export function lineEdit(line: SentenceLine, stage: LineStage): SentenceEdit | null {
  const was = initStage(line).cards;
  const keys = new Set([...Object.keys(was), ...Object.keys(stage.cards)]);
  const on: SentenceEdit["on"] = [];
  const off: SentenceEdit["off"] = [];
  for (const key of keys) {
    const before = Boolean(was[key]?.on);
    const after = Boolean(stage.cards[key]?.on);
    if (after && !before) on.push(spanOf(key));
    if (before && !after) off.push(spanOf(key));
  }
  if (on.length === 0 && off.length === 0) return null;
  return { line_key: line.line_key, on, off, confirm: stage.confirmed.length > 0 };
}

/** How many cards the line's Save adds: turned on now, plus, before the study, the list's own still on. */
export function newCards(line: SentenceLine, stage: LineStage, studied: boolean): number {
  const was = initStage(line).cards;
  return Object.entries(stage.cards).filter(
    ([key, c]) => c.on && (!was[key]?.on || (!studied && c.origin === "lesson")),
  ).length;
}

/** How many cards the line's Save turns off. */
export function turnedOff(line: SentenceLine, stage: LineStage): number {
  return lineEdit(line, stage)?.off.length ?? 0;
}

/** A blank the learner added on this page, with the line's own gloss of its word ("" = written at Save). */
export function addedBlanks(
  line: SentenceLine,
  stage: LineStage,
): { key: string; word: string; gloss: string }[] {
  const was = initStage(line).cards;
  return line.tokens
    .filter((t) => {
      const c = stage.cards[tokenKey(t)];
      return c?.on && c.origin === "mine" && !was[tokenKey(t)]?.on;
    })
    .map((t) => ({ key: tokenKey(t), word: line.text.slice(...tokenSpan(t)), gloss: t.gloss }));
}
