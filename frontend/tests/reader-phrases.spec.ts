import { devices } from "@playwright/test";
import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Making a phrase card from a word's popover on a phone (bd tunatale-1409), and
 * the neutral tint that marks a phrase in the text (bd tunatale-yh47.5). Both
 * designs are the user's, decided 2026-10-13: the popover's "Phrase…" button
 * (behind More…) starts a phrase at that word, a bar asks for the last word, a
 * tap on a word of the same line ends the phrase, and words already inside a
 * phrase are greyed and refuse the tap.
 *
 * WHY REAL TOUCH. The predecessor, tap-to-select, passed jsdom for six months
 * while never completing in a browser: a real tap is pointerdown, pointerup,
 * click, and `fireEvent.click` is only the last. While the bar is up a tap on a
 * word must SELECT instead of opening its popover, which is that same gesture.
 * Every step of the journey below is `locator.tap()` with `hasTouch`.
 *
 * The vocabulary is invented (as in real-tap.spec.ts): SRS items are keyed per
 * lemma in the shared e2e DB, and real words would hand other specs items they
 * never asked for. The card-create request is answered by a stub, so a repeated
 * run never finds the phrase it made last time already painted as a span.
 */

const TOPIC = "reader-phrases-e2e";

const LINE_1 = "Bavko lemi tarsa pilun gove.";
const LINE_2 = "Sorna vidu kemal tapo.";

const GLOSSES = [
	{ word: "bavko", translation: "Bavko" },
	{ word: "lemi", translation: "carries" },
	{ word: "tarsa", translation: "warm" },
	{ word: "pilun", translation: "bread" },
	{ word: "gove", translation: "home" },
	{ word: "sorna", translation: "tomorrow" },
	{ word: "vidu", translation: "sees" },
	{ word: "kemal", translation: "old" },
	{ word: "tapo", translation: "bridge" },
];

const STORY = {
	title: "Reader phrases",
	key_phrases: [],
	scenes: [
		{
			label: "Scene one",
			lines: [
				{ speaker: "female-1", text: LINE_1, translation: "Bavko carries warm bread home." },
				{ speaker: "male-1", text: LINE_2, translation: "Tomorrow he sees the old bridge." },
			],
		},
	],
	dialogue_glosses: GLOSSES,
	morphology_focus: [],
};

const PHRASE = "tarsa pilun";

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
					collocations: [PHRASE],
					learning_objective: "make a phrase card",
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

	// One existing phrase card, so the line has a span the new phrase must not
	// run into. 409 = an earlier run made it.
	const res = await request.post(`${BACKEND}/api/srs/items`, {
		data: { text: PHRASE, translation: "warm bread", language_code: "sl", word_count: 2 },
	});
	if (!res.ok() && res.status() !== 409)
		throw new Error(`seeding the phrase card failed: ${res.status()} ${await res.text()}`);

	seeded = { curriculumId: curriculum.id };
	return seeded;
}

async function openRead(page: import("@playwright/test").Page, curriculumId: string) {
	await page.goto(`/c/${curriculumId}`);
	await page.getByRole("button", { name: "Day 1" }).click();
	await expect(page.getByRole("button", { name: "Render Audio" })).toBeVisible({ timeout: 15000 });
	await page.getByRole("button", { name: "Read", exact: true }).click();
	await expect(page.locator(".dialogue-words .tt-wrap").first()).toBeVisible({ timeout: 15000 });
	await expect(page.locator(".collocation-span"), "the seeded phrase is not painted as a span").toHaveCount(1);
}

/** The leaf `.tt-wrap` in the dialogue whose word text is exactly *text*.
 *  Punctuation is not part of it: WordSpan renders it in sibling `.punct` spans,
 *  and `:text-is` reads only the element's own text nodes, so the line's last
 *  word is "tapo", not "tapo." (its accessible name, which a snapshot shows, is
 *  "tapo."). */
function wordWrap(page: import("@playwright/test").Page, text: string) {
	return page
		.locator(".dialogue-words .tt-wrap")
		.filter({ has: page.locator(`.word:text-is("${text}")`) });
}

/** Any popover the user can see. `.tt` is display:none when closed. */
function visibleTips(page: import("@playwright/test").Page) {
	return page.locator(".tt:visible");
}

test.describe.configure({ mode: "serial" });

test.describe("phrase from the popover, real touch", () => {
	const PIXEL = devices["Pixel 7"];
	test.use({
		viewport: PIXEL.viewport,
		userAgent: PIXEL.userAgent,
		deviceScaleFactor: PIXEL.deviceScaleFactor,
		isMobile: PIXEL.isMobile,
		hasTouch: PIXEL.hasTouch,
	});

	test("Phrase… → tap the last word → Add card sends the phrase with its sentence", async ({
		page,
		request,
	}) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		const { curriculumId } = await seed(request);

		// The bar fetches a translation when the end is picked (an LLM call, so a
		// cassette miss unless stubbed), and the create is stubbed so a re-run
		// never meets its own phrase as an existing span.
		let translateCalls = 0;
		await page.route("**/api/srs/translate", async (route) => {
			translateCalls += 1;
			await route.fulfill({
				status: 200,
				contentType: "application/json",
				body: JSON.stringify({ translation: "tomorrow he sees" }),
			});
		});
		const created: Array<Record<string, unknown>> = [];
		await page.route("**/api/srs/items", async (route) => {
			if (route.request().method() !== "POST") return route.continue();
			created.push(route.request().postDataJSON());
			await route.fulfill({ status: 201, contentType: "application/json", body: "{}" });
		});

		await openRead(page, curriculumId);
		const mq = await page.evaluate(() => matchMedia("(hover: hover)").matches);
		expect(mq, "(hover: hover) is true — this would measure the desktop hover path").toBe(false);

		// 1. Open "Sorna"'s popover. "Phrase…" is a utility, so it sits behind More….
		const sorna = wordWrap(page, "Sorna");
		await expect(sorna).toHaveCount(1);
		await sorna.locator(".word").tap();
		const tip = sorna.locator("> .tt");
		await expect(tip).toBeVisible();
		const phraseBtn = tip.getByRole("button", { name: "Phrase…" });
		await expect(phraseBtn, "Phrase… must live behind More… (decided 2026-10-13)").toBeHidden();
		await tip.getByRole("button", { name: "More…" }).tap();
		await expect(phraseBtn).toBeVisible();

		// 2. Tap Phrase…. The popover closes; the bar asks for the last word.
		await phraseBtn.tap();
		await expect(visibleTips(page)).toHaveCount(0);
		const bar = page.locator(".phrase-confirm-bar");
		await expect(bar).toBeVisible();
		await expect(bar).toContainText("Tap the last word of the phrase");
		await expect(bar.getByRole("button", { name: "Add card" })).toBeDisabled();

		// 3. Tap "kemal" on the same line. It SELECTS — no popover opens — and the
		//    bar now holds the phrase, its fetched translation, and Add card.
		await wordWrap(page, "kemal").locator(".word").tap();
		await expect(visibleTips(page), "a tap while picking opened a popover").toHaveCount(0);
		await expect(bar.locator(".phrase-preview")).toHaveText("Sorna vidu kemal");
		await expect(bar.locator(".phrase-translation-input")).toHaveValue("tomorrow he sees");
		expect(translateCalls).toBe(1);

		// 4. Add card. The request carries the phrase and where it came from.
		await bar.getByRole("button", { name: "Add card" }).tap();
		await expect.poll(() => created.length).toBe(1);
		const body = created[0];
		expect(body.text).toBe("Sorna vidu kemal");
		expect(body.word_count).toBe(3);
		expect(body.translation).toBe("tomorrow he sees");
		expect(body.source_line_index).toBe(1);
		expect(body.source_sentence).toBe("Sorna vidu kemal tapo.");
		expect(typeof body.source_lesson_id).toBe("string");
		await expect(bar).toBeHidden();

		// 5. Back to normal: a tap on a word opens its popover again.
		await expect(wordWrap(page, "tapo")).toHaveCount(1);
		await wordWrap(page, "tapo").locator(".word").tap();
		await expect(wordWrap(page, "tapo").locator("> .tt")).toBeVisible();
	});

	test("a phrase cannot run into an existing one, and Cancel ends picking", async ({
		page,
		request,
	}) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		const { curriculumId } = await seed(request);
		await page.route("**/api/srs/translate", (route) =>
			route.fulfill({ status: 200, contentType: "application/json", body: '{"translation":"x"}' }),
		);
		await openRead(page, curriculumId);

		// "lemi" is followed by the phrase "tarsa pilun", and "gove" ends the line:
		// neither can start a phrase, so neither offers the button.
		for (const w of ["lemi", "gove"]) {
			const wrap = wordWrap(page, w);
			await expect(wrap, `no reader word exactly '${w}'`).toHaveCount(1);
			await wrap.locator(".word").tap();
			const tip = wrap.locator("> .tt");
			await expect(tip).toBeVisible();
			const more = tip.getByRole("button", { name: "More…" });
			if (await more.isVisible()) await more.tap();
			await expect(tip.getByRole("button", { name: "Phrase…" }), `${w} offers Phrase…`).toHaveCount(0);
			await wrap.locator(".word").tap(); // close it
			await expect(tip).toBeHidden();
		}

		// Start at "Bavko".
		const bavko = wordWrap(page, "Bavko");
		await bavko.locator(".word").tap();
		const tip = bavko.locator("> .tt");
		await tip.getByRole("button", { name: "More…" }).tap();
		await tip.getByRole("button", { name: "Phrase…" }).tap();
		const bar = page.locator(".phrase-confirm-bar");
		await expect(bar).toContainText("Tap the last word of the phrase");

		// The existing phrase is greyed while picking.
		const span = page.locator(".collocation-span");
		const opacity = Number(await span.evaluate((el) => getComputedStyle(el).opacity));
		expect(opacity, "the existing phrase is not greyed while picking").toBeLessThanOrEqual(0.5);

		// Tapping a word of it does nothing: still picking, no popover.
		await span.locator(".word").first().tap();
		await expect(visibleTips(page)).toHaveCount(0);
		await expect(bar).toContainText("Tap the last word of the phrase");
		await expect(bar.getByRole("button", { name: "Add card" })).toBeDisabled();

		// Cancel ends picking: the bar goes, the phrase is no longer greyed, and a
		// tap opens a popover again.
		await bar.getByRole("button", { name: "Cancel" }).tap();
		await expect(bar).toBeHidden();
		expect(Number(await span.evaluate((el) => getComputedStyle(el).opacity))).toBe(1);
		await wordWrap(page, "lemi").locator(".word").tap();
		await expect(wordWrap(page, "lemi").locator("> .tt")).toBeVisible();
	});
});

/**
 * The tint (bd tunatale-yh47.5): a phrase gets a faint NEUTRAL background, in
 * both themes, and it costs no row height. Neutral = no mastery colour: the tint
 * that was removed carried the band colour. Row height is read off the real line
 * boxes with the tint on and with it stripped, so a tint that grew the rows would
 * show as a difference here, not only in the rails spec.
 */
for (const scheme of ["light", "dark"] as const) {
	test(`a phrase is tinted neutral in the ${scheme} theme at no row-height cost`, async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		const { curriculumId } = await seed(request);
		await page.emulateMedia({ colorScheme: scheme });
		await page.setViewportSize({ width: 390, height: 844 });
		await openRead(page, curriculumId);
		expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe(scheme);

		const span = page.locator(".collocation-span");
		const bg = await span.evaluate((el) => getComputedStyle(el).backgroundColor);
		const m = bg.match(/rgba?\(([\d.]+), ([\d.]+), ([\d.]+)(?:, ([\d.]+))?\)/);
		expect(m, `unparsed background ${bg}`).toBeTruthy();
		const [r, g, b] = [m![1], m![2], m![3]].map(Number);
		const a = m![4] === undefined ? 1 : Number(m![4]);
		expect(a, `phrase has no tint (${bg})`).toBeGreaterThan(0);
		expect(a, `tint is not faint (${bg})`).toBeLessThanOrEqual(0.2);
		expect(Math.max(r, g, b) - Math.min(r, g, b), `tint is not neutral grey (${bg})`).toBeLessThanOrEqual(12);
		// Not the ignored state's look: that one is struck through.
		expect(await span.evaluate((el) => getComputedStyle(el).textDecorationLine)).not.toContain("line-through");

		const heights = async () =>
			page.evaluate(() =>
				[...document.querySelectorAll(".dialogue-words")].map((el) => el.getBoundingClientRect().height),
			);
		const withTint = await heights();
		await page.addStyleTag({
			content: ".collocation-span{background-color:transparent!important;padding-left:0!important;padding-right:0!important;margin-left:0!important;margin-right:0!important}",
		});
		expect(await heights(), "the tint changed a line's height").toEqual(withTint);
	});
}
