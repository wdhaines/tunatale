import { test, expect, devices } from "@playwright/test";
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

/**
 * F-8 — the same invariant, but with a popover OPEN.
 *
 * The test above only ever measures CLOSED popovers. Its header says it asserts
 * document scrollWidth "however the popovers are later positioned", which is the
 * right instinct — but it never enters the open state, so it is vacuous for the
 * open case. A user hit exactly that gap on 2026-08-04: "Word tooltips can
 * trigger sideways scroll if they are towards the right side of the screen."
 *
 * ROOT CAUSE — there are THREE popover states, and only two are held in bounds:
 *
 *   1. closed        — `display: none`. Contributes no width. Fixed by 5c794d6,
 *                      pinned by the test above.
 *   2. click-opened  — `.tt-wrap.open > .tt`. `open === true`, so the JS
 *                      edge-clamp `$effect` runs and nudges it via `shiftX`.
 *   3. HOVER-REVEALED — `@media (hover: hover) { .tt-wrap:hover > .tt }`.
 *                      Displayed at full opacity, but `open` is still FALSE.
 *
 * The clamp is gated on `open`:
 *
 *     $effect(() => { if (!open || !ttEl) { shiftX = 0; return; } … });
 *
 * so state 3 gets `shiftX = 0` — centred on its word (`left: 50%`,
 * `translateX(-50%)`) with no clamp at all, up to 280px wide. A word near the
 * right margin therefore overhangs the viewport whenever the pointer merely
 * rests on it. That is desktop-only, because `@media (hover: hover)` is what
 * opens the hole — matching the user's report exactly.
 *
 * Note this is NOT the scrollbar-width bug it first looks like. `window.innerWidth`
 * vs `documentElement.clientWidth` would explain a few pixels; this explains up
 * to ~140px, and it explains why the fix for the closed case did not help.
 *
 * This asserts the same document-level invariant as the closed test rather than
 * any popover geometry: the fix is free to reposition, flip, or shrink the
 * popover however it likes, so long as the page does not scroll sideways.
 */
test("lesson page: a HOVER-revealed tooltip near the right margin never overflows", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId } = await seed(request);

	const failures: string[] = [];
	// Phone widths plus desktop widths. The desktop ones are the important
	// addition: they are where a vertical scrollbar exists, and the user's
	// report was from a desktop browser.
	for (const width of [390, 432, 800, 1280]) {
		await page.setViewportSize({ width, height: 700 });
		await page.goto(`/c/${curriculumId}`);
		await page.getByRole("button", { name: "Day 1" }).click();
		await expect(page.getByRole("button", { name: "Render Audio" })).toBeVisible({
			timeout: 15000,
		});
		await page.getByRole("button", { name: "Read", exact: true }).click();
		await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

		// Words closest to the right margin are the ones whose popovers have to be
		// clamped; a word mid-line passes trivially and would make this vacuous.
		//
		// Restricted to words that actually HAVE a popover (`.tt` is rendered only
		// when Tooltip's `hasContent` holds — a bare untracked word with no gloss
		// renders none). A contentless word cannot overflow anything.
		//
		// Sweeps the rightmost SEVERAL rather than only the single rightmost: which
		// word lands nearest the margin depends on where the dialogue happens to
		// wrap at each width, so a one-word probe passes or fails by luck of
		// line-breaking. With a sweep, the assertion holds regardless.
		const targets = await page.evaluate(() => {
			return [...document.querySelectorAll(".tt-wrap")]
				.map((el, i) => ({ i, right: el.getBoundingClientRect().right, w: el.getBoundingClientRect().width, has: !!el.querySelector(".tt") }))
				.filter((t) => t.has && t.w > 0)
				.sort((a, b) => b.right - a.right)
				.slice(0, 7)
				.map((t) => ({ index: t.i, right: Math.round(t.right) }));
		});
		expect(targets.length, "no words with popovers rendered").toBeGreaterThan(0);

		for (const t of targets) {
			const word = page.locator(".tt-wrap").nth(t.index);
			await word.hover();
			// Guard against measuring a popover that never appeared — that would
			// pass for the same reason the closed-only test does. `.tt` is
			// `display: none` until revealed, so this is a real check that the
			// hover-reveal state was actually entered.
			await expect(word.locator(".tt").first()).toBeVisible({ timeout: 5000 });

			const r = await page.evaluate(() => {
				const doc = document.documentElement;
				const shown = [...document.querySelectorAll(".tt")].filter(
					(el) => getComputedStyle(el).display !== "none",
				);
				const rect = shown[0]?.getBoundingClientRect();
				return {
					scrollW: doc.scrollWidth,
					clientW: doc.clientWidth,
					shownCount: shown.length,
					tipRight: rect ? Math.round(rect.right) : null,
					tipLeft: rect ? Math.round(rect.left) : null,
				};
			});
			expect(r.shownCount, "no popover displayed — measurement would be vacuous").toBeGreaterThan(0);

			if (r.scrollW > r.clientW)
				failures.push(
					`${width}px: page scrolls to ${r.scrollW} (viewport ${r.clientW}, overhang ${
						r.scrollW - r.clientW
					}); hovered word ends at ${t.right}, its popover spans ${r.tipLeft}..${r.tipRight}`,
				);
		}
	}

	expect(failures, failures.join("\n")).toEqual([]);
});

/**
 * F-11 / F-12 — the popover on a COARSE pointer, clamped on BOTH axes.
 *
 * Two defects, one blind spot.
 *
 * F-12, the blind spot: `playwright.config.ts` declares exactly one project,
 * `devices['Desktop Chrome']` — no `hasTouch`, no `isMobile`. Both tests above
 * resize the viewport but never emulate a coarse pointer, so the
 * `@media (pointer: coarse)` block in Tooltip.svelte — which takes `.tt` from
 * `12px`/`4px 8px` to `14px`/`8px 10px` and `.tt-btn` from `11px`/`2px 6px` to
 * `14px`/`8px 12px` — has never been exercised by ANY test. The oracle measured
 * a materially smaller popover than the user's phone renders. That is why K4
 * went green on desktop while the phone kept failing.
 *
 * Measured 2026-08-04 at a 412px viewport:
 *
 *   context                | (pointer: coarse) | (hover: hover) | .tt font/padding
 *   default Playwright     | false             | true           | 12px / 4px 8px
 *   hasTouch + isMobile    | true              | FALSE          | 14px / 8px 10px
 *
 * F-11, the live bug: the positioning `$effect` computes `shiftX` ONLY, the
 * inline style is `translateX(…)` only, and `.tt` is pinned `bottom: 100%` with
 * no flip. So a popover taller than the space above its word renders off the
 * TOP of the viewport and is clipped — the user's screenshot showed only its
 * last button row (`Reset`) surviving. Tall popovers are ordinary:
 * `.tt-actions` is `flex-wrap: wrap`, so a tracked word with several actions
 * takes a second row.
 *
 * ⚠️ WHY THIS CANNOT USE .hover(). Read the `(hover: hover) = false` cell
 * above. At coarse pointer the CSS hover-reveal never applies and the `hovered`
 * flag is inert, so a hovered popover stays `display: none` — a zero-size rect
 * that satisfies every containment assertion VACUOUSLY. Long-press is the only
 * touch opener: `LONG_PRESS_MS = 450`, with a `MOVE_CANCEL_PX = 10` jitter
 * budget. Hence the press-hold-release below, and the explicit visibility guard.
 */
test.describe("coarse pointer (touch phone)", () => {
	// NOT `{ ...devices["Pixel 7"] }`. A device descriptor carries
	// `defaultBrowserType`, and Playwright rejects that inside a describe
	// ("forces a new worker"). Only the emulation fields are spread — these are
	// the ones that flip `(pointer: coarse)`, which is the whole point.
	const PIXEL = devices["Pixel 7"];
	test.use({
		viewport: PIXEL.viewport,
		userAgent: PIXEL.userAgent,
		deviceScaleFactor: PIXEL.deviceScaleFactor,
		isMobile: PIXEL.isMobile,
		hasTouch: PIXEL.hasTouch,
	});

	test("lesson page: a long-pressed tooltip never pushes the page sideways", async ({
		page,
		request,
	}) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		const { curriculumId } = await seed(request);

		await page.goto(`/c/${curriculumId}`);
		// Proves the emulation actually took. If this ever reads `false` the whole
		// test is measuring desktop CSS again and F-12 has silently returned.
		const mq = await page.evaluate(() => ({
			coarse: matchMedia("(pointer: coarse)").matches,
			hoverHover: matchMedia("(hover: hover)").matches,
		}));
		expect(mq.coarse, "pointer is not coarse — this test would measure desktop CSS").toBe(true);
		expect(mq.hoverHover, "(hover: hover) is true — .hover() would open the popover and this test would not be testing the touch path").toBe(false);

		await page.getByRole("button", { name: "Day 1" }).click();
		await expect(page.getByRole("button", { name: "Render Audio" })).toBeVisible({
			timeout: 15000,
		});
		await page.getByRole("button", { name: "Read", exact: true }).click();
		await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

		// Words nearest the right margin are the ones whose popovers must clamp.
		// Sweeping several keeps the result independent of where the dialogue
		// happens to wrap.
		const targets = await page.evaluate(() =>
			[...document.querySelectorAll(".tt-wrap")]
				.map((el, i) => {
					const r = el.getBoundingClientRect();
					return { i, right: r.right, w: r.width, has: !!el.querySelector(".tt") };
				})
				.filter((t) => t.has && t.w > 0)
				.sort((a, b) => b.right - a.right)
				.slice(0, 7)
				.map((t) => ({ index: t.i, right: Math.round(t.right) })),
		);
		expect(targets.length, "no words with popovers rendered").toBeGreaterThan(0);

		const failures: string[] = [];

		for (const t of targets) {
			const box = await page.locator(".tt-wrap").nth(t.index).boundingBox();
			if (!box) continue;

			// Long press — the ONLY touch opener. Held well past LONG_PRESS_MS
			// (450), with no movement between down and up so the MOVE_CANCEL_PX
			// (10) jitter guard cannot cancel it.
			await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
			await page.mouse.down();
			await page.waitForTimeout(700);
			await page.mouse.up();
			await page.waitForTimeout(150);

			// Measured GLOBALLY rather than through the word's locator: opening the
			// popover re-renders the transcript and Svelte recreates the spans, so
			// any handle taken before the press is stale afterwards.
			const r = await page.evaluate(() => {
				const doc = document.documentElement;
				const shown = [...document.querySelectorAll(".tt")].filter(
					(el) => getComputedStyle(el).display !== "none",
				);
				const rect = shown[0]?.getBoundingClientRect();
				return {
					shownCount: shown.length,
					left: rect ? Math.round(rect.left) : null,
					right: rect ? Math.round(rect.right) : null,
					vw: window.innerWidth,
					scrollW: doc.scrollWidth,
					clientW: doc.clientWidth,
				};
			});

			// The guard that stops this passing on a popover that never opened: a
			// `display: none` popover has no box and clears every check vacuously.
			expect(r.shownCount, `popover never opened for word ${t.index}`).toBeGreaterThan(0);

			const where = `word ${t.index} (ends at ${t.right}), popover ${r.left}..${r.right}`;
			if (r.scrollW > r.clientW)
				failures.push(
					`${where}: page scrolls sideways to ${r.scrollW} (viewport ${r.clientW}, overhang ${r.scrollW - r.clientW})`,
				);
			if (r.right !== null && r.right > r.vw)
				failures.push(`${where}: popover overhangs the RIGHT by ${r.right - r.vw}px`);
			if (r.left !== null && r.left < 0)
				failures.push(`${where}: popover overhangs the LEFT by ${-r.left}px`);

			// Close before the next probe. NOT a click at a screen corner: Tooltip
			// closes on any outside `pointerdown`, and a blind corner click landed
			// on a nav control and navigated the page away — the next iteration
			// then measured a document collapsed to the viewport height (957 ->
			// 340). A synthetic document-level pointerdown closes it and can hit
			// nothing.
			await page.evaluate(() =>
				document.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true })),
			);
		}

		expect(failures, failures.join("\n")).toEqual([]);
	});
});
