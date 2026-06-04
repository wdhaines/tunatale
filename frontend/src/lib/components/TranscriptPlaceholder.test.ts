/**
 * Tests for TranscriptPlaceholder.svelte — plain, classla-free transcript preview
 * shown while the enriched (word-state) transcript is being fetched.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/svelte";
import TranscriptPlaceholder from "./TranscriptPlaceholder.svelte";
import type { LessonDetail } from "$lib/api";

const lessonWithDialogue: LessonDetail = {
  id: "l1",
  day: 1,
  title: "Day 1",
  language_code: "sl",
  sections: [
    {
      type: "natural_speed",
      phrases: [
        { text: "Dober dan", role: "Petra", language_code: "sl", voice_id: "v1" },
        // A non-L2 (narrator/English) line — the enriched transcript drops these,
        // so the placeholder must too.
        { text: "Good day", role: "Narrator", language_code: "en", voice_id: "v2" },
      ],
    },
  ],
  key_phrases: [{ phrase: "dober dan", translation: "good day" }],
};

const emptyLesson: LessonDetail = {
  id: "l2",
  day: 2,
  title: "Day 2",
  language_code: "sl",
  sections: [{ type: "key_phrases", phrases: [] }],
  key_phrases: [],
};

describe("TranscriptPlaceholder", () => {
  it("always shows the preparing hint", () => {
    const { getByText } = render(TranscriptPlaceholder, { props: { lesson: emptyLesson } });
    expect(getByText(/Preparing word states/)).toBeTruthy();
  });

  it("renders key phrases and L2 dialogue, dropping non-L2 lines", () => {
    const { getByText, queryByText } = render(TranscriptPlaceholder, {
      props: { lesson: lessonWithDialogue },
    });
    // Key phrase + translation
    expect(getByText("dober dan")).toBeTruthy();
    expect(getByText("good day")).toBeTruthy();
    // L2 dialogue line (role + text)
    expect(getByText("Petra")).toBeTruthy();
    expect(getByText("Dober dan")).toBeTruthy();
    // The English narrator line is filtered out
    expect(queryByText("Good day")).toBeFalsy();
    expect(queryByText("Narrator")).toBeFalsy();
  });

  it("shows neither section when there is no natural-speed dialogue or key phrases", () => {
    const { queryByText } = render(TranscriptPlaceholder, { props: { lesson: emptyLesson } });
    expect(queryByText("Key Phrases")).toBeFalsy();
    expect(queryByText("Dialogue")).toBeFalsy();
  });
});
