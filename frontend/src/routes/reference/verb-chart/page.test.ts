/**
 * /reference/verb-chart — loads the chart; `?row=` highlights a row and scrolls to the
 * copy of it that is laid out (the grid on a desktop, the card on a phone).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/svelte";
import type { VerbChart } from "#lib/api.js";
import chartJson from "../../../../../backend/app/plugins/languages/ceb/data/verb_chart.json";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("$app/state", () => ({
  page: {
    get url() {
      return new URL(`http://test/reference/verb-chart${nav.search}`);
    },
  },
}));
vi.mock("#lib/api.js", () => ({ api: { getVerbChart: vi.fn() } }));

import { api } from "#lib/api.js";
import ChartPage from "./+page.svelte";

const mockChart = vi.mocked(api.getVerbChart);
const chart = chartJson as VerbChart;
const scrolled: Element[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = "";
  scrolled.length = 0;
  // jsdom lays nothing out: report the CARD copy of a row as the one on screen.
  vi.spyOn(Element.prototype, "getClientRects").mockImplementation(function (this: Element) {
    const shown = this.matches("section.card");
    return { length: shown ? 1 : 0 } as DOMRectList;
  });
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
    scrolled.push(this);
  });
});
afterEach(() => vi.restoreAllMocks());

describe("/reference/verb-chart", () => {
  it("says it is loading, then draws the chart under its title", async () => {
    mockChart.mockResolvedValue(chart);
    const { getByText, findByRole } = render(ChartPage);
    expect(getByText("Loading…")).toBeTruthy();
    expect((await findByRole("heading", { level: 1 })).textContent).toBe("The verb chart");
    expect(scrolled).toEqual([]);
  });

  it("scrolls to the laid-out copy of the row a link names", async () => {
    nav.search = "?row=patient-on";
    mockChart.mockResolvedValue(chart);
    render(ChartPage);
    await waitFor(() => expect(scrolled).toHaveLength(1));
    expect(scrolled[0].matches('section.card[data-row="patient-on"]')).toBe(true);
  });

  it("scrolls nowhere for a row the chart does not have", async () => {
    nav.search = "?row=nope";
    mockChart.mockResolvedValue(chart);
    const { findByRole } = render(ChartPage);
    await findByRole("heading", { level: 1 });
    await new Promise((resolve) => setTimeout(resolve, 0)); // past the post-render tick
    expect(scrolled).toEqual([]);
  });

  it("says the language has no chart when the server has none", async () => {
    mockChart.mockRejectedValue(new Error("404"));
    const { findByText } = render(ChartPage);
    expect(await findByText("No verb chart for this language.")).toBeTruthy();
  });
});
