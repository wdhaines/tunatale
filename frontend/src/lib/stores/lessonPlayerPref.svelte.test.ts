import { describe, it, expect, beforeEach } from "vitest";
import { lessonPlayerPref, pillsForSection, type PlayerSelection } from "./lessonPlayerPref.svelte";

const STORAGE_KEY = "lessonPlayerSelection";
const DEFAULT: PlayerSelection = { phase: "dialogue", enunciation: "natural", english: "off" };

beforeEach(() => {
  localStorage.clear();
  // Reset the singleton to a known (default) state between tests.
  lessonPlayerPref.init();
  localStorage.clear();
});

describe("lessonPlayerPref", () => {
  it("defaults to Dialogue · Natural · English-off when nothing is stored", () => {
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(DEFAULT);
  });

  it("seeds from a valid stored selection", () => {
    const stored: PlayerSelection = {
      phase: "key_phrases",
      enunciation: "enunciated_0.8",
      english: "en_first",
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(stored);
  });

  it("set() persists to localStorage and updates the live selection", () => {
    const next: PlayerSelection = {
      phase: "dialogue",
      enunciation: "enunciated",
      english: "l2_first",
    };
    lessonPlayerPref.set(next);
    expect(lessonPlayerPref.selection).toEqual(next);
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toEqual(next);
  });

  it("migrates a legacy english:true boolean to l2_first", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ phase: "dialogue", enunciation: "natural", english: true }),
    );
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual({
      phase: "dialogue",
      enunciation: "natural",
      english: "l2_first",
    });
  });

  it("migrates a legacy english:false boolean to off", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ phase: "key_phrases", enunciation: "enunciated", english: false }),
    );
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual({
      phase: "key_phrases",
      enunciation: "enunciated",
      english: "off",
    });
  });

  it("falls back to default on malformed JSON", () => {
    localStorage.setItem(STORAGE_KEY, "{not valid json");
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(DEFAULT);
  });

  it("falls back to default when the stored value is not an object", () => {
    localStorage.setItem(STORAGE_KEY, "5");
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(DEFAULT);
  });

  it("falls back to default when the stored value is null", () => {
    localStorage.setItem(STORAGE_KEY, "null");
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(DEFAULT);
  });

  it("falls back to default on an unknown phase", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ phase: "sideways", enunciation: "natural", english: "off" }),
    );
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(DEFAULT);
  });

  it("falls back to default when enunciation is not a string", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ phase: "dialogue", enunciation: 3, english: "off" }),
    );
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(DEFAULT);
  });

  it("falls back to default on an unknown english value", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ phase: "dialogue", enunciation: "natural", english: "maybe" }),
    );
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(DEFAULT);
  });

  it("re-seeding after storage is cleared restores the default", () => {
    lessonPlayerPref.set({ phase: "key_phrases", enunciation: "enunciated", english: "en_first" });
    localStorage.clear();
    lessonPlayerPref.init();
    expect(lessonPlayerPref.selection).toEqual(DEFAULT);
  });
});

describe("pillsForSection", () => {
  it("maps key_phrases to the Key Phrases phase (leaving enun/english untouched)", () => {
    expect(pillsForSection("key_phrases")).toEqual({ phase: "key_phrases" });
  });

  it("maps natural_speed to Dialogue · Natural · English-off", () => {
    expect(pillsForSection("natural_speed")).toEqual({
      phase: "dialogue",
      enunciation: "natural",
      english: "off",
    });
  });

  it("maps translated to Dialogue · Natural · L2-first English", () => {
    expect(pillsForSection("translated")).toEqual({
      phase: "dialogue",
      enunciation: "natural",
      english: "l2_first",
    });
  });

  it("maps en_translated to Dialogue · Natural · English-first", () => {
    expect(pillsForSection("en_translated")).toEqual({
      phase: "dialogue",
      enunciation: "natural",
      english: "en_first",
    });
  });

  it("maps slow_speed to Dialogue · English-off (keeping the enunciation level)", () => {
    expect(pillsForSection("slow_speed", "enunciated_0.8")).toEqual({
      phase: "dialogue",
      enunciation: "enunciated_0.8",
      english: "off",
    });
  });

  it("maps slow_translated to Dialogue · L2-first English (keeping the enunciation level)", () => {
    expect(pillsForSection("slow_translated", "enunciated_0.9")).toEqual({
      phase: "dialogue",
      enunciation: "enunciated_0.9",
      english: "l2_first",
    });
  });

  describe("a slow section reached WITHOUT the pill (hands-free, transcript tap)", () => {
    // ⚠️ THE BUG THIS CLOSES. The level really is unrecoverable from the section
    // type — slow_speed could be any of the three enunciated rates — so this
    // used to return no `enunciation` at all and leave the pill on whatever it
    // held. That is right when the user cycled the pill to GET here, and wrong
    // for every other route in: hands-free selects slow_speed directly, so the
    // pill stayed on "Natural" while an enunciated track played, unhighlighted.
    it("promotes a natural pill to plain Enunciated for slow_speed", () => {
      expect(pillsForSection("slow_speed", "natural")).toEqual({
        phase: "dialogue",
        enunciation: "enunciated",
        english: "off",
      });
    });

    it("promotes a natural pill to plain Enunciated for slow_translated", () => {
      expect(pillsForSection("slow_translated", "natural")).toEqual({
        phase: "dialogue",
        enunciation: "enunciated",
        english: "l2_first",
      });
    });

    it("promotes a natural pill to plain Enunciated for slow_en_translated", () => {
      expect(pillsForSection("slow_en_translated", "natural")).toEqual({
        phase: "dialogue",
        enunciation: "enunciated",
        english: "en_first",
      });
    });

    it("does NOT overwrite an already-enunciated rate — that one IS recoverable", () => {
      // The original intent survives: someone drilling at 0.8x who lands on a
      // slow section by any route keeps 0.8x.
      for (const level of ["enunciated", "enunciated_0.9", "enunciated_0.8"]) {
        expect(pillsForSection("slow_speed", level).enunciation).toBe(level);
      }
    });

    it("with no current level supplied at all, still names an enunciated one", () => {
      expect(pillsForSection("slow_speed").enunciation).toBe("enunciated");
    });
  });

  it("maps slow_en_translated to Dialogue · English-first (keeping the enunciation level)", () => {
    expect(pillsForSection("slow_en_translated", "enunciated_0.8")).toEqual({
      phase: "dialogue",
      enunciation: "enunciated_0.8",
      english: "en_first",
    });
  });

  it("returns an empty object for a null or unknown section (no forcing)", () => {
    expect(pillsForSection(null)).toEqual({});
    expect(pillsForSection("weird")).toEqual({});
  });
});
