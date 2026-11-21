/**
 * Tests for SyncButton component (AnkiWeb peer-sync).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import { tick } from "svelte";

vi.mock("#lib/api.js", () => ({
  api: {
    peerSync: vi.fn(),
  },
}));

import { api } from "#lib/api.js";
import SyncButton from "#lib/components/SyncButton.svelte";

const mockPeerSync = vi.mocked(api.peerSync);

const RESULT = {
  auth_success: true,
  pull_required: 0,
  push_required: 1,
  tt_push_pull_exit: 0,
  held_sentence_lines: [],
  dry_run: false,
};

describe("SyncButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the sync button", () => {
    const { getByText, queryByText } = render(SyncButton);
    expect(getByText("Sync with AnkiWeb")).toBeTruthy();
    expect(queryByText("Synced with AnkiWeb")).toBeNull();
  });

  it("keeps the full accessible name while carrying a short phone label", () => {
    // CSS swaps which label is visible (jsdom sees no media queries); the
    // aria-label is what keeps the name whole when the long span is display:none.
    const { getByRole, container } = render(SyncButton);
    expect(getByRole("button", { name: "Sync with AnkiWeb" })).toBeTruthy();
    const short = container.querySelector(".label-short");
    expect(short?.textContent).toBe("Sync");
    expect(short?.getAttribute("aria-hidden")).toBe("true");
  });

  it("calls peerSync on click", async () => {
    mockPeerSync.mockResolvedValue(RESULT);
    const { getByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync with AnkiWeb"));
    await waitFor(() => expect(mockPeerSync).toHaveBeenCalledWith(false));
  });

  it("shows loading state while syncing", async () => {
    let resolveSync: ((value: any) => void) | undefined;
    mockPeerSync.mockReturnValue(new Promise((r) => (resolveSync = r)));
    const { getByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync with AnkiWeb"));
    expect(getByText("Syncing…")).toBeTruthy();
    resolveSync!(RESULT);
  });

  it("calls onSyncResult callback when sync succeeds", async () => {
    const onSyncResult = vi.fn();
    mockPeerSync.mockResolvedValue(RESULT);
    const { getByText } = render(SyncButton, { props: { onSyncResult } });
    await fireEvent.click(getByText("Sync with AnkiWeb"));
    await waitFor(() =>
      expect(onSyncResult).toHaveBeenCalledWith(expect.objectContaining({ auth_success: true })),
    );
  });

  it("shows summary when sync succeeds (no onSyncResult)", async () => {
    mockPeerSync.mockResolvedValue(RESULT);
    const { getByText, findByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync with AnkiWeb"));
    await findByText("Synced with AnkiWeb");
  });

  it("auto-dismisses the success confirmation after the flash delay", async () => {
    vi.useFakeTimers();
    try {
      mockPeerSync.mockResolvedValue(RESULT);
      const { getByText, queryByText } = render(SyncButton);
      await fireEvent.click(getByText("Sync with AnkiWeb"));
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
    await fireEvent.click(getByText("Sync with AnkiWeb"));
    expect(await findByText("No AnkiWeb password found.")).toBeTruthy();
  });

  it("sets error when peerSync fails with non-Error value", async () => {
    mockPeerSync.mockRejectedValue("string error");
    const { getByText, findByText } = render(SyncButton);
    await fireEvent.click(getByText("Sync with AnkiWeb"));
    expect(await findByText("string error")).toBeTruthy();
  });

  // ── held sentence lines (tunatale-h0q3.68): a mined line Anki cannot take yet ──

  it("says how many sentence lines the sync held, and keeps saying it", async () => {
    vi.useFakeTimers();
    try {
      mockPeerSync.mockResolvedValue({ ...RESULT, held_sentence_lines: ["sent:a:1", "sent:a:2"] });
      const { getByText, getByRole, queryByText } = render(SyncButton, {
        props: { onSyncResult: vi.fn() },
      });
      await fireEvent.click(getByText("Sync with AnkiWeb"));
      await vi.advanceTimersByTimeAsync(0);
      await tick();
      const alert = getByRole("alert");
      expect(alert.textContent).toBe(
        "2 sentence lines are not in Anki yet: this deck has no TT Sentence notetype.",
      );
      expect(queryByText("Synced with AnkiWeb")).toBeNull();
      await vi.advanceTimersByTimeAsync(4000);
      await tick();
      expect(getByRole("alert")).toBe(alert); // not flashed away like the success note
    } finally {
      vi.useRealTimers();
    }
  });

  it("says one held line in the singular, and a later clean sync clears it", async () => {
    mockPeerSync.mockResolvedValueOnce({ ...RESULT, held_sentence_lines: ["sent:a:1"] });
    const { getByText, findByRole, queryByRole } = render(SyncButton);
    await fireEvent.click(getByText("Sync with AnkiWeb"));
    expect((await findByRole("alert")).textContent).toBe(
      "1 sentence line is not in Anki yet: this deck has no TT Sentence notetype.",
    );
    mockPeerSync.mockResolvedValueOnce(RESULT);
    await fireEvent.click(getByText("Sync with AnkiWeb"));
    await waitFor(() => expect(queryByRole("alert")).toBeNull());
  });

  // ── syncAvailable (Stage 4 — anki_sync plugin capability gate) ────────────

  it("renders nothing when syncAvailable is false", () => {
    const { container, queryByText } = render(SyncButton, { props: { syncAvailable: false } });
    expect(queryByText("Sync with AnkiWeb")).toBeNull();
    expect(container.querySelector(".sync-button")).toBeNull();
  });

  it("renders the button when syncAvailable is true", () => {
    const { getByText } = render(SyncButton, { props: { syncAvailable: true } });
    expect(getByText("Sync with AnkiWeb")).toBeTruthy();
  });
});
