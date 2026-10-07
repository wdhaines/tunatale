/**
 * The reader's two setting chips (bd tunatale-685k).
 *
 * They replace three identical pills in the Dialogue header that the user
 * could not scroll back to, drawn like Listen mode's chips: a field label over
 * its current value. "English" cycles the translation text; "Recall" blurs the
 * words due for production. Collapsing the player keeps only Recall, on the
 * row with Mark as Listened.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";

import ReaderChips from "./ReaderChips.svelte";
import { readerEnglishPref } from "$lib/stores/readerEnglishPref.svelte";
import { readerProductionPref } from "$lib/stores/readerProductionPref.svelte";

beforeEach(() => {
  localStorage.clear();
  readerEnglishPref.set("off");
  readerProductionPref.set(false);
  localStorage.clear();
});

const parts = (chip: HTMLElement) => ({
  label: chip.querySelector(".chip-label")?.textContent?.trim(),
  value: chip.querySelector(".chip-value")?.textContent?.trim(),
});

describe("ReaderChips", () => {
  it("shows English and Recall, both off, neither marked active", () => {
    const { getByTestId } = render(ReaderChips);
    const english = getByTestId("reader-english-chip");
    const recall = getByTestId("reader-recall-chip");
    expect(parts(english)).toEqual({ label: "English", value: "Off" });
    expect(parts(recall)).toEqual({ label: "Recall", value: "Off" });
    expect(english.classList.contains("active")).toBe(false);
    expect(recall.classList.contains("active")).toBe(false);
    expect(recall.getAttribute("aria-pressed")).toBe("false");
  });

  it("English cycles Off, Idiomatic, Literal, Both, Off and drives the preference", async () => {
    const { getByTestId } = render(ReaderChips);
    const english = getByTestId("reader-english-chip");
    const steps: Array<[string, string]> = [
      ["Idiomatic", "idiomatic"],
      ["Literal", "literal"],
      ["Both", "both"],
      ["Off", "off"],
    ];
    for (const [shown, stored] of steps) {
      await fireEvent.click(english);
      expect(parts(english).value).toBe(shown);
      expect(readerEnglishPref.value).toBe(stored);
      // Active marks a non-default value, as it does on Listen's chips.
      expect(english.classList.contains("active")).toBe(stored !== "off");
    }
  });

  it("Recall toggles between Off and Blurred and drives the preference", async () => {
    const { getByTestId } = render(ReaderChips);
    const recall = getByTestId("reader-recall-chip");

    await fireEvent.click(recall);
    expect(parts(recall).value).toBe("Blurred");
    expect(readerProductionPref.enabled).toBe(true);
    expect(recall.getAttribute("aria-pressed")).toBe("true");
    expect(recall.classList.contains("active")).toBe(true);

    await fireEvent.click(recall);
    expect(parts(recall).value).toBe("Off");
    expect(readerProductionPref.enabled).toBe(false);
    expect(recall.getAttribute("aria-pressed")).toBe("false");
  });

  it("opens showing what was already chosen", () => {
    readerEnglishPref.set("both");
    readerProductionPref.set(true);
    const { getByTestId } = render(ReaderChips);
    expect(parts(getByTestId("reader-english-chip")).value).toBe("Both");
    expect(parts(getByTestId("reader-recall-chip")).value).toBe("Blurred");
  });

  it("inline: only Recall, and it still works", async () => {
    const { getByTestId, queryByTestId } = render(ReaderChips, { props: { inline: true } });
    expect(queryByTestId("reader-english-chip")).toBeNull();
    const recall = getByTestId("reader-recall-chip");
    expect(parts(recall)).toEqual({ label: "Recall", value: "Off" });
    await fireEvent.click(recall);
    expect(readerProductionPref.enabled).toBe(true);
    expect(parts(recall).value).toBe("Blurred");
  });
});
