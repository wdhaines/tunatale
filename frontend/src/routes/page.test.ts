/**
 * Component tests for the home (Lessons library) +page.svelte route.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import Page from "./+page.svelte";

// Mock $app/navigation
const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

// Mock $lib/api — listCurricula (this page) + generateCurriculum (CurriculumForm)
vi.mock("$lib/api", () => ({
  api: {
    listCurricula: vi.fn(),
    generateCurriculum: vi.fn(),
  },
}));

// Mock $lib/storage (used by CurriculumForm)
vi.mock("$lib/storage", () => ({
  saveFormPreferences: vi.fn(),
  loadFormPreferences: vi.fn().mockReturnValue(null),
}));

import { api } from "$lib/api";
const mockListCurricula = vi.mocked(api.listCurricula);
const mockGenerate = vi.mocked(api.generateCurriculum);

beforeEach(() => {
  vi.clearAllMocks();
  mockListCurricula.mockResolvedValue([
    { id: "x", topic: "test", created_at: "2026-01-01 00:00:00" },
  ]);
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

describe("New curriculum disclosure", () => {
  it("keeps the generate form hidden until '+ New curriculum' is clicked", async () => {
    const { getByRole, queryByText } = render(Page);
    await waitFor(() => expect(mockListCurricula).toHaveBeenCalled());
    expect(queryByText("Generate Curriculum")).toBeNull();

    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    expect(getByRole("heading", { name: "Generate Curriculum" })).toBeTruthy();
  });

  it("toggles the form closed again via Cancel", async () => {
    const { getByRole, queryByText } = render(Page);
    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    expect(queryByText("Generate Curriculum")).not.toBeNull();

    await fireEvent.click(getByRole("button", { name: "Cancel" }));
    expect(queryByText("Generate Curriculum")).toBeNull();
  });

  it("generates a curriculum, prepends it to the list, and navigates", async () => {
    mockGenerate.mockResolvedValue({
      id: "new-id",
      topic: "New Topic",
      language_code: "sl",
      days: 7,
    });
    const { getByRole, getByPlaceholderText, findByText } = render(Page);

    await fireEvent.click(getByRole("button", { name: "+ New curriculum" }));
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "New Topic" },
    });
    await fireEvent.click(getByRole("button", { name: "Generate" }));

    await waitFor(() => {
      expect(mockGenerate).toHaveBeenCalledWith("New Topic", "A2", 7);
      expect(mockGoto).toHaveBeenCalledWith("/c/new-id");
    });
    // Optimistically prepended as a link in the library
    expect(
      (await findByText("New Topic", { selector: ".topic" })).closest("a")?.getAttribute("href"),
    ).toBe("/c/new-id");
  });
});
