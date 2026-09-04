import { describe, it, expect } from "vitest";
import { resolveCommand } from "./phraseTable";

describe("resolveCommand", () => {
  it.each([
    ["play", "TOGGLE_PLAY"],
    ["pause", "TOGGLE_PLAY"],
    ["back ten", "SEEK_BACK_10"],
    ["back 10", "SEEK_BACK_10"],
    ["forward ten", "SEEK_FORWARD_10"],
    ["forward 10", "SEEK_FORWARD_10"],
    ["next sentence", "NEXT_CUE"],
    ["last sentence", "PREV_CUE"],
    ["again", "REPEAT_CUE"],
    ["repeat", "REPEAT_CUE"],
    ["start over", "RESTART_SECTION"],
    ["slower", "ENUN_SLOWER"],
    ["faster", "ENUN_FASTER"],
    ["english", "TOGGLE_ENGLISH"],
    ["key phrases", "PHASE_KEY_PHRASES"],
    ["dialogue", "PHASE_DIALOGUE"],
  ])("resolves %s -> %s", (utterance, command) => {
    expect(resolveCommand(utterance)).toBe(command);
  });

  it.each([
    "play it",
    "playing",
    "repeat that",
    "dialogues",
    "pla",
    "nex",
    "start",
    "please play",
    "ok play",
    "hey play",
    "sentence next",
    "over start",
    "back twenty",
    "back 11",
    "forward 5",
    "",
    "   ",
    ".",
    "banana",
  ])("rejects near-miss %j", (utterance) => {
    expect(resolveCommand(utterance)).toBeNull();
  });

  it.each([
    ["Play", "TOGGLE_PLAY"],
    ["  play  ", "TOGGLE_PLAY"],
    ["play.", "TOGGLE_PLAY"],
    ["PLAY!", "TOGGLE_PLAY"],
    ["next   sentence", "NEXT_CUE"],
    ["Key Phrases", "PHASE_KEY_PHRASES"],
  ])("normalizes %j -> %s", (utterance, command) => {
    expect(resolveCommand(utterance)).toBe(command);
  });
});

describe("resolveCommand — lookup-mechanism hazards", () => {
  // Found auditing the first implementation (2026-09-04). A bare index into an
  // object literal reaches Object.prototype, and `?? null` does not stop it
  // because the inherited value is not nullish. Only the already-lowercase
  // members get here, since normalize() lowercases before the lookup.
  it.each(["constructor", "__proto__", "Constructor", "__PROTO__", "  constructor  "])(
    "returns null for the prototype member %j",
    (utterance) => {
      expect(resolveCommand(utterance)).toBeNull();
    },
  );

  it("never returns a non-string, whatever the input", () => {
    for (const utterance of ["constructor", "__proto__", "toString", "valueOf", "play"]) {
      const got = resolveCommand(utterance);
      expect(got === null || typeof got === "string").toBe(true);
    }
  });
});
