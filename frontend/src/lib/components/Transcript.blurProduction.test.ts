/**
 * The "Produce" toggle (bd tunatale-dvdm.3) turns the blur-as-cloze reader on
 * for the dialogue, persists across reloads, and is OFF by default.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/svelte";
import { tick } from "svelte";
import Transcript from "./Transcript.svelte";
import type { TranscriptData } from "$lib/api";
import { makeWordToken } from "../../test/factories";
import { readerProductionPref } from "$lib/stores/readerProductionPref.svelte";
import { readerEnglishPref } from "$lib/stores/readerEnglishPref.svelte";

vi.mock("$lib/api", () => ({
  api: { translate: vi.fn() },
}));

const transcript: TranscriptData = {
  lesson_id: "l1",
  key_phrases: [],
  dialogue_lines: [
    {
      role: "female-1",
      sentence: "Huset er rødt",
      words: [
        makeWordToken({
          surface: "Huset",
          lemma: "hus",
          srs_state: "review",
          srs_item_id: 11,
          active_state: "review",
          active_direction: "recognition",
          is_due: false,
          production_due: true,
        }),
        makeWordToken({
          surface: "er",
          lemma: "være",
          srs_state: "review",
          srs_item_id: 12,
          active_state: "review",
          active_direction: "recognition",
          is_due: true,
          production_due: false,
        }),
      ],
    },
  ],
};

function words(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll(".dialogue-line .word, .word")] as HTMLElement[];
}

beforeEach(() => {
  localStorage.clear();
  readerProductionPref.set(false);
  readerEnglishPref.set("off");
  localStorage.clear();
});

describe("Transcript — Produce toggle", () => {
  it("is off by default: nothing blurs", () => {
    const { container } = render(Transcript, { props: { transcript } });
    expect(container.querySelectorAll(".word-blurred")).toHaveLength(0);
  });

  it("turning it on blurs exactly the production-due word, and persists", async () => {
    const { container } = render(Transcript, { props: { transcript } });
    readerProductionPref.set(true);
    await tick();
    const blurred = container.querySelectorAll(".word-blurred");
    expect(blurred).toHaveLength(1);
    // The recognition-due neighbour still bolds.
    const er = words(container).find((w) => w.textContent === "er");
    expect(er?.classList.contains("word-due")).toBe(true);
    expect(localStorage.getItem("readerProduction")).toBe("on");
  });

  it("a stored 'on' is honoured at mount", () => {
    localStorage.setItem("readerProduction", "on");
    const { container } = render(Transcript, { props: { transcript } });
    expect(container.querySelectorAll(".word-blurred")).toHaveLength(1);
  });
});
