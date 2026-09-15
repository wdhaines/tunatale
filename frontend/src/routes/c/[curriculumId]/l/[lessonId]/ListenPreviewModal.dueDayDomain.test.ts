/**
 * ORACLE for the dueDays day-domain fix (bd tunatale-l0b6).
 * Full brief: .beads-tasks/briefs/brief-listen-preview-day-domain-2026-09.md.
 *
 * THE BUG: dueDays compared the due_at's UTC date against the browser's UTC
 * calendar date, but TT's "study day" is the LOCAL calendar date with a 4 AM
 * LOCAL rollover (backend/app/srs/anki_mirror/rollover.py::anki_today). At the
 * user's offset (UTC-4) the browser's UTC date rolls at 20:00 local while the
 * Anki day rolls at 04:00 local, so every day count read one too low from
 * 20:00 until 04:00 — about 8 hours a day.
 *
 * THE FIX: only the "today" side changes — the study day is the LOCAL date of
 * the most recent 04:00 LOCAL rollover. A card due 2026-09-15 has
 * due_at "2026-09-15T04:00:00Z" (due_at_rollover_utc convention); a card due
 * 2026-09-17 has due_at "2026-09-17T04:00:00Z".
 *
 * All rows pin TZ to America/New_York (EDT = UTC-4 in mid-September 2026).
 * Rows 1 and 4 are regression guards: they must pass BEFORE and AFTER the fix.
 * Rows 2 and 3 are the discriminators: they read "-1d"/"1d" before the fix
 * (the UTC date had rolled while the Anki day had not) and "today"/"2d" after.
 * Row 4 is what proves the fix is not "subtract a day everywhere".
 */
process.env.TZ = "America/New_York";

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "$lib/components/ListenPreviewModal.svelte";
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
  listenCountdownPref.set("off");
});

afterEach(() => {
  vi.useRealTimers();
});

// ── Fixtures ──────────────────────────────────────────────────────────

const base = {
  item_id: 42,
  rating: "good" as const,
  translation: "",
  progress: 0.3,
  well_known: false,
  due_at: null,
  will_create: true,
  understand_band: null as string | null,
  understand_stability: null as number | null,
  produce_band: null as string | null,
  produce_stability: null as number | null,
};

// Day-level due_at in the due_at_rollover_utc convention: 04:00 UTC on the
// due date.
const DUE_SEP_15 = "2026-09-15T04:00:00Z";
const DUE_SEP_17 = "2026-09-17T04:00:00Z";

const dueRow = (text: string, due_at: string): ListenPreviewCandidate => ({
  ...base,
  kind: "word",
  text,
  grade_class: "due",
  due_at,
});

const preview = () => ({
  candidates: [dueRow("mesec15", DUE_SEP_15), dueRow("mesec17", DUE_SEP_17)],
});

// ── Helpers ───────────────────────────────────────────────────────────

/** The day tag's text on the row whose word cell reads `text`. */
function dayTagText(container: HTMLElement, text: string): string {
  const found = [...container.querySelectorAll("li.candidate")].find(
    (li) => li.querySelector(".text")?.textContent?.trim() === text,
  );
  if (!found) throw new Error(`no candidate row for "${text}"`);
  const tag = found.querySelector(".tag.day");
  if (!tag) throw new Error(`no day tag on row "${text}"`);
  return tag.textContent?.trim() ?? "";
}

/** Render the modal with the system clock pinned to an oracle instant. */
async function openAt(systemTime: string) {
  vi.useFakeTimers();
  vi.setSystemTime(Date.parse(systemTime));
  mockGetListenPreview.mockResolvedValue(preview());
  const rendered = render(ListenPreviewModal, {
    props: { lessonId: "l1", onDone: vi.fn() },
  });
  await waitFor(() => rendered.getByText("mesec15"));
  return rendered;
}

// ── Tests ─────────────────────────────────────────────────────────────

describe("dueDays — the day domain is the Anki study day (local 4 AM rollover), not the UTC date", () => {
  // Row 1 — 11:18 Sep 15 local (EDT). Regression guard: passes BEFORE and AFTER.
  it("2026-09-15T15:18:00Z (11:18 Sep 15 local) → today / 2d", async () => {
    const rendered = await openAt("2026-09-15T15:18:00Z");
    expect(dayTagText(rendered.container, "mesec15")).toBe("today");
    expect(dayTagText(rendered.container, "mesec17")).toBe("2d");
    rendered.unmount();
  });

  // Row 2 — 21:30 Sep 15 local. Discriminator: before the fix this read
  // "-1d"/"1d" because the UTC date had rolled to Sep 16.
  it("2026-09-16T01:30:00Z (21:30 Sep 15 local) → today / 2d", async () => {
    const rendered = await openAt("2026-09-16T01:30:00Z");
    expect(dayTagText(rendered.container, "mesec15")).toBe("today");
    expect(dayTagText(rendered.container, "mesec17")).toBe("2d");
    rendered.unmount();
  });

  // Row 3 — 01:30 Sep 16 local, before the 04:00 rollover; still Sep 15's Anki
  // day. Discriminator, same "-1d"/"1d" bug before the fix.
  it("2026-09-16T05:30:00Z (01:30 Sep 16 local) → today / 2d", async () => {
    const rendered = await openAt("2026-09-16T05:30:00Z");
    expect(dayTagText(rendered.container, "mesec15")).toBe("today");
    expect(dayTagText(rendered.container, "mesec17")).toBe("2d");
    rendered.unmount();
  });

  // Row 4 — 04:30 Sep 16 local, just after the rollover. The Sep 15 card is
  // GENUINELY one day overdue; this is the proof the fix does not just
  // subtract a day everywhere.
  it("2026-09-16T08:30:00Z (04:30 Sep 16 local) → -1d / 1d", async () => {
    const rendered = await openAt("2026-09-16T08:30:00Z");
    expect(dayTagText(rendered.container, "mesec15")).toBe("-1d");
    expect(dayTagText(rendered.container, "mesec17")).toBe("1d");
    rendered.unmount();
  });
});
