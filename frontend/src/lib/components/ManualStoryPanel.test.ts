import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ManualStoryPanel from "./ManualStoryPanel.svelte";

const mockClipboard = {
  writeText: vi.fn().mockResolvedValue(undefined),
};
Object.assign(navigator, { clipboard: mockClipboard });

const PROMPT_TEXT = "You are a helpful story writer.\n\nWrite a story about coffee in Slovene.";

const copyPrompt = vi.fn().mockResolvedValue(PROMPT_TEXT);
const importRaw = vi.fn().mockResolvedValue({
  id: "new-lesson-42",
  warnings: [],
});
const onImported = vi.fn();
const onDelete = vi.fn();

const PROPS = {
  copyPrompt,
  importRaw,
  onImported,
  onDelete,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockClipboard.writeText.mockResolvedValue(undefined);
});

describe("ManualStoryPanel", () => {
  it("copies story prompt to clipboard on button click", async () => {
    const { getByText } = render(ManualStoryPanel, { props: PROPS });
    const btn = getByText("Copy story prompt");
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(copyPrompt).toHaveBeenCalledOnce();
    });
    expect(mockClipboard.writeText).toHaveBeenCalledWith(PROMPT_TEXT);
  });

  it("shows Copied label after successful copy", async () => {
    const { getByText, findByText } = render(ManualStoryPanel, {
      props: PROPS,
    });
    const btn = getByText("Copy story prompt");
    await fireEvent.click(btn);

    expect(await findByText("Copied ✓")).toBeTruthy();
  });

  it("import sends raw text via importRaw prop", async () => {
    const { container } = render(ManualStoryPanel, { props: PROPS });
    const textarea = container.querySelector("textarea")!;
    const rawText = '{"title":"Test"}';
    await fireEvent.input(textarea, { target: { value: rawText } });

    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);

    await waitFor(() => {
      expect(importRaw).toHaveBeenCalledWith(rawText);
    });
  });

  it("calls onImported immediately when import succeeds without warnings", async () => {
    importRaw.mockResolvedValue({ id: "new-lesson-42", warnings: [] });

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
    importRaw.mockResolvedValue({
      id: "new-lesson-42",
      warnings: ["speaker 'unknown' is not in the sl voice map"],
    });

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

  it("import error shows error message and does not call onImported", async () => {
    importRaw.mockRejectedValue(new Error("POST /api/story/import: Unparseable JSON"));

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
    copyPrompt.mockRejectedValue(new Error("Network error"));

    const { getByText } = render(ManualStoryPanel, { props: PROPS });
    const btn = getByText("Copy story prompt");
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(getByText("Network error")).toBeTruthy();
    });
  });

  it("import failure surfaces error message", async () => {
    importRaw.mockRejectedValue(new Error("POST /api/story/import: 422"));

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
    importRaw.mockReturnValue(new Promise(() => {}));

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
    importRaw.mockReturnValue(new Promise(() => {}));

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
    it("renders a Delete this day button when onDelete is provided", () => {
      const { getByText } = render(ManualStoryPanel, { props: PROPS });
      expect(getByText("Delete this day")).toBeTruthy();
    });

    it("does not render delete button when onDelete is omitted", () => {
      const { queryByText } = render(ManualStoryPanel, {
        props: { copyPrompt, importRaw, onImported },
      });
      expect(queryByText("Delete this day")).toBeNull();
    });

    it("requires a second click to confirm before deleting", async () => {
      const { getByText } = render(ManualStoryPanel, { props: PROPS });
      await fireEvent.click(getByText("Delete this day"));
      expect(getByText("Confirm delete")).toBeTruthy();
      expect(onDelete).not.toHaveBeenCalled();
    });

    it("calls onDelete on the second click", async () => {
      const { getByText } = render(ManualStoryPanel, { props: PROPS });
      await fireEvent.click(getByText("Delete this day"));
      await fireEvent.click(getByText("Confirm delete"));

      await waitFor(() => {
        expect(onDelete).toHaveBeenCalledOnce();
      });
    });

    it("resets the confirm state on blur without deleting", async () => {
      const { getByText } = render(ManualStoryPanel, { props: PROPS });
      await fireEvent.click(getByText("Delete this day"));
      expect(getByText("Confirm delete")).toBeTruthy();

      await fireEvent.blur(getByText("Confirm delete"));
      expect(getByText("Delete this day")).toBeTruthy();
      expect(onDelete).not.toHaveBeenCalled();
    });

    it("shows an error when deletion fails", async () => {
      onDelete.mockRejectedValue(new Error("delete failed"));
      const { getByText, findByText } = render(ManualStoryPanel, {
        props: PROPS,
      });
      await fireEvent.click(getByText("Delete this day"));
      await fireEvent.click(getByText("Confirm delete"));

      expect(await findByText("delete failed")).toBeTruthy();
    });
  });
});
