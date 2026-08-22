import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Layout guards for the lesson page's sticky player card, in a REAL browser.
 *
 * Same reason as listen-preview-layout.spec.ts: jsdom performs no layout, so
 * the unit suite reports every element at 0×0 and can assert structure but
 * never geometry. Both invariants here are geometric.
 *
 * 1. The stats line spans the card's full content width. When the card header
 *    became a two-column row (title | Read/Listen toggle) on 2026-07-27, the
 *    stats line inherited the LEFT column's width and wrapped to two lines on a
 *    phone — the toggle is only two lines tall, so there is no reason for the
 *    stats to keep clearing it.
 * 2. "Mark as Listened" stays horizontally centered in the card. Folding the
 *    action block from a centered column into a row left-aligned it.
 */

const PHONE = { width: 390, height: 844 };

const TOPIC = "lesson-header-layout-e2e";

/**
 * Imported, not generated: the shared "ordering coffee" cassette has a fixed
 * number of recorded plays and another consumer would exhaust it. Deliberately
 * wordy — enough distinct lemmas that the stats line has several segments and
 * is long enough for the full-width assertion to bite.
 */
const STORY = {
	title: "Lesson header layout",
	key_phrases: [{ phrase: "dober dan", translation: "good day" }],
	scenes: [
		{
			label: "At the Café",
			lines: [
				{ speaker: "female-1", text: "Dober dan, prosim kavo.", translation: "Good day, a coffee please." },
				{ speaker: "male-1", text: "Enainštirideset evrov, prosim.", translation: "Forty-one euros, please." },
				{ speaker: "female-1", text: "Hvala lepa, nasvidenje.", translation: "Thank you, goodbye." },
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
	if (!impRes.ok()) throw new Error(`story import failed: ${impRes.status()} ${await impRes.text()}`);

	const id: string = curriculum.id;
	seededCurriculumId = id;
	return id;
}

async function openLesson(page: import("@playwright/test").Page, cid: string) {
	await page.goto(`/c/${cid}`);
	await page.getByRole("button", { name: "Day 1" }).click();
	await expect(page.getByRole("button", { name: "Render Audio" })).toBeVisible({ timeout: 15000 });
	const card = page.locator(".player-card");
	await expect(card.locator(".mastery-line")).toBeVisible({ timeout: 10000 });
	return card;
}

test.describe.configure({ mode: "serial" });

test("lesson card: stats span the card's full content width on a phone", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	await page.setViewportSize(PHONE);
	const card = await openLesson(page, await curriculumId(request));

	const stats = await card.locator(".mastery-line").boundingBox();
	const toggle = await card.locator(".toggle-pill").boundingBox();
	const cardBox = await card.boundingBox();
	expect(stats && toggle && cardBox).toBeTruthy();

	// The right edge clears the toggle column entirely — that's what "full
	// width" means here, and it is exactly what the two-column header denied.
	expect(stats!.x + stats!.width).toBeGreaterThanOrEqual(toggle!.x + toggle!.width - 1);

	// And it reaches the card's content box (padding is the only slack).
	const pad = stats!.x - cardBox!.x;
	expect(stats!.width).toBeGreaterThanOrEqual(cardBox!.width - 2 * pad - 1);

	// One line, not two: the wrap this test exists to prevent doubles the height.
	// Reference is the percentage span inside it — one inline box on one line —
	// since computed line-height here is `normal`, which parses to NaN.
	const oneLine = await card.locator(".mastery-line .mastery-pct").boundingBox();
	expect(oneLine).toBeTruthy();
	expect(stats!.height).toBeLessThan(oneLine!.height * 1.5);
});

test("lesson card: Mark as Listened stays centered in the card", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	await page.setViewportSize(PHONE);
	const card = await openLesson(page, await curriculumId(request));

	const btn = await card.getByRole("button", { name: "Mark as Listened" }).boundingBox();
	const cardBox = await card.boundingBox();
	expect(btn && cardBox).toBeTruthy();

	// This lesson has never been listened to, so the button is the row's only
	// item and lands dead centre.
	const btnCenter = btn!.x + btn!.width / 2;
	const cardCenter = cardBox!.x + cardBox!.width / 2;
	expect(Math.abs(btnCenter - cardCenter)).toBeLessThanOrEqual(1);

	// Pinned as a rule too, because the geometry above only covers the
	// button-alone state: once a listen adds a confirmation / check-work link
	// beside it, the ROW stays centered and the pair reads as one unit.
	const justify = await card
		.locator(".listen-actions")
		.evaluate((el) => getComputedStyle(el).justifyContent);
	expect(justify).toBe("center");
});
