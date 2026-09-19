/**
 * The panel's half of "one paste must not become two sessions" (bd tunatale-rwkz.1).
 *
 * The server single-flights on an Idempotency-Key, which does nothing unless the
 * client sends the SAME key for what the learner considers one attempt. The rule
 * the panel implements:
 *
 *   - a key is minted when an import starts and kept while it keeps failing, so
 *     a retry after a dropped connection joins the first attempt instead of
 *     creating a twin;
 *   - it is dropped once an import SUCCEEDS, so a later deliberate second import
 *     is a new intent and really makes a second session.
 *
 * ⚠️ The failing case is the one that matters and the easy one to get backwards.
 * On 2026-09-19 the first import's fetch died on a mobile connection while the
 * SERVER kept working and wrote the session; the button re-enabled and a second
 * request went out. Minting a fresh key on that second attempt would reproduce
 * the duplicate exactly, with idempotency "implemented" on both sides.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ManualStoryPanel from "./ManualStoryPanel.svelte";

const copyPrompt = vi.fn().mockResolvedValue("prompt");
const onImported = vi.fn();
const onDelete = vi.fn();

function importedKeys(importRaw: ReturnType<typeof vi.fn>): Array<string | undefined> {
  return importRaw.mock.calls.map((c) => c[1] as string | undefined);
}

async function typeAndImport(container: HTMLElement, raw = '{"title":"Test"}') {
  const textarea = container.querySelector("textarea")!;
  await fireEvent.input(textarea, { target: { value: raw } });
  const importBtn = container.querySelector('[data-testid="import-btn"]')!;
  await fireEvent.click(importBtn);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ManualStoryPanel idempotency key", () => {
  it("sends a key with the import", async () => {
    const importRaw = vi.fn().mockResolvedValue({ id: "s1", warnings: [] });
    const { container } = render(ManualStoryPanel, {
      props: { copyPrompt, importRaw, onImported, onDelete },
    });

    await typeAndImport(container);

    await waitFor(() => expect(importRaw).toHaveBeenCalledOnce());
    const [key] = importedKeys(importRaw);
    expect(key).toBeTruthy();
  });

  it("reuses the same key when a failed import is retried", async () => {
    const importRaw = vi
      .fn()
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValueOnce({ id: "s1", warnings: [] });
    const { container } = render(ManualStoryPanel, {
      props: { copyPrompt, importRaw, onImported, onDelete },
    });

    await typeAndImport(container);
    await waitFor(() => expect(importRaw).toHaveBeenCalledOnce());
    await typeAndImport(container);
    await waitFor(() => expect(importRaw).toHaveBeenCalledTimes(2));

    const [first, second] = importedKeys(importRaw);
    expect(first).toBeTruthy();
    expect(second).toBe(first);
  });

  it("mints a fresh key after a successful import", async () => {
    const importRaw = vi
      .fn()
      .mockResolvedValueOnce({ id: "s1", warnings: [] })
      .mockResolvedValueOnce({ id: "s2", warnings: [] });
    const { container } = render(ManualStoryPanel, {
      props: { copyPrompt, importRaw, onImported, onDelete },
    });

    await typeAndImport(container);
    await waitFor(() => expect(importRaw).toHaveBeenCalledOnce());
    await typeAndImport(container, '{"title":"A second story"}');
    await waitFor(() => expect(importRaw).toHaveBeenCalledTimes(2));

    const [first, second] = importedKeys(importRaw);
    expect(second).toBeTruthy();
    expect(second).not.toBe(first);
  });
});
