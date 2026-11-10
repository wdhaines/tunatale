import { readFileSync } from "node:fs";
import { test, expect } from "./fixtures";
import { backendAvailable } from "./helpers";

/**
 * Geometry of the Reference tab's verb chart (tunatale-h0q3.47), in a REAL browser:
 * jsdom lays nothing out. The published chart was 1,187px wide inside a 340px box on a
 * phone, so the owner chose a card per row there (option A, 2026-11-09); the grid stays
 * for a desktop. The e2e backend has no Cebuano, so the two reference routes are served
 * the committed chart file: what is under test is layout, and the routes themselves are
 * tested in backend/tests/test_reference_verb_chart.py.
 */

const CHART = readFileSync(
  new URL("../../backend/app/plugins/languages/ceb/data/verb_chart.json", import.meta.url),
  "utf-8",
);

async function stubReference(page: import("@playwright/test").Page) {
  await page.route("**/api/reference", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ pages: [{ id: "verb-chart", title: "The verb chart" }] }),
    }),
  );
  await page.route("**/api/reference/verb-chart", (route) =>
    route.fulfill({ contentType: "application/json", body: CHART }),
  );
}

const overflowX = (page: import("@playwright/test").Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

for (const [name, width, height] of [
  ["a 412px phone", 412, 915],
  ["a 320px phone", 320, 700],
] as const) {
  test(`the verb chart on ${name}: cards, not the grid, and nothing wider than the screen`, async ({
    page,
    request,
  }) => {
    test.skip(!(await backendAvailable(request)), "Backend not available");
    await page.setViewportSize({ width, height });
    await stubReference(page);
    // The opened card holds the longest example sentences: they must wrap, not widen the page.
    await page.goto("/reference/verb-chart?row=stative-mag");

    const card = page.locator('section.card[data-row="stative-mag"]');
    await expect(card.locator(".entry").first()).toBeVisible({ timeout: 15000 });
    await expect(page.locator(".grid-box")).toBeHidden();
    await expect(page.locator(".jump.narrow")).toBeVisible();
    expect(await overflowX(page)).toBe(0);

    // The card's column labels and its body sit side by side inside the card.
    const fits = await card.evaluate((el) => {
      const box = el.getBoundingClientRect();
      return [...el.querySelectorAll(".entry-body, .chip")].every((c) => {
        const r = c.getBoundingClientRect();
        return r.left >= box.left - 0.5 && r.right <= box.right + 0.5;
      });
    });
    expect(fits).toBe(true);
  });
}

test("the verb chart on a 1280px desktop: the whole grid, no sideways scroll", async ({ page, request }) => {
  test.skip(!(await backendAvailable(request)), "Backend not available");
  await page.setViewportSize({ width: 1280, height: 900 });
  await stubReference(page);
  await page.goto("/reference/verb-chart");

  const box = page.locator(".grid-box");
  await expect(box.locator("tbody tr").first()).toBeVisible({ timeout: 15000 });
  await expect(page.locator("section.card").first()).toBeHidden();
  expect(await overflowX(page)).toBe(0);
  // The grid fits its box: the published chart's own check at 1280px.
  expect(await box.evaluate((el) => el.scrollWidth - el.clientWidth)).toBe(0);
  // The Reference tab is in the nav, because the stub says this language has a page.
  // Scoped to the nav: the page's breadcrumb is a second "Reference" link.
  await expect(page.locator("nav.global-nav").getByRole("link", { name: "Reference" })).toBeVisible();
});

/** How far a jump target's top sits below the sticky nav's bottom (negative = hidden under it). */
const belowNav = (page: import("@playwright/test").Page, selector: string) =>
  page.evaluate((sel) => {
    const nav = document.querySelector("nav.global-nav")!.getBoundingClientRect();
    return document.querySelector(sel)!.getBoundingClientRect().top - nav.bottom;
  }, selector);

for (const [name, width, height, jumps, target] of [
  ["a 412px phone", 412, 915, ".jump.narrow", "#card-group-patient"],
  ["a 1280px desktop", 1280, 900, ".jump.wide", "#group-patient"],
] as const) {
  test(`on ${name}, a jump lands its trigger below the sticky nav, not under it`, async ({ page, request }) => {
    test.skip(!(await backendAvailable(request)), "Backend not available");
    await page.setViewportSize({ width, height });
    await stubReference(page);
    await page.goto("/reference/verb-chart");
    await expect(page.locator(jumps)).toBeVisible({ timeout: 15000 });
    // Tap once the nav has its final height, as a person would: the review badge and
    // the Reference tab arrive after the page, and on a phone they add to the nav's
    // height (the gate's shared backend has queue stats, so its badge loads late).
    await expect(page.locator("nav.global-nav").getByRole("link", { name: "Reference" })).toBeVisible();
    await page.waitForLoadState("networkidle");

    await page.locator(jumps).getByRole("link", { name: "Patient" }).click();
    await expect(page).toHaveURL(new RegExp(`${target}$`));
    // One frame for the scroll to settle before reading rects.
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve(null))));
    const gap = await belowNav(page, target);
    expect(gap).toBeGreaterThanOrEqual(0);
    expect(gap).toBeLessThan(40);
  });
}
