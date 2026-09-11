import { afterEach, describe, expect, it } from "vitest";
import { en } from "./en";
import type { MessageKey, PluralMessage } from "./i18n.svelte";
import { getLocale, registerCatalog, setLocale, t } from "./i18n.svelte";

const CANCEL = "transcript.cancel" as MessageKey;
const HOLDS_DAYS = "masteryBands.holdsDays" as MessageKey;
const HOLDS_MONTHS = "masteryBands.holdsMonths" as MessageKey;

const slCatalog = {
  "cards.due": {
    one: "{count} karta zapade",
    two: "{count} kartata zapadeta",
    few: "{count} karte zapadejo",
    other: "{count} kart zapade",
  },
  "cards.greeting": "Pozdravljen {name}!",
} as Partial<Record<MessageKey, PluralMessage | string>>;

afterEach(() => setLocale("en"));

describe("t", () => {
  it("resolves a plain key against the English catalog", () => {
    expect(t(CANCEL)).toBe("Cancel");
    expect(en[CANCEL]).toBe("Cancel");
  });

  it("returns the key itself when the key is unknown", () => {
    expect(t("nope.missing" as MessageKey)).toBe("nope.missing");
  });

  it("returns the key for Object.prototype-adjacent keys", () => {
    for (const key of ["constructor", "__proto__", "toString", "hasOwnProperty"]) {
      expect(t(key as MessageKey)).toBe(key);
    }
  });

  it("selects the English plural form by count", () => {
    expect(t(HOLDS_DAYS, { count: 1 })).toBe("holds ~1 day");
    expect(t(HOLDS_DAYS, { count: 0 })).toBe("holds ~0 days");
    expect(t(HOLDS_DAYS, { count: 2 })).toBe("holds ~2 days");
  });

  it("falls back to the other form when the category has no form, preserving today's ungrammatical English", () => {
    expect(t(HOLDS_MONTHS, { count: 1 })).toBe("holds ~1 months");
  });

  it("uses the other form when count is missing from params entirely", () => {
    expect(t(HOLDS_DAYS)).toBe("holds ~{count} days");
  });

  it("getLocale reflects the current locale, including after setLocale", () => {
    expect(getLocale()).toBe("en");
    setLocale("sl");
    expect(getLocale()).toBe("sl");
  });

  it("selects Slovene plural forms after registerCatalog + setLocale, and falls back to en for missing keys", () => {
    registerCatalog("sl", slCatalog);
    setLocale("sl");
    expect(t("cards.due" as MessageKey, { count: 1 })).toBe("1 karta zapade");
    expect(t("cards.due" as MessageKey, { count: 2 })).toBe("2 kartata zapadeta");
    expect(t("cards.due" as MessageKey, { count: 3 })).toBe("3 karte zapadejo");
    expect(t("cards.due" as MessageKey, { count: 5 })).toBe("5 kart zapade");
    expect(t(CANCEL)).toBe("Cancel");
  });

  it("interpolates named params into the message", () => {
    registerCatalog("sl", slCatalog);
    setLocale("sl");
    expect(t("cards.greeting" as MessageKey, { name: "Ana" })).toBe("Pozdravljen Ana!");
  });

  it("leaves a placeholder verbatim when its param is missing", () => {
    registerCatalog("sl", slCatalog);
    setLocale("sl");
    expect(t("cards.greeting" as MessageKey)).toBe("Pozdravljen {name}!");
  });

  it("leaves a placeholder verbatim when params is given but lacks that key", () => {
    registerCatalog("sl", slCatalog);
    setLocale("sl");
    expect(t("cards.greeting" as MessageKey, { other: "x" })).toBe("Pozdravljen {name}!");
  });
});
