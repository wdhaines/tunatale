/**
 * Component tests for the home (Lessons library) +page.svelte route.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import Page from "./+page.svelte";

// Mock $app/navigation
const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

// Mock $lib/api — listCurricula (this page)
vi.mock("$lib/api", () => ({
  api: {
    listCurricula: vi.fn(),
    startPlan: vi.fn(),
    getCurriculumProgress: vi.fn(),
  },
}));

// Mock $lib/stores/listened.svelte — same signal the lesson page uses
vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: { has: vi.fn().mockReturnValue(false) },
}));

import { api } from "$lib/api";
import { listenedStore } from "$lib/stores/listened.svelte";
const mockListCurricula = vi.mocked(api.listCurricula);
const mockStartPlan = vi.mocked(api.startPlan);
const mockGetCurriculumProgress = vi.mocked(api.getCurriculumProgress);
const mockListenedHas = vi.mocked(listenedStore.has);

beforeEach(() => {
  vi.clearAllMocks();
  mockListCurricula.mockResolvedValue([
    { id: "x", topic: "test", created_at: "2026-01-01 00:00:00" },
  ]);
  mockGetCurriculumProgress.mockResolvedValue([]);
  mockListenedHas.mockReturnValue(false);
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
    // Date should be displayed in an unambiguous "Apr 10, 2026" format
    expect(await findByText("Apr 10, 2026")).toBeTruthy();
    expect(await findByText("Apr 7, 2026")).toBeTruthy();
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

describe("Per-curriculum progress", () => {
  it("shows 'M of N days listened' and links Continue to the first unlistened day", async () => {
    mockListCurricula.mockResolvedValue([
      { id: "slug-abc123", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" },
    ]);
    mockGetCurriculumProgress.mockResolvedValue([
      { day: 1, lesson_id: "lesson-1" },
      { day: 2, lesson_id: "lesson-2" },
      { day: 3, lesson_id: "lesson-3" },
      { day: 4, lesson_id: "lesson-4" },
      { day: 5, lesson_id: "lesson-5" },
      { day: 6, lesson_id: "lesson-6" },
      { day: 7, lesson_id: "lesson-7" },
    ]);
    mockListenedHas.mockImplementation((id: string) =>
      ["lesson-1", "lesson-2", "lesson-3"].includes(id),
    );

    const { findByText, getByRole } = render(Page);

    expect(await findByText("3 of 7 days listened")).toBeTruthy();
    const continueLink = getByRole("link", { name: /Continue/ }) as HTMLAnchorElement;
    expect(continueLink.textContent).toContain("Continue");
    expect(continueLink.textContent).toContain("Day 4");
    expect(continueLink.getAttribute("href")).toBe("/c/slug-abc123/l/lesson-4");
  });

  it("shows 'All N days listened ✓' and a Revisit link to the last day when fully listened", async () => {
    mockListCurricula.mockResolvedValue([
      { id: "slug-abc123", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" },
    ]);
    mockGetCurriculumProgress.mockResolvedValue([
      { day: 1, lesson_id: "lesson-1" },
      { day: 2, lesson_id: "lesson-2" },
      { day: 3, lesson_id: "lesson-3" },
    ]);
    mockListenedHas.mockReturnValue(true);

    const { findByText, getByRole } = render(Page);

    expect(await findByText("3 of 3 days listened")).toBeTruthy();
    expect(await findByText("All 3 days listened ✓")).toBeTruthy();
    const revisitLink = getByRole("link", { name: /Revisit/ }) as HTMLAnchorElement;
    expect(revisitLink.textContent).toContain("Revisit");
    expect(revisitLink.textContent).toContain("Day 3");
    expect(revisitLink.getAttribute("href")).toBe("/c/slug-abc123/l/lesson-3");
  });

  it("renders a plain card with no progress UI when fetchCurriculumProgress fails", async () => {
    mockListCurricula.mockResolvedValue([
      { id: "slug-abc123", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" },
    ]);
    mockGetCurriculumProgress.mockRejectedValue(new Error("boom"));

    const { findByText, queryByText, queryByRole } = render(Page);

    expect(await findByText("Ordering Coffee")).toBeTruthy();
    expect(queryByText(/days listened/)).toBeNull();
    expect(queryByRole("link", { name: /Continue/ })).toBeNull();
    expect(queryByRole("link", { name: /Revisit/ })).toBeNull();
  });

  it("renders a plain card with no progress UI when a curriculum has zero days", async () => {
    mockListCurricula.mockResolvedValue([
      { id: "slug-abc123", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" },
    ]);
    mockGetCurriculumProgress.mockResolvedValue([]);

    const { findByText, queryByText, queryByRole } = render(Page);

    expect(await findByText("Ordering Coffee")).toBeTruthy();
    expect(queryByText(/days listened/)).toBeNull();
    expect(queryByRole("link", { name: /Continue/ })).toBeNull();
  });

  it("computes independent progress for multiple curricula", async () => {
    mockListCurricula.mockResolvedValue([
      { id: "curric-a", topic: "Ordering Coffee", created_at: "2026-04-10 12:00:00" },
      { id: "curric-b", topic: "At the Airport", created_at: "2026-04-07 08:30:00" },
    ]);
    mockGetCurriculumProgress.mockImplementation(async (id: string) => {
      if (id === "curric-a") {
        return [
          { day: 1, lesson_id: "a-lesson-1" },
          { day: 2, lesson_id: "a-lesson-2" },
        ];
      }
      return [
        { day: 1, lesson_id: "b-lesson-1" },
        { day: 2, lesson_id: "b-lesson-2" },
        { day: 3, lesson_id: "b-lesson-3" },
        { day: 4, lesson_id: "b-lesson-4" },
      ];
    });
    mockListenedHas.mockImplementation((id: string) => id === "a-lesson-1" || id === "b-lesson-1");

    const { findByText } = render(Page);

    expect(await findByText("1 of 2 days listened")).toBeTruthy();
    expect(await findByText("1 of 4 days listened")).toBeTruthy();
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
    expect(
      (await findByText("New Topic", { selector: ".topic" })).closest("a")?.getAttribute("href"),
    ).toBe("/c/new-id");
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
