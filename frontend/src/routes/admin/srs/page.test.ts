/**
 * Component tests for the /admin/srs +page.svelte route.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import AdminSRSPage from "./+page.svelte";

vi.mock("$lib/api", () => {
  const requestMock = vi.fn().mockResolvedValue({ enabled: false });
  return {
    api: {
      request: requestMock,
      getClozeSetting: vi.fn(async function (this: { request: typeof requestMock }) {
        return this.request("/api/srs/settings/cloze");
      }),
      setClozeSetting: vi.fn(async function (
        this: { request: typeof requestMock },
        enabled: boolean,
      ) {
        return this.request("/api/srs/settings/cloze", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        });
      }),
      listSRSItems: vi.fn(),
      updateSRSItem: vi.fn(),
      deleteSRSItem: vi.fn(),
      bulkDeleteSRSItems: vi.fn(),
      resetSRSItem: vi.fn(),
      suspendSRSItem: vi.fn(),
      syncWithAnki: vi.fn(),
      fetchQueueStats: vi.fn(),
      fetchAnkiStatus: vi.fn(),
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
const mockSyncWithAnki = vi.mocked(api.syncWithAnki);
const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);
const mockFetchAnkiStatus = vi.mocked(api.fetchAnkiStatus);
import { makeSRSItemDetail } from "../../../test/factories";

/** Yield to let pending microtasks (Svelte DOM updates) drain. */
function flushMicrotasks(): Promise<void> {
  return new Promise((resolve) => queueMicrotask(resolve));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  mockList.mockResolvedValue({ items: [], total: 0 });
  mockFetchQueueStats.mockResolvedValue({
    new: 0,
    learning: 0,
    review: 0,
    daily_new_cap: 20,
    cap_source: "default",
    fsrs_source: "default",
  });
  mockFetchAnkiStatus.mockResolvedValue({ anki_running: false, lock_acquirable: true });
});

describe("admin/srs/+page.svelte", () => {
  it("renders rows returned from listSRSItems", async () => {
    mockList.mockResolvedValue({
      items: [
        makeSRSItemDetail({ id: 1, text: "zdravo" }),
        makeSRSItemDetail({ id: 2, text: "hvala" }),
      ],
      total: 2,
    });
    const { findByText } = render(AdminSRSPage);
    expect(await findByText("zdravo")).toBeTruthy();
    expect(await findByText("hvala")).toBeTruthy();
  });

  it("formats due_at as a short human-readable date (no raw ISO)", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "Bog", due_at: "2026-09-15T04:00:00+00:00" })],
      total: 1,
    });
    const { findByText, queryByText } = render(AdminSRSPage);
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
    const { findByText, container } = render(AdminSRSPage);
    await findByText("empty_due");
    // Empty due should not render any obvious text for the due column
    expect(container.textContent).not.toContain("Invalid Date");
  });

  it("shows raw string when due_at is an invalid date", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "bad_date", due_at: "not-a-date" })],
      total: 1,
    });
    const { findByText } = render(AdminSRSPage);
    await findByText("bad_date");
    expect(await findByText("not-a-date")).toBeTruthy();
  });

  it("typing in search re-queries after debounce", async () => {
    mockList.mockResolvedValue({ items: [makeSRSItemDetail({ id: 1, text: "zdravo" })], total: 1 });
    const { getByPlaceholderText } = render(AdminSRSPage);
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
    mockList.mockResolvedValue({ items: [makeSRSItemDetail({ id: 1, text: "a" })], total: 1 });
    const { findByText } = render(AdminSRSPage);

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
    const item = makeSRSItemDetail({ id: 42, text: "zdravo", translation: "trans_zdravo" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockUpdate.mockResolvedValue({ ...item, text: "Zdravo!", translation: "Hello!" });

    const { findByText, getAllByRole } = render(AdminSRSPage);
    await findByText("zdravo");

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
      expect(mockUpdate).toHaveBeenCalledWith(42, { text: "Zdravo!", translation: "Hello!" });
    });
  });

  it("selecting two rows and clicking Bulk delete calls bulkDeleteSRSItems", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" }), makeSRSItemDetail({ id: 2, text: "b" })],
      total: 2,
    });
    mockBulkDelete.mockResolvedValue({ deleted: 2 });
    vi.stubGlobal("confirm", () => true);

    const { findAllByRole, findByText } = render(AdminSRSPage);
    await findByText("a");

    const allCheckboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    // [0]=cloze flag, [1]=select-all header, [2+]=item rows
    const itemCheckboxes = allCheckboxes.slice(2);
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

    const { findByText } = render(AdminSRSPage);
    await findByText("lep");

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

    const { findByText } = render(AdminSRSPage);
    await findByText("lep");

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

    const { findByText } = render(AdminSRSPage);
    await findByText("kava");

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

    const { findByText } = render(AdminSRSPage);
    await findByText("kava");

    await fireEvent.click(await findByText("Reset"));

    expect(await findByText("reset failed")).toBeTruthy();
  });

  it("shows error when toggleSuspend fails", async () => {
    const item = makeSRSItemDetail({ id: 12, text: "voda", state: "review" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockSuspend.mockRejectedValue(new Error("suspend failed"));

    const { findByText } = render(AdminSRSPage);
    await findByText("voda");

    await fireEvent.click(await findByText("Suspend"));

    expect(await findByText("suspend failed")).toBeTruthy();
  });

  it("clicking Cancel during edit closes the edit row without saving", async () => {
    const item = makeSRSItemDetail({ id: 5, text: "miza" });
    mockList.mockResolvedValue({ items: [item], total: 1 });

    const { findByText } = render(AdminSRSPage);
    await findByText("miza");

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

    const { findAllByRole, findByText } = render(AdminSRSPage);
    await findByText("a");

    // Header checkbox is the second checkbox ([1]); [0]=cloze flag
    const allCheckboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    const headerCheckbox = allCheckboxes[1];

    await fireEvent.click(headerCheckbox);

    // "Delete selected (2)" button should appear
    expect(await findByText(/Delete selected \(2\)/)).toBeTruthy();
  });

  it("shows error when saveEdit fails with non-Error", async () => {
    const item = makeSRSItemDetail({ id: 15, text: "vino" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockUpdate.mockRejectedValue("plain update error");

    const { findByText } = render(AdminSRSPage);
    await findByText("vino");

    await fireEvent.click(await findByText("Edit"));
    await fireEvent.click(await findByText("Save"));

    expect(await findByText("plain update error")).toBeTruthy();
  });

  it("shows error when deleteItem fails with non-Error", async () => {
    const item = makeSRSItemDetail({ id: 16, text: "sir" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockDelete.mockRejectedValue("plain delete error");
    vi.stubGlobal("confirm", () => true);

    const { findByText } = render(AdminSRSPage);
    await findByText("sir");

    await fireEvent.click(await findByText("Delete"));

    expect(await findByText("plain delete error")).toBeTruthy();
  });

  it("shows Unsuspend button for a suspended item and calls suspendSRSItem(id, false)", async () => {
    const item = makeSRSItemDetail({ id: 20, text: "kava", state: "suspended" });
    mockList.mockResolvedValue({ items: [item], total: 1 });
    mockSuspend.mockResolvedValue({ ...item, state: "new" });

    const { findByText } = render(AdminSRSPage);
    await findByText("kava");

    const unsuspendBtn = await findByText("Unsuspend");
    await fireEvent.click(unsuspendBtn);

    await waitFor(() => {
      expect(mockSuspend).toHaveBeenCalledWith(20, false);
    });
  });

  it("clicking same sort column twice flips order from asc to desc then back to asc", async () => {
    mockList.mockResolvedValue({ items: [makeSRSItemDetail({ id: 1, text: "a" })], total: 1 });

    const { findByText } = render(AdminSRSPage);
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
    mockList.mockResolvedValue({ items: [makeSRSItemDetail({ id: 1, text: "a" })], total: 1 });

    const { findByText } = render(AdminSRSPage);
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

    const { findByDisplayValue } = render(AdminSRSPage);
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

    const { findAllByRole, findByText } = render(AdminSRSPage);
    await findByText("a");

    const checkboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    // [0]=cloze flag, [1]=select-all header, [2+]=item rows
    await fireEvent.click(checkboxes[2]);
    await fireEvent.click(checkboxes[3]);

    await fireEvent.click(await findByText(/Delete selected/));

    expect(await findByText("bulk delete failed")).toBeTruthy();
  });

  it("shows stringified error when listSRSItems throws a non-Error", async () => {
    mockList.mockRejectedValue("network failure string");
    const { findByText } = render(AdminSRSPage);
    expect(await findByText("network failure string")).toBeTruthy();
  });

  // ── Anki status button gating ─────────────────────────────────────────────

  it("mounts and calls fetchAnkiStatus", async () => {
    const { findByText } = render(AdminSRSPage);
    await findByText(/0 total/);
    await waitFor(() => {
      expect(mockFetchAnkiStatus).toHaveBeenCalled();
    });
  });

  it("Sync button is enabled when anki_running is false", async () => {
    mockFetchAnkiStatus.mockResolvedValue({ anki_running: false, lock_acquirable: true });
    const { findByText } = render(AdminSRSPage);
    const btn = (await findByText("Sync with Anki")) as HTMLButtonElement;
    await waitFor(() => {
      expect(btn.disabled).toBe(false);
    });
  });

  it("Sync button is disabled when anki_running is true", async () => {
    mockFetchAnkiStatus.mockResolvedValue({ anki_running: true, lock_acquirable: false });
    const { findByText } = render(AdminSRSPage);
    const btn = (await findByText("Sync with Anki")) as HTMLButtonElement;
    await waitFor(() => {
      expect(btn.disabled).toBe(true);
    });
  });

  it('shows "Close Anki to sync" when Anki is running', async () => {
    mockFetchAnkiStatus.mockResolvedValue({ anki_running: true, lock_acquirable: false });
    const { findByText } = render(AdminSRSPage);
    expect(await findByText(/Close Anki to sync/)).toBeTruthy();
  });

  it("visibilitychange event triggers a re-fetch of Anki status", async () => {
    const { findByText } = render(AdminSRSPage);
    await findByText(/0 total/);
    const callsBefore = mockFetchAnkiStatus.mock.calls.length;

    document.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => {
      expect(mockFetchAnkiStatus.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });

  it("shows 409 Close Anki error in syncStatus when sync rejects with that message", async () => {
    mockFetchAnkiStatus.mockResolvedValue({ anki_running: false, lock_acquirable: true });
    mockSyncWithAnki.mockRejectedValue(
      new Error("Close Anki to sync — TunaTale needs exclusive access to collection.anki2."),
    );
    const { findByText } = render(AdminSRSPage);
    await fireEvent.click(await findByText("Sync with Anki"));
    expect(await findByText(/Close Anki to sync/)).toBeTruthy();
  });

  it("clicking header checkbox when all items are selected deselects all", async () => {
    mockList.mockResolvedValue({
      items: [makeSRSItemDetail({ id: 1, text: "a" }), makeSRSItemDetail({ id: 2, text: "b" })],
      total: 2,
    });

    const { findByText, findAllByRole, queryByText } = render(AdminSRSPage);
    await findByText("a");

    const checkboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    // [0]=cloze flag, [1]=select-all header, [2+]=item rows
    // Select both items individually
    await fireEvent.click(checkboxes[2]);
    await fireEvent.click(checkboxes[3]);

    // Verify "Delete selected" is visible (all selected)
    expect(await findByText(/Delete selected \(2\)/)).toBeTruthy();

    // Click header checkbox to deselect all
    await fireEvent.click(checkboxes[1]);

    await waitFor(() => {
      expect(queryByText(/Delete selected/)).toBeFalsy();
    });
  });

  it("clicking next/prev pagination changes the page", async () => {
    // total > PAGE_SIZE (50) to enable next button
    mockList.mockResolvedValue({ items: [makeSRSItemDetail({ id: 1, text: "a" })], total: 100 });

    const { findByText } = render(AdminSRSPage);
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
    mockList.mockResolvedValue({ items: [makeSRSItemDetail({ id: 1, text: "a" })], total: 1 });

    const { findByText } = render(AdminSRSPage);
    await findByText("a");

    for (const col of ["state", "due", "reps"]) {
      const callsBefore = mockList.mock.calls.length;
      await fireEvent.click(await findByText(new RegExp(`^${col}`)));
      await waitFor(() => {
        expect(mockList.mock.calls.length).toBeGreaterThan(callsBefore);
      });
    }
  });

  // ── Sync with Anki ────────────────────────────────────────────────────────

  it("renders Sync with Anki button", async () => {
    const { findByText } = render(AdminSRSPage);
    expect(await findByText("Sync with Anki")).toBeTruthy();
  });

  it("clicking Sync with Anki calls syncWithAnki(false)", async () => {
    mockSyncWithAnki.mockResolvedValue({
      created: 0,
      linked: 0,
      skipped: 0,
      notes_pulled: 5,
      directions_pulled: 10,
      conflicts: 0,
      mode: "offline",
      notes_pushed: 0,
      directions_pushed: 0,
      dry_run: false,
    });
    const { findByText } = render(AdminSRSPage);
    await fireEvent.click(await findByText("Sync with Anki"));
    await waitFor(() => {
      expect(mockSyncWithAnki).toHaveBeenCalledWith(false);
    });
  });

  it("shows counts in status after successful sync", async () => {
    mockSyncWithAnki.mockResolvedValue({
      created: 2,
      linked: 1,
      skipped: 0,
      notes_pulled: 7,
      directions_pulled: 14,
      conflicts: 0,
      mode: "offline",
      notes_pushed: 0,
      directions_pushed: 0,
      dry_run: false,
    });
    const { findByText } = render(AdminSRSPage);
    await fireEvent.click(await findByText("Sync with Anki"));
    expect(await findByText(/Created 2/)).toBeTruthy();
    expect(await findByText(/Pulled 14/)).toBeTruthy();
  });

  it("shows conflict count when conflicts > 0", async () => {
    mockSyncWithAnki.mockResolvedValue({
      created: 0,
      linked: 0,
      skipped: 0,
      notes_pulled: 3,
      directions_pulled: 6,
      conflicts: 2,
      mode: "offline",
      notes_pushed: 0,
      directions_pushed: 0,
      dry_run: false,
    });
    const { findByText } = render(AdminSRSPage);
    await fireEvent.click(await findByText("Sync with Anki"));
    expect(await findByText(/Conflicts 2/)).toBeTruthy();
  });

  it("shows error message when sync fails", async () => {
    mockSyncWithAnki.mockRejectedValue(new Error("AnkiConnect unavailable"));
    const { findByText } = render(AdminSRSPage);
    await fireEvent.click(await findByText("Sync with Anki"));
    expect(await findByText(/AnkiConnect unavailable/)).toBeTruthy();
  });

  it("re-fetches queueStats after successful sync", async () => {
    mockSyncWithAnki.mockResolvedValue({
      created: 0,
      linked: 0,
      skipped: 0,
      notes_pulled: 3,
      directions_pulled: 6,
      conflicts: 0,
      mode: "offline",
      notes_pushed: 0,
      directions_pushed: 0,
      dry_run: false,
    });
    mockFetchQueueStats.mockResolvedValue({
      new: 5,
      learning: 3,
      review: 2,
      daily_new_cap: 30,
      cap_source: "cache",
      fsrs_source: "cache",
    });

    const { findByText } = render(AdminSRSPage);
    // Wait for initial load
    await findByText(/0 total/);
    const callsBefore = mockFetchQueueStats.mock.calls.length;

    await fireEvent.click(await findByText("Sync with Anki"));

    await waitFor(() => {
      expect(mockFetchQueueStats.mock.calls.length).toBeGreaterThan(callsBefore);
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
    const { findByText } = render(AdminSRSPage);
    expect(await findByText(/12 new/)).toBeTruthy();
    expect(await findByText(/8 learning/)).toBeTruthy();
    expect(await findByText(/39 review/)).toBeTruthy();
  });

  it("renders without stats line when fetchQueueStats rejects", async () => {
    mockFetchQueueStats.mockRejectedValue(new Error("AnkiConnect down"));
    const { findByText, queryByText } = render(AdminSRSPage);
    // Page still loads items fine
    await findByText(/0 total/);
    // No "X new · Y due today" stats line should appear
    expect(queryByText(/\d+ new · \d+ due today/)).toBeFalsy();
  });

  it("leaves button enabled when fetchAnkiStatus throws (non-fatal)", async () => {
    mockFetchAnkiStatus.mockRejectedValue(new Error("status unavailable"));
    const { findByText } = render(AdminSRSPage);
    const btn = (await findByText("Sync with Anki")) as HTMLButtonElement;
    await waitFor(() => {
      expect(btn.disabled).toBe(false);
    });
  });

  it("window.focus event triggers a re-fetch of Anki status", async () => {
    const { findByText } = render(AdminSRSPage);
    await findByText(/0 total/);
    const callsBefore = mockFetchAnkiStatus.mock.calls.length;

    window.dispatchEvent(new Event("focus"));

    await waitFor(() => {
      expect(mockFetchAnkiStatus.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });

  it("clicking Sync re-polls status before calling syncWithAnki", async () => {
    mockSyncWithAnki.mockResolvedValue({
      created: 0,
      linked: 0,
      skipped: 0,
      notes_pulled: 0,
      directions_pulled: 0,
      conflicts: 0,
      mode: "offline",
      notes_pushed: 0,
      directions_pushed: 0,
      dry_run: false,
    });
    const { findByText } = render(AdminSRSPage);
    await findByText(/0 total/);
    const callsBefore = mockFetchAnkiStatus.mock.calls.length;

    await fireEvent.click(await findByText("Sync with Anki"));

    await waitFor(() => {
      // fetchAnkiStatus must be called again before (or during) the sync
      expect(mockFetchAnkiStatus.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });

  it("re-polls Anki status after sync error so button state reflects reality", async () => {
    mockSyncWithAnki.mockRejectedValue(new Error("Close Anki to sync"));
    mockFetchAnkiStatus
      .mockResolvedValueOnce({ anki_running: false, lock_acquirable: true }) // initial mount
      .mockResolvedValueOnce({ anki_running: false, lock_acquirable: true }) // pre-click
      .mockResolvedValueOnce({ anki_running: true, lock_acquirable: false }); // post-error re-poll
    const { findByText } = render(AdminSRSPage);
    await findByText(/0 total/);

    await fireEvent.click(await findByText("Sync with Anki"));

    // After the 409-like error, status is re-polled and button must become disabled
    const btn = (await findByText("Sync with Anki")) as HTMLButtonElement;
    await waitFor(() => {
      expect(btn.disabled).toBe(true);
    });
  });

  it("loads cloze setting on mount and renders feature flag checkbox", async () => {
    vi.mocked(api.getClozeSetting).mockResolvedValue({ enabled: true });
    const { getAllByRole, findByText } = render(AdminSRSPage);
    expect(await findByText(/Function-word cloze cards/)).toBeTruthy();
    await flushMicrotasks();
    expect(api.getClozeSetting).toHaveBeenCalled();
    const checkboxes = getAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes[0].checked).toBe(true);
  });

  it("toggling cloze checkbox calls setClozeSetting via request", async () => {
    vi.mocked(api.getClozeSetting).mockResolvedValue({ enabled: true });
    const { getAllByRole, findByText } = render(AdminSRSPage);
    await findByText(/0 total/);
    await flushMicrotasks();
    const checkboxes = getAllByRole("checkbox") as HTMLInputElement[];
    // Checkbox is checked (enabled: true from loadClozeSetting); clicking toggles off
    await fireEvent.click(checkboxes[0]);
    expect(api.setClozeSetting).toHaveBeenCalledWith(false);
  });

  it("shows error when getClozeSetting fails", async () => {
    vi.mocked(api.getClozeSetting).mockRejectedValue(new Error("settings unavailable"));
    const { findByText } = render(AdminSRSPage);
    expect(await findByText("settings unavailable")).toBeTruthy();
  });

  it("shows error when setClozeSetting fails", async () => {
    vi.mocked(api.getClozeSetting).mockResolvedValue({ enabled: false });
    vi.mocked(api.setClozeSetting).mockRejectedValue(new Error("toggle failed"));
    const { getAllByRole, findByText } = render(AdminSRSPage);
    await findByText(/0 total/);
    await flushMicrotasks();
    const checkboxes = getAllByRole("checkbox") as HTMLInputElement[];
    await fireEvent.click(checkboxes[0]);
    expect(await findByText("toggle failed")).toBeTruthy();
  });
});
