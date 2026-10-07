/**
 * The reader's English setting (bd tunatale-685k): one persisted value where
 * there used to be two throwaway toggles named in linguistics jargon. The
 * user's words, 2026-10-07: "a translation chip that cycles between off,
 * idiomatic, literal, both", called English "to match Listen mode".
 */
import { describe, it, expect, beforeEach } from "vitest";

import { readerEnglishPref } from "./readerEnglishPref.svelte";

beforeEach(() => {
  localStorage.clear();
  readerEnglishPref.set("off");
  localStorage.clear();
});

describe("readerEnglishPref", () => {
  it("is off when storage is empty", () => {
    readerEnglishPref.init();
    expect(readerEnglishPref.value).toBe("off");
    expect(readerEnglishPref.showIdiomatic).toBe(false);
    expect(readerEnglishPref.showLiteral).toBe(false);
  });

  it.each([
    ["idiomatic", true, false],
    ["literal", false, true],
    ["both", true, true],
    ["off", false, false],
  ] as const)("reads a stored %s", (stored, idiomatic, literal) => {
    localStorage.setItem("readerEnglish", stored);
    readerEnglishPref.init();
    expect(readerEnglishPref.value).toBe(stored);
    expect(readerEnglishPref.showIdiomatic).toBe(idiomatic);
    expect(readerEnglishPref.showLiteral).toBe(literal);
  });

  it("ignores garbage and stays off", () => {
    localStorage.setItem("readerEnglish", "banana");
    readerEnglishPref.init();
    expect(readerEnglishPref.value).toBe("off");
  });

  it("set() writes the value itself as the stored string", () => {
    readerEnglishPref.set("both");
    expect(readerEnglishPref.value).toBe("both");
    expect(localStorage.getItem("readerEnglish")).toBe("both");
  });

  it("next() walks Off, Idiomatic, Literal, Both and back to Off, saving each step", () => {
    const seen: string[] = [];
    for (let i = 0; i < 5; i++) {
      readerEnglishPref.next();
      seen.push(readerEnglishPref.value);
      expect(localStorage.getItem("readerEnglish")).toBe(readerEnglishPref.value);
    }
    expect(seen).toEqual(["idiomatic", "literal", "both", "off", "idiomatic"]);
  });
});
