/**
 * Tests for CurriculumForm.svelte.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  api: { generateCurriculum: vi.fn() },
}));
vi.mock("$lib/storage", () => ({
  saveFormPreferences: vi.fn(),
  loadFormPreferences: vi.fn().mockReturnValue(null),
}));

import CurriculumForm from "./CurriculumForm.svelte";
import { api } from "$lib/api";
import { saveFormPreferences, loadFormPreferences } from "$lib/storage";

const mockGenerate = vi.mocked(api.generateCurriculum);
const mockSavePrefs = vi.mocked(saveFormPreferences);
const mockLoadPrefs = vi.mocked(loadFormPreferences);

beforeEach(() => {
  vi.clearAllMocks();
  mockLoadPrefs.mockReturnValue(null);
});

describe("CurriculumForm", () => {
  it("renders a Generate button", () => {
    const { getByRole } = render(CurriculumForm, { props: { onGenerate: vi.fn() } });
    expect(getByRole("button", { name: "Generate" })).toBeTruthy();
  });

  it("Generate button is disabled when topic is empty", () => {
    const { getByRole } = render(CurriculumForm, { props: { onGenerate: vi.fn() } });
    expect((getByRole("button", { name: "Generate" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("enables Generate button when topic is filled", async () => {
    const { getByRole, getByPlaceholderText } = render(CurriculumForm, {
      props: { onGenerate: vi.fn() },
    });
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "hiking" },
    });
    expect((getByRole("button", { name: "Generate" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("calls onGenerate with the curriculum on success", async () => {
    const curriculum = { id: "c1", topic: "hiking", language_code: "sl", days: 7 };
    mockGenerate.mockResolvedValue(curriculum);
    const onGenerate = vi.fn();

    const { getByRole, getByPlaceholderText } = render(CurriculumForm, { props: { onGenerate } });
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "hiking" },
    });
    await fireEvent.click(getByRole("button", { name: "Generate" }));

    await waitFor(() => {
      expect(onGenerate).toHaveBeenCalledWith(curriculum);
    });
  });

  it("shows error message when generateCurriculum fails", async () => {
    mockGenerate.mockRejectedValue(new Error("LLM offline"));
    const { getByRole, getByPlaceholderText, findByText } = render(CurriculumForm, {
      props: { onGenerate: vi.fn() },
    });
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "hiking" },
    });
    await fireEvent.click(getByRole("button", { name: "Generate" }));
    expect(await findByText("LLM offline")).toBeTruthy();
  });

  it("shows stringified error when generateCurriculum throws a non-Error", async () => {
    mockGenerate.mockRejectedValue("plain string error");
    const { getByRole, getByPlaceholderText, findByText } = render(CurriculumForm, {
      props: { onGenerate: vi.fn() },
    });
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "hiking" },
    });
    await fireEvent.click(getByRole("button", { name: "Generate" }));
    expect(await findByText("plain string error")).toBeTruthy();
  });

  it("shows CEFR level descriptions below the select", () => {
    const { getByText } = render(CurriculumForm, { props: { onGenerate: vi.fn() } });
    expect(getByText(/Complete beginner/)).toBeTruthy();
  });

  it("restores saved prefs (topic, cefrLevel, numDays) from localStorage on mount", async () => {
    mockLoadPrefs.mockReturnValue({ topic: "camping", cefrLevel: "B1", numDays: 5 });

    const { getByPlaceholderText, getByDisplayValue } = render(CurriculumForm, {
      props: { onGenerate: vi.fn() },
    });

    await waitFor(() => {
      expect((getByPlaceholderText(/ordering coffee/i) as HTMLInputElement).value).toBe("camping");
    });
    expect((getByDisplayValue("B1") as HTMLSelectElement).value).toBe("B1");
  });

  it("saves prefs to localStorage when form values change", async () => {
    const { getByPlaceholderText, getByDisplayValue } = render(CurriculumForm, {
      props: { onGenerate: vi.fn() },
    });
    // Change topic
    await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
      target: { value: "hiking" },
    });
    // Change CEFR level
    await fireEvent.change(getByDisplayValue("A2"), { target: { value: "B1" } });
    // Change days
    const numInput = document.querySelector('input[type="number"]') as HTMLInputElement;
    await fireEvent.input(numInput, { target: { value: "14" } });

    // Wait for effect to run
    await new Promise((resolve) => setTimeout(resolve, 100));

    // Verify saveFormPreferences was called
    expect(mockSavePrefs).toHaveBeenCalled();
  });
});
