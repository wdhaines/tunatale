import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import ReadListenToggle from "./ReadListenToggle.svelte";
import { lessonModePref } from "$lib/stores/lessonModePref.svelte";
import { playerCollapsedPref } from "$lib/stores/playerCollapsedPref.svelte";
import { tick } from "svelte";

beforeEach(() => {
  // jsdom has no matchMedia, and lessonModePref.init() reads it to pick the
  // viewport default — the same stub the page suites use.
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(() => ({
    matches: false,
    media: "(max-width: 640px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  localStorage.clear();
  lessonModePref.set("read");
  playerCollapsedPref.set(false);
  localStorage.clear();
});

describe("ReadListenToggle", () => {
  it("renders the two modes", () => {
    const { container } = render(ReadListenToggle);
    expect(
      Array.from(container.querySelectorAll(".toggle-pill button")).map((b) =>
        b.textContent?.trim(),
      ),
    ).toEqual(["Read", "Listen"]);
  });

  it("switching mode writes the pref", async () => {
    const { getByText } = render(ReadListenToggle);
    await fireEvent.click(getByText("Listen"));
    expect(lessonModePref.mode).toBe("listen");
  });
});

describe("the collapse control beside the pill", () => {
  it("is absent unless the caller says there is a player to collapse", () => {
    const { container } = render(ReadListenToggle);
    expect(container.querySelector(".collapse-toggle")).toBeFalsy();
  });

  it("appears on Read when collapsible", () => {
    const { container } = render(ReadListenToggle, { props: { collapsible: true } });
    const t = container.querySelector(".collapse-toggle");
    expect(t).toBeTruthy();
    expect(t!.getAttribute("aria-expanded")).toBe("true");
  });

  it("is absent in Listen — there the player IS the content", async () => {
    lessonModePref.set("listen");
    const { container } = render(ReadListenToggle, { props: { collapsible: true } });
    await tick();
    expect(container.querySelector(".collapse-toggle")).toBeFalsy();
  });

  it("sits in the same row as the pill, which is what carries its meaning", () => {
    // It has no room for a word, so the grouping is the affordance. If it ever
    // stops being a sibling of the pill it is a stray icon again.
    const { container } = render(ReadListenToggle, { props: { collapsible: true } });
    const row = container.querySelector(".mode-row")!;
    expect(row.querySelector(":scope > .toggle-pill")).toBeTruthy();
    expect(row.querySelector(":scope > .collapse-toggle")).toBeTruthy();
  });

  it("toggles the pref and flips its own state", async () => {
    const { container } = render(ReadListenToggle, { props: { collapsible: true } });
    const t = container.querySelector<HTMLButtonElement>(".collapse-toggle")!;
    await fireEvent.click(t);
    expect(playerCollapsedPref.collapsed).toBe(true);
    expect(t.getAttribute("aria-expanded")).toBe("false");
    await fireEvent.click(t);
    expect(playerCollapsedPref.collapsed).toBe(false);
    expect(t.getAttribute("aria-expanded")).toBe("true");
  });

  it("names what the next press does, for anyone who cannot see the chevron", async () => {
    const { container } = render(ReadListenToggle, { props: { collapsible: true } });
    const t = container.querySelector<HTMLButtonElement>(".collapse-toggle")!;
    expect(t.getAttribute("aria-label")).toMatch(/hide/i);
    await fireEvent.click(t);
    expect(t.getAttribute("aria-label")).toMatch(/show/i);
  });

  it("restores a stored collapse on mount", () => {
    localStorage.setItem("playerCollapsed", "on");
    const { container } = render(ReadListenToggle, { props: { collapsible: true } });
    expect(container.querySelector(".collapse-toggle")!.getAttribute("aria-expanded")).toBe(
      "false",
    );
  });
});
