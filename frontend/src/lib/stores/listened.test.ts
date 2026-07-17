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

const LEGACY_LISTENED_KEY = "tunatale:listened-lessons";
const LEGACY_HOME_KEY = "tunatale:home";

beforeEach(() => {
  localStorage.clear();
  vi.resetModules();
  vi.clearAllMocks();
});

async function freshStore() {
  const mod = await import("./listened.svelte");
  return mod.listenedStore;
}

async function getApiMock() {
  const { api } = await import("$lib/api");
  return vi.mocked(api);
}

describe("listenedStore", () => {
  it("has() returns false when store is empty", async () => {
    const store = await freshStore();
    expect(store.has("lesson-1")).toBe(false);
  });

  it("count() returns 0 when store is empty", async () => {
    const store = await freshStore();
    expect(store.count("lesson-1")).toBe(0);
  });

  describe("hydrate()", () => {
    it("fetches server state and populates the store", async () => {
      const api = await getApiMock();
      api.getListens.mockResolvedValue({
        lessons: [
          { lesson_id: "l1", listen_count: 3, last_listened_at: "2026-01-01T00:00:00Z" },
          { lesson_id: "l2", listen_count: 1, last_listened_at: "2026-01-02T00:00:00Z" },
        ],
      });

      const store = await freshStore();
      await store.hydrate();

      expect(api.getListens).toHaveBeenCalled();
      expect(store.has("l1")).toBe(true);
      expect(store.has("l2")).toBe(true);
      expect(store.count("l1")).toBe(3);
      expect(store.count("l2")).toBe(1);
    });

    it("is idempotent — only calls API once", async () => {
      const api = await getApiMock();
      api.getListens.mockResolvedValue({ lessons: [] });

      const store = await freshStore();
      await store.hydrate();
      await store.hydrate();

      expect(api.getListens).toHaveBeenCalledTimes(1);
    });

    describe("localStorage migration", () => {
      it("calls getLanguages, then importListens for each language with full id list", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["old-l1", "old-l2"]));

        const api = await getApiMock();
        api.getLanguages.mockResolvedValue({
          languages: [
            { code: "sl", name: "Slovene" },
            { code: "no", name: "Norwegian" },
          ],
          active: "sl",
        });
        api.importListens.mockResolvedValue({
          imported: [],
          already_present: [],
          unknown: [],
        });
        api.getListens.mockResolvedValue({ lessons: [] });

        const store = await freshStore();
        await store.hydrate();

        expect(api.importListens).toHaveBeenCalledTimes(2);
        expect(api.importListens).toHaveBeenCalledWith(["old-l1", "old-l2"], "sl");
        expect(api.importListens).toHaveBeenCalledWith(["old-l1", "old-l2"], "no");
      });

      it("migrates from legacy tunatale:home key", async () => {
        localStorage.setItem(LEGACY_HOME_KEY, JSON.stringify({ listenedLessonIds: ["legacy-1"] }));

        const api = await getApiMock();
        api.getLanguages.mockResolvedValue({
          languages: [{ code: "sl", name: "Slovene" }],
          active: "sl",
        });
        api.importListens.mockResolvedValue({
          imported: ["legacy-1"],
          already_present: [],
          unknown: [],
        });
        api.getListens.mockResolvedValue({ lessons: [] });

        const store = await freshStore();
        await store.hydrate();

        expect(api.importListens).toHaveBeenCalledWith(["legacy-1"], "sl");
      });

      it("cleans up localStorage only after ALL language imports succeed", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1"]));
        localStorage.setItem(LEGACY_HOME_KEY, JSON.stringify({ listenedLessonIds: ["l2"] }));

        const api = await getApiMock();
        api.getLanguages.mockResolvedValue({
          languages: [
            { code: "sl", name: "Slovene" },
            { code: "no", name: "Norwegian" },
          ],
          active: "sl",
        });
        api.importListens.mockResolvedValue({
          imported: ["l1"],
          already_present: [],
          unknown: [],
        });
        api.getListens.mockResolvedValue({ lessons: [] });

        const store = await freshStore();
        await store.hydrate();

        expect(localStorage.getItem(LEGACY_LISTENED_KEY)).toBeNull();
        expect(localStorage.getItem(LEGACY_HOME_KEY)).toBeNull();
      });

      it("keeps BOTH localStorage keys when ANY language import fails", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1", "l2"]));
        localStorage.setItem(LEGACY_HOME_KEY, JSON.stringify({ listenedLessonIds: ["l3"] }));

        const api = await getApiMock();
        api.getLanguages.mockResolvedValue({
          languages: [
            { code: "sl", name: "Slovene" },
            { code: "no", name: "Norwegian" },
          ],
          active: "sl",
        });
        // Language "sl" succeeds, language "no" fails
        api.importListens
          .mockResolvedValueOnce({
            imported: ["l1", "l2"],
            already_present: [],
            unknown: [],
          })
          .mockRejectedValueOnce(new Error("Network error"));
        api.getListens.mockResolvedValue({ lessons: [] });

        const store = await freshStore();
        await store.hydrate();

        // Keys survived — next hydrate() will retry
        expect(localStorage.getItem(LEGACY_LISTENED_KEY)).toBe(JSON.stringify(["l1", "l2"]));
        expect(localStorage.getItem(LEGACY_HOME_KEY)).toBe(
          JSON.stringify({ listenedLessonIds: ["l3"] }),
        );
      });

      it("skips migration when localStorage is empty", async () => {
        const api = await getApiMock();
        api.getListens.mockResolvedValue({ lessons: [] });

        const store = await freshStore();
        await store.hydrate();

        expect(api.importListens).not.toHaveBeenCalled();
        expect(api.getLanguages).not.toHaveBeenCalled();
      });

      it("handles localStorage parse errors gracefully", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, "not-valid-json{{{");

        const api = await getApiMock();
        api.getListens.mockResolvedValue({ lessons: [] });

        const store = await freshStore();
        await store.hydrate();

        expect(api.importListens).not.toHaveBeenCalled();
      });

      it("does not run migration if already hydrated", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1"]));

        const api = await getApiMock();
        api.getLanguages.mockResolvedValue({
          languages: [{ code: "sl", name: "Slovene" }],
          active: "sl",
        });
        api.importListens.mockResolvedValue({
          imported: ["l1"],
          already_present: [],
          unknown: [],
        });
        api.getListens.mockResolvedValue({ lessons: [] });

        const store = await freshStore();
        await store.hydrate();
        await store.hydrate();

        expect(api.importListens).toHaveBeenCalledTimes(1);
      });

      it("re-runs migration after refresh() resets hydrated flag", async () => {
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1"]));

        const api = await getApiMock();
        api.getLanguages.mockResolvedValue({
          languages: [{ code: "sl", name: "Slovene" }],
          active: "sl",
        });
        api.importListens.mockResolvedValue({
          imported: ["l1"],
          already_present: [],
          unknown: [],
        });
        api.getListens.mockResolvedValue({ lessons: [] });

        const store = await freshStore();
        await store.hydrate();
        expect(api.getListens).toHaveBeenCalledTimes(1);
        expect(api.importListens).toHaveBeenCalledTimes(1);

        // After first hydrate, keys were cleaned up — re-seed for retry test
        localStorage.setItem(LEGACY_LISTENED_KEY, JSON.stringify(["l1"]));

        // Simulate language switch via refresh()
        await store.refresh();
        expect(api.getListens).toHaveBeenCalledTimes(2);
        expect(api.importListens).toHaveBeenCalledTimes(2);
      });
    });

    it("handles server errors gracefully — keeps empty store", async () => {
      const api = await getApiMock();
      api.getListens.mockRejectedValue(new Error("Network error"));

      const store = await freshStore();
      await store.hydrate();

      expect(store.has("l1")).toBe(false);
      expect(store.count("l1")).toBe(0);
    });
  });

  describe("markListened()", () => {
    it("returns full ListenResponse and updates state", async () => {
      const api = await getApiMock();
      const response = {
        status: "ok",
        registered: 3,
        created: 1,
        graded: 2,
        remaining_candidates: 5,
        listen_count: 4,
      };
      api.markAsListened.mockResolvedValue(response);

      const store = await freshStore();
      const result = await store.markListened("l1");

      expect(api.markAsListened).toHaveBeenCalledWith("l1", {});
      expect(result).toEqual(response);
      expect(store.has("l1")).toBe(true);
      expect(store.count("l1")).toBe(4);
    });

    it("passes word_ratings to API", async () => {
      const api = await getApiMock();
      api.markAsListened.mockResolvedValue({
        status: "ok",
        registered: 1,
        created: 0,
        graded: 1,
        remaining_candidates: 0,
        listen_count: 1,
      });

      const store = await freshStore();
      await store.markListened("l1", { banka: "hard" });

      expect(api.markAsListened).toHaveBeenCalledWith("l1", { banka: "hard" });
    });

    it("does not update state on API error", async () => {
      const api = await getApiMock();
      api.markAsListened.mockRejectedValue(new Error("Server error"));

      const store = await freshStore();
      await expect(store.markListened("l1")).rejects.toThrow("Server error");
      expect(store.has("l1")).toBe(false);
    });
  });

  describe("language switch", () => {
    it("refresh() refetches getListens and clears old entries", async () => {
      const api = await getApiMock();
      api.getListens
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

      const store = await freshStore();
      await store.hydrate();

      expect(store.has("sl-1")).toBe(true);
      expect(store.has("sl-2")).toBe(true);
      expect(store.has("no-1")).toBe(false);

      // Simulate language switch — the layout's $effect calls refresh()
      await store.refresh();

      expect(api.getListens).toHaveBeenCalledTimes(2);
      expect(store.has("sl-1")).toBe(false);
      expect(store.has("sl-2")).toBe(false);
      expect(store.has("no-1")).toBe(true);
      expect(store.count("no-1")).toBe(3);
    });
  });

  it("hydrate() populates has() and count() from server", async () => {
    const api = await getApiMock();
    api.getListens.mockResolvedValue({
      lessons: [{ lesson_id: "l1", listen_count: 1, last_listened_at: "2026-01-01T00:00:00Z" }],
    });

    const store = await freshStore();
    await store.hydrate();

    expect(store.has("l1")).toBe(true);
    expect(store.count("l1")).toBe(1);
  });
});
