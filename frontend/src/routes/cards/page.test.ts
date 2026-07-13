/**
 * Component tests for the /cards +page.svelte route.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import CardsPage from "./+page.svelte";

/** Open the row-actions overflow menu for the row whose text is `itemText`. */
async function openRowMenu(
  findByLabelText: (text: RegExp | string) => Promise<HTMLElement>,
  itemText: string,
) {
  const trigger = await findByLabelText(new RegExp(`^Actions for ${itemText}`));
  await fireEvent.click(trigger);
  return trigger;
}

vi.mock("$lib/api", () => {
  return {
    api: {
      listSRSItems: vi.fn(),
      updateSRSItem: vi.fn(),
      deleteSRSItem: vi.fn(),
      bulkDeleteSRSItems: vi.fn(),
      resetSRSItem: vi.fn(),
      suspendSRSItem: vi.fn(),
      fetchQueueStats: vi.fn(),
      fetchImageCandidates: vi.fn(),
      setItemImageFromUrl: vi.fn(),
      uploadItemImage: vi.fn(),
      removeItemImage: vi.fn(),
    },
  };
});

import { api } from "$lib/api";
const mockList = vi.mocked(api.listSRSItems);
const mockUpdate = vi.mocked(api.updateSRSItem);
const mockDelete = vi.mocked(api.deleteSRSItem);
const mockBulkDelete = vi.mocked(api.bulkDeleteSRSItems);
const mockReset = vi.mocked(api.resetSRSItem);
const mockSuspend = vi.mocked(api.suspendSRSItem);
const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);
const mockFetchImageCandidates = vi.mocked(api.fetchImageCandidates);
const mockRemoveItemImage = vi.mocked(api.removeItemImage);
import { syncStore } from "$lib/stores/sync.svelte";
import { makeSRSItemDetail } from "../../test/factories";

/** Yield to let pending microtasks (Svelte DOM updates) drain. */
function flushMicrotasks(): Promise<void> {
  return new Promise((resolve) => queueMicrotask(resolve));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  syncStore.notify(null);
  mockList.mockResolvedValue({ items: [], total: 0 });
  mockFetchQueueStats.mockResolvedValue({
    new: 0,
    learning: 0,
    review: 0,
    daily_new_cap: 20,
    cap_source: "default",
    fsrs_source: "default",
  });
  mockFetchImageCandidates.mockResolvedValue({ query: "", status: "ok", candidates: [] });
});

describe("cards/+page.svelte", () => {
  it("renders rows returned from listSRSItems", async () => {
    mockList.mockResolvedValue({
      items: [
        makeSRSItemDetail({ id: 1, text: "zdravo" }),
        makeSRSItemDetail({ id: 2, text: "hvala" }),
      ],
      total: 2,
    });
    const { findByText } = render(CardsPage);
    expect(await findByText("zdravo")).toBeTruthy();
    expect(await findByText("hvala")).toBeTruthy();
  });

  it("stale-response race: a slow earlier fetch does not overwrite a fast later fetch", async () => {
    let resolveStale: (v: unknown) => void;
    const stalePromise = new Promise((resolve) => {
      resolveStale = resolve;
    });
    // Distinct payloads so the assertion can tell which fetch won.
    const staleItems = {
      items: [makeSRSItemDetail({ id: 1, text: "STALE-DATA" })],
      total: 1,
    };
    const freshItems = {
      items: [makeSRSItemDetail({ id: 2, text: "hvala" })],
      total: 1,
    };
    // Earlier fetch is slow (stale); the later fetch resolves immediately (fresh).
    mockList
      .mockReturnValueOnce(stalePromise as ReturnType<typeof api.listSRSItems>)
      .mockReturnValueOnce(Promise.resolve(freshItems));

    const { findByText, queryByText } = render(CardsPage);
    await vi.waitFor(() => {
      expect(mockList).toHaveBeenCalledTimes(2);
    });
    // The later (fresh) fetch lands first and renders.
    await findByText("hvala");

    // The earlier (stale) fetch resolves LAST, with different data — the
    // sequence-token guard must discard it rather than clobber the fresh rows.
    resolveStale!(staleItems);
    // Fully drain the stale resolution (promise chain + Svelte DOM flush) so
    // that IF the guard were missing, STALE-DATA would have rendered by now.
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    await flushMicrotasks();
    expect(queryByText("STALE-DATA")).toBeNull();
    expect(await findByText("hvala")).toBeTruthy();
  });

  it("formats due_at as a short human-readable date (no raw ISO)", async () => {
    mockList.mockResolvedValue({
      items: [
        makeSRSItemDetail({
          id: 1,
          text: "Bog",
          due_at: "2026-09-15T04:00:00+00:00",
        }),
      ],
      total: 1,
    });
    const { findByText, queryByText } = render(CardsPage);
    await findByText("Bog");
    // Raw ISO must NOT appear in the rendered output
    expect(queryByText(/2026-09-15T04:00:00/)).toBeFalsy();
    // A short formatted date should appear (e.g. "Sep 15, 2026" in en-US)
    expect(await findByText(/Sep\s*1[45],?\s*2026/)).toBeTruthy();
  });

  it("shows empty string when due_at is empty string", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "empty_due", due_at: "" })],
      total: 1,
    });
    const { findByText, container } = render(CardsPage);
    await findByText("empty_due");
    // Empty due should not render any obvious text for the due column
    expect(container.textContent).not.toContain("Invalid Date");
  });

  it("shows raw string when due_at is an invalid date", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "bad_date", due_at: "not-a-date" })],
      total: 1,
    });
    const { findByText } = render(CardsPage);
    await findByText("bad_date");
    expect(await findByText("not-a-date")).toBeTruthy();
  });

  it("strips [sound:...] tags from displayed text and translation", async () => {
    mockList.mockResolvedValue({
      items: [
        makeSRSItemDetail({
          id: 1,
          text: "[sound:sl_zdravo.mp3]zdravo",
          translation: "[sound:x.mp3]Hello",
        }),
      ],
      total: 1,
    });
    const { findByText, queryByText } = render(CardsPage);
    expect(await findByText("Hello")).toBeTruthy();
    expect(await findByText("zdravo")).toBeTruthy();
    expect(queryByText(/\[sound:/)).toBeFalsy();
  });

  it("uses 'Search cards' as the search input placeholder", () => {
    const { getByPlaceholderText } = render(CardsPage);
    expect(getByPlaceholderText("Search cards")).toBeTruthy();
  });

  it("typing in search re-queries after debounce", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "zdravo" })],
      total: 1,
    });
    const { getByPlaceholderText } = render(CardsPage);
    const input = getByPlaceholderText(/Search/);

    await fireEvent.input(input, { target: { value: "zdr" } });
    // Should not have re-queried yet
    const callCount = mockList.mock.calls.length;

    // Advance debounce timer
    vi.runAllTimers();
    await waitFor(() => {
      expect(mockList.mock.calls.length).toBeGreaterThan(callCount);
    });
  });

  it("clicking column header flips sort order", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" })],
      total: 1,
    });
    const { findByText } = render(CardsPage);

    // Wait for initial load
    await findByText("a");

    const callsBefore = mockList.mock.calls.length;
    const textHeader = await findByText(/^text/);
    await fireEvent.click(textHeader);

    await waitFor(() => {
      expect(mockList.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });

  it("clicking Edit, changing inputs, clicking Save calls updateSRSItem", async () => {
    const item = makeSRSItemDetail({
      id: 42,
      text: "zdravo",
      translation: "trans_zdravo",
    });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockUpdate.mockResolvedValue({
      ...item,
      text: "Zdravo!",
      translation: "Hello!",
    });

    const { findByText, findByLabelText, getAllByRole } = render(CardsPage);
    await findByText("zdravo");

    await openRowMenu(findByLabelText, "zdravo");
    const editBtn = await findByText("Edit");
    await fireEvent.click(editBtn);

    const inputs = getAllByRole("textbox") as HTMLInputElement[];
    const textInput = inputs.find((i) => i.value === "zdravo")!;
    const transInput = inputs.find((i) => i.value === "trans_zdravo")!;

    await fireEvent.input(textInput, { target: { value: "Zdravo!" } });
    await fireEvent.input(transInput, { target: { value: "Hello!" } });

    const saveBtn = await findByText("Save");
    await fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith(42, {
        text: "Zdravo!",
        translation: "Hello!",
      });
    });
  });

  it("selecting two rows and clicking Bulk delete calls bulkDeleteSRSItems", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" }), makeSRSItemDetail({ id: 2, text: "b" })],
      total: 2,
    });
    mockBulkDelete.mockResolvedValue({ deleted: 2 });
    vi.stubGlobal("confirm", () => true);

    const { findAllByRole, findByText } = render(CardsPage);
    await findByText("a");

    const allCheckboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    // [0]=select-all header, [1+]=item rows
    const itemCheckboxes = allCheckboxes.slice(1);
    await fireEvent.click(itemCheckboxes[0]);
    await fireEvent.click(itemCheckboxes[1]);

    const bulkBtn = await findByText(/Delete selected/);
    await fireEvent.click(bulkBtn);

    await waitFor(() => {
      expect(mockBulkDelete).toHaveBeenCalledWith([1, 2]);
    });
  });

  it("clicking Delete with confirm stubbed calls deleteSRSItem", async () => {
    const item = makeSRSItemDetail({ id: 7, text: "lep" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockDelete.mockResolvedValue({ status: "deleted" });
    vi.stubGlobal("confirm", () => true);

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("lep");

    await openRowMenu(findByLabelText, "lep");
    const deleteBtn = await findByText("Delete");
    await fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(mockDelete).toHaveBeenCalledWith(7);
    });
  });

  it("clicking Suspend on a review-state row calls suspendSRSItem(id, true)", async () => {
    const item = makeSRSItemDetail({ id: 9, text: "lep", state: "review" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockSuspend.mockResolvedValue({ ...item, state: "suspended" });

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("lep");

    await openRowMenu(findByLabelText, "lep");
    const suspendBtn = await findByText("Suspend");
    await fireEvent.click(suspendBtn);

    await waitFor(() => {
      expect(mockSuspend).toHaveBeenCalledWith(9, true);
    });
  });

  it("clicking Reset with confirm stubbed calls resetSRSItem", async () => {
    const item = makeSRSItemDetail({ id: 11, text: "kava", state: "review" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockReset.mockResolvedValue({ ...item, state: "new", reps: 0 });
    vi.stubGlobal("confirm", () => true);

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("kava");

    await openRowMenu(findByLabelText, "kava");
    const resetBtn = await findByText("Reset");
    await fireEvent.click(resetBtn);

    await waitFor(() => {
      expect(mockReset).toHaveBeenCalledWith(11);
    });
  });

  it("shows error when resetSRSItem fails", async () => {
    const item = makeSRSItemDetail({ id: 11, text: "kava", state: "review" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockReset.mockRejectedValue(new Error("reset failed"));
    vi.stubGlobal("confirm", () => true);

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("kava");

    await openRowMenu(findByLabelText, "kava");
    await fireEvent.click(await findByText("Reset"));

    expect(await findByText("reset failed")).toBeTruthy();
  });

  it("shows error when toggleSuspend fails", async () => {
    const item = makeSRSItemDetail({ id: 12, text: "voda", state: "review" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockSuspend.mockRejectedValue(new Error("suspend failed"));

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("voda");

    await openRowMenu(findByLabelText, "voda");
    await fireEvent.click(await findByText("Suspend"));

    expect(await findByText("suspend failed")).toBeTruthy();
  });

  it("clicking Cancel during edit closes the edit row without saving", async () => {
    const item = makeSRSItemDetail({ id: 5, text: "miza" });
    mockList.mockResolvedValue({ items: [item], total: 1 });

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("miza");

    await openRowMenu(findByLabelText, "miza");
    await fireEvent.click(await findByText("Edit"));
    // Edit row should be open (Save/Cancel visible)
    expect(await findByText("Cancel")).toBeTruthy();

    await fireEvent.click(await findByText("Cancel"));

    // Normal row should reappear
    expect(await findByText("miza")).toBeTruthy();
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("clicking header checkbox when nothing is selected selects all items", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" }), makeSRSItemDetail({ id: 2, text: "b" })],
      total: 2,
    });

    const { findAllByRole, findByText } = render(CardsPage);
    await findByText("a");

    // Header checkbox is the first checkbox ([0]) now that the cloze flag is gone
    const allCheckboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    const headerCheckbox = allCheckboxes[0];

    await fireEvent.click(headerCheckbox);

    // "Delete selected (2)" button should appear
    expect(await findByText(/Delete selected \(2\)/)).toBeTruthy();
  });

  it("shows error when saveEdit fails with non-Error", async () => {
    const item = makeSRSItemDetail({ id: 15, text: "vino" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockUpdate.mockRejectedValue("plain update error");

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("vino");

    await openRowMenu(findByLabelText, "vino");
    await fireEvent.click(await findByText("Edit"));
    await fireEvent.click(await findByText("Save"));

    expect(await findByText("plain update error")).toBeTruthy();
  });

  it("shows error when deleteItem fails with non-Error", async () => {
    const item = makeSRSItemDetail({ id: 16, text: "sir" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockDelete.mockRejectedValue("plain delete error");
    vi.stubGlobal("confirm", () => true);

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("sir");

    await openRowMenu(findByLabelText, "sir");
    await fireEvent.click(await findByText("Delete"));

    expect(await findByText("plain delete error")).toBeTruthy();
  });

  it("shows error.message when listSRSItems rejects with an Error instance", async () => {
    // Covers the truthy branch of `e instanceof Error ? e.message : String(e)` in loadItems.
    mockList.mockRejectedValue(new Error("list failed (Error instance)"));

    const { findByText } = render(CardsPage);

    expect(await findByText("list failed (Error instance)")).toBeTruthy();
  });

  it("shows error.message when saveEdit rejects with an Error instance", async () => {
    // Covers the truthy branch of `e instanceof Error ? e.message : String(e)` in saveEdit.
    const item = makeSRSItemDetail({ id: 21, text: "voda" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockUpdate.mockRejectedValue(new Error("save failed (Error instance)"));

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("voda");

    await openRowMenu(findByLabelText, "voda");
    await fireEvent.click(await findByText("Edit"));
    await fireEvent.click(await findByText("Save"));

    expect(await findByText("save failed (Error instance)")).toBeTruthy();
  });

  it("shows error.message when deleteSRSItem rejects with an Error instance", async () => {
    // Covers the truthy branch of `e instanceof Error ? e.message : String(e)` in deleteItem.
    const item = makeSRSItemDetail({ id: 22, text: "sol" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockDelete.mockRejectedValue(new Error("delete failed (Error instance)"));
    vi.stubGlobal("confirm", () => true);

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("sol");

    await openRowMenu(findByLabelText, "sol");
    await fireEvent.click(await findByText("Delete"));

    expect(await findByText("delete failed (Error instance)")).toBeTruthy();
  });

  it("Delete does nothing when user cancels the confirm dialog", async () => {
    // Covers `if (!confirm('Delete this item?')) return;` early-return in deleteItem.
    const item = makeSRSItemDetail({ id: 30, text: "mleko" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    vi.stubGlobal("confirm", () => false);

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("mleko");

    await openRowMenu(findByLabelText, "mleko");
    await fireEvent.click(await findByText("Delete"));
    await flushMicrotasks();

    expect(mockDelete).not.toHaveBeenCalled();
  });

  it("Reset does nothing when user cancels the confirm dialog", async () => {
    // Covers `if (!confirm('Reset this item to new state?')) return;` in resetItem.
    const item = makeSRSItemDetail({ id: 31, text: "čaj", state: "review" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    vi.stubGlobal("confirm", () => false);

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("čaj");

    await openRowMenu(findByLabelText, "čaj");
    await fireEvent.click(await findByText("Reset"));
    await flushMicrotasks();

    expect(mockReset).not.toHaveBeenCalled();
  });

  it("Bulk delete does nothing when user cancels the confirm dialog", async () => {
    // Covers `if (!confirm(...)) return;` early-return in bulkDelete.
    const item = makeSRSItemDetail({ id: 32, text: "kruh" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    vi.stubGlobal("confirm", () => false);

    const { findAllByRole, findByText } = render(CardsPage);
    await findByText("kruh");

    // [0]=select-all header, [1+]=item rows
    const checkboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    await fireEvent.click(checkboxes[1]);

    await fireEvent.click(await findByText(/Delete selected/));
    await flushMicrotasks();

    expect(mockBulkDelete).not.toHaveBeenCalled();
  });

  it("toggling a row checkbox twice deselects it (covers Set.delete branch)", async () => {
    // Covers `if (next.has(id)) next.delete(id); else next.add(id);` toggle in toggleSelect.
    const item = makeSRSItemDetail({ id: 40, text: "sok" });
    mockList.mockResolvedValue({ items: [item], total: 1 });

    const { findAllByRole, findByText } = render(CardsPage);
    await findByText("sok");

    // [0]=select-all header, [1+]=item rows
    const checkboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    const itemBox = checkboxes[1];
    // First click: add to selection
    await fireEvent.click(itemBox);
    expect(itemBox.checked).toBe(true);
    // Second click: delete from selection (the uncovered branch)
    await fireEvent.click(itemBox);
    expect(itemBox.checked).toBe(false);
  });

  it("shows Unsuspend button for a suspended item and calls suspendSRSItem(id, false)", async () => {
    const item = makeSRSItemDetail({
      id: 20,
      text: "kava",
      state: "suspended",
    });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockSuspend.mockResolvedValue({ ...item, state: "new" });

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("kava");

    await openRowMenu(findByLabelText, "kava");
    const unsuspendBtn = await findByText("Unsuspend");
    await fireEvent.click(unsuspendBtn);

    await waitFor(() => {
      expect(mockSuspend).toHaveBeenCalledWith(20, false);
    });
  });

  it("clicking same sort column twice flips order from asc to desc then back to asc", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" })],
      total: 1,
    });

    const { findByText } = render(CardsPage);
    await findByText("a");

    const textHeader = await findByText(/^text/);

    // First click: asc → desc
    const callsBefore1 = mockList.mock.calls.length;
    await fireEvent.click(textHeader);
    await waitFor(() => {
      expect(mockList.mock.calls.length).toBeGreaterThan(callsBefore1);
    });

    // Second click: desc → asc
    const callsBefore2 = mockList.mock.calls.length;
    await fireEvent.click(textHeader);
    await waitFor(() => {
      expect(mockList.mock.calls.length).toBeGreaterThan(callsBefore2);
      const lastCall = mockList.mock.calls[mockList.mock.calls.length - 1][0];
      expect(lastCall?.order).toBe("asc");
    });
  });

  it("clicking a different sort column changes sort to that column", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" })],
      total: 1,
    });

    const { findByText } = render(CardsPage);
    await findByText("a");

    const callsBefore = mockList.mock.calls.length;
    const translationHeader = await findByText(/^translation/);
    await fireEvent.click(translationHeader);

    await waitFor(() => {
      expect(mockList.mock.calls.length).toBeGreaterThan(callsBefore);
      const lastCall = mockList.mock.calls[mockList.mock.calls.length - 1][0];
      expect(lastCall?.sort).toBe("translation");
      expect(lastCall?.order).toBe("asc");
    });
  });

  it("changing state filter triggers reload with state param", async () => {
    mockList.mockResolvedValue({ items: [], total: 0 });

    const { findByDisplayValue } = render(CardsPage);
    const select = await findByDisplayValue("All states");

    await fireEvent.change(select, { target: { value: "review" } });

    await waitFor(() => {
      const calls = mockList.mock.calls;
      const lastCall = calls[calls.length - 1][0];
      expect(lastCall?.state).toBe("review");
    });
  });

  it("shows error when bulkDeleteSRSItems fails", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" }), makeSRSItemDetail({ id: 2, text: "b" })],
      total: 2,
    });
    mockBulkDelete.mockRejectedValue(new Error("bulk delete failed"));
    vi.stubGlobal("confirm", () => true);

    const { findAllByRole, findByText } = render(CardsPage);
    await findByText("a");

    const checkboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    // [0]=select-all header, [1+]=item rows
    await fireEvent.click(checkboxes[1]);
    await fireEvent.click(checkboxes[2]);

    await fireEvent.click(await findByText(/Delete selected/));

    expect(await findByText("bulk delete failed")).toBeTruthy();
  });

  it("shows stringified error when listSRSItems throws a non-Error", async () => {
    mockList.mockRejectedValue("network failure string");
    const { findByText } = render(CardsPage);
    expect(await findByText("network failure string")).toBeTruthy();
  });

  it("clicking header checkbox when all items are selected deselects all", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" }), makeSRSItemDetail({ id: 2, text: "b" })],
      total: 2,
    });

    const { findByText, findAllByRole, queryByText } = render(CardsPage);
    await findByText("a");

    const checkboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    // [0]=select-all header, [1+]=item rows
    // Select both items individually
    await fireEvent.click(checkboxes[1]);
    await fireEvent.click(checkboxes[2]);

    // Verify "Delete selected" is visible (all selected)
    expect(await findByText(/Delete selected \(2\)/)).toBeTruthy();

    // Click header checkbox to deselect all
    await fireEvent.click(checkboxes[0]);

    await waitFor(() => {
      expect(queryByText(/Delete selected/)).toBeFalsy();
    });
  });

  it("clicking next/prev pagination changes the page", async () => {
    // total > PAGE_SIZE (50) to enable next button
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" })],
      total: 100,
    });

    const { findByText } = render(CardsPage);
    await findByText("page 1 / 2");

    await fireEvent.click(await findByText("next ▶"));

    await waitFor(async () => {
      expect(await findByText("page 2 / 2")).toBeTruthy();
    });

    await fireEvent.click(await findByText("◀ prev"));

    await waitFor(async () => {
      expect(await findByText("page 1 / 2")).toBeTruthy();
    });
  });

  it("clicking state, due, and reps sort columns each trigger a reload", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" })],
      total: 1,
    });

    const { findByText } = render(CardsPage);
    await findByText("a");

    for (const col of ["state", "due", "reps"]) {
      const callsBefore = mockList.mock.calls.length;
      await fireEvent.click(await findByText(new RegExp(`^${col}`)));
      await waitFor(() => {
        expect(mockList.mock.calls.length).toBeGreaterThan(callsBefore);
      });
    }
  });

  // ── Sync via store notification ──────────────────────────────────────────

  const PEER_RESULT = {
    auth_success: true,
    pull_required: 0,
    push_required: 1,
    tt_push_pull_exit: 0,
    dry_run: false,
  };

  it("shows synced status after a successful peer sync", async () => {
    const { findByText } = render(CardsPage);
    syncStore.notify(PEER_RESULT);
    expect(await findByText("Synced with AnkiWeb")).toBeTruthy();
  });

  it("reloads items after a successful peer sync", async () => {
    const { findByText } = render(CardsPage);
    await findByText(/0 total/);
    const callsBefore = mockList.mock.calls.length;
    syncStore.notify(PEER_RESULT);
    await waitFor(() => {
      expect(mockList.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });

  // ── queue-stats toolbar line ──────────────────────────────────────────────

  it("shows new, learning, and review counts in toolbar after stats load", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 12,
      learning: 8,
      review: 39,
      daily_new_cap: 30,
      cap_source: "cache",
      fsrs_source: "cache",
    });
    const { findByText } = render(CardsPage);
    expect(await findByText(/12 new/)).toBeTruthy();
    expect(await findByText(/8 learning/)).toBeTruthy();
    expect(await findByText(/39 review/)).toBeTruthy();
  });

  it("renders without stats line when fetchQueueStats rejects", async () => {
    mockFetchQueueStats.mockRejectedValue(new Error("AnkiConnect down"));
    const { findByText, queryByText } = render(CardsPage);
    // Page still loads items fine
    await findByText(/0 total/);
    // No "X new · Y due today" stats line should appear
    expect(queryByText(/\d+ new · \d+ due today/)).toBeFalsy();
  });

  // ── row-actions overflow menu ──────────────────────────────────────────────

  describe("row actions overflow menu", () => {
    it("renders a single '⋯' actions trigger per row instead of four buttons", async () => {
      const item = makeSRSItemDetail({ id: 1, text: "zdravo" });
      mockList.mockResolvedValue({ items: [item], total: 1 });

      const { findByText, queryByText, findByLabelText } = render(CardsPage);
      await findByText("zdravo");

      expect(await findByLabelText("Actions for zdravo")).toBeTruthy();
      // The four actions are no longer directly visible as row buttons
      expect(queryByText("Edit")).toBeFalsy();
      expect(queryByText("Reset")).toBeFalsy();
      expect(queryByText("Suspend")).toBeFalsy();
      expect(queryByText("Delete")).toBeFalsy();
    });

    it("clicking the trigger opens a menu with aria-expanded=true and the four actions", async () => {
      const item = makeSRSItemDetail({
        id: 1,
        text: "zdravo",
        state: "review",
      });
      mockList.mockResolvedValue({ items: [item], total: 1 });

      const { findByText, findByLabelText, getByRole } = render(CardsPage);
      await findByText("zdravo");

      const trigger = await findByLabelText("Actions for zdravo");
      expect(trigger.getAttribute("aria-haspopup")).toBe("menu");
      expect(trigger.getAttribute("aria-expanded")).toBe("false");

      await fireEvent.click(trigger);

      expect(trigger.getAttribute("aria-expanded")).toBe("true");
      const menu = getByRole("menu");
      expect(menu).toBeTruthy();
      expect(await findByText("Edit")).toBeTruthy();
      expect(await findByText("Reset")).toBeTruthy();
      expect(await findByText("Suspend")).toBeTruthy();
      expect(await findByText("Delete")).toBeTruthy();
    });

    it("clicking the trigger again closes the menu (toggle)", async () => {
      const item = makeSRSItemDetail({ id: 1, text: "zdravo" });
      mockList.mockResolvedValue({ items: [item], total: 1 });

      const { findByText, findByLabelText, queryByRole } = render(CardsPage);
      await findByText("zdravo");

      const trigger = await findByLabelText("Actions for zdravo");
      await fireEvent.click(trigger);
      expect(queryByRole("menu")).toBeTruthy();

      await fireEvent.click(trigger);
      expect(queryByRole("menu")).toBeFalsy();
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
    });

    it("pressing Escape closes the open menu", async () => {
      const item = makeSRSItemDetail({ id: 1, text: "zdravo" });
      mockList.mockResolvedValue({ items: [item], total: 1 });

      const { findByText, findByLabelText, queryByRole } = render(CardsPage);
      await findByText("zdravo");

      const trigger = await findByLabelText("Actions for zdravo");
      await fireEvent.click(trigger);
      expect(queryByRole("menu")).toBeTruthy();

      await fireEvent.keyDown(document, { key: "Escape" });

      expect(queryByRole("menu")).toBeFalsy();
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
    });

    it("clicking outside the menu closes it", async () => {
      const item = makeSRSItemDetail({ id: 1, text: "zdravo" });
      mockList.mockResolvedValue({ items: [item], total: 1 });

      const { findByText, findByLabelText, queryByRole } = render(CardsPage);
      await findByText("zdravo");

      const trigger = await findByLabelText("Actions for zdravo");
      await fireEvent.click(trigger);
      expect(queryByRole("menu")).toBeTruthy();

      await fireEvent.click(document.body);

      expect(queryByRole("menu")).toBeFalsy();
    });

    it("opening another row's menu closes the first row's menu", async () => {
      mockList.mockResolvedValue({
        items: [
          makeSRSItemDetail({ id: 1, text: "zdravo" }),
          makeSRSItemDetail({ id: 2, text: "hvala" }),
        ],
        total: 2,
      });

      const { findByText, findByLabelText, queryByRole, getAllByRole } = render(CardsPage);
      await findByText("zdravo");

      const triggerA = await findByLabelText("Actions for zdravo");
      const triggerB = await findByLabelText("Actions for hvala");

      await fireEvent.click(triggerA);
      expect(queryByRole("menu")).toBeTruthy();
      expect(triggerA.getAttribute("aria-expanded")).toBe("true");

      await fireEvent.click(triggerB);

      // Only one menu should be open at a time
      expect(getAllByRole("menu")).toHaveLength(1);
      expect(triggerA.getAttribute("aria-expanded")).toBe("false");
      expect(triggerB.getAttribute("aria-expanded")).toBe("true");
    });

    it("selecting Edit from the menu closes the menu and enters edit mode", async () => {
      const item = makeSRSItemDetail({
        id: 1,
        text: "zdravo",
        translation: "hello",
      });
      mockList.mockResolvedValue({ items: [item], total: 1 });

      const { findByText, findByLabelText, queryByRole } = render(CardsPage);
      await findByText("zdravo");

      await openRowMenu(findByLabelText, "zdravo");
      await fireEvent.click(await findByText("Edit"));

      // Menu is closed once edit mode is entered
      expect(queryByRole("menu")).toBeFalsy();
      // Edit row is open (Save/Cancel visible)
      expect(await findByText("Save")).toBeTruthy();
      expect(await findByText("Cancel")).toBeTruthy();
    });

    it("Delete menu item is styled with the danger color and separated by a divider", async () => {
      const item = makeSRSItemDetail({ id: 1, text: "zdravo" });
      mockList.mockResolvedValue({ items: [item], total: 1 });

      const { findByText, findByLabelText } = render(CardsPage);
      await findByText("zdravo");

      await openRowMenu(findByLabelText, "zdravo");
      const deleteItem = await findByText("Delete");
      expect(deleteItem.className).toContain("danger");

      // A divider element should precede the danger item within the menu
      const menu = deleteItem.closest('[role="menu"]')!;
      expect(menu.querySelector(".menu-divider")).toBeTruthy();
    });

    it("menu items are real buttons with role=menuitem", async () => {
      const item = makeSRSItemDetail({
        id: 1,
        text: "zdravo",
        state: "review",
      });
      mockList.mockResolvedValue({ items: [item], total: 1 });

      const { findByText, findByLabelText, getAllByRole } = render(CardsPage);
      await findByText("zdravo");

      await openRowMenu(findByLabelText, "zdravo");
      const menuItems = getAllByRole("menuitem");
      expect(menuItems).toHaveLength(5);
      for (const mi of menuItems) {
        expect(mi.tagName).toBe("BUTTON");
      }
    });
  });

  // ── image column ──────────────────────────────────────────────────────

  it("renders thumbnail for items with image_url", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "zdravo", image_url: "/api/media/zdravo.jpg" })],
      total: 1,
    });
    const { findByText } = render(CardsPage);
    await findByText("zdravo");
    const imgs = document.querySelectorAll(".thumb-btn img");
    expect(imgs.length).toBe(1);
    expect((imgs[0] as HTMLImageElement).src).toContain("/api/media/zdravo.jpg");
  });

  it("no thumbnail for items without image_url", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "zdravo", image_url: null })],
      total: 1,
    });
    const { findByText } = render(CardsPage);
    await findByText("zdravo");
    expect(document.querySelectorAll(".thumb-btn img").length).toBe(0);
    expect(document.querySelectorAll(".thumb-empty").length).toBe(1);
  });

  it("Change image menu item opens modal", async () => {
    mockFetchImageCandidates.mockResolvedValue({ query: "", status: "ok", candidates: [] });
    const item = makeSRSItemDetail({ id: 1, text: "zdravo" });
    mockList.mockResolvedValue({ items: [item], total: 1 });

    const { findByText, findByLabelText } = render(CardsPage);
    await findByText("zdravo");

    await openRowMenu(findByLabelText, "zdravo");
    await fireEvent.click(await findByText("Change image…"));

    expect(await findByText("Edit Image")).toBeTruthy();
  });

  it("clicking thumbnail with image_url opens modal", async () => {
    mockFetchImageCandidates.mockResolvedValue({ query: "", status: "ok", candidates: [] });
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "zdravo", image_url: "/api/media/zdravo.jpg" })],
      total: 1,
    });
    const { findByText } = render(CardsPage);
    await findByText("zdravo");
    const thumb = document.querySelector(".thumb-btn") as HTMLButtonElement;
    await fireEvent.click(thumb);
    expect(await findByText("Edit Image")).toBeTruthy();
  });

  it("clicking empty thumbnail opens modal", async () => {
    mockFetchImageCandidates.mockResolvedValue({ query: "", status: "ok", candidates: [] });
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "zdravo", image_url: null })],
      total: 1,
    });
    const { findByText } = render(CardsPage);
    await findByText("zdravo");
    const thumb = document.querySelector(".thumb-empty") as HTMLButtonElement;
    await fireEvent.click(thumb);
    expect(await findByText("Edit Image")).toBeTruthy();
  });

  it("modal onupdated closes modal and reloads items", async () => {
    const item = makeSRSItemDetail({ id: 1, text: "zdravo", image_url: "/img/zdravo.jpg" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockFetchImageCandidates.mockResolvedValue({ query: "", status: "ok", candidates: [] });
    mockRemoveItemImage.mockResolvedValue(makeSRSItemDetail({ id: 1, image_url: null }));
    const { findByText, queryByText } = render(CardsPage);
    await findByText("zdravo");

    const thumb = document.querySelector(".thumb-btn") as HTMLButtonElement;
    await fireEvent.click(thumb);
    await findByText("Edit Image");

    const removeBtn = await findByText("Remove");
    await fireEvent.click(removeBtn);

    await waitFor(() => {
      expect(mockRemoveItemImage).toHaveBeenCalledWith(1);
      expect(queryByText("Edit Image")).toBeNull();
    });
    expect(mockList.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("pressing Escape closes the image modal and clears imageEditItem", async () => {
    mockFetchImageCandidates.mockResolvedValue({ query: "", status: "ok", candidates: [] });
    const item = makeSRSItemDetail({ id: 1, text: "zdravo", image_url: "/img/zdravo.jpg" });
    mockList.mockResolvedValue({ items: [item], total: 1 });

    const { findByText, queryByText } = render(CardsPage);
    await findByText("zdravo");

    const thumb = document.querySelector(".thumb-btn") as HTMLButtonElement;
    await fireEvent.click(thumb);
    await findByText("Edit Image");

    const backdrop = document.querySelector(".backdrop")!;
    await fireEvent.keyDown(backdrop, { key: "Escape" });

    await waitFor(() => {
      expect(queryByText("Edit Image")).toBeNull();
    });
  });
});
