import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Making a phrase card by dragging across words in the dialogue, with a REAL
 * mouse (bd tunatale-685k).
 *
 * WHY THIS IS IN A BROWSER. The sibling of this gesture, tap-to-select, passed
 * its unit tests from April to October 2026 while never completing in a
 * browser: jsdom's `fireEvent.click` dispatches ONE event, and a real tap
 * dispatches pointerdown, pointerup, click. The pointer handlers cleared the
 * anchor the click handler then set, every time. Nothing walked the gesture
 * with real input, so nothing saw it. Tap mode was removed; drag is the path
 * that stays, and this is the journey nobody had: press on a word, move, let
 * go, confirm, and the card request leaves with its context.
 *
 * The event SEQUENCE is the browser's to produce, which is what puts this
 * above the component tier (.claude/rules/test-tiers.md).
 */

const TOPIC = "phrase-drag-e2e";

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

test("dragging across three words offers a phrase card, and Create sends it with its sentence", async ({
  page,
  request,
}) => {
  test.skip(!(await backendAvailable(request)), "Backend not available");
  await page.setViewportSize({ width: 1100, height: 900 });
  // The bar's translate button asks the backend for a translation, which is
  // an LLM call, and creating a card with NO translation makes the backend
  // ask for one itself: either is a cassette miss, and the e2e run fails on
  // any miss. Stubbed and clicked, it also proves the translation reaches
  // the card.
  await page.route("**/api/srs/translate", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ translation: "please one large" }),
    });
  });
  await page.goto(`/c/${await curriculumId(request)}`);
  await page.getByRole("button", { name: "Day 1" }).click();
  await page.getByRole("button", { name: "Read", exact: true }).click();

  const word = (i: number) => page.locator(`[data-line-index="0"][data-word-index="${i}"]`).first();
  await expect(word(3)).toBeVisible({ timeout: 15000 });
  const texts = await Promise.all(
    [1, 2, 3].map(async (i) => (await word(i).textContent())!.trim()),
  );

  const from = (await word(1).boundingBox())!;
  const to = (await word(3).boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 8 });
  await page.mouse.up();

  const bar = page.locator(".phrase-confirm-bar");
  await expect(bar).toBeVisible();

  await bar.locator(".phrase-translate-btn").click();
  await expect(bar.locator(".phrase-translation-input")).toHaveValue("please one large");

  const created = page.waitForRequest(
    (r) => r.method() === "POST" && new URL(r.url()).pathname === "/api/srs/items",
  );
  await bar.locator(".confirm-create").click();
  const body = (await created).postDataJSON();

  // Punctuation belongs to the word's surface in some lines; compare on letters.
  const letters = (s: string) => s.replace(/[^\p{L} ]/gu, "").trim();
  expect(letters(body.text)).toBe(letters(texts.join(" ")));
  expect(body.word_count).toBe(3);
  expect(body.translation).toBe("please one large");
  expect(body.source_line_index).toBe(0);
  expect(typeof body.source_sentence).toBe("string");
  expect(letters(body.source_sentence)).toContain(letters(body.text));
  await expect(bar).toBeHidden();
});
