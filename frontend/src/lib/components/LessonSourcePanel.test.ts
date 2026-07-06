import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import LessonSourcePanel from "./LessonSourcePanel.svelte";

const mockClipboard = {
  writeText: vi.fn().mockResolvedValue(undefined),
};
Object.assign(navigator, { clipboard: mockClipboard });

vi.mock("$lib/api", () => ({
  api: {
    getStorySource: vi.fn(),
    importStory: vi.fn(),
  },
}));

import { api } from "$lib/api";

const mockGetStorySource = vi.mocked(api.getStorySource);
const mockImportStory = vi.mocked(api.importStory);

const SOURCE_STORY = {
  title: "Kavarna",
  key_phrases: [{ phrase: "ena kava", translation: "one coffee" }],
  scenes: [
    {
      label: "Scene 1",
      lines: [{ speaker: "barista", text: "Dober dan", translation: "Good day" }],
    },
  ],
  dialogue_glosses: [],
  morphology_focus: [],
};

const SOURCE_RESPONSE = {
  curriculum_id: "cid-1",
  day: 1,
  story: SOURCE_STORY,
};

const IMPORT_RESULT = {
  id: "new-lesson-123",
  title: "Kavarna v2",
  sections: [{ type: "key_phrases", phrase_count: 1 }],
  warnings: ["speaker 'barman' is not in the sl voice map; its lines will use the narrator voice"],
};

const onImported = vi.fn();
const PROPS = {
  lessonId: "l-abc",
  curriculumId: "cid-1",
  day: 1,
  onImported,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockClipboard.writeText.mockResolvedValue(undefined);
  mockGetStorySource.mockResolvedValue(SOURCE_RESPONSE);
  mockImportStory.mockResolvedValue(IMPORT_RESULT);
});

async function openPanel(container: HTMLElement) {
  const summary = container.querySelector("summary");
  if (!summary) throw new Error("No summary element");
  await fireEvent.click(summary);
}

describe("LessonSourcePanel", () => {
  it("renders as a closed details element", () => {
    const { container } = render(LessonSourcePanel, { props: PROPS });
    const details = container.querySelector("details");
    expect(details).toBeTruthy();
    expect(details!.getAttribute("open")).toBeNull();
  });

  it("fetches source on first open and shows formatted JSON", async () => {
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(mockGetStorySource).toHaveBeenCalledWith("l-abc");
    });

    const pre = container.querySelector("pre");
    await waitFor(() => {
      expect(pre?.textContent).toContain("Kavarna");
    });
  });

  it("shows error when source fetch fails", async () => {
    mockGetStorySource.mockRejectedValue(new Error("source not found"));
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.textContent).toContain("source not found");
    });
  });

  it("copies JSON to clipboard", async () => {
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="copy-json"]')).toBeTruthy();
    });
    const copyBtn = container.querySelector('[data-testid="copy-json"]')!;
    await fireEvent.click(copyBtn);

    expect(mockClipboard.writeText).toHaveBeenCalled();
    const written = mockClipboard.writeText.mock.calls[0][0];
    expect(JSON.parse(written)).toEqual(SOURCE_STORY);
  });

  it("copies Claude prompt to clipboard", async () => {
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="copy-prompt"]')).toBeTruthy();
    });
    const promptBtn = container.querySelector('[data-testid="copy-prompt"]')!;
    await fireEvent.click(promptBtn);

    expect(mockClipboard.writeText).toHaveBeenCalled();
    const written = mockClipboard.writeText.mock.calls[0][0];
    expect(written).toContain("edit this story");
    expect(written).toContain("Dober dan");
  });

  it("shows 'Copied ✓' label after copy", async () => {
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="copy-json"]')).toBeTruthy();
    });
    const copyBtn = container.querySelector('[data-testid="copy-json"]')!;
    await fireEvent.click(copyBtn);

    await waitFor(() => {
      expect(container.textContent).toContain("Copied");
    });
  });

  it("past invalid JSON shows inline error", async () => {
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector("textarea")).toBeTruthy();
    });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea!, { target: { value: "{invalid json}" } });
    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(container.textContent).toContain("Invalid JSON");
    });
  });

  it("shows 422 detail string when import fails with validation error", async () => {
    mockImportStory.mockRejectedValue(new Error("422: title must be a non-empty string"));
    const { container, getByText } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector("textarea")).toBeTruthy();
    });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea!, { target: { value: JSON.stringify(SOURCE_STORY) } });
    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(getByText(/title must be a non-empty string/)).toBeTruthy();
    });
  });

  it("import with warnings shows them and DEFERS navigation until Continue", async () => {
    // Regression: onImported used to fire immediately alongside the warnings,
    // so the page navigated away before the user could read them.
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector("textarea")).toBeTruthy();
    });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea!, { target: { value: JSON.stringify(SOURCE_STORY) } });
    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(container.textContent).toContain("speaker 'barman' is not in the sl voice map");
    });
    expect(onImported).not.toHaveBeenCalled();

    const continueBtn = container.querySelector('[data-testid="continue-btn"]')!;
    expect(continueBtn).toBeTruthy();
    await fireEvent.click(continueBtn);
    expect(onImported).toHaveBeenCalledWith("new-lesson-123");
  });

  it("editing the pasted text after a warned import returns to the Import button", async () => {
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector("textarea")).toBeTruthy();
    });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea!, { target: { value: JSON.stringify(SOURCE_STORY) } });
    await fireEvent.click(container.querySelector('[data-testid="import-btn"]')!);
    await waitFor(() => {
      expect(container.querySelector('[data-testid="continue-btn"]')).toBeTruthy();
    });

    await fireEvent.input(textarea!, { target: { value: "{}" } });
    expect(container.querySelector('[data-testid="continue-btn"]')).toBeNull();
    expect(container.querySelector('[data-testid="import-btn"]')).toBeTruthy();
  });

  it("calls onImported immediately when import succeeds without warnings", async () => {
    mockImportStory.mockResolvedValue({ ...IMPORT_RESULT, warnings: [] });
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector("textarea")).toBeTruthy();
    });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea!, { target: { value: JSON.stringify(SOURCE_STORY) } });
    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(onImported).toHaveBeenCalledWith("new-lesson-123");
    });
  });

  it("disables import button while import is loading", async () => {
    mockImportStory.mockReturnValue(new Promise(() => {}));
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector("textarea")).toBeTruthy();
    });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea!, { target: { value: JSON.stringify(SOURCE_STORY) } });
    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(
        (container.querySelector('[data-testid="import-btn"]') as HTMLButtonElement).disabled,
      ).toBe(true);
    });
  });

  it("shows 'Importing…' while import is loading", async () => {
    mockImportStory.mockReturnValue(new Promise(() => {}));
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);

    await waitFor(() => {
      expect(container.querySelector("textarea")).toBeTruthy();
    });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea!, { target: { value: JSON.stringify(SOURCE_STORY) } });
    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="import-btn"]')?.textContent).toContain(
        "Importing",
      );
    });
  });

  it("does not fetch source again when details is toggled closed and reopened", async () => {
    const { container } = render(LessonSourcePanel, { props: PROPS });
    await openPanel(container);
    await vi.waitFor(() => {
      expect(mockGetStorySource).toHaveBeenCalledTimes(1);
    });

    await openPanel(container);
    await openPanel(container);
    await vi.waitFor(() => {
      expect(mockGetStorySource).toHaveBeenCalledTimes(1);
    });
  });
});
