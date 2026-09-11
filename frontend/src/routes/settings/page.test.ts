/**
 * Tests for /settings — the home for the set-and-forget prefs that used to live
 * in the header: theme, auto-download-on-wifi, and the language switcher.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  LANGUAGE_STORAGE_KEY: "tt-language",
  api: {
    getLanguages: vi.fn().mockResolvedValue({ languages: [], active: "sl" }),
    getAuthStatus: vi.fn(),
    getMe: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
  },
  setUnauthorizedHandler: vi.fn(),
}));

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

import { api } from "$lib/api";
import { themeStore } from "$lib/stores/theme.svelte";
import { prefetchPrefStore } from "$lib/stores/prefetchPref.svelte";
import { listenCountdownPref } from "$lib/stores/listenCountdownPref.svelte";
import { languageStore } from "$lib/stores/language.svelte";
import { authStore } from "$lib/stores/auth.svelte";
import Settings from "./+page.svelte";

const mockGetLanguages = vi.mocked(api.getLanguages);
const mockGetAuthStatus = vi.mocked(api.getAuthStatus);
const mockGetMe = vi.mocked(api.getMe);

async function signIn(): Promise<void> {
  mockGetAuthStatus.mockResolvedValue({ auth_enabled: true });
  mockGetMe.mockResolvedValue({ email: "a@b.c" });
  await authStore.init();
}

beforeEach(async () => {
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
  // Reset the auth singleton to the dev-box shape — gate off, nobody signed in.
  // `getMe` is deliberately left unstubbed here: with the gate off the store
  // never calls it, and stubbing it would hide a regression that did.
  mockGetAuthStatus.mockResolvedValue({ auth_enabled: false });
  await authStore.init();
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

  it("selecting a countdown option updates the store and pressed state", async () => {
    listenCountdownPref.set("off");
    const { getByRole } = render(Settings);
    expect(getByRole("button", { name: "Off" }).getAttribute("aria-pressed")).toBe("true");
    expect(getByRole("button", { name: "30s" }).getAttribute("aria-pressed")).toBe("false");

    await fireEvent.click(getByRole("button", { name: "30s" }));
    expect(listenCountdownPref.value).toBe("30");
    expect(getByRole("button", { name: "30s" }).getAttribute("aria-pressed")).toBe("true");
    expect(getByRole("button", { name: "Off" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("renders no Account section when the deployment has no login", () => {
    const { queryByRole } = render(Settings);
    expect(queryByRole("heading", { name: /account/i })).toBeNull();
    expect(queryByRole("button", { name: /sign out/i })).toBeNull();
  });

  it("names the signed-in user and offers a way out", async () => {
    await signIn();
    const { getByRole } = render(Settings);
    expect(getByRole("heading", { name: /account/i })).toBeTruthy();
    expect(getByRole("button", { name: /sign out/i })).toBeTruthy();
    const section = getByRole("heading", { name: /account/i }).closest("section")!;
    expect(section.textContent).toContain("a@b.c");
  });

  it("signing out calls the backend and lands on the login page", async () => {
    await signIn();
    vi.mocked(api.logout).mockResolvedValue({ status: "logged out" });
    const { getByRole } = render(Settings);
    await fireEvent.click(getByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(api.logout).toHaveBeenCalled());
    expect(mockGoto).toHaveBeenCalledWith("/login");
  });

  describe("Media button log", () => {
    function seedLog(): void {
      localStorage.setItem("mediaTrace", "on");
      localStorage.setItem("mediaTraceLog", JSON.stringify(["first", "second"]));
    }

    it("is hidden when the trace is off", () => {
      const { queryByRole } = render(Settings);
      expect(queryByRole("heading", { name: "Media button log" })).toBeNull();
    });

    it("shows the count and the entries newest first when on", () => {
      seedLog();
      const { getByRole, container } = render(Settings);
      const heading = getByRole("heading", { name: "Media button log" });
      expect(heading).toBeTruthy();
      const section = heading.closest("section")!;
      expect(section.textContent).toContain("2 entries");
      expect(container.querySelector(".media-trace")?.textContent).toBe("second\nfirst");
    });

    it("Clear empties the list, the count and the buffer", async () => {
      seedLog();
      const { getByRole, container } = render(Settings);
      await fireEvent.click(getByRole("button", { name: "Clear" }));
      expect(container.querySelector(".media-trace")?.textContent).toBe("");
      expect(localStorage.getItem("mediaTraceLog")).toBeNull();
      const section = getByRole("heading", { name: "Media button log" }).closest("section")!;
      expect(section.textContent).toContain("0 entries");
    });

    it("Copy writes the oldest-first joined entries to the clipboard", async () => {
      seedLog();
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText },
        configurable: true,
      });
      const { getByRole } = render(Settings);
      await fireEvent.click(getByRole("button", { name: "Copy" }));
      expect(writeText).toHaveBeenCalledWith("first\nsecond");
    });

    it("Copy with the clipboard missing does not throw", async () => {
      seedLog();
      Object.defineProperty(navigator, "clipboard", {
        value: undefined,
        configurable: true,
      });
      const { getByRole } = render(Settings);
      const copyButton = getByRole("button", { name: "Copy" });
      await fireEvent.click(copyButton);
    });

    it("Copy swallows a rejected clipboard write", async () => {
      seedLog();
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
        configurable: true,
      });
      const { getByRole } = render(Settings);
      const copyButton = getByRole("button", { name: "Copy" });
      await fireEvent.click(copyButton);
    });

    it("Turn off hides the section and writes the off key", async () => {
      seedLog();
      const { getByRole, queryByRole } = render(Settings);
      await fireEvent.click(getByRole("button", { name: "Turn off" }));
      expect(queryByRole("heading", { name: "Media button log" })).toBeNull();
      expect(localStorage.getItem("mediaTrace")).toBe("off");
    });
  });
});
