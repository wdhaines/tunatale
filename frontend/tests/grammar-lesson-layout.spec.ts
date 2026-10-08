import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Geometry of the grammar-lesson UI on a phone (bd tunatale-ve4p.5), in a REAL
 * browser: jsdom lays nothing out, so "fits" and "on one row" are true of any
 * elements there.
 *
 * Two surfaces, both new and both built from text whose length the app does
 * not choose (root lists, affixed forms):
 *
 *   - "Add a grammar lesson" on the curriculum page: each pattern is a row of
 *     text beside an Add button. The text must wrap INSIDE its row; the button
 *     must stay on the row and inside the screen.
 *   - The table of forms on a drill-only lesson page: a root and its forms are
 *     one row of three cells, and a long form wraps in its cell.
 *
 * Neither may widen the page. The e2e backend is Slovene, which has no affix
 * patterns, so the pattern list, the lesson body and the audio are stubbed on
 * top of a real imported curriculum: what is under test is layout, and the
 * routes themselves are tested against a real Cebuano deck in the backend.
 */

const TOPIC = "grammar-lesson-layout-e2e";

const STORY = {
  title: "Grammar layout",
  key_phrases: [{ phrase: "dober dan", translation: "good day" }],
  scenes: [
    {
      label: "At the Café",
      lines: [{ speaker: "female-1", text: "Dober dan.", translation: "Good day." }],
    },
  ],
  dialogue_glosses: [{ word: "dan", translation: "day" }],
  morphology_focus: [],
};

// The owner's deck on 2026-10-07: five roots each for the first two patterns
// (the longest line this row has had to hold), and none for the third.
const PATTERNS = [
  {
    key: "mo-mi",
    title: "Affix drill: mo- / mi-",
    roots: ["lakaw", "inom", "adto", "anhi", "uban"],
    ready: true,
  },
  {
    key: "mag-nag",
    title: "Affix drill: mag- / nag-",
    roots: ["lakaw", "ampo", "dala", "kita", "uban"],
    ready: true,
  },
  { key: "ma-na", title: "Affix drill: ma- / na-", roots: [], ready: false },
];

const root = (name: string, english: string, isNew: boolean, forms: [string, string][]) => ({
  root: name,
  english,
  new: isNew,
  forms: forms.map(([form, formEnglish]) => ({ form, english: formEnglish })),
});

const DRILL = {
  pattern: "mag-nag",
  title: "Affix drill: mag- / nag-",
  roots: [
    root("uban", "accompany", false, [
      ["mag-uban", "will go together"],
      ["nag-uban", "went together"],
    ]),
    // Longer than any real row so far: a long root, long forms, long English.
    root("istoryahanay", "talk with one another", true, [
      ["mag-istoryahanay", "will talk with one another"],
      ["nag-istoryahanay", "talked with one another"],
    ]),
  ],
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
          learning_objective: "greet",
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

async function stubGrammar(page: import("@playwright/test").Page) {
  await page.route("**/api/curriculum/*/grammar-patterns", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PATTERNS) }),
  );
  // The real lesson, turned into a drill-only one: same id, so every other
  // request the page makes for it still reaches the backend and finds it.
  await page.route("**/api/story/*", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const real = await (await route.fetch()).json();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...real,
        title: DRILL.title,
        key_phrases: [],
        sections: [
          {
            type: "affix_drill",
            phrases: [
              { text: "Say: went together.", role: "prompt", language_code: "en", voice_id: "v" },
            ],
          },
        ],
        drill: DRILL,
      }),
    });
  });
  await page.route("**/api/audio/lesson/*", async (route) => {
    const cue = {
      index: 0,
      start_ms: 0,
      end_ms: 900,
      section_index: 0,
      section_type: "affix_drill",
      phrase_index: 0,
      role: "prompt",
      language_code: "en",
      text: "Say: went together.",
      ref: { kind: "drill", target_index: 0 },
    };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        audio_id: "stub-audio",
        lesson_id: "any",
        sections: [
          {
            audio_id: "stub-drill",
            section_index: 0,
            section_type: "affix_drill",
            title: "Affix Drill",
            cues: [cue],
          },
        ],
        cues: [cue],
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
  test(`adding a grammar lesson on ${name}: every Add button stays on its row and on the screen`, async ({
    page,
    request,
  }) => {
    test.skip(!(await backendAvailable(request)), "Backend not available");
    await page.setViewportSize({ width, height });
    await stubGrammar(page);
    await page.goto(`/c/${await curriculumId(request)}`);

    await page.getByRole("button", { name: /Add a grammar lesson/ }).click();
    const rows = page.locator(".grammar-pattern");
    await expect(rows).toHaveCount(3);

    // Every rect is read in ONE frame. Read one await at a time, a row and its
    // own button were measured either side of the rate-limit chip above them
    // arriving, and the button "left" its row by the chip's 9px (the gate,
    // 2026-10-07; the spec passed when run alone).
    const measured = await rows.evaluateAll((els) =>
      els.map((el) => {
        const rect = (node: Element) => {
          const r = node.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
        };
        return {
          row: rect(el),
          text: rect(el.querySelector(".pattern-text")!),
          add: rect(el.querySelector(".pattern-add")!),
        };
      }),
    );
    expect(measured).toHaveLength(3);
    for (const { row, text, add } of measured) {
      // Beside the text, not under it; inside the row; inside the screen.
      expect(add.left).toBeGreaterThanOrEqual(text.right);
      expect(add.top).toBeGreaterThanOrEqual(row.top);
      expect(add.bottom).toBeLessThanOrEqual(row.bottom + 0.5);
      expect(add.right).toBeLessThanOrEqual(width);
    }
    expect(await overflowX(page)).toBe(0);
  });

  test(`a drill-only lesson on ${name}: each root and its forms are one row, inside the screen`, async ({
    page,
    request,
  }) => {
    test.skip(!(await backendAvailable(request)), "Backend not available");
    await page.setViewportSize({ width, height });
    await stubGrammar(page);
    await page.goto(`/c/${await curriculumId(request)}`);
    await page.getByRole("button", { name: "Day 1" }).click();

    const rows = page.locator(".drill-root");
    await expect(rows).toHaveCount(2, { timeout: 15000 });
    // The player is there and has no phase row: if this fails the audio stub
    // stopped producing a player and the page below is not the one a learner sees.
    await expect(page.locator(".player .play-btn")).toBeVisible();
    await expect(page.locator(".phase-row")).toHaveCount(0);

    // One frame for every rect, for the reason given in the test above.
    const measured = await rows.evaluateAll((els) =>
      els.map((el) =>
        [...el.querySelectorAll(".root-cell, .form-cell")].map((cell) => {
          const c = cell.getBoundingClientRect();
          const w = cell.querySelector(".root, .form")!.getBoundingClientRect();
          return { left: c.left, right: c.right, top: c.top, wordRight: w.right };
        }),
      ),
    );
    expect(measured).toHaveLength(2);
    for (const cells of measured) {
      expect(cells).toHaveLength(3);
      // One row: the three cells start at the same height and run left to right.
      expect(Math.abs(cells[0].top - cells[1].top)).toBeLessThanOrEqual(0.5);
      expect(Math.abs(cells[1].top - cells[2].top)).toBeLessThanOrEqual(0.5);
      expect(cells[1].left).toBeGreaterThanOrEqual(cells[0].right);
      expect(cells[2].left).toBeGreaterThanOrEqual(cells[1].right);
      expect(cells[2].right).toBeLessThanOrEqual(width);
      // And each cell's own words stay inside it. The cells are grid tracks, so
      // they keep their width whatever is in them: a root too long for its
      // track ran on over the first form (seen at 412px, 2026-10-07) while
      // every cell box above was still exactly where it should be.
      for (const cell of cells) expect(cell.wordRight).toBeLessThanOrEqual(cell.right + 0.5);
    }
    expect(await overflowX(page)).toBe(0);
  });
}
