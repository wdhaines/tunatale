import { devices } from "@playwright/test";
import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * bd tunatale-iofm — two tap journeys that only synthetic events had ever driven.
 *
 * A real tap is pointerdown, pointerup, then click. jsdom's `fireEvent.click`
 * sends only the last, so a handler the first two undo passes in unit tests and
 * never completes in a browser; tap-to-select shipped broken that way for six
 * months. The two journeys here are the word popover (`Tooltip.svelte`) and the
 * phrase drill-in (`Transcript.svelte`), and BOTH are walked with real touch
 * input: every tap in the two tests below is `locator.tap()` — no `.click()`, no
 * `page.mouse`, no `dispatchEvent`, no event-firing `page.evaluate`. The setup
 * (`openRead`) still clicks, because it is not part of either journey.
 *
 * ⚠️ This spec MEASURES the app, it does not make one pass. These journeys have
 * never been walked in a browser, so a real regression is a possible outcome (and
 * a successful run reports it). The header's emulation guard is an assertion, not
 * a comment: at a fine pointer `Tooltip.svelte`'s `@media (hover: hover)` block
 * reveals popovers on hover, so the tap path would go untested.
 *
 * Seeding notes:
 *  1. The vocabulary is INVENTED. E2E specs share one backend DB and SRS items
 *     are keyed per lemma; seeding real Slovene words would hand other specs
 *     items they never asked for.
 *  2. No word needs TRACKING: `srs/transcript.py` falls back to the lesson gloss
 *     (`translation = db_translation if db_translation else gloss`) and `gloss`
 *     comes from the story's `dialogue_glosses`, so a popover has content from
 *     the dialogue gloss alone. Test 2 adds only the one phrase card it needs.
 */

const TOPIC = "real-tap-guards-e2e";

const LINE_1 = "Mirka plovi zurni kivot danec.";
const LINE_2 = "Jutre drema novu telunu.";

// Invented tokens — see seeding note 1. The e2e backend runs the `lowercase`
// lemmatizer, so each lemma is just its surface form lowercased.
const GLOSSES = [
	{ word: "plovi", translation: "drinks" },
	{ word: "zurni", translation: "green" },
	{ word: "kivot", translation: "tea" },
	{ word: "danec", translation: "today" },
	{ word: "jutre", translation: "tomorrow" },
	{ word: "drema", translation: "buys" },
	{ word: "novu", translation: "new" },
	{ word: "telunu", translation: "cup" },
];

// `key_phrases: []` on purpose: the phrase the drill-in walks is a seeded SRS
// card (below), not a story key phrase, so nothing else may introduce a span.
const STORY = {
	title: "Real tap guards",
	key_phrases: [],
	scenes: [
		{
			label: "Scene one",
			lines: [
				{ speaker: "female-1", text: LINE_1, translation: "Mirka drinks green tea today." },
				{ speaker: "male-1", text: LINE_2, translation: "Tomorrow she buys a new cup." },
			],
		},
	],
	dialogue_glosses: GLOSSES,
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
					collocations: ["zurni kivot"],
					learning_objective: "walk the tap journeys",
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

	seeded = { curriculumId: curriculum.id };
	return seeded;
}

let phraseSeeded = false;

/** Give `zurni kivot` a tracked multi-word card, so `build_phrase_index` paints
 *  the collocation span. 409 means an earlier run already made it (idempotent
 *  seeding) — the same body `listen-preview-layout.spec.ts` uses. */
async function seedPhraseCard(request: import("@playwright/test").APIRequestContext) {
	if (phraseSeeded) return;
	const res = await request.post(`${BACKEND}/api/srs/items`, {
		data: { text: "zurni kivot", translation: "green tea", language_code: "sl", word_count: 2 },
	});
	if (!res.ok() && res.status() !== 409)
		throw new Error(`seeding the key phrase card failed: ${res.status()} ${await res.text()}`);
	phraseSeeded = true;
}

test.describe.configure({ mode: "serial" });

test.describe("real touch taps", () => {
	// NOT `{ ...devices["Pixel 7"] }` — a device descriptor carries
	// `defaultBrowserType`, which Playwright rejects inside a describe ("forces a
	// new worker"). Only the emulation fields are spread; `hasTouch` is what makes
	// `locator.tap()` real touch input, and the pointer media queries are what
	// disable the hover reveal. Pattern copied from `tooltip-popover.spec.ts`.
	const PIXEL = devices["Pixel 7"];
	test.use({
		viewport: PIXEL.viewport,
		userAgent: PIXEL.userAgent,
		deviceScaleFactor: PIXEL.deviceScaleFactor,
		isMobile: PIXEL.isMobile,
		hasTouch: PIXEL.hasTouch,
	});

	async function openRead(page: import("@playwright/test").Page, curriculumId: string) {
		await page.goto(`/c/${curriculumId}`);
		// Proves the emulation took. If this ever reads false the spec is measuring
		// desktop CSS, where `.hover()` opens the popover and the tap path would go
		// untested.
		const mq = await page.evaluate(() => ({
			coarse: matchMedia("(pointer: coarse)").matches,
			hoverHover: matchMedia("(hover: hover)").matches,
		}));
		expect(mq.coarse, "pointer is not coarse — this spec would measure desktop CSS").toBe(true);
		expect(
			mq.hoverHover,
			"(hover: hover) is true — the popover would open on hover and the touch path would go untested",
		).toBe(false);

		await page.getByRole("button", { name: "Day 1" }).click();
		await expect(page.getByRole("button", { name: "Render Audio" })).toBeVisible({ timeout: 15000 });
		await page.getByRole("button", { name: "Read", exact: true }).click();
		// Scoped to the transcript: mastery chips also render `.tt-wrap`, so the
		// first `.tt-wrap` anywhere would not prove the dialogue is on screen.
		await expect(page.locator(".dialogue-words .tt-wrap").first()).toBeVisible({ timeout: 15000 });
	}

	/** The single leaf `.tt-wrap` in the dialogue whose word text is exactly *text*. */
	function wordWrap(page: import("@playwright/test").Page, text: string) {
		return page
			.locator(".dialogue-words .tt-wrap")
			.filter({ has: page.locator(`.word:text-is("${text}")`) });
	}

	/** A visible element outside every `.tt-wrap`, with no click handler and not a
	 *  link or button: the lesson title `<h1>`. A blind corner tap is explicitly
	 *  ruled out by the existing specs (it once navigated away mid-sweep). */
	function outside(page: import("@playwright/test").Page) {
		return page.locator(".player-title-area h1");
	}

	test("a real tap elsewhere closes a word popover", async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		const { curriculumId } = await seed(request);
		await openRead(page, curriculumId);

		const aWrap = wordWrap(page, "plovi"); // A — line 1
		const bWrap = wordWrap(page, "drema"); // B — line 2
		await expect(aWrap, "no reader word exactly 'plovi'").toHaveCount(1);
		await expect(bWrap, "no reader word exactly 'drema'").toHaveCount(1);
		const aTip = aWrap.locator("> .tt");
		const bTip = bWrap.locator("> .tt");
		const aWord = aWrap.locator(".word");
		const bWord = bWrap.locator(".word");

		// 1. Neither popover is open.
		await expect(aTip).toBeHidden();
		await expect(bTip).toBeHidden();

		// 2. Tap A. A's popover is open.
		await aWord.tap();
		await expect(aTip).toBeVisible();

		// 3. Tap A again. pointerdown lands inside A's wrap, so the outside handler
		//    leaves it alone and the click toggles it shut.
		await aWord.tap();
		await expect(aTip).toBeHidden();

		// 4. Tap A. Open again. The next step must tap B's WORD, so first prove A's
		//    open popover does not cover it: if it did, step 5 would tap the popover
		//    (inside A's wrap) and pass or fail for the wrong reason. B's box is
		//    B's word box — its popover is closed (all-zero) at this point, so that
		//    would clear the check vacuously.
		await aWord.tap();
		await expect(aTip).toBeVisible();
		const aBox = await aTip.boundingBox();
		const bBox = await bWord.boundingBox();
		expect(aBox, "A's open popover has no box").toBeTruthy();
		expect(bBox, "B's word has no box").toBeTruthy();
		const overlaps =
			aBox!.x < bBox!.x + bBox!.width &&
			bBox!.x < aBox!.x + aBox!.width &&
			aBox!.y < bBox!.y + bBox!.height &&
			bBox!.y < aBox!.y + aBox!.height;
		expect(
			overlaps,
			`A's popover ${JSON.stringify(aBox)} intersects B's word ${JSON.stringify(bBox)} — the next tap would land on the popover`,
		).toBe(false);

		// 5. Tap B. A's popover is closed AND B's is open.
		await bWord.tap();
		await expect(aTip).toBeHidden();
		await expect(bTip).toBeVisible();

		// 5b. Tap B's neighbour on the SAME line (`novu`), which sits within the
		//     width of B's open popover. B's closes and the neighbour's opens.
		//     (This step passed before the hover-bridge fix that the drill-in
		//     test's step 4b guards; it is here for the journey, not for that bug.)
		const cWrap = wordWrap(page, "novu");
		await expect(cWrap, "no reader word exactly 'novu'").toHaveCount(1);
		const cTip = cWrap.locator("> .tt");
		const cWord = cWrap.locator(".word");
		const bTipBox = (await bTip.boundingBox())!;
		const cBox = (await cWord.boundingBox())!;
		const cCentre = cBox.x + cBox.width / 2;
		expect(
			cCentre > bTipBox.x && cCentre < bTipBox.x + bTipBox.width,
			`novu ${JSON.stringify(cBox)} is not within the width of drema's popover ${JSON.stringify(bTipBox)}`,
		).toBe(true);
		await cWord.tap();
		await expect(bTip).toBeHidden();
		await expect(cTip).toBeVisible();

		// 6. Tap the outside element. The open popover is closed.
		await expect(outside(page), "outside tap target is not visible").toBeVisible();
		await outside(page).tap();
		await expect(cTip).toBeHidden();
	});

	test("phrase drill-in by real taps", async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		const { curriculumId } = await seed(request);
		await seedPhraseCard(request);
		await openRead(page, curriculumId);

		const span = page.locator(".collocation-span");
		await expect(span).toHaveCount(1);
		const phraseWrap = page.locator(".tt-wrap:has(> .collocation-span)");
		await expect(phraseWrap).toHaveCount(1);
		const phraseTip = phraseWrap.locator("> .tt");
		// The two words' own popovers. They are rendered only while drilled in
		// (`Transcript.svelte` suppresses them otherwise), and the phrase popover
		// is a SIBLING of `span`, not a descendant — so this locator cannot pick
		// it up.
		const inner = span.locator(".tt-wrap > .tt");

		// 1. Exactly one collocation span is visible, holding both words; no inner
		//    popovers exist yet. These two make the rest non-vacuous.
		await expect(span).toBeVisible();
		await expect(span).toContainText("zurni");
		await expect(span).toContainText("kivot");
		await expect(inner).toHaveCount(0);

		// 2. Tap the phrase where a finger lands: on one of its words. The phrase
		//    popover opens and offers `Words…`. (The tap also toggles that inner
		//    word's own, suppressed, popover state; the `Words…` tap below is a
		//    pointerdown outside it, which clears it again. Measured 2026-10-08:
		//    after drill-in both inner popovers exist and both are closed.)
		const zurni = span.locator('.word:text-is("zurni")');
		const kivot = span.locator('.word:text-is("kivot")');
		await zurni.tap();
		await expect(phraseTip).toBeVisible();
		const wordsBtn = phraseTip.getByRole("button", { name: "Words…" });
		await expect(wordsBtn).toBeVisible();

		// 3. Tap `Words…`. The phrase popover is gone and the two inner words'
		//    popovers now exist.
		await wordsBtn.tap();
		await expect(phraseTip).toHaveCount(0);
		await expect(inner).toHaveCount(2);
		await expect(inner.nth(0)).toBeHidden();
		await expect(inner.nth(1)).toBeHidden();

		// 4. Tap the first inner word (`zurni`). Drill-in survives a tap inside the
		//    phrase and that word's own popover opens.
		const zurniTip = inner.nth(0);
		const kivotTip = inner.nth(1);
		await zurni.tap();
		await expect(inner).toHaveCount(2);
		await expect(zurniTip).toBeVisible();

		// 4b. Tap the OTHER inner word, which sits right beside the first and so
		//     directly under (or over) zurni's open popover. zurni's popover closes
		//     and kivot's opens. This is the step that was broken: the popover's
		//     invisible 8px hover bridge (`.tt::before`) covered the top of the
		//     word row across the popover's whole width, and a touch on the
		//     neighbouring word was delivered to the popover instead — nothing
		//     happened, however often it was tapped. The precondition proves the
		//     neighbour really is inside the popover's width, or this step would
		//     pass without ever having been at risk.
		const tipBox = (await zurniTip.boundingBox())!;
		const kivotBox = (await kivot.boundingBox())!;
		const kivotCentre = kivotBox.x + kivotBox.width / 2;
		expect(
			kivotCentre > tipBox.x && kivotCentre < tipBox.x + tipBox.width,
			`kivot ${JSON.stringify(kivotBox)} is not within the width of zurni's popover ${JSON.stringify(tipBox)}`,
		).toBe(true);
		await kivot.tap();
		await expect(inner).toHaveCount(2);
		await expect(zurniTip).toBeHidden();
		await expect(kivotTip).toBeVisible();

		// 5. Tap the outside element. Drill-in ends: no inner popovers, and the
		//    phrase popover element exists again but is hidden.
		await expect(outside(page), "outside tap target is not visible").toBeVisible();
		await outside(page).tap();
		await expect(inner).toHaveCount(0);
		await expect(phraseTip).toHaveCount(1);
		await expect(phraseTip).toBeHidden();
	});
});
