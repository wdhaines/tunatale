/**
 * Tests for the listenedStore (server-backed listened lesson tracking).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("$lib/api", () => ({
  api: {
    getListens: vi.fn(),
    markAsListened: vi.fn(),
    importListens: vi.fn(),
    getLanguages: vi.fn(),
  },
}));

import { api } from "$lib/api";
import { listenedStore } from "./listened.svelte";

const mockApi = vi.mocked(api);

const LEGACY_LISTENED_KEY = "tunatale:listened-lessons";
const LEGACY_HOME_KEY = "tunatale:home";

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  listenedStore.reset();
});

describe("listenedStore", () => {
  it("has() returns false when store is empty", () => {
    expect(listenedStore.has("lesson-1")).toBe(false);
  });

  it("count() returns 0 when store is empty", () => {
    expect(listenedStore.count("lesson-1")).toBe(0);
  });

  describe("hydrate()", () => {
    it("fetches server state and populates the store", async () => {
      mockApi.getListens.mockResolvedValue({
        lessons: [
          { lesson_id: "l1", listen_count: 3, last_listened_at: "2026-01-01T00:00:00Z" },
          { lesson_id: "l2", listen_count: 1, last_listened_at: "2026-01-02T00:00:00Z" },
        ],
      });

      await listenedStore.hydrate();

      expect(mockApi.getListens).toHaveBeenCalled();
      expect(listenedStore.has("l1")).toBe(true);
      expect(listenedStore.has("l2")).toBe(true);
      expect(listenedStore.count("l1")).toBe(3);
      expect(listenedStore.count("l2")).toBe(1);
    });

    it("is idempotent — only calls API once", async () => {
      mockApi.getListens.mockResolvedValue({ lessons: [] });

      await listenedStore.hydrate();
      await listenedStore.hydrate();

      expect(mockApi.getListens).toHaveBeenCalledTimes(1);
    });

    describe("localStorage migration", () => {
      it("calls getLanguages, then importListens for each language with full id list", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["old-l1", "old-l2"]));

        mockApi.getLanguages.mockResolvedValue({
          languages: [
            { code: "sl", name: "Slovene" },
            { code: "no", name: "Norwegian" },
          ],
          active: "sl",
        });
        mockApi.importListens.mockResolvedValue({
          imported: [],
          already_present: [],
          unknown: [],
        });
        mockApi.getListens.mockResolvedValue({ lessons: [] });

        await listenedStore.hydrate();

        expect(mockApi.importListens).toHaveBeenCalledTimes(2);
        expect(mockApi.importListens).toHaveBeenCalledWith(["old-l1", "old-l2"], "sl");
        expect(mockApi.importListens).toHaveBeenCalledWith(["old-l1", "old-l2"], "no");
      });

      it("migrates from legacy tunatale:home key", async () => {
        localStorage.setItem(LEGACY_HOME_KEY, JSON.stringify({ listenedLessonIds: ["legacy-1"] }));

        mockApi.getLanguages.mockResolvedValue({
          languages: [{ code: "sl", name: "Slovene" }],
          active: "sl",
        });
        mockApi.importListens.mockResolvedValue({
          imported: ["legacy-1"],
          already_present: [],
          unknown: [],
        });
        mockApi.getListens.mockResolvedValue({ lessons: [] });

        await listenedStore.hydrate();

        expect(mockApi.importListens).toHaveBeenCalledWith(["legacy-1"], "sl");
      });

      it("cleans up localStorage only after ALL language imports succeed", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1"]));
        localStorage.setItem(LEGACY_HOME_KEY, JSON.stringify({ listenedLessonIds: ["l2"] }));

        mockApi.getLanguages.mockResolvedValue({
          languages: [
            { code: "sl", name: "Slovene" },
            { code: "no", name: "Norwegian" },
          ],
          active: "sl",
        });
        mockApi.importListens.mockResolvedValue({
          imported: ["l1"],
          already_present: [],
          unknown: [],
        });
        mockApi.getListens.mockResolvedValue({ lessons: [] });

        await listenedStore.hydrate();

        expect(localStorage.getItem(LEGACY_LISTENED_KEY)).toBeNull();
        expect(localStorage.getItem(LEGACY_HOME_KEY)).toBeNull();
      });

      it("keeps BOTH localStorage keys when ANY language import fails", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1", "l2"]));
        localStorage.setItem(LEGACY_HOME_KEY, JSON.stringify({ listenedLessonIds: ["l3"] }));

        mockApi.getLanguages.mockResolvedValue({
          languages: [
            { code: "sl", name: "Slovene" },
            { code: "no", name: "Norwegian" },
          ],
          active: "sl",
        });
        // Language "sl" succeeds, language "no" fails
        mockApi.importListens
          .mockResolvedValueOnce({
            imported: ["l1", "l2"],
            already_present: [],
            unknown: [],
          })
          .mockRejectedValueOnce(new Error("Network error"));
        mockApi.getListens.mockResolvedValue({ lessons: [] });

        await listenedStore.hydrate();

        // Keys survived — next hydrate() will retry
        expect(localStorage.getItem(LEGACY_LISTENED_KEY)).toBe(JSON.stringify(["l1", "l2"]));
        expect(localStorage.getItem(LEGACY_HOME_KEY)).toBe(
          JSON.stringify({ listenedLessonIds: ["l3"] }),
        );
      });

      it("skips migration when localStorage is empty", async () => {
        mockApi.getListens.mockResolvedValue({ lessons: [] });

        await listenedStore.hydrate();

        expect(mockApi.importListens).not.toHaveBeenCalled();
        expect(mockApi.getLanguages).not.toHaveBeenCalled();
      });

      it("handles localStorage parse errors gracefully", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, "not-valid-json{{{");

        mockApi.getListens.mockResolvedValue({ lessons: [] });

        await listenedStore.hydrate();

        expect(mockApi.importListens).not.toHaveBeenCalled();
      });

      it("does not run migration if already hydrated", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1"]));

        mockApi.getLanguages.mockResolvedValue({
          languages: [{ code: "sl", name: "Slovene" }],
          active: "sl",
        });
        mockApi.importListens.mockResolvedValue({
          imported: ["l1"],
          already_present: [],
          unknown: [],
        });
        mockApi.getListens.mockResolvedValue({ lessons: [] });

        await listenedStore.hydrate();
        await listenedStore.hydrate();

        expect(mockApi.importListens).toHaveBeenCalledTimes(1);
      });

      it("re-runs migration after refresh() resets hydrated flag", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1"]));

        mockApi.getLanguages.mockResolvedValue({
          languages: [{ code: "sl", name: "Slovene" }],
          active: "sl",
        });
        mockApi.importListens.mockResolvedValue({
          imported: ["l1"],
          already_present: [],
          unknown: [],
        });
        mockApi.getListens.mockResolvedValue({ lessons: [] });

        await listenedStore.hydrate();
        expect(mockApi.getListens).toHaveBeenCalledTimes(1);
        expect(mockApi.importListens).toHaveBeenCalledTimes(1);

        // After first hydrate, keys were cleaned up — re-seed for retry test
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1"]));

        // Simulate language switch via refresh()
        await listenedStore.refresh();
        expect(mockApi.getListens).toHaveBeenCalledTimes(2);
        expect(mockApi.importListens).toHaveBeenCalledTimes(2);
      });
    });

    it("handles server errors gracefully — keeps empty store", async () => {
      mockApi.getListens.mockRejectedValue(new Error("Network error"));

      await listenedStore.hydrate();

      expect(listenedStore.has("l1")).toBe(false);
      expect(listenedStore.count("l1")).toBe(0);
    });
  });

  describe("markListened()", () => {
    it("returns full ListenResponse and updates state", async () => {
      const response = {
        status: "ok",
        registered: 3,
        created: 1,
        graded: 2,
        remaining_candidates: 5,
        listen_count: 4,
      };
      mockApi.markAsListened.mockResolvedValue(response);

      const result = await listenedStore.markListened("l1");

      expect(mockApi.markAsListened).toHaveBeenCalledWith("l1", {});
      expect(result).toEqual(response);
      expect(listenedStore.has("l1")).toBe(true);
      expect(listenedStore.count("l1")).toBe(4);
    });

    it("passes word_ratings to API", async () => {
      mockApi.markAsListened.mockResolvedValue({
        status: "ok",
        registered: 1,
        created: 0,
        graded: 1,
        remaining_candidates: 0,
        listen_count: 1,
      });

      await listenedStore.markListened("l1", { banka: "hard" });

      expect(mockApi.markAsListened).toHaveBeenCalledWith("l1", { banka: "hard" });
    });

    it("does not update state on API error", async () => {
      mockApi.markAsListened.mockRejectedValue(new Error("Server error"));

      await expect(listenedStore.markListened("l1")).rejects.toThrow("Server error");
      expect(listenedStore.has("l1")).toBe(false);
    });
  });

  describe("language switch", () => {
    it("refresh() refetches getListens and clears old entries", async () => {
      mockApi.getListens
        .mockResolvedValueOnce({
          lessons: [
            { lesson_id: "sl-1", listen_count: 2, last_listened_at: "2026-01-01T00:00:00Z" },
            { lesson_id: "sl-2", listen_count: 1, last_listened_at: "2026-01-02T00:00:00Z" },
          ],
        })
        .mockResolvedValueOnce({
          lessons: [
            { lesson_id: "no-1", listen_count: 3, last_listened_at: "2026-02-01T00:00:00Z" },
          ],
        });

      await listenedStore.hydrate();

      expect(listenedStore.has("sl-1")).toBe(true);
      expect(listenedStore.has("sl-2")).toBe(true);
      expect(listenedStore.has("no-1")).toBe(false);

      // Simulate language switch — the layout's $effect calls refresh()
      await listenedStore.refresh();

      expect(mockApi.getListens).toHaveBeenCalledTimes(2);
      expect(listenedStore.has("sl-1")).toBe(false);
      expect(listenedStore.has("sl-2")).toBe(false);
      expect(listenedStore.has("no-1")).toBe(true);
      expect(listenedStore.count("no-1")).toBe(3);
    });
  });

  it("hydrate() populates has() and count() from server", async () => {
    mockApi.getListens.mockResolvedValue({
      lessons: [{ lesson_id: "l1", listen_count: 1, last_listened_at: "2026-01-01T00:00:00Z" }],
    });

    await listenedStore.hydrate();

    expect(listenedStore.has("l1")).toBe(true);
    expect(listenedStore.count("l1")).toBe(1);
  });

  describe("reset()", () => {
    it("clears entries and the hydration latch so a subsequent hydrate() refetches", async () => {
      mockApi.getListens.mockResolvedValue({
        lessons: [{ lesson_id: "l1", listen_count: 2, last_listened_at: "2026-01-01T00:00:00Z" }],
      });

      await listenedStore.hydrate();
      expect(listenedStore.has("l1")).toBe(true);
      expect(mockApi.getListens).toHaveBeenCalledTimes(1);

      listenedStore.reset();

      expect(listenedStore.has("l1")).toBe(false);
      expect(listenedStore.count("l1")).toBe(0);

      // The hydration latch must really be cleared — a subsequent hydrate()
      // refetches from the server instead of being a no-op.
      await listenedStore.hydrate();
      expect(mockApi.getListens).toHaveBeenCalledTimes(2);
    });
  });
});
