import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/svelte";

const urlParams = vi.hoisted(() => new URLSearchParams());
vi.mock("$app/state", () => ({
  page: {
    params: { contentId: "l9" },
    get url() {
      return { searchParams: urlParams };
    },
  },
}));
vi.mock("#lib/api.js", () => ({
  api: {
    getGradePlan: vi.fn(() => new Promise(() => {})),
    getTranscript: vi.fn(() => new Promise(() => {})),
  },
}));

import { api } from "#lib/api.js";
import Page from "./+page.svelte";

describe("/grade/[contentId]", () => {
  it("opens the Grade page for the content in the path, with its name and way back", () => {
    urlParams.set("back", "/c/c1/l/l9");
    urlParams.set("title", "Wolff 1");
    const view = render(Page);
    expect(api.getGradePlan).toHaveBeenCalledWith("l9", 3);
    expect(view.getByRole("link", { name: "← Back" }).getAttribute("href")).toBe("/c/c1/l/l9");
    expect(view.getByText("Wolff 1")).toBeTruthy();
  });

  it("goes home when the way back is missing or off-site, and has no name to show", () => {
    urlParams.delete("title");
    urlParams.set("back", "//evil.example");
    const view = render(Page);
    expect(view.getByRole("link", { name: "← Back" }).getAttribute("href")).toBe("/");
  });
});
