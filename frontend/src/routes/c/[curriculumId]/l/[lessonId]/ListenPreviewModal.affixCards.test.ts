/**
 * The contrast cards a listen adds, as the preview lists them (tunatale-ve4p.6).
 *
 * A grammar lesson has no words to grade, so before this the preview said
 * "No new words to add" and Mark as Listened would have added cards the
 * learner was never shown. The rows here are not candidates: a contrast card
 * is added or skipped, never graded, so each row carries a two-state control
 * and none of them enters the ratings maps.
 *
 * The two rules that carry the risk are the tail's, restated for these rows:
 *  1. A row past the day's budget starts with NEITHER button pressed and emits
 *     nothing. Taking a card past the limit costs one deliberate tap on Add.
 *  2. Grade All and Skip All never touch a row past the budget.
 *
 * Companion: `backend/tests/test_api_grammar_lesson.py` pins what the listen
 * does with `skipped_affix_cards` / `over_cap_affix_cards`.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ListenPreviewModal from "$lib/components/ListenPreviewModal.svelte";
import { api, type AffixCardPreview, type ListenPreview } from "$lib/api";
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
const mockMarkAsListened = vi.mocked(api.markAsListened);

beforeEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  listenCountdownPref.set("off");
  mockMarkAsListened.mockResolvedValue({
    status: "ok",
    staged: 0,
    applied: 0,
    created: 0,
    remaining_candidates: 0,
    listen_count: 1,
  });
});

const card = (
  form: string,
  english: string,
  root: string,
  model: string,
  live = true,
): AffixCardPreview => ({
  form,
  english,
  root,
  model: [model],
  will_create: live,
});

// The owner's mo-/mi- lesson with two slots left in the day.
const CARDS = [
  card("miuban", "went along", "uban", "mouban"),
  card("moinom", "will drink", "inom", "miinom"),
  card("miadto", "went", "adto", "moadto", false),
];

async function open(preview: ListenPreview) {
  mockGetListenPreview.mockResolvedValue(preview);
  const utils = render(ListenPreviewModal, {
    props: { lessonId: "drill-1", languageCode: "ceb", onDone: vi.fn() },
  });
  await waitFor(() => expect(utils.queryByText("Loading...")).toBeNull());
  const button = (form: string, choice: "add" | "skip") =>
    utils.container.querySelector<HTMLButtonElement>(
      `button[data-affix="${form}"][data-choice="${choice}"]`,
    )!;
  const pressed = (form: string) =>
    (["add", "skip"] as const).filter(
      (choice) => button(form, choice).getAttribute("aria-pressed") === "true",
    );
  const commit = async () => {
    await fireEvent.click(
      utils.container.querySelector<HTMLButtonElement>(".footer button:not(.cancel)")!,
    );
    await waitFor(() => expect(mockMarkAsListened).toHaveBeenCalled());
    return mockMarkAsListened.mock.calls[0][1]!;
  };
  return { ...utils, button, pressed, commit };
}

describe("the affix cards group", () => {
  it("lists one row per card under the empty word list", async () => {
    const { getByText, container } = await open({ candidates: [], affix_cards: CARDS });

    expect(getByText("No new words to add.")).toBeTruthy();
    expect(getByText("Affix cards")).toBeTruthy();
    expect(getByText("3 new cards")).toBeTruthy();
    const rows = [...container.querySelectorAll(".candidate.affix")];
    expect(rows.map((row) => row.querySelector(".text")!.textContent)).toEqual([
      "miuban",
      "moinom",
      "miadto",
    ]);
    // What the card asks for, and the form it shows as the model.
    expect(rows.map((row) => row.querySelector(".affix-gloss")!.textContent)).toEqual([
      "went along · beside mouban",
      "will drink · beside miinom",
      "went · beside moadto",
    ]);
  });

  it("counts one card as one card", async () => {
    const { getByText } = await open({ candidates: [], affix_cards: [CARDS[0]] });
    expect(getByText("1 new card")).toBeTruthy();
  });

  it.each([
    ["the field is absent", { candidates: [] }],
    ["the list is empty", { candidates: [], affix_cards: [] }],
  ])("is not drawn when %s", async (_name, preview) => {
    const { queryByText, container } = await open(preview);
    expect(queryByText("Affix cards")).toBeNull();
    expect(container.querySelector(".affix-group")).toBeNull();
  });

  it("says which rows the day's budget reaches", async () => {
    const { container } = await open({ candidates: [], affix_cards: CARDS });
    const due = [...container.querySelectorAll(".candidate.affix .tag.day")].map((tag) =>
      tag.textContent!.trim(),
    );
    expect(due).toEqual(["new", "new", "later"]);
    const tail = [...container.querySelectorAll(".candidate.affix")].map((row) =>
      row.classList.contains("tail"),
    );
    expect(tail).toEqual([false, false, true]);
  });
});

describe("what a row starts as", () => {
  it("a row the budget reaches starts on Add, a row past it on nothing", async () => {
    const { pressed } = await open({ candidates: [], affix_cards: CARDS });
    expect([pressed("miuban"), pressed("moinom"), pressed("miadto")]).toEqual([
      ["add"],
      ["add"],
      [],
    ]);
  });

  it("the footer counts the cards that will be added", async () => {
    const { getByText } = await open({ candidates: [], affix_cards: CARDS });
    expect(getByText("Mark 2 as listened")).toBeTruthy();
  });

  it("untouched, the listen is sent with nothing skipped and nothing past the limit", async () => {
    const { commit } = await open({ candidates: [], affix_cards: CARDS });
    const payload = await commit();
    expect(payload.skippedAffixCards).toEqual([]);
    expect(payload.overCapAffixCards).toEqual([]);
  });
});

describe("one row at a time", () => {
  it("Skip leaves that card out and Add puts it back", async () => {
    const { button, pressed, getByText, commit } = await open({
      candidates: [],
      affix_cards: CARDS,
    });

    await fireEvent.click(button("moinom", "skip"));
    expect(pressed("moinom")).toEqual(["skip"]);
    expect(getByText("Mark 1 as listened")).toBeTruthy();

    await fireEvent.click(button("moinom", "add"));
    expect(pressed("moinom")).toEqual(["add"]);

    await fireEvent.click(button("miuban", "skip"));
    const payload = await commit();
    expect(payload.skippedAffixCards).toEqual(["miuban"]);
    expect(payload.overCapAffixCards).toEqual([]);
  });

  it("Add on a row past the budget takes that card past the day's limit", async () => {
    const { button, pressed, getByText, container, commit } = await open({
      candidates: [],
      affix_cards: CARDS,
    });

    await fireEvent.click(button("miadto", "add"));
    expect(pressed("miadto")).toEqual(["add"]);
    expect(getByText("Mark 3 as listened")).toBeTruthy();
    // Still a tail row: opting in does not move it across the cut.
    const row = container.querySelectorAll(".candidate.affix")[2];
    expect([row.classList.contains("tail"), row.classList.contains("opted")]).toEqual([true, true]);

    const payload = await commit();
    expect(payload.overCapAffixCards).toEqual(["miadto"]);
    expect(payload.skippedAffixCards).toEqual([]);
  });

  it("Skip on a row past the budget sends nothing for it", async () => {
    const { button, commit } = await open({ candidates: [], affix_cards: CARDS });
    await fireEvent.click(button("miadto", "add"));
    await fireEvent.click(button("miadto", "skip"));

    const payload = await commit();
    // Not in `skipped` either: that list is for rows the budget reached.
    expect(payload.skippedAffixCards).toEqual([]);
    expect(payload.overCapAffixCards).toEqual([]);
  });
});

describe("the bulk buttons", () => {
  it("Skip All skips the rows the budget reaches and leaves the rest alone", async () => {
    const { getByText, pressed, commit } = await open({ candidates: [], affix_cards: CARDS });

    await fireEvent.click(getByText("Skip All"));
    expect([pressed("miuban"), pressed("moinom"), pressed("miadto")]).toEqual([
      ["skip"],
      ["skip"],
      [],
    ]);

    const payload = await commit();
    expect(payload.skippedAffixCards).toEqual(["miuban", "moinom"]);
    expect(payload.overCapAffixCards).toEqual([]);
  });

  it("Grade All puts skipped rows back on and never opts a row past the limit", async () => {
    const { getByText, button, pressed, commit } = await open({
      candidates: [],
      affix_cards: CARDS,
    });

    await fireEvent.click(button("miuban", "skip"));
    await fireEvent.click(getByText(/Grade All/));
    expect([pressed("miuban"), pressed("moinom"), pressed("miadto")]).toEqual([
      ["add"],
      ["add"],
      [],
    ]);

    const payload = await commit();
    expect(payload.skippedAffixCards).toEqual([]);
    expect(payload.overCapAffixCards).toEqual([]);
  });

  it("Grade All leaves a row the learner opted past the limit as they set it", async () => {
    const { getByText, button, pressed } = await open({ candidates: [], affix_cards: CARDS });
    await fireEvent.click(button("miadto", "add"));
    await fireEvent.click(getByText("Skip All"));
    await fireEvent.click(getByText(/Grade All/));
    expect(pressed("miadto")).toEqual(["add"]);
  });
});
