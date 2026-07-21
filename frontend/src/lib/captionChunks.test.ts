import { describe, it, expect } from "vitest";
import { splitCaption, activeChunkIndex, MAX_CAPTION_CHARS } from "./captionChunks";

describe("splitCaption", () => {
  it("returns [text] for empty string", () => {
    expect(splitCaption("")).toEqual([""]);
  });

  it("returns [text] for a short single sentence", () => {
    expect(splitCaption("Hello world.")).toEqual(["Hello world."]);
  });

  it("returns [text] for a short dialogue line (no terminal punct)", () => {
    expect(splitCaption("Pozdravljeni")).toEqual(["Pozdravljeni"]);
  });

  it("splits multiple short sentences", () => {
    expect(splitCaption("First. Second. Third.")).toEqual(["First.", "Second.", "Third."]);
  });

  it("splits on ! and ? as sentence boundaries", () => {
    expect(splitCaption("Really?! Yes!")).toEqual(["Really?!", "Yes!"]);
  });

  it("greedily packs an over-budget multi-word sentence at word boundaries", () => {
    const words = Array.from({ length: 30 }, (_, i) => `Word${i}`);
    const long = words.join(" ");
    const chunks = splitCaption(long);
    expect(chunks.length).toBeGreaterThanOrEqual(2);
    for (const c of chunks) {
      expect(c.length).toBeLessThanOrEqual(MAX_CAPTION_CHARS);
    }
    expect(chunks.join(" ")).toBe(long);
  });

  it("never splits mid-word even if a single word exceeds the budget", () => {
    const text = "X".repeat(100);
    const chunks = splitCaption(text);
    expect(chunks).toEqual([text]);
  });

  it("trims chunks and drops empties", () => {
    const chunks = splitCaption("  Hello.   World.  ");
    expect(chunks).toEqual(["Hello.", "World."]);
  });

  it("preserves the full cue text when it is a short single sentence", () => {
    const text = "This is a short sentence.";
    expect(splitCaption(text)).toEqual([text]);
  });
});

describe("activeChunkIndex", () => {
  it("returns 0 for a single chunk", () => {
    expect(activeChunkIndex(["Hello."], 0, 1000, 500)).toBe(0);
  });

  it("clamps to 0 when currentMs <= startMs", () => {
    expect(activeChunkIndex(["A.", "B."], 1000, 3000, 500)).toBe(0);
  });

  it("clamps to last when currentMs >= endMs", () => {
    expect(activeChunkIndex(["A.", "B."], 1000, 3000, 4000)).toBe(1);
  });

  it("returns 0 when endMs <= startMs", () => {
    expect(activeChunkIndex(["A.", "B."], 1000, 1000, 1000)).toBe(0);
  });

  it("returns 0 when total chars is 0 (empty chunks)", () => {
    expect(activeChunkIndex([], 0, 1000, 500)).toBe(0);
  });

  it("returns 0 when all chunks are empty strings", () => {
    expect(activeChunkIndex(["", ""], 0, 1000, 500)).toBe(0);
  });

  it("returns the correct index for proportional allocation", () => {
    const chunks = ["Short.", "A longer sentence here."];
    const totalLen = "Short.".length + "A longer sentence here.".length;
    const startMs = 0;
    const endMs = 10000;
    const boundary = startMs + (endMs - startMs) * ("Short.".length / totalLen);
    expect(activeChunkIndex(chunks, startMs, endMs, boundary - 1)).toBe(0);
    expect(activeChunkIndex(chunks, startMs, endMs, boundary)).toBe(1);
  });

  it("returns last index at the exact endMs boundary", () => {
    const chunks = ["A.", "B.", "C."];
    expect(activeChunkIndex(chunks, 0, 3000, 3000)).toBe(2);
  });

  it("falls through to last index when elapsed equals cumulative boundary", () => {
    const chunks = ["A.", "B.", "C."];
    expect(activeChunkIndex(chunks, 0, 9999, 9999)).toBe(2);
  });
});
