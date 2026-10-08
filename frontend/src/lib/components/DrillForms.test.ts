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
