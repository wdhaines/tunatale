import { describe, it, expect } from "vitest";
import { splitCaption, activeChunkIndex, chunkStartMs, MAX_CAPTION_CHARS } from "./captionChunks";

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

  it("splits at the comma first, then word-packs the long clause, keeping every chunk in budget", () => {
    const text = "En kvinne hadde forsvunnet, og Hansen visste at dette var en vanskelig sak.";
    const chunks = splitCaption(text);
    // The comma boundary survives even though the second clause is longer than
    // MAX and must itself be word-packed into further chunks.
    expect(chunks[0]).toBe("En kvinne hadde forsvunnet,");
    expect(chunks.length).toBeGreaterThan(2);
    for (const c of chunks) {
      expect(c.length).toBeLessThanOrEqual(MAX_CAPTION_CHARS);
    }
    // No word is lost or split across chunks.
    expect(chunks.join(" ")).toBe(text);
  });

  it("splits at semicolon for sentences over MAX", () => {
    const text =
      "Dette er en veldig lang setning som maa deles opp; den er for lang aa vise paa en gang.";
    const chunks = splitCaption(text);
    expect(chunks.length).toBeGreaterThanOrEqual(2);
    for (const c of chunks) {
      expect(c.length).toBeLessThanOrEqual(MAX_CAPTION_CHARS);
    }
  });

  it("keeps punctuation on the left piece after clause split", () => {
    // Long enough (> MAX) to actually split at the comma.
    const text = "The first clause runs here, and the second clause runs there.";
    const chunks = splitCaption(text);
    expect(text.length).toBeGreaterThan(MAX_CAPTION_CHARS);
    expect(chunks[0].endsWith(",")).toBe(true);
  });

  it("passes through short sentences unchanged", () => {
    expect(splitCaption("Hello world.")).toEqual(["Hello world."]);
    expect(splitCaption("Short.")).toEqual(["Short."]);
  });

  it("splits at dash for sentences over MAX", () => {
    const text = "A very long sentence part — another very long sentence part that continues here.";
    const chunks = splitCaption(text);
    if (text.length > MAX_CAPTION_CHARS) {
      expect(chunks.length).toBeGreaterThanOrEqual(2);
      expect(chunks[0]).toContain("—");
    }
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

describe("chunkStartMs", () => {
  it("returns startMs when there is only one chunk", () => {
    expect(chunkStartMs(["Hello."], 1000, 3000, 0)).toBe(1000);
  });

  it("returns startMs when endMs <= startMs", () => {
    expect(chunkStartMs(["A.", "B."], 1000, 1000, 0)).toBe(1000);
  });

  it("returns startMs when total chars is 0", () => {
    expect(chunkStartMs([], 0, 1000, 0)).toBe(0);
    expect(chunkStartMs(["", ""], 0, 1000, 1)).toBe(0);
  });

  it("returns startMs for idx <= 0", () => {
    expect(chunkStartMs(["A.", "B."], 1000, 3000, -1)).toBe(1000);
    expect(chunkStartMs(["A.", "B."], 1000, 3000, 0)).toBe(1000);
  });

  it("clamps idx >= length to last chunk's start", () => {
    const result = chunkStartMs(["A.", "B."], 1000, 3000, 99);
    const totalLen = "A.".length + "B.".length;
    const lastChunkStart = 1000 + (3000 - 1000) * ("A.".length / totalLen);
    expect(result).toBeCloseTo(lastChunkStart, 0);
  });

  it("returns proportional start for each chunk", () => {
    const chunks = ["Short.", "A longer sentence here."];
    const s = 0;
    const e = 10000;
    const totalLen = "Short.".length + "A longer sentence here.".length;
    const boundary = s + (e - s) * ("Short.".length / totalLen);
    expect(chunkStartMs(chunks, s, e, 0)).toBe(s);
    expect(chunkStartMs(chunks, s, e, 1)).toBeCloseTo(boundary, 0);
  });

  it("round-trip invariant: activeChunkIndex(chunkStartMs(...)) === idx", () => {
    const chunks = splitCaption(
      "En kvinne hadde forsvunnet, og Hansen visste at dette var en vanskelig sak.",
    );
    const s = 0;
    const e = 10000;
    for (let idx = 0; idx < chunks.length; idx++) {
      const ms = chunkStartMs(chunks, s, e, idx);
      expect(activeChunkIndex(chunks, s, e, ms)).toBe(idx);
    }
  });

  it("single-chunk round-trip returns 0", () => {
    const chunks = ["Hello world."];
    const ms = chunkStartMs(chunks, 0, 5000, 0);
    expect(ms).toBe(0);
    expect(activeChunkIndex(chunks, 0, 5000, ms)).toBe(0);
  });
});
