/**
 * The pinned word list of a hand-written review session, between copying the
 * prompt and pasting the story.
 *
 * The incident these guard (2026-10-01): the list lived in the home page's
 * component state. The learner copied the prompt, opened the session auto mode
 * had just made, and came back to a remounted page with an empty list — so the
 * paste was refused with "'review_words' must not be empty", an error naming a
 * field the learner never typed.
 *
 * `freshModule` is the reload: module state is gone, only storage is left.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { LANGUAGE_STORAGE_KEY } from "$lib/api";

const WORDS = ["oppføre", "dessuten"];
const KEY = "tt-review-draft";

async function freshModule() {
  vi.resetModules();
  return import("./reviewDraft");
}

function blockStorage() {
  for (const method of ["getItem", "setItem", "removeItem"] as const) {
    vi.spyOn(Storage.prototype, method).mockImplementation(() => {
      throw new Error("blocked");
    });
  }
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "no");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the pinned review words", () => {
  it("are empty until a prompt has been copied", async () => {
    const { pinnedReviewWords } = await freshModule();
    expect(pinnedReviewWords()).toEqual([]);
  });

  it("come back as pinned", async () => {
    const { pinReviewWords, pinnedReviewWords } = await freshModule();
    pinReviewWords(WORDS);
    expect(pinnedReviewWords()).toEqual(WORDS);
  });

  it("survive a reload", async () => {
    const first = await freshModule();
    first.pinReviewWords(WORDS);

    const reloaded = await freshModule();
    expect(reloaded.pinnedReviewWords()).toEqual(WORDS);
  });

  it("are not handed to another language", async () => {
    const { pinReviewWords, pinnedReviewWords } = await freshModule();
    pinReviewWords(WORDS);

    localStorage.setItem(LANGUAGE_STORAGE_KEY, "sl");

    expect(pinnedReviewWords()).toEqual([]);
  });

  it("are gone once unpinned, on this visit and after a reload", async () => {
    const first = await freshModule();
    first.pinReviewWords(WORDS);
    first.unpinReviewWords();
    expect(first.pinnedReviewWords()).toEqual([]);

    const reloaded = await freshModule();
    expect(reloaded.pinnedReviewWords()).toEqual([]);
  });

  it("read as empty when the stored value is not JSON", async () => {
    localStorage.setItem(KEY, "{not json");
    const { pinnedReviewWords } = await freshModule();
    expect(pinnedReviewWords()).toEqual([]);
  });

  it("read as empty when the stored value has the wrong shape", async () => {
    localStorage.setItem(KEY, JSON.stringify({ language: "no", words: "oppføre" }));
    const { pinnedReviewWords } = await freshModule();
    expect(pinnedReviewWords()).toEqual([]);
  });

  it("still serve this visit when storage is blocked", async () => {
    const { pinReviewWords, pinnedReviewWords } = await freshModule();
    blockStorage();

    pinReviewWords(WORDS);

    expect(pinnedReviewWords()).toEqual(WORDS);
  });

  it("can be unpinned when storage is blocked", async () => {
    const { pinReviewWords, pinnedReviewWords, unpinReviewWords } = await freshModule();
    blockStorage();
    pinReviewWords(WORDS);

    unpinReviewWords();

    expect(pinnedReviewWords()).toEqual([]);
  });
});
