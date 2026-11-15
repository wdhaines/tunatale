/**
 * The Grade page (tunatale-5i1p.3): one lesson's cards due within a range,
 * nothing pre-graded, Save sends only what was tapped.
 * Design: .beads-tasks/briefs/design-grade-page-2026-11.md § The page.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor, within } from "@testing-library/svelte";
import { makeGradePlan, makeGradePlanCard, makeWordToken } from "../../test/factories";

vi.mock("#lib/api.js", () => ({
  api: {
    getGradePlan: vi.fn(),
    saveGrades: vi.fn(),
    getTranscript: vi.fn(),
  },
}));
vi.mock("#lib/stores/listened.svelte.js", () => ({ listenedStore: { refresh: vi.fn() } }));
vi.mock("#lib/stores/queueStats.svelte.js", () => ({ queueStatsStore: { refresh: vi.fn() } }));

import { api } from "#lib/api.js";
import { listenedStore } from "#lib/stores/listened.svelte.js";
import { queueStatsStore } from "#lib/stores/queueStats.svelte.js";
import { gradeRangePref } from "#lib/stores/gradeRangePref.svelte.js";
import GradePage from "./GradePage.svelte";

const getPlan = vi.mocked(api.getGradePlan);
const save = vi.mocked(api.saveGrades);
const getTranscript = vi.mocked(api.getTranscript);

const today = new Date().toISOString();
const inDays = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString();

const word = makeGradePlanCard({ item_id: 1, text: "búntag", gloss: "morning", due_at: today });
const ahead = makeGradePlanCard({
  item_id: 2,
  text: "dínhi",
  gloss: "here",
  due_at: inDays(2),
  grade_class: "ahead",
});
const say = makeGradePlanCard({
  item_id: 3,
  direction: "production",
  text: "Nia",
  gloss: "is here",
  due_at: today,
});

const plan = (over = {}) =>
  makeGradePlan({
    cards: [word, ahead, say],
    later: { recognition: 4, production: 0 },
    ...over,
  });

const transcript = {
  lesson_id: "l1",
  key_phrases: [],
  dialogue_lines: [
    {
      role: "male-1",
      words: [
        makeWordToken({ surface: "Nia", srs_item_id: 3 }),
        makeWordToken({ surface: "mi", srs_item_id: 4 }),
      ],
    },
  ],
};

const ok = { status: "ok", applied: 0, created: 0, recorded_study: true, listen_count: 1 };

function renderPage() {
  return render(GradePage, {
    props: { contentId: "l1", title: "Wolff 1", backHref: "/c/c1/l/l1" },
  });
}

async function ready() {
  const view = renderPage();
  await view.findByText("búntag");
  return view;
}

function rowOf(text: string): HTMLElement {
  return document.querySelector(`[data-row-text="${text}"]`) as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  gradeRangePref.set(3);
  localStorage.clear();
  getPlan.mockResolvedValue(plan());
  getTranscript.mockResolvedValue(transcript);
  save.mockResolvedValue(ok);
});

describe("GradePage — loading and the range", () => {
  it("reads the plan at the remembered range, three days by default", async () => {
    await ready();
    expect(getPlan).toHaveBeenCalledWith("l1", 3);
    expect(document.querySelector('[data-range="3"]')?.getAttribute("aria-pressed")).toBe("true");
  });

  it("a range chip refetches at that range and is remembered", async () => {
    const view = await ready();
    await fireEvent.click(view.getByRole("button", { name: "7 days" }));
    await waitFor(() => expect(getPlan).toHaveBeenLastCalledWith("l1", 7));
    expect(gradeRangePref.value).toBe(7);
    await fireEvent.click(view.getByRole("button", { name: "all" }));
    await waitFor(() => expect(getPlan).toHaveBeenLastCalledWith("l1", null));
    expect(view.getByTestId("range-value").textContent).toBe("all");
  });

  it("− and + move the range one day", async () => {
    const view = await ready();
    await fireEvent.click(view.getByRole("button", { name: "One day more" }));
    await waitFor(() => expect(getPlan).toHaveBeenLastCalledWith("l1", 4));
    expect(view.getByTestId("range-value").textContent).toBe("4 d");
    await fireEvent.click(view.getByRole("button", { name: "One day less" }));
    await fireEvent.click(view.getByRole("button", { name: "One day less" }));
    await waitFor(() => expect(getPlan).toHaveBeenLastCalledWith("l1", 2));
  });

  it("says how many more are due past the range", async () => {
    const view = await ready();
    expect(view.getByText("4 more due later. Widen the range to see them.")).toBeTruthy();
  });

  it("shows a load failure", async () => {
    getPlan.mockRejectedValueOnce(new Error("boom"));
    const view = renderPage();
    expect(await view.findByText("boom")).toBeTruthy();
  });

  it("a transcript failure only costs Say it its line context", async () => {
    getTranscript.mockRejectedValueOnce(new Error("no transcript"));
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: /Say it/ }));
    expect(rowOf("Nia").querySelector(".hidden-word")).toBeTruthy();
    expect(view.queryByText("no transcript")).toBeNull();
  });

  it("an empty range says so", async () => {
    getPlan.mockResolvedValue(plan({ cards: [], later: { recognition: 0, production: 0 } }));
    const view = renderPage();
    expect(await view.findByText("Nothing due in this range.")).toBeTruthy();
  });
});

describe("GradePage — Understand", () => {
  it("lists recognition rows only, soonest first, with their due tags", async () => {
    const view = await ready();
    const rows = [...document.querySelectorAll("[data-row-text]")].map((r) =>
      r.getAttribute("data-row-text"),
    );
    expect(rows).toEqual(["búntag", "dínhi"]);
    expect(within(rowOf("búntag")).getByText("today")).toBeTruthy();
    expect(within(rowOf("dínhi")).getByText("in 2d")).toBeTruthy();
    expect(view.getByRole("tab", { name: "Understand · 2" })).toBeTruthy();
    expect(view.getByRole("tab", { name: "Say it · 1" })).toBeTruthy();
  });

  it("blurs the meaning until it is tapped", async () => {
    await ready();
    const gloss = within(rowOf("búntag")).getByText("morning");
    expect(gloss.classList.contains("blurred")).toBe(true);
    await fireEvent.click(gloss);
    expect(gloss.classList.contains("blurred")).toBe(false);
  });

  it("nothing is graded until tapped, and tapping the chosen grade clears it", async () => {
    const view = await ready();
    const good = within(rowOf("búntag")).getByRole("button", { name: "Good" });
    expect(good.getAttribute("aria-pressed")).toBe("false");
    expect(view.getByRole("button", { name: /^Save/ }).textContent).toContain("0 grades");
    await fireEvent.click(good);
    expect(good.getAttribute("aria-pressed")).toBe("true");
    expect(view.getByRole("button", { name: /^Save/ }).textContent).toContain("1 grade ");
    await fireEvent.click(good);
    expect(good.getAttribute("aria-pressed")).toBe("false");
  });
});

describe("GradePage — Say it", () => {
  it("shows the card's line with it hidden; a tap reveals it and offers the grades", async () => {
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: /Say it/ }));
    const row = rowOf("Nia");
    expect(row.textContent).toContain("mi");
    expect(within(row).queryByRole("group", { name: "Grade" })).toBeNull();
    const hidden = within(row).getByRole("button", { name: "Show the hidden word" });
    expect(hidden.classList.contains("shown")).toBe(false);
    await fireEvent.click(hidden);
    expect(hidden.classList.contains("shown")).toBe(true);
    await fireEvent.click(within(row).getByRole("button", { name: "Hard" }));
    expect(within(row).getByRole("button", { name: "Hard" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(row.textContent).toContain("is here");
  });

  it("shows a drill card's prompt above its hidden answer", async () => {
    const drill = {
      kind: "substitution" as const,
      step: 1,
      of: 16,
      cue: "sila",
      answer: "Nia sila dinhi.",
      english: "They are here.",
      before: "Nia siya dinhi.",
      before_cue: null,
      answer_audio_url: null,
      before_audio_url: null,
      before_cue_audio_url: null,
      cue_audio_url: null,
    };
    getPlan.mockResolvedValue(
      plan({
        cards: [
          word,
          makeGradePlanCard({
            item_id: 8,
            kind: "drill",
            direction: "production",
            text: "Nia sila dinhi.",
            drill,
          }),
        ],
      }),
    );
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: /Say it/ }));
    const row = rowOf("Nia sila dinhi.");
    expect([...row.querySelectorAll(".prompt")].map((p) => p.textContent)).toEqual([
      "Nia siya dinhi.",
      "sila",
    ]);
    expect(row.querySelector(".step")?.textContent).toBe("step 2 of 16");
  });

  it("a word in no transcript line is hidden under its meaning", async () => {
    getTranscript.mockResolvedValueOnce({ ...transcript, dialogue_lines: [] });
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: /Say it/ }));
    expect(rowOf("Nia").textContent).toContain("is here");
  });
});

describe("GradePage — New cards", () => {
  const suggestions = {
    words: [
      { key: "lang", text: "lang", gloss: "just", will_create: true },
      { key: "kuan", text: "kùán", gloss: "what's-it", will_create: false },
    ],
    contrast: [{ form: "miadto", root: "adto", english: "went", model: [], will_create: true }],
  };

  it("lists suggestions on when they fit the budget, off past it", async () => {
    getPlan.mockResolvedValue(plan({ suggestions }));
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: "New cards · 2" }));
    const toggle = (text: string) => within(rowOf(text)).getByRole("button");
    expect(toggle("lang").getAttribute("aria-pressed")).toBe("true");
    expect(toggle("kùán").getAttribute("aria-pressed")).toBe("false");
    expect(toggle("kùán").textContent).toBe("Over budget");
    expect(toggle("miadto").getAttribute("aria-pressed")).toBe("true");
    expect(view.queryByRole("group", { name: "Due range" })).toBeNull();
  });

  it("Save sends what was turned off and what was opted past the budget", async () => {
    getPlan.mockResolvedValue(plan({ suggestions }));
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: /New cards/ }));
    await fireEvent.click(within(rowOf("lang")).getByRole("button"));
    await fireEvent.click(within(rowOf("kùán")).getByRole("button"));
    await fireEvent.click(within(rowOf("miadto")).getByRole("button"));
    expect(within(rowOf("lang")).getByRole("button").textContent).toBe("Off");
    await fireEvent.click(view.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][1]).toEqual({
      card_grades: [],
      declined: [
        { kind: "word", key: "lang" },
        { kind: "contrast", key: "miadto" },
      ],
      over_cap: [{ kind: "word", key: "kuan" }],
      studied: false,
    });
    // A turned-off word goes on the ignore list, so the notice says so (Fable, PR #425).
    expect(await view.findByText("Saved: 2 turned off · counted as studied")).toBeTruthy();
  });

  it("says when there is nothing to add", async () => {
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: /New cards/ }));
    expect(view.getByText("Nothing new to add from this lesson.")).toBeTruthy();
  });
});

describe("GradePage — Save", () => {
  it("sends only the rows tapped, then reloads the plan and the counts", async () => {
    save.mockResolvedValue({ ...ok, applied: 2 });
    const view = await ready();
    await fireEvent.click(within(rowOf("dínhi")).getByRole("button", { name: "Easy" }));
    await fireEvent.click(view.getByRole("tab", { name: /Say it/ }));
    await fireEvent.click(
      within(rowOf("Nia")).getByRole("button", { name: "Show the hidden word" }),
    );
    await fireEvent.click(within(rowOf("Nia")).getByRole("button", { name: "Again" }));
    await fireEvent.click(view.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]).toEqual([
      "l1",
      {
        card_grades: [
          { item_id: 2, direction: "recognition", rating: "easy" },
          { item_id: 3, direction: "production", rating: "again" },
        ],
        declined: [],
        over_cap: [],
        studied: false,
      },
    ]);
    expect(await view.findByText("Saved: 2 graded · counted as studied")).toBeTruthy();
    expect(getPlan).toHaveBeenCalledTimes(2);
    expect(listenedStore.refresh).toHaveBeenCalled();
    expect(queueStatsStore.refresh).toHaveBeenCalled();
    expect(view.getByRole("button", { name: /^Save/ }).textContent).toContain("0 grades");
  });

  it("a grade on a row the range has since hidden is not sent", async () => {
    const view = await ready();
    await fireEvent.click(within(rowOf("dínhi")).getByRole("button", { name: "Good" }));
    getPlan.mockResolvedValue(plan({ cards: [word, say] }));
    await fireEvent.click(view.getByRole("button", { name: "now" }));
    await waitFor(() => expect(rowOf("dínhi")).toBeNull());
    await fireEvent.click(view.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][1].card_grades).toEqual([]);
  });

  it("on a studied lesson, Save needs something to send, and can count another study pass", async () => {
    getPlan.mockResolvedValue(plan({ studied: true, listen_count: 2 }));
    save.mockResolvedValue({ ...ok, recorded_study: true, listen_count: 3 });
    const view = await ready();
    const button = view.getByRole("button", { name: /^Save/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(view.queryByText("Saving marks this lesson studied.")).toBeNull();
    await fireEvent.click(view.getByLabelText("I studied it again (studied 2 times)"));
    expect(button.disabled).toBe(false);
    await fireEvent.click(button);
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][1].studied).toBe(true);
  });

  it("an unstudied lesson can be saved with nothing graded: that is the study mark", async () => {
    save.mockResolvedValue({ ...ok, created: 3 });
    const view = await ready();
    expect(view.queryByLabelText(/studied it again/)).toBeNull();
    // Said before the tap, not only after it.
    expect(view.getByText("Saving marks this lesson studied.")).toBeTruthy();
    const button = view.getByRole("button", { name: /^Save/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    await fireEvent.click(button);
    expect(await view.findByText("Saved: 3 new cards · counted as studied")).toBeTruthy();
  });

  it("a later Save that records nothing says only what it did", async () => {
    getPlan.mockResolvedValue(plan({ studied: true, listen_count: 1 }));
    save.mockResolvedValue({ ...ok, applied: 1, recorded_study: false });
    const view = await ready();
    await fireEvent.click(within(rowOf("búntag")).getByRole("button", { name: "Good" }));
    await fireEvent.click(view.getByRole("button", { name: /^Save/ }));
    expect(await view.findByText("Saved: 1 graded")).toBeTruthy();
  });

  it("a stale plan (409) refetches and keeps the grades still on screen", async () => {
    save.mockRejectedValueOnce(Object.assign(new Error("stale"), { status: 409 }));
    const view = await ready();
    await fireEvent.click(within(rowOf("búntag")).getByRole("button", { name: "Good" }));
    await fireEvent.click(view.getByRole("button", { name: /^Save/ }));
    expect(
      await view.findByText(
        "Some cards changed since this page loaded. The list is refreshed; check it and save again.",
      ),
    ).toBeTruthy();
    expect(getPlan).toHaveBeenCalledTimes(2);
    expect(
      within(rowOf("búntag")).getByRole("button", { name: "Good" }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("any other failure is shown and nothing is cleared", async () => {
    save.mockRejectedValueOnce(new Error("server down"));
    const view = await ready();
    await fireEvent.click(within(rowOf("búntag")).getByRole("button", { name: "Good" }));
    await fireEvent.click(view.getByRole("button", { name: /^Save/ }));
    expect(await view.findByText("server down")).toBeTruthy();
    expect(getPlan).toHaveBeenCalledTimes(1);
    expect(
      within(rowOf("búntag")).getByRole("button", { name: "Good" }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("links back to where it was opened from", async () => {
    const view = await ready();
    expect(view.getByRole("link", { name: "‹ Back" }).getAttribute("href")).toBe("/c/c1/l/l1");
    expect(view.getByRole("heading", { name: "Grade" })).toBeTruthy();
    expect(view.getByText("Wolff 1")).toBeTruthy();
  });

  it("a Save that changed nothing countable just says Saved", async () => {
    getPlan.mockResolvedValue(plan({ studied: true, listen_count: 1 }));
    save.mockResolvedValue({ ...ok, recorded_study: false });
    const view = await ready();
    await fireEvent.click(within(rowOf("búntag")).getByRole("button", { name: "Good" }));
    await fireEvent.click(view.getByRole("button", { name: /^Save/ }));
    expect(await view.findByText("Saved")).toBeTruthy();
  });

  it("shows a failure that is not an Error as text", async () => {
    getPlan.mockRejectedValueOnce("offline");
    const view = renderPage();
    expect(await view.findByText("offline")).toBeTruthy();
    save.mockRejectedValueOnce("refused");
    getPlan.mockResolvedValue(plan());
    await fireEvent.click(view.getByRole("button", { name: "now" }));
    await view.findByText("búntag");
    await fireEvent.click(view.getByRole("button", { name: /^Save/ }));
    expect(await view.findByText("refused")).toBeTruthy();
  });
});

describe("GradePage — tabs", () => {
  it("returns to Understand from another tab", async () => {
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: /Say it/ }));
    expect(rowOf("búntag")).toBeNull();
    await fireEvent.click(view.getByRole("tab", { name: /Understand/ }));
    expect(rowOf("búntag")).toBeTruthy();
  });

  it("New cards says how many of the lesson's cards are still new", async () => {
    getPlan.mockResolvedValue(plan({ waiting_new: 5 }));
    const view = await ready();
    await fireEvent.click(view.getByRole("tab", { name: /New cards/ }));
    expect(
      view.getByText("5 cards from this lesson are still new; card reviews introduce them."),
    ).toBeTruthy();
  });
});
