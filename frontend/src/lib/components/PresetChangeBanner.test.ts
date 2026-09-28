/**
 * Tests for PresetChangeBanner (tunatale-c649).
 *
 * The banner is the app half of the NOT_RESCHEDULED preset-change alert: a sync
 * found the deck's FSRS preset changed and Anki wrote no reschedule, so the due
 * dates no longer match the stabilities. It is alert state with one dismissal.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$lib/api", () => ({
  api: {
    getPresetChange: vi.fn(),
    dismissPresetChange: vi.fn(),
  },
}));

import { api } from "$lib/api";
import { syncStore } from "$lib/stores/sync.svelte";
import PresetChangeBanner from "$lib/components/PresetChangeBanner.svelte";

const mockGet = vi.mocked(api.getPresetChange);
const mockDismiss = vi.mocked(api.dismissPresetChange);

const CHANGE = {
  deck_name: "Norwegian (TunaTale)",
  detected_at_ms: 1_790_000_000_000,
  desired_retention_old: 0.9,
  desired_retention_new: 0.95,
  weights_changed: true,
  due_ratio_median_old: 1.5,
  due_ratio_median_new: 6,
};

const SYNC_RESULT = {
  auth_success: true,
  pull_required: 0,
  push_required: 1,
  tt_push_pull_exit: 0,
  dry_run: false,
};

describe("PresetChangeBanner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    syncStore.notify(null);
  });

  it("renders nothing when there is no change", async () => {
    mockGet.mockResolvedValue({ change: null });
    const { container } = render(PresetChangeBanner, { props: { syncAvailable: true } });
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("shows the deck, the retention move and both medians", async () => {
    mockGet.mockResolvedValue({ change: CHANGE });
    const { getByRole } = render(PresetChangeBanner, { props: { syncAvailable: true } });
    const banner = await waitFor(() => getByRole("alert"));
    expect(banner.textContent).toContain("Norwegian (TunaTale)");
    expect(banner.textContent).toContain("0.90 → 0.95");
    expect(banner.textContent).toContain("1.50 → 6.00");
  });

  it("prints an em dash for a median that was never measured", async () => {
    // 0.00 is a real ratio; "no review rows" is not, and must not read as one.
    mockGet.mockResolvedValue({
      change: { ...CHANGE, due_ratio_median_old: null, due_ratio_median_new: null },
    });
    const { getByRole } = render(PresetChangeBanner, { props: { syncAvailable: true } });
    const banner = await waitFor(() => getByRole("alert"));
    expect(banner.textContent).toContain("— → —");
  });

  it("dismisses through the API and then hides itself", async () => {
    mockGet.mockResolvedValue({ change: CHANGE });
    mockDismiss.mockResolvedValue({ change: null });
    const { getByRole, getByText, queryByRole } = render(PresetChangeBanner, {
      props: { syncAvailable: true },
    });
    await waitFor(() => getByRole("alert"));

    await fireEvent.click(getByText("Dismiss"));
    await waitFor(() => expect(mockDismiss).toHaveBeenCalled());
    expect(queryByRole("alert")).toBeNull();
  });

  it("refetches when a sync finishes", async () => {
    mockGet.mockResolvedValue({ change: null });
    render(PresetChangeBanner, { props: { syncAvailable: true } });
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));

    syncStore.notify(SYNC_RESULT);
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it("neither fetches nor renders when sync is unavailable", async () => {
    mockGet.mockResolvedValue({ change: CHANGE });
    const { container } = render(PresetChangeBanner, { props: { syncAvailable: false } });
    await waitFor(() => expect(container.querySelector('[role="alert"]')).toBeNull());
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("shows nothing at all when the fetch fails", async () => {
    mockGet.mockRejectedValue(new Error("offline"));
    const { container } = render(PresetChangeBanner, { props: { syncAvailable: true } });
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
