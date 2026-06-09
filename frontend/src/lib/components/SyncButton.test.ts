/**
 * Tests for SyncButton component (AnkiWeb peer-sync).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import { tick } from "svelte";

vi.mock("$lib/api", () => ({
  api: {
    peerSync: vi.fn(),
  },
}));

import { api } from "$lib/api";
import SyncButton from "$lib/components/SyncButton.svelte";

const mockPeerSync = vi.mocked(api.peerSync);

const RESULT = {
  auth_success: true,
  pull_required: 0,
  push_required: 1,
  tt_push_pull_exit: 0,
  dry_run: false,
};

describe("SyncButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the sync button", () => {
    const { getByText, queryByText } = render(SyncButton);
    expect(getByText("Sync to AnkiWeb")).toBeTruthy();
    expect(queryByText("Synced with AnkiWeb")).toBeNull();
  });

  it("calls peerSync on click", async () => {
    mockPeerSync.mockResolvedValue(RESULT);
    const { getByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync to AnkiWeb"));
    await waitFor(() => expect(mockPeerSync).toHaveBeenCalledWith(false));
  });

  it("shows loading state while syncing", async () => {
    let resolveSync: ((value: any) => void) | undefined;
    mockPeerSync.mockReturnValue(new Promise((r) => (resolveSync = r)));
    const { getByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync to AnkiWeb"));
    expect(getByText("Syncing…")).toBeTruthy();
    resolveSync!(RESULT);
  });

  it("calls onSyncResult callback when sync succeeds", async () => {
    const onSyncResult = vi.fn();
    mockPeerSync.mockResolvedValue(RESULT);
    const { getByText } = render(SyncButton, { props: { onSyncResult } });
    await fireEvent.click(getByText("Sync to AnkiWeb"));
    await waitFor(() =>
      expect(onSyncResult).toHaveBeenCalledWith(expect.objectContaining({ auth_success: true })),
    );
  });

  it("shows summary when sync succeeds (no onSyncResult)", async () => {
    mockPeerSync.mockResolvedValue(RESULT);
    const { getByText, findByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync to AnkiWeb"));
    await findByText("Synced with AnkiWeb");
  });

  it("auto-dismisses the success confirmation after the flash delay", async () => {
    vi.useFakeTimers();
    try {
      mockPeerSync.mockResolvedValue(RESULT);
      const { getByText, queryByText } = render(SyncButton);
      await fireEvent.click(getByText("Sync to AnkiWeb"));
      await vi.advanceTimersByTimeAsync(0);
      await tick();
      expect(queryByText("Synced with AnkiWeb")).not.toBeNull();

      await vi.advanceTimersByTimeAsync(4000);
      await tick();
      expect(queryByText("Synced with AnkiWeb")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("sets error when peerSync fails with Error instance", async () => {
    mockPeerSync.mockRejectedValue(new Error("No AnkiWeb password found."));
    const { getByText, findByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync to AnkiWeb"));
    expect(await findByText("No AnkiWeb password found.")).toBeTruthy();
  });

  it("sets error when peerSync fails with non-Error value", async () => {
    mockPeerSync.mockRejectedValue("string error");
    const { getByText, findByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync to AnkiWeb"));
    expect(await findByText("string error")).toBeTruthy();
  });
});
