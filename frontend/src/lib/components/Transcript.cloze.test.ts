/**
 * Read's Cloze… (tunatale-h0q3.69): the popover item, the line editor it opens under the line,
 * and what a save refreshes. The tap journey itself is tests/read-cloze.spec.ts's.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import Transcript from "./Transcript.svelte";
import { api } from "#lib/api.js";
import type { TranscriptData } from "#lib/api.js";
import type { SentenceLine, SentenceLineToken } from "#lib/grading/sentenceStage.js";
import { makeWordToken } from "../../test/factories";
import { readerEnglishPref } from "#lib/stores/readerEnglishPref.svelte.js";

vi.mock("#lib/api.js", () => ({
  api: { getSentenceLines: vi.fn(), saveSentenceLines: vi.fn(), translateTerm: vi.fn() },
}));

const getLines = vi.mocked(api.getSentenceLines);
const saveLines = vi.mocked(api.saveSentenceLines);

const words = (...surfaces: string[]) =>
  surfaces.map((surface) => makeWordToken({ surface, lemma: surface.toLowerCase() }));

/** Line 0 is a scene line the server matched; line 1 (the narrator's) is not one. */
const transcript: TranscriptData = {
  lesson_id: "l1",
  key_phrases: [],
  dialogue_lines: [
    { role: "female-1", sentence: "Bra, takk, og du?", words: words("Bra", "takk", "og", "du") },
    { role: "female-1", sentence: "Slutt.", words: words("Slutt") },
  ],
};

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
  ...over,
});

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
    tok(14, 16),
  ],
  whole: tok(0, 17),
  dialogue_index: 0,
  spoken_map: [0, 1, 2, 3],
  ...over,
});

const el = (container: HTMLElement, index: number, lineIdx = 0) =>
  container.querySelector(
    `[data-line-index="${lineIdx}"][data-word-index="${index}"]`,
  ) as HTMLElement;

const clozeButton = (container: HTMLElement, index: number, lineIdx = 0) =>
  Array.from(el(container, index, lineIdx).closest(".tt-wrap")!.querySelectorAll("button")).find(
    (b) => b.textContent === "Cloze…",
  );

async function mount(lines: SentenceLine[] = [line()], props: Record<string, unknown> = {}) {
  getLines.mockResolvedValue({ lines });
  const onLinesSaved = vi.fn();
  const r = render(Transcript, {
    props: { transcript, contentId: "lesson-1", onLinesSaved, ...props },
  });
  await waitFor(() => expect(getLines).toHaveBeenCalledWith("lesson-1"));
  await waitFor(() =>
    expect(clozeButton(r.container, 0) ?? (lines.length ? undefined : true)).toBeTruthy(),
  );
  return { ...r, onLinesSaved };
}

async function openCloze(container: HTMLElement, index: number) {
  await fireEvent.click(el(container, index));
  await fireEvent.click(clozeButton(container, index)!);
}

const editor = (container: HTMLElement) =>
  container.querySelector(".read-cloze") as HTMLElement | null;
const tokenButton = (container: HTMLElement, word: string) =>
  Array.from(editor(container)!.querySelectorAll("button")).find(
    (b) => b.textContent?.trim() === word,
  )!;
const button = (container: HTMLElement, label: string) =>
  Array.from(container.querySelectorAll("button")).find((b) => b.textContent === label)!;

describe("Read's Cloze…", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    readerEnglishPref.set("off");
  });

  it("is offered on a line the lesson's sentence lines cover, and not on one they don't", async () => {
    // A lesson line the voice does not say has no transcript line, and offers nothing.
    const { container } = await mount([
      line(),
      line({ line_key: "sent:test-01:9", dialogue_index: null }),
    ]);
    expect(clozeButton(container, 3)).toBeTruthy();
    expect(clozeButton(container, 0, 1)).toBeUndefined();
  });

  it("is not offered without a content id, and asks for no lines", () => {
    const { container } = render(Transcript, { props: { transcript } });
    expect(getLines).not.toHaveBeenCalled();
    expect(clozeButton(container, 0)).toBeUndefined();
  });

  it("is not offered when the lines cannot be read", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    getLines.mockRejectedValue(new Error("boom"));
    const { container } = render(Transcript, { props: { transcript, contentId: "lesson-1" } });
    await waitFor(() => expect(err).toHaveBeenCalled());
    expect(clozeButton(container, 0)).toBeUndefined();
    err.mockRestore();
  });

  it("opens the editor under the line with the tapped word on", async () => {
    const { container } = await mount();
    await openCloze(container, 2);
    expect(editor(container)).toBeTruthy();
    expect(tokenButton(container, "og").className).toContain("mine");
    expect(tokenButton(container, "Bra").className).not.toContain("on");
    expect(clozeButton(container, 0)).toBeUndefined(); // one editor at a time
  });

  it("opens with no word on when the transcript's words do not map to the card's", async () => {
    const { container } = await mount([line({ spoken_map: null })]);
    await openCloze(container, 2);
    expect(tokenButton(container, "og").className).not.toContain("on");
  });

  it("opens with no word on when the map's length is not the transcript line's", async () => {
    const { container } = await mount([line({ spoken_map: [0, 1, 2] })]);
    await openCloze(container, 2);
    expect(tokenButton(container, "og").className).not.toContain("on");
  });

  it("leaves a word that is already a card as it is", async () => {
    const { container } = await mount();
    await openCloze(container, 1);
    expect(tokenButton(container, "takk").className).toContain("on");
    await fireEvent.click(button(container, "Save"));
    expect(saveLines).not.toHaveBeenCalled(); // nothing changed: Save just closes
    expect(editor(container)).toBeNull();
  });

  it("Cancel discards", async () => {
    const { container } = await mount();
    await openCloze(container, 2);
    await fireEvent.click(button(container, "Cancel"));
    expect(editor(container)).toBeNull();
    expect(saveLines).not.toHaveBeenCalled();
    expect(clozeButton(container, 0)).toBeTruthy();
  });

  it("Save posts the line's edit, re-reads the lines and the transcript, and says what it made", async () => {
    saveLines.mockResolvedValue({ status: "ok", created: 1, not_made: [] });
    const { container, onLinesSaved } = await mount();
    await openCloze(container, 2);
    await fireEvent.click(button(container, "Save"));
    expect(saveLines).toHaveBeenCalledWith("lesson-1", [
      { line_key: "sent:test-01:3", on: [[11, 13]], off: [], confirm: false },
    ]);
    await waitFor(() => expect(onLinesSaved).toHaveBeenCalled());
    expect(getLines).toHaveBeenCalledTimes(2);
    expect(editor(container)).toBeNull();
    expect(container.querySelector(".cloze-notice")?.textContent).toBe("Saved: 1 new card");
  });

  it("names a blank it could not make", async () => {
    saveLines.mockResolvedValue({
      status: "ok",
      created: 0,
      not_made: [{ line_key: "sent:test-01:3", start: 11, end: 13, word: "og" }],
    });
    const { container } = await mount();
    await openCloze(container, 2);
    await fireEvent.click(button(container, "Save"));
    await waitFor(() =>
      expect(container.querySelector(".cloze-notice")?.textContent).toBe(
        "Saved Not made, no meaning found: og. Everything else was saved.",
      ),
    );
  });

  it("a save with no refresh callback still closes and re-reads the lines", async () => {
    saveLines.mockResolvedValue({ status: "ok", created: 2, not_made: [] });
    const { container } = await mount([line()], { onLinesSaved: undefined });
    await openCloze(container, 2);
    await fireEvent.click(button(container, "Save"));
    await waitFor(() =>
      expect(container.querySelector(".cloze-notice")?.textContent).toBe("Saved: 2 new cards"),
    );
  });

  it("a refusal is named in the editor and nothing closes", async () => {
    saveLines.mockRejectedValue(
      Object.assign(new Error("409"), {
        status: 409,
        detail: "the lesson's text changed under it",
      }),
    );
    const { container } = await mount();
    await openCloze(container, 2);
    await fireEvent.click(button(container, "Save"));
    await waitFor(() =>
      expect(container.querySelector(".cloze-error")?.textContent).toBe(
        "Not saved: the lesson's text changed under it",
      ),
    );
    expect(editor(container)).toBeTruthy();
  });

  it("a reviewed card the server asks about is named", async () => {
    saveLines.mockRejectedValue(
      Object.assign(new Error("409"), {
        status: 409,
        detail: {
          message: "these cards have been reviewed",
          needs_confirm: ["sent:test-01:3:5-9"],
        },
      }),
    );
    const { container } = await mount();
    await openCloze(container, 2);
    await fireEvent.click(button(container, "Save"));
    await waitFor(() =>
      expect(container.querySelector(".cloze-error")?.textContent).toBe(
        "Not saved: these cards have been reviewed",
      ),
    );
  });

  it("any other failure shows its message, and touching the line clears it", async () => {
    saveLines.mockRejectedValue(new Error("POST /x: HTTP 500"));
    const { container } = await mount();
    await openCloze(container, 2);
    await fireEvent.click(button(container, "Save"));
    await waitFor(() =>
      expect(container.querySelector(".cloze-error")?.textContent).toBe("POST /x: HTTP 500"),
    );
    await fireEvent.click(tokenButton(container, "Bra"));
    expect(container.querySelector(".cloze-error")).toBeNull();
  });

  it("a thrown non-Error is shown as text", async () => {
    saveLines.mockRejectedValue("offline");
    const { container } = await mount();
    await openCloze(container, 2);
    await fireEvent.click(button(container, "Save"));
    await waitFor(() =>
      expect(container.querySelector(".cloze-error")?.textContent).toBe("offline"),
    );
  });

  it("is offered on a phrase's own words once drilled in", async () => {
    const coll = (surface: string, start: boolean) =>
      makeWordToken({
        surface,
        lemma: surface,
        collocation_span_id: 5,
        collocation_start: start,
        collocation_srs_state: "new",
        collocation_lemma: "og du",
        collocation_translation: "and you",
      });
    const withPhrase: TranscriptData = {
      ...transcript,
      dialogue_lines: [
        {
          role: "female-1",
          sentence: "",
          words: [...words("Bra", "takk"), coll("og", true), coll("du", false)],
        },
      ],
    };
    const { container } = await mount([line()], { transcript: withPhrase });
    expect(clozeButton(container, 2)).toBeUndefined(); // the phrase's popover is the phrase's
    await fireEvent.click(button(container, "Words…"));
    await waitFor(() => expect(clozeButton(container, 2)).toBeTruthy());
    await fireEvent.click(clozeButton(container, 2)!);
    expect(tokenButton(container, "og").className).toContain("mine"); // the phrase's word, on
  });
});
