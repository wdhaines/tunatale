import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Layout guards for the lesson page's sticky player card, in a REAL browser.
 *
 * Same reason as listen-preview-layout.spec.ts: jsdom performs no layout, so
 * the unit suite reports every element at 0×0 and can assert structure but
 * never geometry. Every invariant here is geometric.
 *
 * 1. The stats line spans the card's full content width. When the card header
 *    became a two-column row (title | Read/Listen toggle) on 2026-07-27, the
 *    stats line inherited the LEFT column's width and wrapped to two lines on a
 *    phone — the toggle is only two lines tall, so there is no reason for the
 *    stats to keep clearing it.
 * 2. "Mark as Listened" stays horizontally centered in the card. Folding the
 *    action block from a centered column into a row left-aligned it.
 * 3. The player's setting chips never move, resize, wrap or cut off a value as
 *    their values change. Content-sized chips reflowed the row on every Speed
 *    tap on a phone (2026-09-11: "Enunciated" pushed Mic onto a second row).
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
	// Reference is a segment chip inside it — one inline box on one line — since
	// computed line-height here is `normal`, which parses to NaN. (The percentage
	// span used as the reference was removed in tunatale-yh47.7.)
	const oneLine = await card.locator(".mastery-line .mastery-segment").first().boundingBox();
	expect(oneLine).toBeTruthy();
	expect(stats!.height).toBeLessThan(oneLine!.height * 1.5);
});

/**
 * The setting chips (Speed / English / Captions / Mic) exist only on a lesson
 * whose audio carries every section with per-section cues, and e2e never renders
 * audio — so the audio response is stubbed with all seven. Without the stub
 * there are no chips and every assertion below would pass on an empty row.
 */
const SECTION_TYPES = [
	"key_phrases",
	"natural_speed",
	"slow_speed",
	"translated",
	"slow_translated",
	"en_translated",
	"slow_en_translated",
];

async function stubFullAudio(page: import("@playwright/test").Page) {
	await page.route("**/api/audio/lesson/*", async (route) => {
		const sections = SECTION_TYPES.map((section_type, section_index) => {
			const cue = {
				index: 0,
				start_ms: 0,
				end_ms: 900,
				section_index,
				section_type,
				phrase_index: 0,
				role: "female-1",
				language_code: "sl",
				text: "Dober dan, prosim kavo.",
				ref:
					section_type === "key_phrases"
						? { kind: "key_phrase", target_index: 0 }
						: { kind: "line", target_index: 0 },
			};
			return { audio_id: `stub-${section_type}`, section_index, section_type, title: section_type, cues: [cue] };
		});
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				audio_id: "stub-audio",
				lesson_id: "any",
				sections,
				cues: sections.flatMap((s) => s.cues),
			}),
		});
	});
}

type ChipBox = {
	value: string;
	x: number;
	y: number;
	w: number;
	h: number;
	wrapped: boolean;
	clipped: boolean;
	labelClipped: boolean;
};

/** Each chip's box RELATIVE to the row, plus whether its value wrapped or its value or label was cut off. */
async function chipBoxes(row: import("@playwright/test").Locator): Promise<ChipBox[]> {
	return row.evaluate((el) => {
		const origin = el.getBoundingClientRect();
		return [...el.children].map((c) => {
			const r = c.getBoundingClientRect();
			const v = c.querySelector(".chip-value") as HTMLElement;
			const l = c.querySelector(".chip-label") as HTMLElement;
			const lineHeight = parseFloat(getComputedStyle(v).lineHeight);
			return {
				value: (v.textContent ?? "").trim(),
				x: Math.round(r.x - origin.x),
				y: Math.round(r.y - origin.y),
				w: Math.round(r.width),
				h: Math.round(r.height),
				wrapped: v.getBoundingClientRect().height > lineHeight * 1.5,
				clipped: v.scrollWidth > v.clientWidth,
				labelClipped: l.scrollWidth > l.clientWidth,
			};
		});
	});
}

// 375 is the common iPhone width, where the reported wrap happened: "Enunciated"
// widened the Speed chip and pushed Mic onto the second row. 320 is the floor —
// the chips may stack there, but they must still never reflow or cut a value.
for (const { width, fourAcross } of [
	{ width: 375, fourAcross: true },
	{ width: 320, fourAcross: false },
]) {
	test(`player: setting chips hold their places through every value at ${width}px`, async ({
		page,
		request,
	}) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		await page.setViewportSize({ width, height: 844 });
		const cid = await curriculumId(request);
		await stubFullAudio(page);
		await page.goto(`/c/${cid}`);
		await page.getByRole("button", { name: "Day 1" }).click();

		const row = page.locator(".player-card .controls-row");
		const speed = row.locator(".enunciation-btn");
		// If this times out the stub stopped working — do NOT relax it, or every
		// check below measures a row with no chips in it and passes.
		await expect(speed).toBeVisible({ timeout: 15000 });

		const baseline = await chipBoxes(row);
		const rowHeight = (await row.boundingBox())!.height;
		const seen: string[] = [];

		async function checkState() {
			const boxes = await chipBoxes(row);
			seen.push(boxes.map((b) => b.value).join("|"));
			for (const [i, b] of boxes.entries()) {
				expect(b.wrapped, `"${b.value}" wraps inside its chip`).toBe(false);
				expect(b.clipped, `"${b.value}" is cut off`).toBe(false);
				expect(b.labelClipped, `the label over "${b.value}" is cut off`).toBe(false);
				const { value: _v, wrapped: _w, clipped: _c, labelClipped: _lc, ...box } = b;
				const { value: _bv, wrapped: _bw, clipped: _bc, labelClipped: _blc, ...base } = baseline[i];
				expect(box, `"${b.value}" moved or resized a chip`).toEqual(base);
			}
			expect((await row.boundingBox())!.height).toBeCloseTo(rowHeight, 0);
		}

		await checkState();
		// Every value each chip can show, one axis at a time, back to the start.
		for (const [chip, clicks] of [
			[speed, 4],
			[row.locator(".english-btn"), 3],
			[row.locator(".caption-blur-btn"), 2],
		] as const) {
			for (let i = 0; i < clicks; i++) {
				await chip.click();
				await checkState();
			}
		}
		// Guard against a vacuous pass: the loop really did cycle the labels.
		expect(new Set(seen).size).toBeGreaterThanOrEqual(7);

		if (fourAcross) {
			const tops = new Set(baseline.slice(0, 4).map((b) => b.y));
			expect(tops.size, "the four setting chips share one row").toBe(1);
		}
	});
}

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
