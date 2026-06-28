/**
 * Tests for root +layout.svelte — global nav (Review/Lessons/Cards), the
 * review-count badge, the Sync button, and stats refetch on focus / after sync.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import { createRawSnippet } from "svelte";

// Mutable pathname so individual tests can assert active-link states.
const nav = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("$app/stores", () => ({
  page: {
    subscribe: vi.fn((cb) => {
      cb({ url: { pathname: nav.pathname } });
      return () => {};
    }),
  },
}));

vi.mock("$lib/api", () => ({
  LANGUAGE_STORAGE_KEY: "tt-language",
  api: {
    peerSync: vi.fn(),
    fetchQueueStats: vi.fn(),
    getLanguages: vi.fn().mockResolvedValue({ languages: [], active: "sl" }),
  },
}));

import { api } from "$lib/api";
import { syncStore } from "$lib/stores/sync.svelte";
import { queueStatsStore } from "$lib/stores/queueStats.svelte";
import { themeStore } from "$lib/stores/theme.svelte";
import Layout from "./+layout.svelte";

const mockPeerSync = vi.mocked(api.peerSync);
const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);

const RESULT = {
  auth_success: true,
  pull_required: 0,
  push_required: 1,
  tt_push_pull_exit: 0,
  dry_run: false,
};

function renderLayout() {
  const children = createRawSnippet(() => ({
    render: () => `<div data-testid="slot">page content</div>`,
  }));
  return render(Layout, { props: { children } });
}

beforeEach(() => {
  vi.clearAllMocks();
  nav.pathname = "/";
  syncStore.notify(null);
  queueStatsStore.set(null); // the badge reads a shared singleton — reset per test
  // jsdom lacks matchMedia; the layout's theme init() needs it.
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(() => ({
    matches: false,
    media: "(prefers-color-scheme: dark)",
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
  localStorage.clear();
  themeStore.set("system");
  mockFetchQueueStats.mockResolvedValue({
    new: 0,
    learning: 0,
    review: 0,
    daily_new_cap: 20,
    cap_source: "default",
    fsrs_source: "default",
  });
});

describe("root +layout.svelte", () => {
  it("renders the brand and the three nav links with correct hrefs", () => {
    const { getByRole } = renderLayout();
    expect(
      (getByRole("link", { name: "TunaTale" }) as HTMLAnchorElement).getAttribute("href"),
    ).toBe("/");
    expect((getByRole("link", { name: "Review" }) as HTMLAnchorElement).getAttribute("href")).toBe(
      "/review",
    );
    expect((getByRole("link", { name: "Lessons" }) as HTMLAnchorElement).getAttribute("href")).toBe(
      "/",
    );
    expect((getByRole("link", { name: "Cards" }) as HTMLAnchorElement).getAttribute("href")).toBe(
      "/cards",
    );
  });

  it("renders the logo mark inside the brand link", () => {
    const { getByRole } = renderLayout();
    const brand = getByRole("link", { name: "TunaTale" });
    const mark = brand.querySelector("img.brand-mark") as HTMLImageElement;
    expect(mark).toBeTruthy();
    // Decorative: the brand's accessible name stays the wordmark text.
    expect(mark.getAttribute("alt")).toBe("");
  });

  it("renders the Sync to AnkiWeb button in the global nav", () => {
    const { getByText } = renderLayout();
    expect(getByText("Sync to AnkiWeb")).toBeTruthy();
  });

  it("sync button calls peerSync on click", async () => {
    mockPeerSync.mockResolvedValue(RESULT);
    const { getByText } = renderLayout();
    await fireEvent.click(getByText("Sync to AnkiWeb"));
    await waitFor(() => expect(mockPeerSync).toHaveBeenCalledWith(false));
  });

  it("renders page content through children snippet", () => {
    const { getByTestId } = renderLayout();
    expect(getByTestId("slot").textContent).toBe("page content");
  });

  it("theme toggle cycles System → Light → Dark", async () => {
    const { getByRole } = renderLayout();
    const btn = getByRole("button", { name: /theme:/i });
    expect(btn.getAttribute("aria-label")?.toLowerCase()).toContain("system");
    await fireEvent.click(btn);
    expect(btn.getAttribute("aria-label")?.toLowerCase()).toContain("light");
    await fireEvent.click(btn);
    expect(btn.getAttribute("aria-label")?.toLowerCase()).toContain("dark");
  });

  // ── active-link states (cover the path-derived booleans) ──────────────────

  it("marks Lessons active on the home path", () => {
    nav.pathname = "/";
    const { getByRole } = renderLayout();
    expect(getByRole("link", { name: "Lessons" }).className).toContain("active");
  });

  it("marks Lessons active on a curriculum path", () => {
    nav.pathname = "/c/abc";
    const { getByRole } = renderLayout();
    expect(getByRole("link", { name: "Lessons" }).className).toContain("active");
  });

  it("marks Review active on the review path", () => {
    nav.pathname = "/review";
    const { getByRole } = renderLayout();
    expect(getByRole("link", { name: "Review" }).className).toContain("active");
  });

  it("marks Cards active on the cards path", () => {
    nav.pathname = "/cards";
    const { getByRole } = renderLayout();
    expect(getByRole("link", { name: "Cards" }).className).toContain("active");
  });

  // ── review-count badge ────────────────────────────────────────────────────

  it("shows the review-count badge after stats load", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 5,
      learning: 2,
      review: 3,
      daily_new_cap: 20,
      cap_source: "cache",
      fsrs_source: "cache",
    });
    const { findByText, container } = renderLayout();
    expect(await findByText("5")).toBeTruthy();
    expect(await findByText("2")).toBeTruthy();
    expect(await findByText("3")).toBeTruthy();
    expect(container.querySelector(".review-badge")).not.toBeNull();
  });

  it("badge updates live when the shared store changes (e.g. a grade on /review)", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 5,
      learning: 2,
      review: 3,
      daily_new_cap: 20,
      cap_source: "cache",
      fsrs_source: "cache",
    });
    const { findByText } = renderLayout();
    await findByText("5");

    // The /review page writes the shared store on every grade; the nav must
    // reflect it without waiting for a focus event.
    queueStatsStore.set({
      new: 4,
      learning: 2,
      review: 3,
      daily_new_cap: 20,
      cap_source: "cache",
      fsrs_source: "cache",
    });

    expect(await findByText("4")).toBeTruthy();
  });

  it("renders no badge when fetchQueueStats rejects", async () => {
    mockFetchQueueStats.mockRejectedValue(new Error("offline"));
    const { container } = renderLayout();
    // Let the rejected fetch settle.
    await waitFor(() => expect(mockFetchQueueStats).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(container.querySelector(".review-badge")).toBeNull();
  });

  it("refetches stats on window focus", async () => {
    mockFetchQueueStats
      .mockResolvedValueOnce({
        new: 5,
        learning: 2,
        review: 3,
        daily_new_cap: 20,
        cap_source: "cache",
        fsrs_source: "cache",
      })
      .mockResolvedValueOnce({
        new: 9,
        learning: 1,
        review: 4,
        daily_new_cap: 20,
        cap_source: "cache",
        fsrs_source: "cache",
      });
    const { findByText } = renderLayout();
    await findByText("5");

    window.dispatchEvent(new Event("focus"));

    expect(await findByText("9")).toBeTruthy();
  });

  it("refetches stats after a successful peer sync", async () => {
    const { container } = renderLayout();
    await waitFor(() => expect(container.querySelector(".review-badge")).not.toBeNull());
    const callsBefore = mockFetchQueueStats.mock.calls.length;

    syncStore.notify(RESULT);

    await waitFor(() => {
      expect(mockFetchQueueStats.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });

  it("stops refetching on focus after unmount (cleanup)", async () => {
    const { container, unmount } = renderLayout();
    await waitFor(() => expect(container.querySelector(".review-badge")).not.toBeNull());
    const callsBefore = mockFetchQueueStats.mock.calls.length;

    unmount();
    window.dispatchEvent(new Event("focus"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockFetchQueueStats.mock.calls.length).toBe(callsBefore);
  });
});
