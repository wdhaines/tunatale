import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Layout guards for the listen-preview modal, in a REAL browser.
 *
 * These cannot live in the vitest suite: jsdom performs no layout, so it
 * reports every element at 0×0 and cannot see the bug this file exists for.
 * The original defect was exactly that — the header and each row were separate
 * grid containers with `auto` tracks, so a row with a narrow "new" pill
 * computed different column widths than one with "today", and nothing lined up
 * with the header. Every unit test passed throughout.
 *
 * The invariant: the header cell and the corresponding cell of every row share
 * one left edge, to the pixel.
 */

const PHONE = { width: 390, height: 844 };

const TOPIC = "listen-preview-layout-e2e";

/**
 * Imported directly rather than generated: story generation replays a shared
 * cassette with a fixed number of recorded plays, and adding a third consumer
 * of the "ordering coffee" entry exhausts it ("used 1 times but only 1
 * recorded"). This story is also richer than the shared canned one on purpose —
 * several glossed words so the blur test has real material, and a 15-character
 * lemma so the no-truncation assertion has something to bite on.
 */
const STORY = {
	title: "Listen preview layout",
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
		{ word: "enainštirideset", translation: "forty-one" },
		{ word: "lepa", translation: "nice, lovely" },
	],
	morphology_focus: [],
};

// Seeded once for the whole file; the suite is serial so the first test does
// the work and the rest reuse it.
let seededCurriculumId: string | null = null;
let seededLessonId: string | null = null;

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
	// The lesson page is reachable directly, so no spec here needs to walk the
	// curriculum page and click "Day 1" to reach it — see `lessonURL`.
	const imported = await impRes.json();
	seededLessonId = imported.id ?? imported.lesson_id;
	return id;
}

/** The lesson page itself.
 *
 * Every spec in this file is about the LISTEN PREVIEW MODAL, never about how you
 * navigate to the lesson — `lesson-navigation.spec.ts` owns that journey and is
 * the only spec that should be walking it. Going direct drops one page load, one
 * click and a 15s-timeout wait per test.
 *
 * ⚠️ It does not weaken these specs, and that is measured rather than asserted:
 * the modal is still opened by the same real click on the same real page, and a
 * sabotage drill (Tooltip.svelte's `clampBounds` forced never to clamp) reddens
 * exactly the same specs before and after the change. */
function lessonURL(curriculumId: string): string {
	return `/c/${curriculumId}/l/${seededLessonId}`;
}

async function openPreview(page: import("@playwright/test").Page, curriculumId: string) {
	await page.goto(lessonURL(curriculumId));
	await page.getByRole("button", { name: "Mark as Listened" }).click();

	const modal = page.locator(".overlay .modal");
	await expect(modal).toBeVisible({ timeout: 10000 });
	await expect(modal.locator(".candidate").first()).toBeVisible({ timeout: 10000 });

	// Cancel the auto-commit countdown so it cannot close the modal mid-measure.
	await modal.locator("h2").click();
	return modal;
}

test.describe.configure({ mode: "serial" });

test("listen preview: header and every row sit on identical column tracks", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");

	await page.setViewportSize(PHONE);
	const modal = await openPreview(page, await curriculumId(request));

	const report = await modal.evaluate((m) => {
		const head = [...m.querySelectorAll(".list-head > *")];
		const rows = [...m.querySelectorAll(".candidate")];
		// Header cell index → the selector occupying that same track in a row.
		const COLS: [string, number][] = [
			[".text", 0],
			[".tag.day", 1],
			[".grade", 2],
		];
		let maxDev = 0;
		let worst: string | null = null;
		for (const row of rows) {
			for (const [sel, i] of COLS) {
				const el = row.querySelector(sel);
				if (!el) continue;
				const dev = Math.abs(el.getBoundingClientRect().left - head[i].getBoundingClientRect().left);
				if (dev > maxDev) {
					maxDev = dev;
					worst = `${row.querySelector(".text")?.textContent?.trim()} ${sel}`;
				}
			}
		}
		// A gloss is stacked beneath its word and must start on the same edge.
		let glossDev = 0;
		for (const row of rows) {
			const g = row.querySelector(".gloss");
			const w = row.querySelector(".text");
			if (!g || !w) continue;
			glossDev = Math.max(
				glossDev,
				Math.abs(g.getBoundingClientRect().left - w.getBoundingClientRect().left),
			);
		}
		return {
			rowCount: rows.length,
			maxDev,
			worst,
			glossDev,
			modalOverflow: m.scrollWidth - m.clientWidth,
			bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
		};
	});

	expect(report.rowCount).toBeGreaterThan(0);
	expect(report.maxDev, `column drift on ${report.worst}`).toBe(0);
	expect(report.glossDev).toBe(0);
	// The modal must never scroll sideways, nor push the page sideways.
	expect(report.modalOverflow).toBe(0);
	expect(report.bodyOverflow).toBe(0);
});

/**
 * The modal must keep real side gutters, and it must keep them when the text
 * scales. Both halves are load-bearing:
 *
 *  - Gutters: `.modal` was `content-box` (there is no global border-box reset),
 *    so `width: 90%` / `max-width: 420px` described the CONTENT box and the
 *    real border-box was +48px padding. Flexbox then shrank the modal to
 *    exactly the viewport, so it rendered edge-to-edge at every phone width —
 *    and 430px wide at a 430px viewport, past its own max-width.
 *  - Text scale: the row grid's two fixed tracks (due + grade) are in `rem`, so
 *    an Android font-size setting scales the layout's hard minimum. At 320px
 *    with an 18px root the modal could not shrink far enough and spilled off
 *    BOTH edges (measured: -3 → 323).
 *
 * The pre-existing guard above missed all of this because it measured one
 * viewport at the default font and asserted `scrollWidth === clientWidth` —
 * which an edge-to-edge modal satisfies. "No overflow" is not "has gutters".
 */
const GUTTER_MIN = 8;

test("listen preview: the modal keeps gutters across phone widths and text sizes", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");

	await page.setViewportSize(PHONE);
	const modal = await openPreview(page, await curriculumId(request));

	// [viewport width, root font px]. 16 is the browser default; 18–20 is what
	// Android Chrome's font-size setting produces, and it scales `rem` tracks.
	const CASES: [number, number][] = [
		[320, 16],
		[320, 18],
		[360, 16],
		[360, 20],
		[390, 16],
		[390, 20],
		[430, 16],
	];

	const failures: string[] = [];
	for (const [width, rootFont] of CASES) {
		await page.setViewportSize({ width, height: 844 });
		await page.evaluate((f) => {
			document.documentElement.style.fontSize = `${f}px`;
		}, rootFont);

		const r = await modal.evaluate((m) => {
			const doc = document.documentElement;
			const rect = m.getBoundingClientRect();
			const clippedLabels = [...m.querySelectorAll<HTMLElement>(".grade button")]
				.filter((b) => b.scrollWidth > b.clientWidth + 1)
				.map((b) => b.textContent?.trim());
			return {
				vw: doc.clientWidth,
				left: rect.left,
				right: rect.right,
				width: rect.width,
				modalOverflow: m.scrollWidth - m.clientWidth,
				docOverflow: doc.scrollWidth - doc.clientWidth,
				clipped: clippedLabels.length,
				clippedSample: clippedLabels.slice(0, 3),
			};
		});

		const tag = `${width}px @ ${rootFont}px root`;
		if (r.left < GUTTER_MIN || r.vw - r.right < GUTTER_MIN)
			failures.push(
				`${tag}: gutters ${r.left.toFixed(1)}/${(r.vw - r.right).toFixed(1)} (modal ${r.width.toFixed(1)}px)`,
			);
		if (r.modalOverflow > 0) failures.push(`${tag}: modal scrolls sideways by ${r.modalOverflow}px`);
		if (r.docOverflow > 0) failures.push(`${tag}: page scrolls sideways by ${r.docOverflow}px`);
		if (r.clipped > 0)
			failures.push(`${tag}: ${r.clipped} clipped grade label(s) ${JSON.stringify(r.clippedSample)}`);
	}

	await page.evaluate(() => {
		document.documentElement.style.fontSize = "";
	});

	expect(failures, failures.join("\n")).toEqual([]);
});

/**
 * Below the point where the three-column row stops fitting, the grade control
 * drops to its own full-width line rather than squeezing the tracks until the
 * modal outgrows the screen. The trigger is a container query in `rem`, so it
 * follows the text size, not just the viewport — which is why this measures at
 * a scaled root font rather than an implausibly narrow phone.
 */
test("listen preview: the grade control restacks instead of overflowing when space runs out", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");

	await page.setViewportSize(PHONE);
	const modal = await openPreview(page, await curriculumId(request));

	await page.setViewportSize({ width: 320, height: 844 });
	await page.evaluate(() => {
		document.documentElement.style.fontSize = "20px";
	});

	const r = await modal.evaluate((m) => {
		const row = m.querySelector(".candidate")!;
		const rowRect = row.getBoundingClientRect();
		const grade = row.querySelector(".grade")!.getBoundingClientRect();
		const text = row.querySelector(".text")!.getBoundingClientRect();
		const headCells = [...m.querySelectorAll(".list-head > *")].filter(
			(c) => getComputedStyle(c).display !== "none",
		);
		return {
			// Stacked: the control starts at the row's left edge (under the word),
			// not in a third column beside it, and runs to the row's content edge.
			gradeLeft: Math.round(grade.left),
			textLeft: Math.round(text.left),
			rowContentRight: Math.round(rowRect.right - parseFloat(getComputedStyle(row).paddingRight)),
			gradeRight: Math.round(grade.right),
			headCellCount: headCells.length,
			headLabels: headCells.map((c) => c.textContent?.trim()),
		};
	});

	await page.evaluate(() => {
		document.documentElement.style.fontSize = "";
	});

	expect(r.gradeLeft).toBe(r.textLeft);
	expect(r.gradeRight).toBe(r.rowContentRight);
	// The header cannot keep advertising a column that no longer exists.
	expect(r.headLabels).toEqual(["Word", "Due"]);
});

test("listen preview: words are never truncated and grade targets keep their floor", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");

	await page.setViewportSize(PHONE);
	const modal = await openPreview(page, await curriculumId(request));

	const report = await modal.evaluate((m) => {
		const rows = [...m.querySelectorAll(".candidate")];
		// The word is the item being graded: it wraps (and hyphenates), never
		// ellipsises. Overflow here means a lemma the user cannot fully read.
		const clippedWords = rows
			.map((r) => r.querySelector(".text") as HTMLElement)
			.filter((el) => el && el.scrollWidth > el.clientWidth + 1)
			.map((el) => el.textContent?.trim());

		// No grade label may be cut off by its segment.
		const clippedLabels = [...m.querySelectorAll<HTMLElement>(".grade button")]
			.filter((b) => b.scrollWidth > b.clientWidth + 1)
			.map((b) => b.textContent?.trim());

		const targets = [...m.querySelectorAll(".grade button")].map((b) => {
			const r = b.getBoundingClientRect();
			return { w: Math.round(r.width), h: Math.round(r.height) };
		});
		return {
			clippedWords,
			clippedLabels,
			minTargetH: Math.min(...targets.map((t) => t.h)),
			minTargetW: Math.min(...targets.map((t) => t.w)),
		};
	});

	expect(report.clippedWords).toEqual([]);
	expect(report.clippedLabels).toEqual([]);
	// Not the 44px ideal — that was traded for row density deliberately — but a
	// floor that must not regress silently the way it did when the due pill was
	// first centred across both rows.
	expect(report.minTargetH).toBeGreaterThanOrEqual(30);
	expect(report.minTargetW).toBeGreaterThanOrEqual(24);
});

/**
 * A blurred gloss is blurred by the ENGINE, not by a class name.
 *
 * The toggling — starts blurred, reveals on click, and only that row's —
 * is app-computed and was stripped from here on 2026-08-15 (`tunatale-vnf.11`).
 * It is asserted in jsdom by `ListenPreviewModal.test.ts::"a gloss starts
 * blurred and reveals on click"` and `::"revealing one row's gloss does not
 * reveal another's"`, which check the exact `.blurred` class transitions this
 * test used to count.
 *
 * What survives here is the half no jsdom tier can make: that the class
 * actually RESOLVES to a filter. jsdom performs no cascade, so
 * `getComputedStyle(el).filter` is `""` there no matter what the CSS says — a
 * gloss whose blur was deleted from the stylesheet stays green at every tier
 * below a real browser.
 */
test("listen preview: a blurred gloss resolves to a real blur filter", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");

	await page.setViewportSize(PHONE);
	const modal = await openPreview(page, await curriculumId(request));

	const glosses = modal.locator(".gloss.blurred");
	// A hard assertion, not a test.skip: a silently skipped layout test is
	// indistinguishable from a passing one, and the seed above guarantees
	// glossed candidates. With nothing blurred on screen there is no computed
	// style worth reading and the assertion below proves nothing.
	expect(await glosses.count()).toBeGreaterThanOrEqual(1);

	expect(await glosses.first().evaluate((el) => getComputedStyle(el).filter)).toContain("blur");
});

/**
 * F-7 — cancelling the auto-mark countdown must not eat the click that cancelled it.
 *
 * Reported 2026-08-04 running the manual test plan's D3: "D3 canceled the text,
 * but it made the cursor jump and the click I made to grade an item seemed to be
 * swallowed."
 *
 * MECHANISM. Cancellation fires on `onpointerdown` (the overlay's
 * `handleInteraction`), while the countdown line is *unmounted* rather than
 * hidden:
 *
 *     {#if !countdownCancelled && countdownId !== null}
 *         <p class="countdown">Auto-marking in {countdown}s</p>
 *     {/if}
 *
 * So the `<p>` is removed synchronously **while the pointer is still down**, and
 * every row below it shifts up by that line's height. A `click` event only fires
 * on the nearest common ancestor of the pointerdown and pointerup targets — the
 * content moved out from under the cursor in between, so the grade never
 * registers. The reported "cursor jump" is the same reflow: the content moved,
 * not the cursor.
 *
 * This must be an e2e test. jsdom performs no layout, so the reflow that causes
 * the bug does not exist there and a vitest version would pass against the
 * broken code.
 *
 * The contract has two halves, deliberately fix-agnostic — they pin what the
 * user sees, not the mechanism:
 *
 *   1. the same click that cancels also applies its rating, and
 *   2. cancelling shifts no geometry.
 *
 * Reserving the line's box (render it always, empty its text) satisfies both.
 * Moving the trigger to pointerup/click satisfies NEITHER: the unmount still
 * happens, just one event later, so the rows still shift. It is also the worse
 * fix on its own terms — cancellation weakens, since a press that never
 * completes would stop cancelling.
 *
 * ⚠️ ONLY HALF 2 IS ASSERTED HERE, and which half is which is the whole point.
 * Measured 2026-08-04 against the broken code: the geometry assertion failed
 * deterministically — `260 -> 247`, a 13px shift, the countdown line's exact
 * height — while the `aria-pressed` assertion PASSED, because Playwright's
 * synthetic click warps the cursor and dispatches mousedown/mouseup at
 * identical coordinates with no human timing, so it survives a 13px reflow that
 * a real pointer near a row boundary does not.
 *
 * So half 1 was never a regression guard HERE: it stayed green with the bug
 * live. It was stripped on 2026-08-15 (`tunatale-vnf.11`) and is asserted where
 * it can be checked directly, in jsdom — `ListenPreviewModal.test.ts::"a grade
 * click cancels the countdown permanently"` clicks a grade during a running
 * countdown and asserts both that the rating landed (`aria-pressed`) and that
 * nothing auto-commits afterwards. Do NOT restore it here in the belief that it
 * detects the reflow, and do NOT "fix" this class of bug by satisfying it: the
 * reflow is the defect, and geometry is the only tier that can see it.
 */
test("listen preview: cancelling the countdown shifts no rows", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const cid = await curriculumId(request);

	// Default is "off"; the countdown must be RUNNING or this tests nothing.
	await page.addInitScript(() => localStorage.setItem("listenCountdown", "60"));
	await page.setViewportSize(PHONE);

	await page.goto(lessonURL(cid));
	await page.getByRole("button", { name: "Mark as Listened" }).click();

	const modal = page.locator(".overlay .modal");
	await expect(modal).toBeVisible({ timeout: 10000 });
	await expect(modal.locator(".candidate").first()).toBeVisible({ timeout: 10000 });

	// Guard against a vacuous pass: with no countdown on screen there is nothing
	// to cancel, and every assertion below would hold trivially.
	//
	// Asserted on a STATE HOOK, not on the countdown's text. Under the option-C
	// placement the seconds live in a span that stays MOUNTED (with
	// `visibility: hidden`) after cancelling, so the button's width cannot change
	// — and `toHaveCount(0)` counts hidden elements, so a text locator would fail
	// here for a reason that has nothing to do with the countdown still running.
	// The tempting "fix" for that failure is to weaken this test. Don't.
	const gradeAll = modal.getByRole("button", { name: /Grade All/ });
	await expect(gradeAll).toHaveAttribute("data-countdown", "running", { timeout: 5000 });

	const hard = modal.locator('button[data-grade="hard"]').first();

	const before = await hard.boundingBox();
	expect(before, "grade button has no box").not.toBeNull();

	await hard.click();

	// Also a vacuity guard, not a claim about cancellation: unless this click
	// really did cancel, there was no unmount, nothing could have reflowed, and
	// the geometry comparison below holds trivially. Same state hook as above,
	// for the same reason.
	await expect(gradeAll).toHaveAttribute("data-countdown", "idle");

	// THE ROOT CAUSE: cancelling must not move anything.
	const after = await hard.boundingBox();
	expect(after, "grade button has no box after cancel").not.toBeNull();
	expect(
		Math.round(after!.y),
		`row shifted ${Math.round(before!.y)} -> ${Math.round(after!.y)} when the countdown was cancelled`,
	).toBe(Math.round(before!.y));
});

/**
 * F-9 / F-10 — the auto-grade countdown lives ON the Grade All button.
 *
 * Placement chosen by the user from a four-option mock set (option C). It
 * replaces the centred `<p class="countdown">` line, whose reserved box —
 * `min-height: 1lh`, kept mounted so cancelling could not reflow the rows —
 * left visible dead space between the heading and the actions once the text
 * cleared. Moving the timer out of the modal's vertical flow removes the gap
 * AND satisfies F-7's constraint structurally rather than by holding a box open.
 *
 * WHY THE GEOMETRY ASSERTION IS THE LOAD-BEARING ONE. F-7 was a reflow bug:
 * cancellation fires on `pointerdown`, so anything whose geometry changes at
 * that moment moves content under a pointer that is still down, and the `click`
 * never lands. Option C moves the timer INTO `.actions`, one row above the
 * list — so a label that reads "Grade All 12s" while running and "Grade All"
 * after cancelling reintroduces F-7 one row higher. The seconds must therefore
 * occupy a fixed-width slot that survives cancellation.
 *
 * Fails today on the first assertion: there is no `data-countdown` attribute.
 *
 * TWO CLAIMS WERE STRIPPED ON 2026-08-15 (`tunatale-vnf.11`) — the button's
 * accessible name reading "auto-grading" (and never the old "auto-marking"),
 * and the absence of the legacy `<p class="countdown">` line. Both are strings
 * the APP computes; neither needs an engine. They are now asserted, as an exact
 * `aria-label` rather than two regexes, by `ListenPreviewModal.test.ts::"shows
 * a decrementing 'Auto-grading' countdown tick while running"`. ⚠️ That test
 * did NOT assert either one before this change — the audit's pairing table
 * claimed it did. The assertions were added there in the same commit; nothing
 * moved down to a tier that was not already checking it.
 *
 * What is left is measurement, plus the guards that keep the measurement
 * meaningful. The tick's `10s` -> `9s` text assertions LOOK app-computed and
 * are not up for stripping: they are what proves the width comparison spans the
 * 3-character-to-2 boundary at all.
 */
test("listen preview: the countdown rides Grade All and never resizes it", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const cid = await curriculumId(request);

	// 10s, NOT 60s — and this is the whole point of the width half of this test.
	// The tick renders `${countdown}s`, so the only place its STRING LENGTH
	// changes is the 10 -> 9 boundary (3 chars -> 2). Sampling 60 -> 59 compares
	// two 3-character strings and cannot see a missing `min-width` at all:
	// verified by drill 2026-08-04, where deleting `.tick { min-width }` left the
	// 60s version of this test GREEN. Ten seconds is also comfortably longer than
	// the ~3s this test needs before it cancels, so the countdown cannot fire.
	await page.addInitScript(() => localStorage.setItem("listenCountdown", "10"));
	await page.setViewportSize(PHONE);

	await page.goto(lessonURL(cid));
	await page.getByRole("button", { name: "Mark as Listened" }).click();

	const modal = page.locator(".overlay .modal");
	await expect(modal).toBeVisible({ timeout: 10000 });
	await expect(modal.locator(".candidate").first()).toBeVisible({ timeout: 10000 });

	const gradeAll = modal.getByRole("button", { name: /Grade All/ });
	const tick = modal.locator(".grade-all .tick");

	// 1. Vacuity guard: the countdown must be RUNNING, or there is no tick to
	//    change width and every measurement below is of a static button.
	await expect(gradeAll).toHaveAttribute("data-countdown", "running", { timeout: 5000 });

	// 2. Geometry is stable ACROSS THE 10 -> 9 BOUNDARY, where the tick's text
	//    goes from 3 characters to 2. Without a fixed-width slot the button
	//    narrows here, one row above the list, which is F-7 again.
	await expect(tick).toHaveText("10s", { timeout: 5000 });
	const running = await gradeAll.boundingBox();
	expect(running, "Grade All has no box").not.toBeNull();

	// Vacuity guard: if the tick never reached "9s" the width comparison below
	// spans no boundary and proves nothing — the exact way the 60s version of
	// this test passed with `min-width` deleted.
	await expect(tick).toHaveText("9s", { timeout: 5000 });
	const stillRunning = await gradeAll.boundingBox();
	expect(
		stillRunning,
		`Grade All resized as the countdown ticked: ${JSON.stringify(running)} -> ${JSON.stringify(stillRunning)}`,
	).toEqual(running);

	// 3. And stable across cancellation — the F-7 constraint, one row up.
	await modal.locator("h2").click();
	await expect(gradeAll).toHaveAttribute("data-countdown", "idle");
	const cancelled = await gradeAll.boundingBox();
	expect(
		cancelled,
		`Grade All resized when the countdown was cancelled: ${JSON.stringify(running)} -> ${JSON.stringify(cancelled)}`,
	).toEqual(running);
});

/**
 * F-13's oracle is the UNIT test, not an e2e test — deliberately, and this note
 * exists so nobody "restores" the e2e one.
 *
 * An e2e version was written and had to be withdrawn: it asserted the tail
 * disclosure's summary (`N words for subsequent listens`), but this file's
 * fixture produces **9 candidates and ZERO tail rows** (probed 2026-08-04). With
 * nothing over budget the component correctly renders no disclosure at all, so
 * the test could never go green — it was red because the fixture had no tail,
 * which looks identical to red because the feature is missing. An oracle that
 * cannot pass is worse than no oracle: it reads as a real failure forever.
 *
 * The real guard is
 * `ListenPreviewModal.creationTail.test.ts::"renders the tail below the divider
 * and collapsed behind one counting disclosure"`, which mocks a 3-row tail and
 * asserts the summary as an EXACT string, plus `.tag.next-listen` count 0 and
 * the live/tail row split. Restoring an e2e version means first seeding this
 * fixture past the daily new-card cap so a tail actually exists.
 */

/**
 * The `learning` due pill must not touch the Skip button.
 *
 * User: "the learning items' badge is flush with skip … I think when the badge
 * was 'learn' instead of 'learning' it worked." Correct, including the cause:
 * the Due column is a FIXED `3rem` track with a `0.35rem` gap, and at the tag's
 * `0.66rem` scale `learning` renders wider than the track, so the pill overruns
 * its own column and eats the whole gap.
 *
 * ⚠️ The fix must NOT make the track `auto`. Fixed tracks are deliberate — an
 * `auto` track resolves against its own container's content, so a row with a
 * narrow "new" pill computed different columns than one with "today" and
 * nothing lined up with the header. That is the bug the top of this file
 * exists for.
 *
 * The label is INJECTED rather than waited for. `dueLabel()` emits a closed set
 * of labels and `learning` is its widest; seeding a real learning card would
 * make this test depend on scheduler state that has nothing to do with the
 * track width. Injecting the worst case tests the invariant directly: the Due
 * track fits the longest label the component can emit.
 */
test("listen preview: the Due track fits the widest label it can emit", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const cid = await curriculumId(request);
	await page.setViewportSize(PHONE);
	const modal = await openPreview(page, cid);
	await expect(modal.locator(".candidate").first()).toBeVisible();

	// [viewport, root font px]. Only three-column cases: below the
	// `@container (max-width: 18rem)` breakpoint the grade control restacks onto
	// its own row, Skip sits on a different line, and "the gap between the pill
	// and Skip" stops meaning anything (measured there as -273 and -331, which
	// would read as a catastrophic pass/fail either way). Root font is varied
	// because Android's font-size setting scales the `rem` tracks — that is the
	// axis the user is on.
	const CASES: [number, number][] = [
		[360, 16],
		[390, 16],
		[390, 18],
		[412, 18],
	];

	const failures: string[] = [];
	for (const [width, root] of CASES) {
		await page.setViewportSize({ width, height: 844 });
		await page.evaluate((px) => {
			document.documentElement.style.fontSize = `${px}px`;
		}, root);

		const m = await page.evaluate(() => {
			const rows = [...document.querySelectorAll(".candidate")];
			let pillW = 0;
			let gap = Infinity;
			let measured = 0;
			let tracks = 0;
			for (const row of rows) {
				const pill = row.querySelector(".tag.day") as HTMLElement | null;
				const skip = row.querySelector(".skip") as HTMLElement | null;
				if (!pill || !skip) continue;
				// `learning` is the widest label dueLabel() can emit. Injected rather
				// than waited for: seeding a real learning card would make this
				// depend on scheduler state that has nothing to do with track width.
				pill.textContent = "learning";
				const p = pill.getBoundingClientRect();
				const sk = skip.getBoundingClientRect();
				const cols = getComputedStyle(row).gridTemplateColumns.split(" ");
				tracks = cols.length;
				pillW = Math.max(pillW, Math.round(p.width * 10) / 10);
				// Same visual row only — the restacked arm puts Skip on a lower line.
				if (Math.abs(sk.top - p.top) < 5)
					gap = Math.min(gap, Math.round((sk.left - p.right) * 10) / 10);
				measured++;
			}
			const dueTrack = Math.round(parseFloat(getComputedStyle(rows[0]).gridTemplateColumns.split(" ")[1]) * 10) / 10;
			return { pillW, gap, measured, tracks, dueTrack };
		});

		const tag = `${width}px @ ${root}px root`;
		if (m.measured === 0) failures.push(`${tag}: no row had both a due pill and a Skip button`);
		if (m.tracks !== 3) failures.push(`${tag}: expected the three-column layout, got ${m.tracks} tracks`);
		// THE invariant. The Due track is deliberately FIXED (an `auto` track
		// resolves per-container and desynchronises the header from the rows —
		// the bug the top of this file exists for), so the fix is to size the
		// track for its content, not to make it elastic.
		if (m.pillW > m.dueTrack)
			failures.push(
				`${tag}: the "learning" pill is ${m.pillW}px in a ${m.dueTrack}px Due track — it overruns its own column by ${Math.round((m.pillW - m.dueTrack) * 10) / 10}px and eats the 0.35rem gap`,
			);
		// The user-visible symptom: the pill ends up flush against Skip.
		if (m.gap < 4)
			failures.push(`${tag}: only ${m.gap}px between the due pill and Skip`);
	}

	expect(failures, failures.join("\n")).toEqual([]);
});

/**
 * The Grade All tick box is reserved for the countdown's whole lifetime — F-7's
 * constraint, that nothing may reflow under a pointer still down. But when the
 * countdown preference is OFF the reservation is dead weight: the countdown can
 * never run, so the invisible 1.6em box is never once used, and it pushes the
 * visible "Grade All" words permanently off-centre inside their button.
 *
 * The reservation must be gated on the PREFERENCE, not on `countdownRunning`.
 * The pref cannot change while the modal is open, so the box is stable for the
 * modal's entire lifetime and F-7 survives; only the pref-off case — where the
 * box would never be used — loses it.
 *
 * The oracle is the GLYPH rect, not the `.label` box: the button centres
 * `.label` regardless, so the box can be centred while its text sits left of
 * centre. The text node's own range is what must land on the button's centre.
 */
async function labelGlyphCentre(gradeAll: import("@playwright/test").Locator) {
	return await gradeAll.evaluate((el) => {
		const label = el.querySelector(".label") as HTMLElement;
		const tn = Array.from(label.childNodes).find(
			(n) => n.nodeType === Node.TEXT_NODE && n.textContent!.trim().length > 0,
		) as Text;
		const r = document.createRange();
		r.setStart(tn, 0);
		r.setEnd(tn, tn.textContent!.trimEnd().length);
		const g = r.getBoundingClientRect();
		const b = el.getBoundingClientRect();
		return { glyphCentre: g.left + g.width / 2, buttonCentre: b.left + b.width / 2, glyphLeft: g.left };
	});
}

test("listen preview: with the countdown pref off, Grade All's label is truly centred", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const cid = await curriculumId(request);

	await page.addInitScript(() => localStorage.setItem("listenCountdown", "off"));
	await page.setViewportSize(PHONE);
	await page.goto(lessonURL(cid));
	await page.getByRole("button", { name: "Mark as Listened" }).click();

	const modal = page.locator(".overlay .modal");
	await expect(modal).toBeVisible({ timeout: 10000 });
	await expect(modal.locator(".candidate").first()).toBeVisible({ timeout: 10000 });
	const gradeAll = modal.getByRole("button", { name: /Grade All/ });

	// Vacuity guard: with the pref off the countdown must not be running, or
	// this measurement could be reading a state this test's setup ruled out.
	await expect(gradeAll).toHaveAttribute("data-countdown", "idle");

	const { glyphCentre, buttonCentre } = await labelGlyphCentre(gradeAll);
	expect(
		Math.abs(glyphCentre - buttonCentre),
		`glyph centre ${glyphCentre.toFixed(2)} vs button centre ${buttonCentre.toFixed(2)}`,
	).toBeLessThanOrEqual(1.5);
});

test("listen preview: with the countdown pref on, cancelling still shifts no glyph (F-7)", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const cid = await curriculumId(request);

	await page.addInitScript(() => localStorage.setItem("listenCountdown", "60"));
	await page.setViewportSize(PHONE);
	await page.goto(lessonURL(cid));
	await page.getByRole("button", { name: "Mark as Listened" }).click();

	const modal = page.locator(".overlay .modal");
	await expect(modal).toBeVisible({ timeout: 10000 });
	await expect(modal.locator(".candidate").first()).toBeVisible({ timeout: 10000 });
	const gradeAll = modal.getByRole("button", { name: /Grade All/ });

	// Guard against a vacuous pass: the countdown must be running before there
	// is anything to cancel.
	await expect(gradeAll).toHaveAttribute("data-countdown", "running", { timeout: 5000 });

	const running = await labelGlyphCentre(gradeAll);

	// The same click that cancels the countdown also grades the row (F-7's
	// contract) — and cancelling must not move the "Grade All" glyph, because
	// the reserved tick box survives the running→idle transition.
	const hard = modal.locator('button[data-grade="hard"]').first();
	await hard.click();
	await expect(gradeAll).toHaveAttribute("data-countdown", "idle");

	const idle = await labelGlyphCentre(gradeAll);
	expect(
		Math.abs(idle.glyphLeft - running.glyphLeft),
		`glyph moved ${running.glyphLeft.toFixed(2)} -> ${idle.glyphLeft.toFixed(2)} on cancel`,
	).toBeLessThanOrEqual(0.5);
});
