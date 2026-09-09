import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Layout guard for the Read transcript's speaker column, in a REAL browser.
 *
 * Same reason as lesson-header-layout.spec.ts and listen-preview-layout.spec.ts:
 * jsdom performs no layout, so the unit suite reports every element at 0×0. This
 * invariant is purely geometric — the DOM order (chip, then words) is identical
 * whether the chip sits above the line or beside it, so only a laid-out browser
 * can tell the two apart.
 *
 * The invariant: on a phone the speaker chip sits BESIDE its line, not above it.
 * `.dialogue-line` used to be `flex-direction: column` with a `min-width: 641px`
 * override flipping it to `row` — i.e. the wide viewport got the compact layout
 * and the narrow one got the stack. Measured at 390×844 over 44 lines, that cost
 * 4495.5px of dialogue where the row costs 2337.5px (−48%).
 */

const PHONE = { width: 390, height: 844 };

const TOPIC = "transcript-layout-e2e";

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
	if (!impRes.ok()) throw new Error(`story import failed: ${impRes.status()} ${await impRes.text()}`);

	const id: string = curriculum.id;
	seededCurriculumId = id;
	return id;
}

test.describe.configure({ mode: "serial" });

test("transcript: the speaker chip sits beside its line on a phone, not above it", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	await page.setViewportSize(PHONE);

	await page.goto(`/c/${await curriculumId(request)}`);
	await page.getByRole("button", { name: "Day 1" }).click();
	await page.getByRole("button", { name: "Read", exact: true }).click();

	const line = page.locator(".dialogue-line").first();
	await expect(line.locator(".dialogue-role-chip")).toBeVisible({ timeout: 15000 });

	const chip = await line.locator(".dialogue-role-chip").boundingBox();
	const body = await line.locator(".dialogue-line-body").boundingBox();
	expect(chip && body).toBeTruthy();

	// BESIDE, not above: the words start to the RIGHT of the chip and share its
	// rows. Stacked, body.y would clear the chip's bottom entirely.
	expect(body!.x).toBeGreaterThanOrEqual(chip!.x + chip!.width);
	expect(body!.y).toBeLessThan(chip!.y + chip!.height);

	// The chip's own row is what the stack spent and the row reclaims: the line
	// is no taller than its text. Reference is the body, which holds every
	// wrapped row of the sentence — a stacked chip adds its height on top.
	expect(line.first()).toBeTruthy();
	const lineBox = await line.boundingBox();
	expect(lineBox!.height).toBeLessThan(body!.height + chip!.height);

	// And the chip is not jammed against the first word.
	expect(body!.x - (chip!.x + chip!.width)).toBeGreaterThanOrEqual(4);
});

/**
 * The key-phrase list has the SAME inversion the dialogue line had: stacked on a
 * phone, compact on desktop. Each mobile row spends a full-width 44px play
 * button — measured 308px of a 691px block, 45% of it — while the
 * `min-width: 641px` block already makes it an inline button.
 *
 * ⚠️ The button renders only when a playable cue exists, so the list is
 * button-less until audio is rendered — and e2e never renders audio (that is a
 * live TTS round trip). The audio response is therefore stubbed. Without the
 * stub this test would pass vacuously against a row that has no button in it at
 * all, which is the exact shape of a test that cannot fail.
 */
async function stubAudio(page: import("@playwright/test").Page, lessonId: string) {
	await page.route("**/api/audio/lesson/*", async (route) => {
		const cue = (index: number, targetIndex: number) => ({
			index,
			start_ms: index * 1000,
			end_ms: index * 1000 + 900,
			section_index: 0,
			section_type: "key_phrases",
			phrase_index: targetIndex,
			role: "female-1",
			language_code: "sl",
			text: "dober dan",
			ref: { kind: "key_phrase", target_index: targetIndex },
		});
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				audio_id: "stub-audio",
				lesson_id: lessonId,
				sections: [
					{
						audio_id: "stub-audio",
						section_index: 0,
						section_type: "key_phrases",
						title: "Key Phrases",
						cues: [cue(0, 0)],
					},
				],
				cues: [cue(0, 0)],
			}),
		});
	});
}

test("transcript: the key-phrase play button sits beside its text on a phone", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	await page.setViewportSize(PHONE);
	const cid = await curriculumId(request);
	await stubAudio(page, "any");

	await page.goto(`/c/${cid}`);
	await page.getByRole("button", { name: "Day 1" }).click();
	await page.getByRole("button", { name: "Read", exact: true }).click();

	const row = page.locator(".key-phrases-list li").first();
	const btn = row.locator(".seek-btn");
	// If this times out the stub stopped working — do NOT relax it into an
	// optional check, or the geometry below starts measuring a row with no
	// button and passes for the wrong reason.
	await expect(btn).toBeVisible({ timeout: 15000 });

	const b = await btn.boundingBox();
	const text = await row.locator(".kp-text").boundingBox();
	const li = await row.boundingBox();
	expect(b && text && li).toBeTruthy();

	// BESIDE, not below: the button starts right of the text and shares its rows.
	expect(b!.x).toBeGreaterThanOrEqual(text!.x + text!.width - 1);
	expect(b!.y).toBeLessThan(text!.y + text!.height);

	// The row costs its tallest child plus padding, not text THEN button.
	expect(li!.height).toBeLessThan(text!.height + b!.height);

	// The tap target survives the compaction — this is the whole risk of the
	// change, and 44x44 is the floor the full-width button was buying.
	expect(b!.width).toBeGreaterThanOrEqual(44);
	expect(b!.height).toBeGreaterThanOrEqual(44);
});

test("transcript: the desktop key-phrase button keeps its full-height target", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	await page.setViewportSize({ width: 1280, height: 900 });
	const cid = await curriculumId(request);
	await stubAudio(page, "any");

	await page.goto(`/c/${cid}`);
	await page.getByRole("button", { name: "Day 1" }).click();
	await page.getByRole("button", { name: "Read", exact: true }).click();

	const row = page.locator(".key-phrases-list li").first();
	const btn = row.locator(".seek-btn");
	await expect(btn).toBeVisible({ timeout: 15000 });

	const b = await btn.boundingBox();
	const text = await row.locator(".kp-text").boundingBox();
	expect(b && text).toBeTruthy();

	// The desktop button STRETCHES to the row rather than sitting at its declared
	// 28px min-height, and that is worth 10px of click target (23x38, not 23x28).
	// The mobile rule sets `align-items: center`; without the explicit
	// `align-items: stretch` in the min-width:641px block it inherits here and
	// silently shrinks this. Row height does not change either way, so a
	// height-only check cannot see it — measure the BUTTON.
	expect(b!.height).toBeGreaterThanOrEqual(text!.height - 1);
	expect(b!.height).toBeGreaterThan(30);
});
