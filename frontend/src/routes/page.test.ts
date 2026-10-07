/**
 * Component tests for the home (Lessons library) +page.svelte route.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import Page from "./+page.svelte";
import { SvelteSet } from "svelte/reactivity";

// Mock $app/navigation
const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

// Mock $lib/api — listCurricula (this page)
vi.mock("$lib/api", () => ({
  api: {
    listCurricula: vi.fn(),
    startPlan: vi.fn(),
    getCurriculum: vi.fn(),
    getCurriculumProgress: vi.fn(),
    listReviewSessions: vi.fn(),
    createReviewSession: vi.fn(),
  },
}));

// Mock $lib/stores/listened.svelte — same signal the lesson page uses
vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: {
    has: vi.fn().mockReturnValue(false),
    lastListenedAt: vi.fn().mockReturnValue(null),
  },
}));

import { api } from "$lib/api";
import { listenedStore } from "$lib/stores/listened.svelte";
const mockListCurricula = vi.mocked(api.listCurricula);
const mockStartPlan = vi.mocked(api.startPlan);
const mockGetCurriculum = vi.mocked(api.getCurriculum);
const mockGetCurriculumProgress = vi.mocked(api.getCurriculumProgress);
const mockListenedHas = vi.mocked(listenedStore.has);
const mockLastListenedAt = vi.mocked(listenedStore.lastListenedAt);

/** GET /api/curriculum/{id}: the only place a lesson's title comes from. */
const plan = (id: string, titles: Record<number, string>) => ({
  id,
  topic: "unused",
  language_code: "no",
  cefr_level: "A2",
  proposed: null,
  days: Object.entries(titles).map(([day, title], i) => ({
    day: Number(day),
    position: i + 1,
    title,
    focus: "",
    collocations: [],
    learning_objective: "",
    story_guidance: "",
  })),
});

/** What a learner can read off each recent-lesson row, top to bottom. */
const rowFacts = (rows: HTMLElement[]) =>
  rows.map((r) => ({
    title: r.querySelector(".topic")?.textContent,
    meta: r.querySelector(".meta")?.textContent,
    href: r.querySelector("a")?.getAttribute("href"),
  }));

beforeEach(() => {
  vi.clearAllMocks();
  mockListCurricula.mockResolvedValue([
    { id: "x", topic: "test", created_at: "2026-01-01 00:00:00" },
  ]);
  mockGetCurriculum.mockImplementation(async (id: string) => plan(id, {}));
  mockGetCurriculumProgress.mockResolvedValue([]);
  vi.mocked(api.listReviewSessions).mockResolvedValue([]);
  mockListenedHas.mockReturnValue(false);
  mockLastListenedAt.mockReturnValue(null);
});

describe("Lessons library (home)", () => {
  it("renders the Lessons heading", () => {
    const { getByRole } = render(Page);
    expect(getByRole("heading", { name: "Lessons", level: 1 })).toBeTruthy();
  });

  it("shows loading state initially", () => {
    mockListCurricula.mockReturnValue(new Promise(() => {})); // never resolves
    const { getByText } = render(Page);
    expect(getByText("Loading…")).toBeTruthy();
  });

  it("renders curricula as links after load", async () => {
    mockListCurricula.mockResolvedValue([
      { id: "slug-abc123", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" },
      { id: "slug-def456", topic: "At the Airport", created_at: "2026-04-07 08:30:00" },
    ]);
    const { findByText, getByRole } = render(Page);
    expect(await findByText("Ordering Coffee")).toBeTruthy();
    expect(
      (getByRole("link", { name: /Ordering Coffee/ }) as HTMLAnchorElement).getAttribute("href"),
    ).toBe("/c/slug-abc123");
    expect(
      (getByRole("link", { name: /At the Airport/ }) as HTMLAnchorElement).getAttribute("href"),
    ).toBe("/c/slug-def456");
  });

  it("shows empty state when no curricula", async () => {
    mockListCurricula.mockResolvedValue([]);
    const { findByText } = render(Page);
    expect(await findByText(/no curricula yet/i)).toBeTruthy();
  });

  it("shows error when listCurricula rejects with an Error", async () => {
    mockListCurricula.mockRejectedValue(new Error("fetch failed"));
    const { findByText } = render(Page);
    expect(await findByText("fetch failed")).toBeTruthy();
  });

  it("shows stringified error when listCurricula rejects with a non-Error", async () => {
    mockListCurricula.mockRejectedValue("boom");
    const { findByText } = render(Page);
    expect(await findByText("boom")).toBeTruthy();
  });
});

describe("Recent lessons", () => {
  const one = [{ id: "slug-abc123", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" }];
  const lessons = (n: number, prefix = "lesson-") =>
    Array.from({ length: n }, (_, i) => ({
      day: i + 1,
      position: i + 1,
      lesson_id: `${prefix}${i + 1}`,
    }));

  it("shows 'M of N days listened' and leads with the first unlistened lesson", async () => {
    mockListCurricula.mockResolvedValue(one);
    mockGetCurriculumProgress.mockResolvedValue(lessons(7));
    mockGetCurriculum.mockImplementation(async (id: string) =>
      plan(id, { 1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven" }),
    );
    mockListenedHas.mockImplementation((id: string) =>
      ["lesson-1", "lesson-2", "lesson-3"].includes(id),
    );

    const { findByText, getAllByTestId } = render(Page);

    expect(await findByText("3 of 7 days listened")).toBeTruthy();
    // One click from home to the next lesson, and to the two before it.
    await waitFor(() =>
      expect(rowFacts(getAllByTestId("recent-lesson-row"))).toEqual([
        { title: "Four", meta: "Day 4", href: "/c/slug-abc123/l/lesson-4" },
        { title: "Three", meta: "Day 3", href: "/c/slug-abc123/l/lesson-3" },
        { title: "Two", meta: "Day 2", href: "/c/slug-abc123/l/lesson-2" },
      ]),
    );
    const [next, ...before] = getAllByTestId("recent-lesson-row");
    expect(next.textContent).toContain("Up next");
    expect(next.textContent).not.toContain("Listened");
    for (const row of before) {
      expect(row.textContent).toContain("Listened ✓");
      expect(row.textContent).not.toContain("Up next");
    }
  });

  it("labels a row by position and titles it by day key, so a deleted day skips neither", async () => {
    mockListCurricula.mockResolvedValue(one);
    // Day 2 was deleted: the second lesson is keyed day 3 but is the plan's day 2.
    mockGetCurriculumProgress.mockResolvedValue([
      { day: 1, position: 1, lesson_id: "lesson-1" },
      { day: 3, position: 2, lesson_id: "lesson-3" },
      { day: 4, position: 3, lesson_id: "lesson-4" },
    ]);
    mockGetCurriculum.mockImplementation(async (id: string) =>
      plan(id, { 1: "One", 3: "Three", 4: "Four" }),
    );
    mockListenedHas.mockImplementation((id: string) => id === "lesson-1");

    const { getAllByTestId } = render(Page);

    await waitFor(() =>
      expect(rowFacts(getAllByTestId("recent-lesson-row"))).toEqual([
        { title: "Three", meta: "Day 2", href: "/c/slug-abc123/l/lesson-3" },
        { title: "One", meta: "Day 1", href: "/c/slug-abc123/l/lesson-1" },
      ]),
    );
  });

  it("shows 'All N days listened ✓' and the last three, none marked next, when fully listened", async () => {
    mockListCurricula.mockResolvedValue(one);
    mockGetCurriculumProgress.mockResolvedValue(lessons(4));
    mockListenedHas.mockReturnValue(true);

    const { findByText, queryByText, getAllByTestId } = render(Page);

    expect(await findByText("All 4 days listened ✓")).toBeTruthy();
    // One readout, not two: the count is inside the sentence already.
    expect(queryByText("4 of 4 days listened")).toBeNull();
    const rows = getAllByTestId("recent-lesson-row");
    expect(rowFacts(rows).map((r) => r.href)).toEqual([
      "/c/slug-abc123/l/lesson-4",
      "/c/slug-abc123/l/lesson-3",
      "/c/slug-abc123/l/lesson-2",
    ]);
    expect(queryByText("Up next")).toBeNull();
  });

  it("still links every row when the titles cannot be fetched", async () => {
    mockListCurricula.mockResolvedValue(one);
    mockGetCurriculumProgress.mockResolvedValue(lessons(2));
    mockGetCurriculum.mockRejectedValue(new Error("boom"));

    const { findAllByTestId } = render(Page);

    // The day label stands in for the title rather than repeating beside it.
    expect(rowFacts(await findAllByTestId("recent-lesson-row"))).toEqual([
      { title: "Day 1", meta: "", href: "/c/slug-abc123/l/lesson-1" },
    ]);
  });

  it("links 'All lessons' to the curriculum page", async () => {
    mockListCurricula.mockResolvedValue(one);
    mockGetCurriculumProgress.mockResolvedValue(lessons(2));

    const { findByRole } = render(Page);

    const all = (await findByRole("link", { name: /All lessons/ })) as HTMLAnchorElement;
    expect(all.getAttribute("href")).toBe("/c/slug-abc123");
  });

  it("shows the curriculum with no progress and no rows when its progress fetch fails", async () => {
    mockListCurricula.mockResolvedValue(one);
    mockGetCurriculumProgress.mockRejectedValue(new Error("boom"));

    const { findByRole, queryByText, queryAllByTestId } = render(Page);

    expect(
      ((await findByRole("link", { name: "Ordering Coffee" })) as HTMLAnchorElement).getAttribute(
        "href",
      ),
    ).toBe("/c/slug-abc123");
    expect(queryByText(/days listened/)).toBeNull();
    expect(queryAllByTestId("recent-lesson-row")).toHaveLength(0);
  });

  it("shows the curriculum with no progress and no rows when it has zero lessons", async () => {
    mockListCurricula.mockResolvedValue(one);
    mockGetCurriculumProgress.mockResolvedValue([]);

    const { findByRole, queryByText, queryAllByTestId } = render(Page);

    expect(await findByRole("link", { name: "Ordering Coffee" })).toBeTruthy();
    expect(queryByText(/days listened/)).toBeNull();
    expect(queryAllByTestId("recent-lesson-row")).toHaveLength(0);
  });

  describe("with more than one curriculum", () => {
    const two = [
      { id: "curric-a", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" },
      { id: "curric-b", topic: "At the Airport", created_at: "2026-04-07 08:30:00" },
    ];
    const progress = async (id: string) => lessons(id === "curric-a" ? 2 : 4, `${id}-lesson-`);

    it("leads with the one listened to most recently and lists the other in one line", async () => {
      mockListCurricula.mockResolvedValue(two);
      mockGetCurriculumProgress.mockImplementation(progress);
      mockListenedHas.mockImplementation(
        (id: string) => id === "curric-a-lesson-1" || id === "curric-b-lesson-1",
      );
      // B is second in the list and was listened to four days after A.
      mockLastListenedAt.mockImplementation((id: string) =>
        id === "curric-a-lesson-1"
          ? "2026-10-01T10:00:00.000Z"
          : id === "curric-b-lesson-1"
            ? "2026-10-05T10:00:00.000Z"
            : null,
      );

      const { findByText, getByTestId, getAllByTestId, getByRole } = render(Page);

      expect(await findByText("1 of 4 days listened")).toBeTruthy();
      const lead = getByTestId("lead-curriculum");
      expect(lead.textContent).toContain("At the Airport");
      expect(lead.textContent).toContain("1 of 4 days listened");
      expect(rowFacts(getAllByTestId("recent-lesson-row")).map((r) => r.href)).toEqual([
        "/c/curric-b/l/curric-b-lesson-2",
        "/c/curric-b/l/curric-b-lesson-1",
      ]);
      expect(getByRole("link", { name: /All lessons/ }).getAttribute("href")).toBe("/c/curric-b");

      expect(getByRole("heading", { name: "Other curricula" })).toBeTruthy();
      const others = getAllByTestId("other-curriculum-row");
      expect(others).toHaveLength(1);
      expect(others[0].textContent).toContain("Ordering Coffee");
      expect(others[0].textContent).toContain("1 of 2 days listened");
      expect(others[0].querySelector("a")?.getAttribute("href")).toBe("/c/curric-a");
    });

    it("leads with the first listed when nothing has been listened to", async () => {
      mockListCurricula.mockResolvedValue(two);
      mockGetCurriculumProgress.mockImplementation(progress);

      const { findByText, getByTestId, getAllByTestId } = render(Page);

      expect(await findByText("0 of 2 days listened")).toBeTruthy();
      expect(getByTestId("lead-curriculum").textContent).toContain("Ordering Coffee");
      expect(getAllByTestId("other-curriculum-row")[0].textContent).toContain(
        "0 of 4 days listened",
      );
    });

    it("lists another curriculum by name alone when its progress never loaded", async () => {
      mockListCurricula.mockResolvedValue(two);
      mockGetCurriculumProgress.mockImplementation(async (id: string) => {
        if (id === "curric-b") throw new Error("boom");
        return lessons(2, "curric-a-lesson-");
      });

      const { findByTestId } = render(Page);

      const other = await findByTestId("other-curriculum-row");
      expect(other.textContent).toContain("At the Airport");
      expect(other.textContent).not.toMatch(/days listened/);
    });
  });

  it("has no 'Other curricula' section with a single curriculum", async () => {
    mockListCurricula.mockResolvedValue(one);
    mockGetCurriculumProgress.mockResolvedValue(lessons(2));

    const { findByText, queryByText, queryAllByTestId } = render(Page);

    expect(await findByText("0 of 2 days listened")).toBeTruthy();
    expect(queryByText("Other curricula")).toBeNull();
    expect(queryAllByTestId("other-curriculum-row")).toHaveLength(0);
  });

  // C3: progress must react to listenedStore changes without remounting.
  // This test uses a SvelteSet-backed fake so that has() reads a reactive set.
  it("C3: progress and rows update reactively when listenedStore changes (no remount)", async () => {
    const listenedIds = new SvelteSet<string>();
    mockListCurricula.mockResolvedValue(one);
    mockGetCurriculumProgress.mockResolvedValue(lessons(3));
    mockListenedHas.mockImplementation((id: string) => listenedIds.has(id));

    const { getByText, findByText, getAllByTestId } = render(Page);

    // Initially empty set → 0 of 3, and the one row is day 1.
    expect(await findByText("0 of 3 days listened")).toBeTruthy();
    expect(rowFacts(getAllByTestId("recent-lesson-row")).map((r) => r.href)).toEqual([
      "/c/slug-abc123/l/lesson-1",
    ]);

    // Mutate the set WITHOUT remounting
    listenedIds.add("lesson-1");

    await waitFor(() => {
      expect(getByText("1 of 3 days listened")).toBeTruthy();
      expect(rowFacts(getAllByTestId("recent-lesson-row")).map((r) => r.href)).toEqual([
        "/c/slug-abc123/l/lesson-2",
        "/c/slug-abc123/l/lesson-1",
      ]);
    });
  });
});

describe("New curriculum disclosure", () => {
  it("keeps the plan form hidden until '+ New curriculum' is clicked", async () => {
    const { getByRole, queryByText } = render(Page);
    await waitFor(() => expect(mockListCurricula).toHaveBeenCalled());
    expect(queryByText("Plan a curriculum")).toBeNull();

    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    expect(getByRole("heading", { name: "Plan a curriculum" })).toBeTruthy();
  });

  it("toggles the form closed again via Cancel", async () => {
    const { getByRole, queryByText } = render(Page);
    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    expect(queryByText("Plan a curriculum")).not.toBeNull();

    await fireEvent.click(getByRole("button", { name: "Cancel" }));
    expect(queryByText("Plan a curriculum")).toBeNull();
  });

  it("disables Start planning until a topic is entered", async () => {
    const { getByRole } = render(Page);
    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    const startButton = getByRole("button", { name: "Start planning" }) as HTMLButtonElement;
    expect(startButton.disabled).toBe(true);
  });

  it("starts a plan, prepends it to the list, and navigates to the chat", async () => {
    mockStartPlan.mockResolvedValue({
      id: "new-id",
      topic: "New Topic",
      language_code: "sl",
      cefr_level: "B1",
      days: 0,
    });
    const { getByRole, getByPlaceholderText, getByLabelText, findByText } = render(Page);

    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "New Topic" },
    });
    await fireEvent.change(getByLabelText(/cefr level/i), { target: { value: "B1" } });
    await fireEvent.click(getByRole("button", { name: "Start planning" }));

    await waitFor(() => {
      expect(mockStartPlan).toHaveBeenCalledWith("New Topic", "B1");
      expect(mockGoto).toHaveBeenCalledWith("/c/new-id/plan");
    });
    // Optimistically prepended as a link in the library
    expect((await findByText("New Topic")).closest("a")?.getAttribute("href")).toBe("/c/new-id");
  });

  it("shows the error and stays open when startPlan fails", async () => {
    mockStartPlan.mockRejectedValue(new Error("POST /api/curriculum/plan: boom"));
    const { getByRole, getByPlaceholderText, findByText } = render(Page);

    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "Topic" },
    });
    await fireEvent.click(getByRole("button", { name: "Start planning" }));

    expect(await findByText(/boom/)).toBeTruthy();
    expect(getByRole("heading", { name: "Plan a curriculum" })).toBeTruthy();
    expect(mockGoto).not.toHaveBeenCalled();
  });

  it("shows a stringified error when startPlan rejects with a non-Error", async () => {
    mockStartPlan.mockRejectedValue("bad thing");
    const { getByRole, getByPlaceholderText, findByText } = render(Page);

    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "Topic" },
    });
    await fireEvent.click(getByRole("button", { name: "Start planning" }));

    expect(await findByText("bad thing")).toBeTruthy();
  });
});

describe("Delete curriculum", () => {
  // Deleting moved to the curriculum's own page (bd tunatale-e6fq): home lists
  // lessons now, and a destructive button beside a lesson row would read as
  // deleting that lesson. The behaviour is pinned in c/[curriculumId]/page.test.ts.
  it("is not offered on home", async () => {
    mockListCurricula.mockResolvedValue([
      { id: "curric-a", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" },
      { id: "curric-b", topic: "At the Airport", created_at: "2026-04-07 08:30:00" },
    ]);
    const { findByText, queryByRole } = render(Page);

    expect(await findByText("At the Airport")).toBeTruthy();
    expect(queryByRole("button", { name: /delete/i })).toBeNull();
  });
});
