/**
 * The dated review-session list on the Lessons index (bd tunatale-kgfb).
 *
 * ⚠️ PLACEMENT IS THE FEATURE AND IT HAS BEEN GOT WRONG ONCE ALREADY. The first
 * attempt put "Review story" on the LESSON page beside Regenerate — a path whose
 * whole job is REPLACING a lesson — when a review session is ADDITIVE. The user:
 * "it shouldn't replace a story that exists!" It was reverted in e17a6ba, and
 * the session now lives here, on the index, as its own dated list.
 *
 * DATED, NOT NUMBERED: a session has no position in a sequence, and numbering
 * one would re-import the day semantics the whole epic exists to shed.
 *
 * The 409 is a FIRST-CLASS STATE, not an error. With nothing due there is
 * genuinely nothing to review today, and that is a normal Tuesday — so it reads
 * as a message, not a failure.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import Page from "./+page.svelte";

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", () => ({
  api: {
    listCurricula: vi.fn(),
    startPlan: vi.fn(),
    getCurriculumProgress: vi.fn(),
    deleteCurriculum: vi.fn(),
    listReviewSessions: vi.fn(),
    createReviewSession: vi.fn(),
  },
}));

vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: { has: vi.fn().mockReturnValue(false) },
}));

import { api } from "$lib/api";
const mockListCurricula = vi.mocked(api.listCurricula);
const mockGetCurriculumProgress = vi.mocked(api.getCurriculumProgress);
const mockListSessions = vi.mocked(api.listReviewSessions);
const mockCreateSession = vi.mocked(api.createReviewSession);

function session(overrides: Record<string, unknown> = {}) {
  return {
    id: "sess-1",
    language_code: "no",
    session_date: "2026-09-02",
    title: "A Missed Train",
    review_requested: ["oppføre", "dessuten"],
    review_used: ["oppføre"],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListCurricula.mockResolvedValue([]);
  mockGetCurriculumProgress.mockResolvedValue([]);
  mockListSessions.mockResolvedValue([]);
});

describe("the review-session list", () => {
  it("is on the index, and never on a lesson page", async () => {
    mockListSessions.mockResolvedValue([session()]);
    const { findByRole } = render(Page);

    expect(await findByRole("heading", { name: /review sessions/i })).toBeTruthy();
  });

  it("names each session by DATE, never by a day number", async () => {
    mockListSessions.mockResolvedValue([session()]);
    const { findByText, queryByText } = render(Page);

    expect(await findByText("2 September")).toBeTruthy();
    expect(queryByText(/^Day \d/)).toBeNull();
  });

  it("renders the date from the string, not through a timezone", async () => {
    /**
     * `new Date("2026-09-02")` is UTC midnight, which renders as 1 September in
     * every negative-offset zone — a wrong date for half the world and a test
     * that passes in London and fails in New York. The date is formatted from
     * the ISO parts directly, so this holds wherever it runs.
     */
    mockListSessions.mockResolvedValue([session({ session_date: "2026-01-01" })]);
    const { findByText } = render(Page);

    expect(await findByText("1 January")).toBeTruthy();
  });

  it("shows what the session actually reused", async () => {
    mockListSessions.mockResolvedValue([session()]);
    const { findByText } = render(Page);

    expect(await findByText(/reused 1 of 2/i)).toBeTruthy();
  });

  it("shows no readout at all when the session was never measured", async () => {
    /** Empty means unmeasurable, not zero. "reused 0 of 0" is a grade where an
     * observation belongs, and every session predating the meter would wear it. */
    mockListSessions.mockResolvedValue([session({ review_requested: null, review_used: null })]);
    const { findByText, queryByText } = render(Page);

    await findByText("2 September");
    expect(queryByText(/reused/i)).toBeNull();
  });

  it("keeps the newest first, as the server ordered them", async () => {
    mockListSessions.mockResolvedValue([
      session({ id: "new", session_date: "2026-09-02", title: "Newer" }),
      session({ id: "old", session_date: "2026-08-28", title: "Older" }),
    ]);
    const { findAllByTestId } = render(Page);

    const rows = await findAllByTestId("review-session-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("2 September"),
      expect.stringContaining("28 August"),
    ]);
  });

  it("survives its own list failing without taking the curricula down", async () => {
    // Two independent surfaces that happen to share a page. A review-session
    // outage must not blank the library the learner came here for.
    mockListCurricula.mockResolvedValue([
      { id: "x", topic: "coffee", created_at: "2026-01-01 00:00:00" },
    ]);
    mockListSessions.mockRejectedValue(new Error("backend down"));
    const { findByText } = render(Page);

    expect(await findByText("coffee")).toBeTruthy();
    expect(await findByText(/no review sessions yet/i)).toBeTruthy();
  });

  it("each row is a link to that session", async () => {
    // The rows exist to be opened. Until this landed they rendered and went
    // nowhere, and manual step 8 ("read the story it wrote") had no home.
    mockListSessions.mockResolvedValue([session()]);
    const { findByRole } = render(Page);

    const link = await findByRole("link", { name: /A Missed Train/ });
    expect(link.getAttribute("href")).toBe("/review-sessions/sess-1");
  });

  it("says nothing is there yet rather than showing an empty list", async () => {
    const { findByText } = render(Page);

    expect(await findByText(/no review sessions yet/i)).toBeTruthy();
  });
});

describe("making a review session", () => {
  it("adds one to the top of the list", async () => {
    mockCreateSession.mockResolvedValue({
      id: "fresh",
      session_date: "2026-09-03",
      title: "The Late Bus",
      review_requested: ["oppføre"],
      review_used: ["oppføre"],
      warnings: [],
    });
    const { findByRole, findByText } = render(Page);

    await fireEvent.click(await findByRole("button", { name: /new review session/i }));

    await waitFor(async () => expect(await findByText("The Late Bus")).toBeTruthy());
    expect(mockCreateSession).toHaveBeenCalledTimes(1);
  });

  it("treats nothing-due as a message, not a failure", async () => {
    /** The 409 is a normal Tuesday. Rendering it in the error style would train
     * the learner to read a working feature as broken. */
    const err = Object.assign(
      new Error("POST /api/review-sessions: Nothing to review right now — no vocabulary is due"),
      { status: 409 },
    );
    mockCreateSession.mockRejectedValue(err);
    const { findByRole, findByText, queryByRole } = render(Page);

    await fireEvent.click(await findByRole("button", { name: /new review session/i }));

    expect(await findByText(/nothing to review right now/i)).toBeTruthy();
    expect(queryByRole("alert")).toBeNull();
  });

  it("still reports a real failure as one", async () => {
    const err = Object.assign(new Error("POST /api/review-sessions: upstream exploded"), {
      status: 502,
    });
    mockCreateSession.mockRejectedValue(err);
    const { findByRole, findByText } = render(Page);

    await fireEvent.click(await findByRole("button", { name: /new review session/i }));

    expect(await findByText(/upstream exploded/i)).toBeTruthy();
  });

  it("does not leave the button looking stuck while it works", async () => {
    type Created = Awaited<ReturnType<typeof api.createReviewSession>>;
    let release!: (v: Created) => void;
    mockCreateSession.mockReturnValue(new Promise<Created>((r) => (release = r)));
    const { findByRole } = render(Page);

    const button = await findByRole("button", { name: /new review session/i });
    await fireEvent.click(button);

    expect((button as HTMLButtonElement).disabled).toBe(true);
    release({
      id: "fresh",
      session_date: "2026-09-03",
      title: "The Late Bus",
      review_requested: [],
      review_used: [],
      warnings: [],
    });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  });
});
