/**
 * Tests for root +layout.svelte — asserts the SyncButton renders in the global nav.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import { createRawSnippet } from "svelte";

vi.mock("$app/stores", () => ({
  page: {
    subscribe: vi.fn((cb) => {
      cb({ url: { pathname: "/" } });
      return () => {};
    }),
  },
}));

vi.mock("$lib/api", () => ({
  api: {
    peerSync: vi.fn(),
  },
}));

import { api } from "$lib/api";
import Layout from "./+layout.svelte";

const mockPeerSync = vi.mocked(api.peerSync);

const RESULT = {
  auth_success: true,
  pull_required: 0,
  push_required: 1,
  tt_push_pull_exit: 0,
  dry_run: false,
};

function renderLayout() {
  const children = createRawSnippet(() => ({
    render: () => `<div data-testid="slot">page content</div>`,
  }));
  return render(Layout, { props: { children } });
}

describe("root +layout.svelte", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the Sync to AnkiWeb button in the global nav", () => {
    const { getByText } = renderLayout();
    expect(getByText("Sync to AnkiWeb")).toBeTruthy();
  });

  it("sync button calls peerSync on click", async () => {
    mockPeerSync.mockResolvedValue(RESULT);
    const { getByText } = renderLayout();
    await fireEvent.click(getByText("Sync to AnkiWeb"));
    await waitFor(() => expect(mockPeerSync).toHaveBeenCalledWith(false));
  });

  it("renders page content through children snippet", () => {
    const { getByTestId } = renderLayout();
    expect(getByTestId("slot").textContent).toBe("page content");
  });
});
