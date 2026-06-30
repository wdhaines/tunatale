/**
 * Tests for /settings — the home for the set-and-forget prefs that used to live
 * in the header: theme, auto-download-on-wifi, and the language switcher.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  LANGUAGE_STORAGE_KEY: "tt-language",
  api: {
    getLanguages: vi.fn().mockResolvedValue({ languages: [], active: "sl" }),
  },
}));

import { api } from "$lib/api";
import { themeStore } from "$lib/stores/theme.svelte";
import { prefetchPrefStore } from "$lib/stores/prefetchPref.svelte";
import { languageStore } from "$lib/stores/language.svelte";
import Settings from "./+page.svelte";

const mockGetLanguages = vi.mocked(api.getLanguages);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  // jsdom lacks matchMedia; themeStore.set() resolves "system" through it.
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(() => ({
    matches: false,
    media: "(prefers-color-scheme: dark)",
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
  themeStore.set("system");
  prefetchPrefStore.set(true);
  mockGetLanguages.mockResolvedValue({ languages: [], active: "sl" });
});

describe("/settings", () => {
  it("renders the three theme options with the current one pressed", () => {
    const { getByRole } = render(Settings);
    expect(getByRole("button", { name: /system/i }).getAttribute("aria-pressed")).toBe("true");
    expect(getByRole("button", { name: /light/i }).getAttribute("aria-pressed")).toBe("false");
    expect(getByRole("button", { name: /dark/i }).getAttribute("aria-pressed")).toBe("false");
  });

  it("selecting a theme updates the store and the pressed state", async () => {
    const { getByRole } = render(Settings);
    await fireEvent.click(getByRole("button", { name: /dark/i }));
    expect(themeStore.pref).toBe("dark");
    expect(getByRole("button", { name: /dark/i }).getAttribute("aria-pressed")).toBe("true");
    expect(getByRole("button", { name: /system/i }).getAttribute("aria-pressed")).toBe("false");
  });

  it("shows the auto-download toggle as On and flips it Off", async () => {
    const { getByRole } = render(Settings);
    const toggle = getByRole("switch");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(toggle.textContent).toContain("On");

    await fireEvent.click(toggle);
    expect(prefetchPrefStore.enabled).toBe(false);
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(toggle.textContent).toContain("Off");
  });

  it("hides the language section for a single-language deployment", () => {
    const { queryByRole } = render(Settings);
    expect(queryByRole("heading", { name: "Language" })).toBeNull();
    expect(queryByRole("combobox", { name: /active language/i })).toBeNull();
  });

  it("shows the language switcher when more than one language is configured", async () => {
    mockGetLanguages.mockResolvedValue({
      languages: [
        { code: "sl", name: "Slovene" },
        { code: "no", name: "Norwegian" },
      ],
      active: "sl",
    });
    await languageStore.init();

    const { getByRole } = render(Settings);
    expect(getByRole("heading", { name: "Language" })).toBeTruthy();
    expect(getByRole("combobox", { name: /active language/i })).toBeTruthy();
  });
});
