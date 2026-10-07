import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Geometry of the reader's chips in the sticky card (bd tunatale-685k), in a
 * REAL browser: jsdom lays nothing out, so every rect there is 0x0 and "the
 * same height" is true of any two elements.
 *
 * The invariants, both the user's:
 *   - Player expanded: English and Recall share one row in the sticky card.
 *   - Player collapsed: only Recall remains, ON the row with Mark as Listened
 *     and exactly as tall as that button ("make the chip the same height as
 *     the listen button in the collapsed mode"). At 320px one line of chip does
 *     not fit beside the button; the row must still be ONE row of equal height,
 *     not a wrapped chip under the button.
 *   - Neither state widens the page.
 *
 * The audio response is stubbed: without a rendered player there is nothing to
 * collapse, and e2e never renders audio.
 */

const TOPIC = "reader-chips-layout-e2e";

/**
 * Imported, not generated, for the reason lesson-header-layout.spec.ts gives:
 * the shared cassettes have a fixed number of recorded plays and another
 * consumer would exhaust them.
 *
 * Two speakers so the chip column has more than one letter in it, and lines long
 * enough to wrap on a 390px phone — an unwrapped line would satisfy the height
 * assertion even when stacked, because a stacked chip on a one-line body is only
 * two rows tall either way.
 */
const STORY = {
  title: "Transcript layout",
  key_phrases: [{ phrase: "dober dan", translation: "good day" }],
  scenes: [
    {
      label: "At the Café",
      lines: [
        {
          speaker: "female-1",
          text: "Dober dan, prosim eno veliko kavo z mlekom in dva kosa torte.",
          translation: "Good day, one large coffee with milk and two pieces of cake please.",
        },
        {
          speaker: "male-1",
          text: "Enainštirideset evrov, prosim, in izvolite račun za vse skupaj.",
          translation: "Forty-one euros please, and here is the receipt for everything.",
        },
        {
          speaker: "female-1",
          text: "Hvala lepa, nasvidenje in lep dan še naprej vam želim.",
          translation: "Thank you very much, goodbye and I wish you a nice day.",
        },
      ],
    },
  ],
  dialogue_glosses: [
    { word: "kavo", translation: "coffee" },
    { word: "evrov", translation: "euros" },
    { word: "hvala", translation: "thanks" },
    { word: "nasvidenje", translation: "goodbye" },
  ],
  morphology_focus: [],
};

let seededCurriculumId: string | null = null;

async function curriculumId(
  request: import("@playwright/test").APIRequestContext,
): Promise<string> {
  if (seededCurriculumId !== null) return seededCurriculumId;

  const currRes = await request.post(`${BACKEND}/api/curriculum/import`, {
    data: {
      topic: TOPIC,
      language_code: "sl",
      cefr_level: "A2",
      days: [
        {
          day: 1,
          title: "Day 1",
          focus: TOPIC,
          collocations: ["dober dan"],
          learning_objective: "greet and order",
          story_guidance: `Practice ${TOPIC}`,
        },
      ],
    },
  });
  if (!currRes.ok())
    throw new Error(`curriculum import failed: ${currRes.status()} ${await currRes.text()}`);
  const curriculum = await currRes.json();

  const impRes = await request.post(`${BACKEND}/api/story/import`, {
    data: { curriculum_id: curriculum.id, day: 1, story: STORY },
  });
  if (!impRes.ok())
    throw new Error(`story import failed: ${impRes.status()} ${await impRes.text()}`);

  const id: string = curriculum.id;
  seededCurriculumId = id;
  return id;
}

test.describe.configure({ mode: "serial" });

async function stubAudio(page: import("@playwright/test").Page) {
  await page.route("**/api/audio/lesson/*", async (route) => {
    const types = ["key_phrases", "natural_speed", "translated", "slow_speed", "slow_translated"];
    const cue = (i: number, type: string) => ({
      index: 0,
      start_ms: 0,
      end_ms: 900,
      section_index: i,
      section_type: type,
      phrase_index: 0,
      role: "female-1",
      language_code: "sl",
      text: "dober dan",
      ref:
        type === "key_phrases"
          ? { kind: "key_phrase", target_index: 0 }
          : { kind: "dialogue_line", target_index: 0 },
    });
    const sections = types.map((type, i) => ({
      audio_id: `stub-${type}`,
      section_index: i,
      section_type: type,
      title: type,
      cues: [cue(i, type)],
    }));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        audio_id: "stub-audio",
        lesson_id: "any",
        sections,
        cues: sections.map((s) => s.cues[0]),
      }),
    });
  });
}

const overflowX = (page: import("@playwright/test").Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

for (const [name, width, height] of [
  ["a 412px phone", 412, 915],
  ["a 320px phone", 320, 700],
] as const) {
  test(`reader chips on ${name}: one row expanded; collapsed, Recall matches the button`, async ({
    page,
    request,
  }) => {
    test.skip(!(await backendAvailable(request)), "Backend not available");
    await page.setViewportSize({ width, height });
    await stubAudio(page);
    await page.goto(`/c/${await curriculumId(request)}`);
    await page.getByRole("button", { name: "Day 1" }).click();
    await page.getByRole("button", { name: "Read", exact: true }).click();

    const card = page.locator(".player-card");
    const english = card.getByTestId("reader-english-chip");
    const recall = card.getByTestId("reader-recall-chip");
    const button = card.locator(".listen-btn");
    // If this times out the audio stub stopped producing a player: do not
    // relax it, or the collapse half below measures a card with no toggle.
    await expect(card.locator(".collapse-toggle")).toBeVisible({ timeout: 15000 });
    await expect(english).toBeVisible();

    // Expanded: the two chips share a row.
    const e = (await english.boundingBox())!;
    const r = (await recall.boundingBox())!;
    expect(Math.abs(e.y - r.y)).toBeLessThanOrEqual(0.5);
    expect(Math.abs(e.height - r.height)).toBeLessThanOrEqual(0.5);
    expect(r.x).toBeGreaterThanOrEqual(e.x + e.width);
    expect(await overflowX(page)).toBe(0);

    // Collapsed: English leaves, Recall joins the button's row at its height.
    await card.locator(".collapse-toggle").click();
    await expect(english).toHaveCount(0);
    await expect(recall).toBeVisible();
    const b = (await button.boundingBox())!;
    const c = (await recall.boundingBox())!;
    expect(Math.abs(b.y - c.y)).toBeLessThanOrEqual(0.5);
    expect(Math.abs(b.height - c.height)).toBeLessThanOrEqual(0.5);
    expect(c.x).toBeGreaterThanOrEqual(b.x + b.width);
    expect(c.x + c.width).toBeLessThanOrEqual(width);
    expect(await overflowX(page)).toBe(0);
  });
}
