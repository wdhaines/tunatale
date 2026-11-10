import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("#lib/api.js", () => ({ api: { getReferencePages: vi.fn() } }));

import { api } from "#lib/api.js";
import { referenceStore } from "./reference.svelte.js";

const mockPages = vi.mocked(api.getReferencePages);

beforeEach(() => vi.clearAllMocks());

describe("referenceStore", () => {
  it("holds the active language's pages after a refresh", async () => {
    mockPages.mockResolvedValue({ pages: [{ id: "verb-chart", title: "The verb chart" }] });
    await referenceStore.refresh();
    expect(referenceStore.pages).toEqual([{ id: "verb-chart", title: "The verb chart" }]);
  });

  it("empties on a failed refresh, so the tab hides rather than offering stale pages", async () => {
    mockPages.mockResolvedValue({ pages: [{ id: "verb-chart", title: "The verb chart" }] });
    await referenceStore.refresh();
    mockPages.mockRejectedValue(new Error("offline"));
    await referenceStore.refresh();
    expect(referenceStore.pages).toEqual([]);
  });
});
