/**
 * /cards shows a learning card's due date as its STUDY day (tunatale-ss5q.3).
 *
 * formatDue rendered every due_at as a UTC date. Right by construction for a
 * review card (due_at is 04:00Z of the due day); wrong for a learning card in
 * the evening west of UTC: due 02:30Z on 09-26 is 22:30 on 09-25 locally,
 * study day 09-25, and it showed "Sep 26".
 */
process.env.TZ = "America/New_York";

import { render } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CardsPage from "./+page.svelte";
import { makeSRSItemDetail } from "../../test/factories";

vi.mock("$lib/api", () => ({
  api: {
    listSRSItems: vi.fn(),
    fetchQueueStats: vi.fn(),
    fetchImageCandidates: vi.fn(),
  },
}));

import { api } from "$lib/api";
const mockList = vi.mocked(api.listSRSItems);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fetchQueueStats).mockResolvedValue({
    new: 0,
    learning: 0,
    review: 0,
    daily_new_cap: 20,
    cap_source: "default",
    fsrs_source: "default",
    daily_review_cap: 100,
    review_cap_source: "default",
  });
  vi.mocked(api.fetchImageCandidates).mockResolvedValue({
    query: "",
    status: "ok",
    candidates: [],
  });
});

describe("cards due date is the study day", () => {
  it("a learning card due late evening local shows that evening's date", async () => {
    mockList.mockResolvedValue({
      items: [
        makeSRSItemDetail({
          id: 1,
          text: "Bog",
          state: "learning",
          due_at: "2026-09-26T02:30:00+00:00",
        }),
      ],
      total: 1,
    });
    const { findByText, queryByText } = render(CardsPage);
    await findByText("Bog");
    expect(await findByText(/Sep\s*25,?\s*2026/)).toBeTruthy();
    expect(queryByText(/Sep\s*26,?\s*2026/)).toBeNull();
  });

  it("a review card keeps its UTC due date (04:00Z convention)", async () => {
    mockList.mockResolvedValue({
      items: [
        makeSRSItemDetail({
          id: 1,
          text: "Bog",
          state: "review",
          due_at: "2026-09-26T04:00:00+00:00",
        }),
      ],
      total: 1,
    });
    const { findByText } = render(CardsPage);
    await findByText("Bog");
    expect(await findByText(/Sep\s*26,?\s*2026/)).toBeTruthy();
  });
});
