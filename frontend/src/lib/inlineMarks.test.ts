import { describe, expect, it } from "vitest";
import { inlineMarks } from "./inlineMarks.js";

describe("inlineMarks", () => {
  it("splits italics and affixes out of plain text", () => {
    expect(inlineMarks("After *walâ* the dependent: `-a`, `-i`.")).toEqual([
      { kind: "text", text: "After " },
      { kind: "em", text: "walâ" },
      { kind: "text", text: " the dependent: " },
      { kind: "affix", text: "-a" },
      { kind: "text", text: ", " },
      { kind: "affix", text: "-i" },
      { kind: "text", text: "." },
    ]);
  });

  it("returns marks back to back with no empty text between them", () => {
    expect(inlineMarks("*a*`b`")).toEqual([
      { kind: "em", text: "a" },
      { kind: "affix", text: "b" },
    ]);
  });

  it("leaves unbalanced marks as plain text", () => {
    expect(inlineMarks("2 * 3 and `x")).toEqual([{ kind: "text", text: "2 * 3 and `x" }]);
  });

  it("returns nothing for empty text", () => {
    expect(inlineMarks("")).toEqual([]);
  });
});
