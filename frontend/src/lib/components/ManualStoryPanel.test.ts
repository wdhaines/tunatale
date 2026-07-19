import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ManualStoryPanel from "./ManualStoryPanel.svelte";

const mockClipboard = {
  writeText: vi.fn().mockResolvedValue(undefined),
};
Object.assign(navigator, { clipboard: mockClipboard });

vi.mock("$lib/api", () => ({
  api: {
    getStoryPrompt: vi.fn(),
    importStory: vi.fn(),
    deleteCurriculumDay: vi.fn(),
  },
}));

import { api } from "$lib/api";

const mockGetStoryPrompt = vi.mocked(api.getStoryPrompt);
const mockImportStory = vi.mocked(api.importStory);
const mockDeleteCurriculumDay = vi.mocked(api.deleteCurriculumDay);

const PROMPT_EXPORT = {
  system_prompt: "You are a helpful story writer.",
  user_prompt: "Write a story about coffee in Slovene.",
};

const IMPORT_RESULT = {
  id: "new-lesson-42",
  title: "Coffee Day 5",
  sections: [{ type: "key_phrases", phrase_count: 2 }],
  warnings: ["speaker 'unknown' is not in the sl voice map"],
};

const onImported = vi.fn();
const onDeleted = vi.fn();
const PROPS = {
  curriculumId: "cid-1",
  day: 5,
  onImported,
  onDeleted,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockClipboard.writeText.mockResolvedValue(undefined);
  mockGetStoryPrompt.mockResolvedValue(PROMPT_EXPORT);
  mockImportStory.mockResolvedValue(IMPORT_RESULT);
});

describe("ManualStoryPanel", () => {
  it("copies story prompt to clipboard on button click", async () => {
    const { getByText } = render(ManualStoryPanel, { props: PROPS });
    const btn = getByText("Copy story prompt");
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(mockGetStoryPrompt).toHaveBeenCalledWith("cid-1", 5);
    });
    expect(mockClipboard.writeText).toHaveBeenCalledWith(
      "You are a helpful story writer.\n\nWrite a story about coffee in Slovene.",
    );
  });

  it("shows Copied label after successful copy", async () => {
    const { getByText, findByText } = render(ManualStoryPanel, { props: PROPS });
    const btn = getByText("Copy story prompt");
    await fireEvent.click(btn);

    expect(await findByText("Copied ✓")).toBeTruthy();
  });

  it("import sends raw text without client-side JSON.parse", async () => {
    const { container } = render(ManualStoryPanel, { props: PROPS });
    const textarea = container.querySelector("textarea")!;
    const rawText = '{"title":"Test"}';
    await fireEvent.input(textarea, { target: { value: rawText } });

    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(mockImportStory).toHaveBeenCalledWith({
        curriculum_id: "cid-1",
        day: 5,
        raw: rawText,
      });
    });
    // Verify the mock was NOT called with a `story` key
    const call = mockImportStory.mock.calls[0][0];
    expect(call).not.toHaveProperty("story");
    expect(call).toHaveProperty("raw", rawText);
  });

  it("calls onImported immediately when import succeeds without warnings", async () => {
    mockImportStory.mockResolvedValue({ ...IMPORT_RESULT, warnings: [] });

    const { container } = render(ManualStoryPanel, { props: PROPS });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea, { target: { value: '{"title":"Test"}' } });

    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(onImported).toHaveBeenCalledWith("new-lesson-42");
    });
  });

  it("import with warnings defers navigation until Continue clicked", async () => {
    const { container } = render(ManualStoryPanel, { props: PROPS });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea, { target: { value: '{"title":"Test"}' } });

    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(container.textContent).toContain("speaker 'unknown' is not in the sl voice map");
    });
    expect(onImported).not.toHaveBeenCalled();

    const continueBtn = container.querySelector('[data-testid="continue-btn"]')!;
    expect(continueBtn).toBeTruthy();
    await fireEvent.click(continueBtn);
    expect(onImported).toHaveBeenCalledWith("new-lesson-42");
  });

  it("import error (422) shows error message and does not call onImported", async () => {
    mockImportStory.mockRejectedValue(new Error("POST /api/story/import: Unparseable JSON"));

    const { container } = render(ManualStoryPanel, { props: PROPS });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea, { target: { value: "not json" } });

    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(container.textContent).toContain("Unparseable JSON");
    });
    expect(onImported).not.toHaveBeenCalled();
  });

  it("copy failure surfaces error message", async () => {
    mockGetStoryPrompt.mockRejectedValue(new Error("Network error"));

    const { getByText } = render(ManualStoryPanel, { props: PROPS });
    const btn = getByText("Copy story prompt");
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(getByText("Network error")).toBeTruthy();
    });
  });

  it("import failure surfaces error message", async () => {
    mockImportStory.mockRejectedValue(new Error("POST /api/story/import: 422"));

    const { container } = render(ManualStoryPanel, { props: PROPS });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea, { target: { value: '{"title":"X"}' } });

    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(container.textContent).toContain("422");
    });
  });

  it("disables import button while import is loading", async () => {
    mockImportStory.mockReturnValue(new Promise(() => {}));

    const { container } = render(ManualStoryPanel, { props: PROPS });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea, { target: { value: '{"title":"X"}' } });

    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(
        (container.querySelector('[data-testid="import-btn"]') as HTMLButtonElement).disabled,
      ).toBe(true);
    });
  });

  it("shows Importing… while loading", async () => {
    mockImportStory.mockReturnValue(new Promise(() => {}));

    const { container } = render(ManualStoryPanel, { props: PROPS });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea, { target: { value: '{"title":"X"}' } });

    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="import-btn"]')?.textContent).toContain(
        "Importing",
      );
    });
  });

  describe("delete this day", () => {
    it("renders a Delete this day button", () => {
      const { getByText } = render(ManualStoryPanel, { props: PROPS });
      expect(getByText("Delete this day")).toBeTruthy();
    });

    it("requires a second click to confirm before deleting", async () => {
      const { getByText } = render(ManualStoryPanel, { props: PROPS });
      await fireEvent.click(getByText("Delete this day"));
      expect(getByText("Confirm delete")).toBeTruthy();
      expect(mockDeleteCurriculumDay).not.toHaveBeenCalled();
    });

    it("deletes the day and calls onDeleted on the second click", async () => {
      mockDeleteCurriculumDay.mockResolvedValue({ deleted_day: 5, days: 0 });
      const { getByText } = render(ManualStoryPanel, { props: PROPS });
      await fireEvent.click(getByText("Delete this day"));
      await fireEvent.click(getByText("Confirm delete"));

      await waitFor(() => {
        expect(mockDeleteCurriculumDay).toHaveBeenCalledWith("cid-1", 5);
        expect(onDeleted).toHaveBeenCalled();
      });
    });

    it("resets the confirm state on blur without deleting", async () => {
      const { getByText } = render(ManualStoryPanel, { props: PROPS });
      await fireEvent.click(getByText("Delete this day"));
      expect(getByText("Confirm delete")).toBeTruthy();

      await fireEvent.blur(getByText("Confirm delete"));
      expect(getByText("Delete this day")).toBeTruthy();
      expect(mockDeleteCurriculumDay).not.toHaveBeenCalled();
    });

    it("shows an error and does not call onDeleted when deletion fails", async () => {
      mockDeleteCurriculumDay.mockRejectedValue(new Error("delete failed"));
      const { getByText, findByText } = render(ManualStoryPanel, { props: PROPS });
      await fireEvent.click(getByText("Delete this day"));
      await fireEvent.click(getByText("Confirm delete"));

      expect(await findByText("delete failed")).toBeTruthy();
      expect(onDeleted).not.toHaveBeenCalled();
    });
  });
});
