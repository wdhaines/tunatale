/**
 * The review-sessions index (bd tunatale-e6fq).
 *
 * The user: "Really having the same IA for both would feel better." A lesson
 * has home → curriculum page → lesson; a session had home → session, with
 * nothing in between, so a session's back link went to "/" and deleting one
 * had nowhere of its own to land. This page is the counterpart of the
 * curriculum page: every session, in order.
 *
 * NEWEST FIRST, and the order is the pager's read backwards: the row above a
 * session is the one its "next →" link opens. Two orderings here would be two
 * answers to one question (see lib/reading/nextReviewSession.ts).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  api: {
    listReviewSessions: vi.fn(),
  },
}));

import { api } from "$lib/api";
import Page from "./+page.svelte";
import { load } from "./+page";

const mockListSessions = vi.mocked(api.listReviewSessions);

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

const page = (...sessions: ReturnType<typeof session>[]) =>
  render(Page, { props: { data: { sessions } } as never });

beforeEach(() => {
  vi.clearAllMocks();
});

describe("load", () => {
  it("returns every session the server has", async () => {
    mockListSessions.mockResolvedValue([session(), session({ id: "sess-2" })] as never);

    const data = (await load({} as never)) as { sessions: { id: string }[] };

    expect(data.sessions.map((s) => s.id)).toEqual(["sess-1", "sess-2"]);
  });

  it("is an error page when the list cannot be read, never an empty list", async () => {
    // On home a failed list degrades to "no sessions yet" because the
    // curricula share the page. Here the list IS the page: showing "none yet"
    // for a backend that is down would be a false statement.
    mockListSessions.mockRejectedValue(new Error("backend down"));

    await expect(load({} as never)).rejects.toMatchObject({ status: 503 });
  });
});

describe("the sessions index", () => {
  it("is titled for what it lists", () => {
    const { getByRole } = page(session());

    expect(getByRole("heading", { level: 1, name: "Review sessions" })).toBeTruthy();
  });

  it("lists every session, newest first, whatever order they arrive in", () => {
    const { getAllByTestId } = page(
      session({ id: "mid", session_date: "2026-09-02", title: "Middle" }),
      session({ id: "new", session_date: "2026-09-05", title: "Newest" }),
      session({ id: "old", session_date: "2026-08-28", title: "Oldest" }),
    );

    const rows = getAllByTestId("review-session-row");
    expect(rows.map((r) => r.querySelector("a")?.getAttribute("href"))).toEqual([
      "/review-sessions/new",
      "/review-sessions/mid",
      "/review-sessions/old",
    ]);
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("5 September"),
      expect.stringContaining("2 September"),
      expect.stringContaining("28 August"),
    ]);
  });

  it("orders a same-date run as the pager does, read backwards", () => {
    // The pager walks alpha → mike → zulu (id order within a date), so the
    // index, newest first, shows zulu above mike above alpha.
    const { getAllByTestId } = page(
      session({ id: "mike", session_date: "2026-09-04" }),
      session({ id: "zulu", session_date: "2026-09-04" }),
      session({ id: "alpha", session_date: "2026-09-04" }),
    );

    expect(
      getAllByTestId("review-session-row").map((r) => r.querySelector("a")?.getAttribute("href")),
    ).toEqual(["/review-sessions/zulu", "/review-sessions/mike", "/review-sessions/alpha"]);
  });

  it("each row is a link to that session, named by its title", () => {
    const { getByRole } = page(session());

    expect(getByRole("link", { name: /A Missed Train/ }).getAttribute("href")).toBe(
      "/review-sessions/sess-1",
    );
  });

  it("renders the date from the string, not through a timezone", () => {
    // `new Date("2026-09-01")` is UTC midnight and reads as 31 August in every
    // negative-offset zone. session_date is a calendar date, not an instant.
    const { getByText, queryByText } = page(session({ session_date: "2026-09-01" }));

    expect(getByText("1 September")).toBeTruthy();
    expect(queryByText(/31 August/)).toBeNull();
  });

  it("shows what a session reused", () => {
    const { getByText } = page(session());

    expect(getByText(/reused 1 of 2/i)).toBeTruthy();
  });

  it("shows a measured zero, which is an observation", () => {
    const { getByText } = page(session({ review_requested: ["a", "b", "c"], review_used: [] }));

    expect(getByText(/reused 0 of 3/i)).toBeTruthy();
  });

  it("shows no readout for a session that was never measured", () => {
    const { getByText, queryByText } = page(session({ review_requested: null, review_used: null }));

    expect(getByText("2 September")).toBeTruthy();
    expect(queryByText(/reused/i)).toBeNull();
  });

  it("says there are none yet rather than showing an empty list", () => {
    const { getByText, queryAllByTestId, container } = page();

    expect(getByText(/no review sessions yet/i)).toBeTruthy();
    expect(queryAllByTestId("review-session-row")).toEqual([]);
    expect(container.querySelector("ul")).toBeNull();
  });

  it("offers a way back to home, as the curriculum page does", () => {
    const { getByRole } = page(session());

    expect(getByRole("link", { name: "← Lessons" }).getAttribute("href")).toBe("/");
  });

  it("is laid out in a <main>, like every other page", () => {
    // check_main_styling.py enforces that the element is STYLED; this pins that
    // it exists, so the checker has something to read.
    const { container } = page(session());

    expect(container.querySelector("main")).toBeTruthy();
  });
});
