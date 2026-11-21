import { describe, it, expect } from "vitest";
import { render } from "@testing-library/svelte";
import BackLink from "./BackLink.svelte";

// One back link for every page that has a way back (Grade, a course, its plan,
// review sessions): same arrow, same place, same look.

describe("BackLink", () => {
  it("links to its target, named with an arrow and the label", () => {
    const view = render(BackLink, { props: { href: "/c/c1", label: "Lessons" } });
    const link = view.getByRole("link", { name: "← Lessons" });
    expect(link.getAttribute("href")).toBe("/c/c1");
    expect(link.classList.contains("back-link")).toBe(true);
  });
});
