import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  LANGUAGE_STORAGE_KEY: "tt-language",
  api: { getLanguages: vi.fn() },
}));

import { api } from "$lib/api";
import { languageStore } from "$lib/stores/language.svelte";
import LanguageSelector from "$lib/components/LanguageSelector.svelte";

const mockGet = vi.mocked(api.getLanguages);
const TWO = {
  languages: [
    { code: "sl", name: "Slovene" },
    { code: "no", name: "Norwegian" },
  ],
  active: "sl",
};

describe("LanguageSelector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem("tt-language");
  });
  afterEach(() => vi.unstubAllGlobals());

  it("does not render when only one language is configured", async () => {
    mockGet.mockResolvedValue({ languages: [{ code: "sl", name: "Slovene" }], active: "sl" });
    await languageStore.init();
    const { container } = render(LanguageSelector);
    expect(container.querySelector("select")).toBeNull();
  });

  it("renders an option per language and switches + reloads on change", async () => {
    mockGet.mockResolvedValue(TWO);
    await languageStore.init();
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });
    const { getByRole, getAllByRole } = render(LanguageSelector);
    expect(getAllByRole("option")).toHaveLength(2);
    await fireEvent.change(getByRole("combobox"), { target: { value: "no" } });
    expect(languageStore.code).toBe("no");
    expect(localStorage.getItem("tt-language")).toBe("no");
    expect(reload).toHaveBeenCalled();
  });

  it("does nothing when re-selecting the already-active language", async () => {
    mockGet.mockResolvedValue(TWO);
    await languageStore.init(); // active = sl
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });
    const { getByRole } = render(LanguageSelector);
    await fireEvent.change(getByRole("combobox"), { target: { value: "sl" } });
    expect(reload).not.toHaveBeenCalled();
  });
});
