/**
 * Coverage sibling for the over-budget creation tail — covers the tail row's
 * gloss-reveal branch, which ListenPreviewModal.creationTail.test.ts does not
 * reach: its fixtures all use `translation: ""`, so the `{:else}` empty-gloss
 * arm is the only one exercised there. The tail is display-only but its gloss
 * stays a live control (blur until tapped), so the reveal path must carry
 * coverage of its own.
 *
 * Brief: bd tunatale-tsq (closed), Option B.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "./ListenPreviewModal.svelte";
import { api, type ListenPreviewCandidate } from "$lib/api";
import { listenCountdownPref } from "$lib/stores/listenCountdownPref.svelte";

vi.mock("$lib/api", () => ({
  api: {
    getListenPreview: vi.fn(),
    markAsListened: vi.fn(),
    getListens: vi.fn(),
  },
}));

vi.mock("$lib/stores/listened.svelte", async () => {
  const actual = await vi.importActual<typeof import("$lib/stores/listened.svelte")>(
    "$lib/stores/listened.svelte",
  );
  return { listenedStore: actual.listenedStore };
});

const mockGetListenPreview = vi.mocked(api.getListenPreview);

beforeEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  listenCountdownPref.set("off");
});

const createCandidate = (text: string, willCreate: boolean): ListenPreviewCandidate => ({
  kind: "create" as const,
  text,
  item_id: null,
  grade_class: "create" as const,
  rating: "good" as const,
  translation: willCreate ? "" : "bread",
  progress: null,
  well_known: false,
  due_at: null,
  will_create: willCreate,
});

describe("ListenPreviewModal — over-budget creation tail gloss", () => {
  it("reveals the gloss of a read-only tail row on tap", async () => {
    mockGetListenPreview.mockResolvedValue({
      candidates: [createCandidate("kake", true), createCandidate("brød", false)],
    });

    const { getByText, container } = render(ListenPreviewModal, {
      props: { lessonId: "l1", onDone: vi.fn() },
    });

    // F-20: the cut line is gone, so the load is signalled by the tail row
    // itself rather than by the "Introducing … limit" line this used to wait on.
    await waitFor(() => getByText("brød"));

    const gloss = container.querySelector("li.candidate.tail .gloss") as HTMLButtonElement | null;
    expect(gloss).toBeTruthy();
    expect(gloss!.textContent).toBe("bread");
    expect(gloss!.classList.contains("blurred")).toBe(true);
    expect(gloss!.getAttribute("aria-label")).toBe("Reveal gloss for brød");

    await fireEvent.click(gloss!);

    await waitFor(() => expect(gloss!.classList.contains("blurred")).toBe(false));
    expect(gloss!.getAttribute("aria-label")).toBe("bread");
  });
});
