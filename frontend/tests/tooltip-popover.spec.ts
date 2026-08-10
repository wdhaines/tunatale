import { test, expect, devices } from "@playwright/test";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * F-17 and F-21 — two defects in the word popover, both reported from a phone on
 * 2026-08-05, both in `Tooltip.svelte`, and deliberately shipped together
 * because F-17's layout change moves the very rect F-21 measures.
 *
 *   F-17  "Tooltip looks right except that it's too narrow and reset wraps."
 *   F-21  "the tooltip pops up behind the div with all the audio UI."
 *
 * ⚠️ BOTH MUST RUN AT COARSE POINTER, and that is not a detail.
 *
 * `playwright.config.ts` declares one project, `devices['Desktop Chrome']`. At a
 * fine pointer `.tt-btn` is 11px/2px 6px; at a coarse pointer the
 * `@media (pointer: coarse)` block takes it to 14px/8px 12px. Measured on a
 * 412px Pixel 7, the four actions of a word in `learning` state
 * (`Got it ✓` / `Ignore` / `Known` / `Reset`) need **301px** against `.tt`'s
 * **280px** content box — so the row wraps and `Reset` lands alone. At the
 * desktop size the same four fit one line and the defect does not exist. A
 * desktop-only oracle would have gone green on a live bug; that is F-12's
 * lesson and the K4 post-mortem, and it is why the emulation guard below is an
 * assertion rather than a comment.
 *
 * Seeding notes:
 *  1. The vocabulary is INVENTED. E2E specs share one backend DB, and SRS items
 *     are keyed per lemma — seeding real Slovene words would hand other specs
 *     items in `learning` state that they never asked for.
 *  2. Words must be TRACKED (`POST /api/srs/listen`) and then promoted out of
 *     `new` (`POST /items/{id}/state {learning}`). In `new`, `showResetNew` is
 *     false, the popover has only three actions, and they fit one line — the
 *     seed would be vacuous for F-17.
 *  3. The dialogue is long on purpose. F-21 needs a word to sit in the band just
 *     under the sticky player, which cannot happen on a page too short to
 *     scroll (measured: a two-line transcript tops out at scrollY 95).
 */

const TOPIC = "tooltip-popover-e2e";

// Invented tokens — see seeding note 1. The e2e backend runs the `lowercase`
// lemmatizer, so each lemma is just its surface form lowercased.
const WORDS = [
	"zvrkolina",
	"mrgalec",
	"plotvišče",
	"krunžava",
	"šembelj",
	"trlepnica",
	"vogradec",
	"žmirkalo",
];

const LINE = `${WORDS[0]} ${WORDS[1]}, ${WORDS[2]} ${WORDS[3]} ${WORDS[4]}, ${WORDS[5]} ${WORDS[6]} ${WORDS[7]}.`;

const STORY = {
	title: "Tooltip popover",
	key_phrases: [{ phrase: `${WORDS[0]} ${WORDS[1]}`, translation: "placeholder phrase" }],
	scenes: [
		{
			label: "Scene one",
			// Long enough that the page scrolls well past the sticky player — see
			// seeding note 3.
			lines: Array.from({ length: 8 }, (_, i) => ({
				speaker: i % 2 === 0 ? "female-1" : "male-1",
				text: LINE,
				translation: "A placeholder line used only for layout measurement.",
			})),
		},
	],
	dialogue_glosses: WORDS.map((w, i) => ({ word: w, translation: `gloss ${i}` })),
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
					collocations: [`${WORDS[0]} ${WORDS[1]}`],
					learning_objective: "measure popover layout",
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

	const listenRes = await request.post(`${BACKEND}/api/srs/listen`, {
		data: { lesson_id: lesson.id ?? lesson.lesson_id, word_ratings: {}, kp_ratings: {} },
	});
	if (!listenRes.ok())
		throw new Error(`listen failed: ${listenRes.status()} ${await listenRes.text()}`);

	// Promote ONLY this spec's own items out of `new` — see seeding notes 1 and 2.
	const itemsRes = await request.get(`${BACKEND}/api/srs/items?limit=10000`);
	if (!itemsRes.ok())
		throw new Error(`items list failed: ${itemsRes.status()} ${await itemsRes.text()}`);
	const mine = ((await itemsRes.json()).items ?? []).filter((i: { text: string }) =>
		WORDS.includes(i.text.toLowerCase()),
	);
	if (mine.length === 0) throw new Error("seed produced no SRS items for this spec's vocabulary");
	for (const item of mine) {
		const res = await request.post(`${BACKEND}/api/srs/items/${item.id}/state`, {
			data: { state: "learning" },
		});
		if (!res.ok()) throw new Error(`promote failed: ${res.status()} ${await res.text()}`);
	}

	seeded = { curriculumId: curriculum.id };
	return seeded;
}

test.describe.configure({ mode: "serial" });

test.describe("word popover (coarse pointer)", () => {
	// NOT `{ ...devices["Pixel 7"] }` — a device descriptor carries
	// `defaultBrowserType`, which Playwright rejects inside a describe ("forces a
	// new worker"). Only the emulation fields are spread; those are the ones that
	// flip `(pointer: coarse)`, which is the entire point. Pattern copied from
	// `transcript-overflow.spec.ts`.
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
		// desktop CSS, where neither defect reproduces (see the header).
		const mq = await page.evaluate(() => ({
			coarse: matchMedia("(pointer: coarse)").matches,
			hoverHover: matchMedia("(hover: hover)").matches,
		}));
		expect(mq.coarse, "pointer is not coarse — this spec would measure desktop CSS").toBe(true);
		expect(
			mq.hoverHover,
			"(hover: hover) is true — .hover() would open the popover and the touch path would go untested",
		).toBe(false);

		await page.getByRole("button", { name: "Day 1" }).click();
		await expect(page.getByRole("button", { name: "Render Audio" })).toBeVisible({
			timeout: 15000,
		});
		await page.getByRole("button", { name: "Read", exact: true }).click();
		await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });
	}

	/** Long-press is the ONLY touch opener: LONG_PRESS_MS = 450, jitter budget
	 *  MOVE_CANCEL_PX = 10. Held well past both, with no movement in between. */
	async function longPress(page: import("@playwright/test").Page, index: number) {
		const box = await page.locator(".tt-wrap").nth(index).boundingBox();
		if (!box) return false;
		await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
		await page.mouse.down();
		await page.waitForTimeout(700);
		await page.mouse.up();
		await page.waitForTimeout(150);
		return true;
	}

	/** Closes via a synthetic document-level pointerdown. NOT a click at a screen
	 *  corner — a blind corner click landed on a nav control and navigated away
	 *  mid-sweep (recorded in transcript-overflow.spec.ts). */
	async function closePopover(page: import("@playwright/test").Page) {
		await page.evaluate(() =>
			document.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true })),
		);
	}

	/**
	 * F-17 — the action row must not leave a short stub line.
	 *
	 * ⚠️ The assertion is deliberately NOT "the row fits on one line", "max-width
	 * is Npx", or "there are two columns". All three pin a mechanism. What the
	 * user saw was a ragged row: three buttons across the top and a lone `Reset`
	 * occupying a quarter of the width below them. So the oracle measures the
	 * ragged-ness — every wrapped line must reach at least 90% of the widest
	 * line's extent — and any layout that achieves it passes: an even grid,
	 * stretched flex items, or a cap wide enough that nothing wraps at all.
	 *
	 * Measured on `main` at 412px: line 1 spans 10..240 (230px), line 2 is
	 * `Reset` alone at 10..73 (63px) — a ratio of 0.27.
	 */
	test("F-17: the popover's action row never leaves a short stub line", async ({
		page,
		request,
	}) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		const { curriculumId } = await seed(request);
		await openRead(page, curriculumId);

		// Words carrying the full action set are the ones that can wrap. A word
		// with two actions passes trivially and would make this vacuous.
		const targets = await page.evaluate(() =>
			[...document.querySelectorAll(".tt-wrap")]
				.map((el, i) => ({ i, btns: el.querySelectorAll(".tt-btn").length }))
				.filter((t) => t.btns >= 4)
				.slice(0, 4)
				.map((t) => t.i),
		);
		expect(
			targets.length,
			"no word rendered 4+ actions — the seed is not in `learning` state and this test would be vacuous",
		).toBeGreaterThan(0);

		const failures: string[] = [];

		for (const index of targets) {
			if (!(await longPress(page, index))) continue;

			const r = await page.evaluate(() => {
				const tip = [...document.querySelectorAll(".tt")].find(
					(el) => getComputedStyle(el).display !== "none",
				);
				if (!tip) return { shown: 0, lines: [] as Array<{ n: number; w: number }> };
				// Group the buttons into visual lines by their top edge. Rounded to
				// 2px: wrapped lines differ by a full row height, so no real grouping
				// is at risk, while sub-pixel layout noise cannot split one line in two.
				const byTop = new Map<number, DOMRect[]>();
				for (const b of tip.querySelectorAll(".tt-btn")) {
					const rect = b.getBoundingClientRect();
					const key = Math.round(rect.top / 2) * 2;
					(byTop.get(key) ?? byTop.set(key, []).get(key)!).push(rect);
				}
				const lines = [...byTop.entries()]
					.sort((a, b) => a[0] - b[0])
					.map(([, rects]) => ({
						n: rects.length,
						w: Math.round(
							Math.max(...rects.map((x) => x.right)) - Math.min(...rects.map((x) => x.left)),
						),
					}));
				return { shown: 1, lines };
			});

			// A `display: none` popover has no boxes and clears every check vacuously.
			expect(r.shown, `popover never opened for word ${index}`).toBe(1);
			expect(r.lines.length, `no action buttons measured for word ${index}`).toBeGreaterThan(0);

			const widest = Math.max(...r.lines.map((l) => l.w));
			for (const line of r.lines) {
				if (line.w < widest * 0.9)
					failures.push(
						`word ${index}: action row is ragged — a line of ${line.n} button(s) spans ${line.w}px against the widest line's ${widest}px (${Math.round(
							(line.w / widest) * 100,
						)}%); lines were ${JSON.stringify(r.lines)}`,
					);
			}

			await closePopover(page);
		}

		expect(failures, failures.join("\n")).toEqual([]);
	});

	/**
	 * F-21 — an open popover must render ABOVE the sticky audio player.
	 *
	 * ⚠️ This asserts OCCLUSION, not `z-index: 30`. Pinning the number passes on a
	 * page where an ancestor stacking context still buries the popover — the fix
	 * would be present, correct, and inert, which is the exact shape of F-8. So
	 * the probe is `elementFromPoint` at a spot where the two rects genuinely
	 * overlap, and the overlap itself is asserted first: without it the hit test
	 * lands on empty page and passes for the wrong reason.
	 */
	test("F-21: an open popover renders above the sticky audio player", async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), "Backend not available");
		const { curriculumId } = await seed(request);
		await openRead(page, curriculumId);

		// Scroll until a word sits in the band just below the player. The popover
		// opens ABOVE its word (`.tt` is `bottom: 100%`), so only a word in that
		// band produces a popover whose rect crosses the player's.
		const index = await page.evaluate(() => {
			const wraps = [...document.querySelectorAll(".tt-wrap")];
			for (let step = 0; step < 60; step++) {
				const player = document.querySelector(".player-card")!.getBoundingClientRect();
				for (let i = 0; i < wraps.length; i++) {
					const r = wraps[i].getBoundingClientRect();
					if (
						wraps[i].querySelector(".tt") &&
						r.width > 0 &&
						r.top > player.bottom + 20 &&
						r.top < player.bottom + 120
					)
						return i;
				}
				window.scrollBy(0, 40);
			}
			return -1;
		});
		expect(
			index,
			"no word could be scrolled into the band below the player — the overlap this test needs never formed",
		).toBeGreaterThanOrEqual(0);

		expect(await longPress(page, index)).toBe(true);

		const r = await page.evaluate(() => {
			const tip = [...document.querySelectorAll(".tt")].find(
				(el) => getComputedStyle(el).display !== "none",
			);
			const player = document.querySelector(".player-card");
			if (!tip || !player) return { shown: 0 };
			const tr = tip.getBoundingClientRect();
			const pr = player.getBoundingClientRect();
			const top = Math.max(tr.top, pr.top);
			const bottom = Math.min(tr.bottom, pr.bottom);
			const left = Math.max(tr.left, pr.left, 0);
			const right = Math.min(tr.right, pr.right, window.innerWidth);
			const x = Math.round((left + right) / 2);
			const y = Math.round((top + bottom) / 2);
			const hit = bottom > top && right > left ? document.elementFromPoint(x, y) : null;
			return {
				shown: 1,
				overlapH: Math.round(bottom - top),
				overlapW: Math.round(right - left),
				probe: { x, y },
				hitInTip: hit ? tip.contains(hit) : false,
				hit: hit ? `${hit.tagName.toLowerCase()}.${String(hit.className).slice(0, 60)}` : null,
			};
		});

		expect(r.shown, "popover never opened").toBe(1);
		expect(r.overlapH ?? 0, "popover and player do not overlap — the hit test would be vacuous").
			toBeGreaterThan(8);
		expect(r.overlapW ?? 0, "popover and player do not overlap — the hit test would be vacuous").
			toBeGreaterThan(8);
		expect(
			r.hitInTip,
			`the player occludes the popover: elementFromPoint(${r.probe?.x}, ${r.probe?.y}) inside the overlap resolved to ${r.hit}`,
		).toBe(true);
	});
});
