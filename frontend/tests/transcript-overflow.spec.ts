import { test, expect } from "@playwright/test";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * The lesson page must not overflow horizontally in Read mode.
 *
 * This is the defect behind a phone report of "the listen preview modal is too
 * wide", and the chain is worth writing down because nothing in it points at
 * the modal:
 *
 *   Every word renders its Tooltip popover into the DOM while CLOSED —
 *   absolutely positioned, `opacity: 0`, `width: max-content`, up to 280px —
 *   and the viewport edge-clamp in Tooltip.svelte only runs when the popover is
 *   OPEN. Closed popovers on words near the right margin therefore hang past
 *   the viewport, and `document.scrollWidth` pins near 495px no matter how
 *   narrow the screen is (measured on real lesson data: 495 at 390px, 495 at
 *   412px, 496 at 432px).
 *
 *   Chromium on Android answers document overflow at load with shrink-to-fit:
 *   it picks a page scale that fits the overflowing width onto the glass. A
 *   phone reporting a 432px visual viewport then reports a 515px LAYOUT
 *   viewport, and in a SPA that scale never resets — so one Read view zooms the
 *   whole app out for the rest of the session, and every later width (`94%` of
 *   515 = a 470px modal) is computed against a viewport 19% wider than the
 *   screen.
 *
 * Two things this test has to get right or it passes vacuously:
 *
 *  1. The words must be TRACKED. An untracked word's popover holds a bare
 *     gloss and shrink-wraps to ~50px; only a word with an SRS item grows the
 *     action row (Review / Ignore / Known / Restore) that reaches the 280px
 *     cap. Hence the `POST /api/srs/listen` below.
 *  2. The dialogue must WRAP. Words only overhang when they sit near the right
 *     margin, so the lines here are long enough to wrap several times on a
 *     phone.
 *
 * It asserts document scrollWidth rather than any popover's geometry on
 * purpose: the invariant is "the page does not overflow", and it must hold
 * however the popovers are later positioned.
 */

const TOPIC = "transcript-overflow-e2e";

const STORY = {
	title: "Transcript overflow",
	key_phrases: [{ phrase: "dober dan", translation: "good day" }],
	scenes: [
		{
			label: "At the Café",
			lines: [
				{
					speaker: "female-1",
					text: "Dober dan, prosim kavo in enainštirideset evrov, hvala lepa, nasvidenje prosim.",
					translation: "Good day, a coffee please and forty-one euros, thank you, goodbye please.",
				},
				{
					speaker: "male-1",
					text: "Enainštirideset evrov prosim, hvala lepa, dober dan nasvidenje kavo.",
					translation: "Forty-one euros please, thank you, good day goodbye coffee.",
				},
			],
		},
	],
	dialogue_glosses: [
		{ word: "kavo", translation: "coffee" },
		{ word: "evrov", translation: "euros" },
		{ word: "hvala", translation: "thanks" },
		{ word: "nasvidenje", translation: "goodbye" },
		{ word: "enainštirideset", translation: "forty-one" },
		{ word: "lepa", translation: "nice, lovely" },
		{ word: "prosim", translation: "please" },
	],
	morphology_focus: [],
};

let seeded: { curriculumId: string } | null = null;

async function seed(request: import("@playwright/test").APIRequestContext) {
	if (seeded !== null) return seeded;

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
	const lesson = await impRes.json();

	// Tracked words — see note 1 above. Without this the popovers are gloss-only
	// and far too narrow to overhang anything.
	const listenRes = await request.post(`${BACKEND}/api/srs/listen`, {
		data: { lesson_id: lesson.id ?? lesson.lesson_id, word_ratings: {}, kp_ratings: {} },
	});
	if (!listenRes.ok())
		throw new Error(`listen failed: ${listenRes.status()} ${await listenRes.text()}`);

	seeded = { curriculumId: curriculum.id };
	return seeded;
}

test.describe.configure({ mode: "serial" });

test("lesson page: Read mode never overflows horizontally", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId } = await seed(request);

	const failures: string[] = [];
	for (const width of [390, 412, 432]) {
		await page.setViewportSize({ width, height: 844 });
		await page.goto(`/c/${curriculumId}`);
		await page.getByRole("button", { name: "Day 1" }).click();
		await expect(page.getByRole("button", { name: "Render Audio" })).toBeVisible({
			timeout: 15000,
		});
		await page.getByRole("button", { name: "Read", exact: true }).click();
		// The transcript is fetched client-side; without this the measurement
		// lands on an empty page and passes for the wrong reason.
		await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

		const r = await page.evaluate(() => {
			const doc = document.documentElement;
			const widest = [...document.querySelectorAll("body *")]
				.map((el) => ({ el, rect: el.getBoundingClientRect() }))
				.filter(({ rect }) => rect.width > 0)
				.sort((a, b) => b.rect.right - a.rect.right)[0];
			return {
				scrollW: doc.scrollWidth,
				clientW: doc.clientWidth,
				words: document.querySelectorAll(".tt-wrap").length,
				// A popover with an action row is what makes this test non-vacuous.
				actionRows: document.querySelectorAll(".tt-actions").length,
				worst: `<${widest.el.tagName.toLowerCase()} class="${widest.el.className}">`.slice(0, 70),
				worstRight: Math.round(widest.rect.right),
			};
		});

		// Guard against a green that only means "nothing rendered".
		expect(r.words, "no transcript words rendered").toBeGreaterThan(5);
		expect(r.actionRows, "no tracked words — popovers would be too narrow to overhang").toBeGreaterThan(0);

		if (r.scrollW > r.clientW)
			failures.push(
				`${width}px: page scrolls to ${r.scrollW} (viewport ${r.clientW}); rightmost is ${r.worst} ending at ${r.worstRight}`,
			);
	}

	expect(failures, failures.join("\n")).toEqual([]);
});
