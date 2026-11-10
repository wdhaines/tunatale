/** /reference — the Reference tab's index of the active language's pages. */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/svelte";

vi.mock("#lib/api.js", () => ({ api: { getReferencePages: vi.fn() } }));

import { api } from "#lib/api.js";
import { referenceStore } from "#lib/stores/reference.svelte.js";
import Reference from "./+page.svelte";

const mockPages = vi.mocked(api.getReferencePages);

beforeEach(() => vi.clearAllMocks());

describe("/reference", () => {
  it("links each page", async () => {
    mockPages.mockResolvedValue({ pages: [{ id: "verb-chart", title: "The verb chart" }] });
    await referenceStore.refresh();
    const { getByRole } = render(Reference);
    expect(getByRole("link", { name: "The verb chart" }).getAttribute("href")).toBe(
      "/reference/verb-chart",
    );
  });

  it("says so when the language has none", async () => {
    mockPages.mockResolvedValue({ pages: [] });
    await referenceStore.refresh();
    const { getByText } = render(Reference);
    expect(getByText("No reference pages for this language yet.")).toBeTruthy();
  });
});
