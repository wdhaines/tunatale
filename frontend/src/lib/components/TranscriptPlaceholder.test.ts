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

  it("renders scene headers from narrator L1 lines", () => {
    const lessonWithScenes: LessonDetail = {
      id: "l3",
      day: 3,
      title: "At the Hotel",
      language_code: "sl",
      sections: [
        {
          type: "natural_speed",
          phrases: [
            { text: "At the hotel", role: "narrator", language_code: "en", voice_id: "v1" },
            { text: "Dober dan", role: "Petra", language_code: "sl", voice_id: "v2" },
            { text: "Dober dan", role: "Marko", language_code: "sl", voice_id: "v3" },
            { text: "In the lobby", role: "narrator", language_code: "en", voice_id: "v1" },
            { text: "Kako si", role: "Petra", language_code: "sl", voice_id: "v2" },
          ],
        },
      ],
      key_phrases: [],
    };

    const { getByText } = render(TranscriptPlaceholder, { props: { lesson: lessonWithScenes } });
    // Scene headers appear
    expect(getByText("At the hotel")).toBeTruthy();
    expect(getByText("In the lobby")).toBeTruthy();
    // L2 lines still render grouped under scenes
    expect(getByText("Kako si")).toBeTruthy();
  });
});
