import { describe, expect, it } from "vitest";
import { chartHref } from "./verbChartLink.js";

describe("chartHref", () => {
  it("names the row and each affix to mark", () => {
    expect(chartHref({ row: "agent-mo", affixes: ["mo-", "mi-"] })).toBe(
      "/reference/verb-chart?row=agent-mo&affix=mo-&affix=mi-",
    );
  });

  it("reads back through URLSearchParams, the way the chart page reads it", () => {
    const href = chartHref({ row: "circumstance-an", affixes: ["gi-…-an"] });
    const params = new URL(href, "http://x").searchParams;
    expect([params.get("row"), params.getAll("affix")]).toEqual(["circumstance-an", ["gi-…-an"]]);
  });
});
