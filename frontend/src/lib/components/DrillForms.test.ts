/**
 * Tests for DrillForms.svelte — the table of what a lesson's affix drill covers.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import DrillForms from "./DrillForms.svelte";
import { captionBlurPref } from "$lib/stores/captionBlurPref.svelte";
import type { LessonDrill } from "$lib/api";

const drill: LessonDrill = {
  pattern: "mo-mi",
  title: "Affix drill: mo- / mi-",
  labels: ["not begun", "begun"],
  roots: [
    {
      root: "uban",
      english: "accompany",
      new: false,
      forms: [
        { form: "mouban", english: "will go along" },
        { form: "miuban", english: "went along" },
      ],
    },
    {
      root: "lakaw",
      english: "walk",
      new: true,
      forms: [
        { form: "molakaw", english: "will walk" },
        { form: "milakaw", english: "walked" },
      ],
    },
  ],
};

beforeEach(() => {
  localStorage.clear();
  captionBlurPref.set(true);
});

describe("DrillForms", () => {
  it("lists each root with its English, and the English of every form", () => {
    const { container, getByText } = render(DrillForms, { props: { drill } });

    // Not the pattern's name: the page's own title already says that.
    expect(getByText("In this drill")).toBeTruthy();
    expect([...container.querySelectorAll(".root")].map((e) => e.textContent)).toEqual([
      "uban",
      "lakaw",
    ]);
    expect(
      [...container.querySelectorAll(".form-cell .english")].map((e) => e.textContent),
    ).toEqual(["will go along", "went along", "will walk", "walked"]);
  });

  it("names the two columns of forms by whether the thing has begun, not by a tense", () => {
    const { container } = render(DrillForms, { props: { drill } });

    expect(
      [...container.querySelectorAll(".drill-labels .label")].map((e) => e.textContent),
    ).toEqual(["not begun", "begun"]);
    // Over the forms, so before the first root.
    const labels = container.querySelector(".drill-labels")!;
    const first = container.querySelector(".drill-root")!;
    expect(labels.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a drill that names no columns has no heading row over them", () => {
    const { container } = render(DrillForms, {
      props: { drill: { ...drill, labels: [] } },
    });

    expect(container.querySelector(".drill-labels")).toBeNull();
    expect(container.querySelectorAll(".drill-root")).toHaveLength(2);
  });

  it("marks only the roots whose forms are asked for without being modelled", () => {
    const { container } = render(DrillForms, { props: { drill } });
    const rows = [...container.querySelectorAll(".drill-root")];

    expect(rows.map((row) => row.querySelector(".new-tag")?.textContent ?? null)).toEqual([
      null,
      "New",
    ]);
  });

  it("hides every form while captions are blurred: the forms are the answers", () => {
    const { container } = render(DrillForms, { props: { drill } });
    const forms = [...container.querySelectorAll(".form")];

    expect(forms).toHaveLength(4);
    expect(forms.every((f) => f.tagName === "BUTTON" && f.classList.contains("blurred"))).toBe(
      true,
    );
  });

  it("a tap shows that one form and leaves the others hidden", async () => {
    const { container, getByRole } = render(DrillForms, { props: { drill } });

    await fireEvent.click(getByRole("button", { name: "Show the form for: went along" }));

    const state = [...container.querySelectorAll(".form")].map(
      (f) => `${f.textContent}:${f.classList.contains("blurred") ? "hidden" : "shown"}`,
    );
    expect(state).toEqual(["mouban:hidden", "miuban:shown", "molakaw:hidden", "milakaw:hidden"]);
    expect(container.querySelectorAll("button.form")).toHaveLength(3);
  });

  it("shows every form, as plain text, when captions are visible", () => {
    captionBlurPref.set(false);
    const { container } = render(DrillForms, { props: { drill } });

    expect(container.querySelectorAll("button.form")).toHaveLength(0);
    expect(container.querySelectorAll(".form.blurred")).toHaveLength(0);
    expect(container.querySelectorAll("span.form")).toHaveLength(4);
  });
});

// How far the learner is with the drill's pattern, under the table (bd
// tunatale-ve4p.30, option B of the mockup): the three parts of the pace gate
// beside what each needs, amber with a Stuck chip after five drill days.
describe("DrillForms progress", () => {
  type Progress = NonNullable<LessonDrill["progress"]>;
  const onTrack: Progress = {
    review_only: false,
    drill_days: 1,
    drill_days_needed: 2,
    first_passes: 0,
    first_passes_needed: 4,
    reviews: 0,
    passes: 0,
    pass_rate_needed: 0.8,
    met: false,
    stuck: false,
  };

  function meters(container: HTMLElement) {
    return [...container.querySelectorAll(".meter")].map((m) => ({
      label: m.querySelector(".meter-label")?.textContent,
      value: m.querySelector(".meter-value")?.textContent,
      width: (m.querySelector(".bar i") as HTMLElement | null)?.style.width,
      short: m.querySelector(".bar i")?.classList.contains("short"),
    }));
  }

  it("shows no meters when the lesson came without progress", () => {
    const { container } = render(DrillForms, { props: { drill } });
    expect(container.querySelector(".meters")).toBeNull();
  });

  it("puts each part of the gate beside what it needs", () => {
    const { container, getByText } = render(DrillForms, {
      props: { drill: { ...drill, progress: onTrack } },
    });

    expect(getByText("Before the next pattern")).toBeTruthy();
    expect(meters(container)).toEqual([
      { label: "Days you heard the drill", value: "1 / 2", width: "50%", short: false },
      { label: "Cards past a first review", value: "0 / 4", width: "0%", short: false },
      { label: "Reviews passed", value: "none yet", width: "0%", short: false },
    ]);
    expect(container.querySelector(".chip")).toBeNull();
  });

  it("measures the pass rate against the 80% it needs", () => {
    const { container } = render(DrillForms, {
      props: { drill: { ...drill, progress: { ...onTrack, reviews: 6, passes: 4 } } },
    });
    // 4 of 6 is 67%, which is 83% of the way to 80%.
    expect(meters(container)[2]).toEqual({
      label: "Reviews passed",
      value: "67% / 80%",
      width: "83%",
      short: false,
    });
  });

  it("goes amber with a Stuck chip, on the parts that are short, after five drill days", () => {
    const stuck = {
      ...onTrack,
      drill_days: 5,
      first_passes: 2,
      reviews: 6,
      passes: 4,
      stuck: true,
    };
    const { container } = render(DrillForms, { props: { drill: { ...drill, progress: stuck } } });

    expect(container.querySelector(".chip")?.textContent).toBe("Stuck");
    expect(meters(container).map((m) => [m.value, m.width, m.short])).toEqual([
      ["5 / 2", "100%", false],
      ["2 / 4", "50%", true],
      ["67% / 80%", "83%", true],
    ]);
  });

  it("says Passed once the pattern has met the gate", () => {
    const met = { ...onTrack, drill_days: 2, first_passes: 5, reviews: 5, passes: 5, met: true };
    const { container } = render(DrillForms, { props: { drill: { ...drill, progress: met } } });
    expect(container.querySelector(".chip")?.textContent).toBe("Passed");
  });

  it("shows a review-only pattern its drill days alone", () => {
    const reviewOnly = {
      ...onTrack,
      review_only: true,
      drill_days_needed: 1,
      first_passes_needed: 0,
      pass_rate_needed: 0,
    };
    const { container } = render(DrillForms, {
      props: { drill: { ...drill, progress: reviewOnly } },
    });
    expect(meters(container)).toEqual([
      { label: "Days you heard the drill", value: "1 / 1", width: "100%", short: false },
    ]);
  });
});
