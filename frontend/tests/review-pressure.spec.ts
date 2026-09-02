import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * The review-pressure control, at the two tiers below which nothing can see it.
 *
 * `plan/page.test.ts` already covers what the control DOES — the endpoint it
 * calls, the value it shows, what it does when the save fails. Those are
 * app-computed and belong there. Two claims are left that a component test
 * structurally cannot make (.claude/rules/test-tiers.md):
 *
 *   1. THE JOURNEY. Setting the dial in the browser has to change the prompt the
 *      backend later builds from the learner's own SRS database. Frontend,
 *      HTTP, storage and the story builder all have to agree, and no tier below
 *      this one puts them in the same process.
 *   2. THE GEOMETRY. The control was added to a toolbar row that already held a
 *      quota chip and a mode toggle. Whether three controls still fit at 320px
 *      is decided by the engine, platform font metrics, and the user's root font
 *      size — the app computes none of it.
 */

const PHONE = { width: 390, height: 844 };
const TOPIC = "review-pressure-e2e";

async function seedCurriculum(
	request: import("@playwright/test").APIRequestContext,
): Promise<string> {
	const res = await request.post(`${BACKEND}/api/curriculum/import`, {
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
					story_guidance: "A short greeting scene",
				},
			],
		},
	});
	if (!res.ok()) throw new Error(`curriculum import failed: ${res.status()} ${await res.text()}`);
	return (await res.json()).id;
}

/** One collocation the selector will actually pick up.
 *
 * ⚠️ A freshly created item is NEW, and NEW is excluded from the review pool by
 * state — a word never introduced cannot be decaying. Promoting it to LEARNING
 * is what puts it in the queue's due pool with today's due_at, which is the pool
 * the selector reads. Without this the prompt renders "(none yet)" at EVERY
 * pressure (by design — that is the cassette-stability rule), and a test that
 * asserted on the instruction text would fail for a reason unrelated to the dial.
 */
async function seedDueWord(
	request: import("@playwright/test").APIRequestContext,
	text: string,
): Promise<void> {
	const created = await request.post(`${BACKEND}/api/srs/items`, {
		data: { text, translation: "seeded", language_code: "sl", word_count: 1 },
	});
	if (!created.ok() && created.status() !== 409)
		throw new Error(`seed item failed: ${created.status()} ${await created.text()}`);
	const id = created.ok() ? (await created.json()).id : null;
	if (id == null) return;
	const promoted = await request.post(`${BACKEND}/api/srs/items/${id}/state`, {
		data: { state: "learning" },
	});
	if (!promoted.ok())
		throw new Error(`promote failed: ${promoted.status()} ${await promoted.text()}`);
}

test("review pressure: choosing it in the browser changes the prompt the backend builds", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "backend not running");
	const curriculumId = await seedCurriculum(request);
	await seedDueWord(request, "zapadlica");

	await page.goto(`/c/${curriculumId}/plan`);
	const control = page.getByLabel(/review words/i);
	await expect(control).toBeVisible({ timeout: 10000 });

	// At rest it shows the plan's stored setting — a new curriculum is NATURAL.
	await expect(control).toHaveValue("NATURAL");

	await control.selectOption("INSISTENT");

	// The round trip that no component test can make: the browser wrote it, and
	// the STORY BUILDER — a different module, reading the curriculum back out of
	// storage — has to render the matching instruction into the prompt it exports.
	await expect
		.poll(
			async () => {
				const res = await request.get(
					`${BACKEND}/api/story/prompt?curriculum_id=${curriculumId}&day=1`,
				);
				if (!res.ok()) return "";
				return (await res.json()).user_prompt as string;
			},
			{ timeout: 10000 },
		)
		.toContain("matters MORE than staying");

	// Reload: the value is persisted, not just held in the page's memory.
	await page.reload();
	await expect(page.getByLabel(/review words/i)).toHaveValue("INSISTENT");
});

test("review pressure: the plan toolbar still fits a narrow phone", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "backend not running");
	const curriculumId = await seedCurriculum(request);

	// 320px at a 20px root, not the default 16: the two existing controls are
	// sized in `rem`, so an Android font-size setting scales the row's hard
	// minimum. This is the combination the modal's own gutter spec found bites.
	await page.setViewportSize({ width: 320, height: 700 });
	await page.addStyleTag({ content: "html { font-size: 20px }" });
	await page.goto(`/c/${curriculumId}/plan`);
	await expect(page.getByLabel(/review words/i)).toBeVisible({ timeout: 10000 });

	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth,
	);
	expect(overflow, `page scrolls sideways by ${overflow}px with the pressure control added`).toBe(
		0,
	);

	// ...and the hint is a full sentence, so it must WRAP rather than be clipped
	// by the toolbar row. An ellipsis here would satisfy the overflow check above
	// while destroying the one line that explains what the setting trades.
	const hint = page.getByTestId("pressure-hint");
	const [box, lineHeight] = await Promise.all([
		hint.boundingBox(),
		hint.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight)),
	]);
	expect(box, "hint must have a box").not.toBeNull();
	expect(box!.height).toBeGreaterThan(lineHeight * 1.5);
});

test("review pressure: a phone still shows the setting without opening it", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "backend not running");
	const curriculumId = await seedCurriculum(request);

	await request.post(`${BACKEND}/api/curriculum/${curriculumId}/review-pressure`, {
		data: { pressure: "BALANCED" },
	});

	await page.setViewportSize(PHONE);
	await page.goto(`/c/${curriculumId}/plan`);

	// Three values means there is no click-to-flip affordance and no other
	// on-screen consequence to infer the state from, so the CURRENT one has to be
	// legible at rest — including on the narrow viewport where a select is most
	// likely to be squeezed to its arrow.
	const control = page.getByLabel(/review words/i);
	await expect(control).toHaveValue("BALANCED");
	const width = (await control.boundingBox())!.width;
	expect(width, "the select is too narrow to show its own label").toBeGreaterThan(60);
});
