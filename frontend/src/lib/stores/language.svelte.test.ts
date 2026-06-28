import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("$lib/api", () => ({
  LANGUAGE_STORAGE_KEY: "tt-language",
  api: { getLanguages: vi.fn() },
}));

import { api } from "$lib/api";
import { languageStore } from "$lib/stores/language.svelte";

const mockGet = vi.mocked(api.getLanguages);
const TWO = {
  languages: [
    { code: "sl", name: "Slovene" },
    { code: "no", name: "Norwegian" },
  ],
  active: "sl",
};

describe("languageStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem("tt-language");
  });
  afterEach(() => localStorage.removeItem("tt-language"));

  it("init populates options + code from the backend and persists", async () => {
    mockGet.mockResolvedValue(TWO);
    await languageStore.init();
    expect(languageStore.options.map((o) => o.code)).toEqual(["sl", "no"]);
    expect(languageStore.code).toBe("sl");
    expect(localStorage.getItem("tt-language")).toBe("sl");
  });

  it("init honors a stored choice that is still configured", async () => {
    localStorage.setItem("tt-language", "no");
    mockGet.mockResolvedValue(TWO);
    await languageStore.init();
    expect(languageStore.code).toBe("no");
    expect(languageStore.name).toBe("Norwegian");
  });

  it("init ignores a stored choice that is no longer configured (uses backend active)", async () => {
    localStorage.setItem("tt-language", "de");
    mockGet.mockResolvedValue(TWO);
    await languageStore.init();
    expect(languageStore.code).toBe("sl");
  });

  it("init falls back to the stored choice when the backend is unreachable", async () => {
    localStorage.setItem("tt-language", "no");
    mockGet.mockRejectedValue(new Error("down"));
    await languageStore.init();
    expect(languageStore.code).toBe("no");
  });

  it("init falls back to empty when the backend is down and nothing is stored", async () => {
    mockGet.mockRejectedValue(new Error("down"));
    await languageStore.init();
    expect(languageStore.code).toBe("");
  });

  it("set persists the choice and updates code", () => {
    languageStore.set("no");
    expect(languageStore.code).toBe("no");
    expect(localStorage.getItem("tt-language")).toBe("no");
  });

  it("name is empty when the active code matches no option", async () => {
    mockGet.mockResolvedValue({ languages: [], active: "" });
    await languageStore.init();
    expect(languageStore.name).toBe("");
  });
});
