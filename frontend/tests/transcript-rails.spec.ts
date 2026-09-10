import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Twin-rail geometry (bd tunatale-yh47) in a REAL browser: the per-word
 * understand/produce rails render under their word and under the line, never
 * crossing into the next wrapped row or the next dialogue line's text.
 *
 * jsdom performs no layout, so none of this is measurable in the unit suite —
 * the DOM is identical whether a rail touches the next line's text or not.
 * Same rationale as transcript-layout.spec.ts, which this models on (seeding,
 * navigation) and transcript-overflow.spec.ts (whose listen-tracking is what
 * makes words carry non-null bands, so rails actually render).
 *
 * Model setup is imported, not generated, for the reason
 * transcript-layout.spec.ts gives: the shared cassettes have a fixed number of
 * recorded plays and another consumer would exhaust them.
 */

const TOPIC = "transcript-rails-e2e";

const STORY = {
	title: "Transcript rails",
	key_phrases: [{ phrase: "dober dan", translation: "good day" }],
	scenes: [
		{
			label: "At the Café",
			lines: [
				{
					speaker: "female-1",
					text: "Dober dan, kako se imate danes na ta lep sončen dan v mestu pravzaprav.",
					translation: "Good day, how are you today on this lovely sunny day in the city, actually.",
				},
				{
					speaker: "male-1",
					text: "Hvala lepa, zelo dobro, enainštirideset let imam in veliko kavo pijem vsako jutro.",
					translation: "Thank you very much, very well, I am forty-one years old and I drink a lot of coffee every morning.",
				},
				{
					speaker: "female-1",
					text: "Nasvidenje in lep dan še naprej, upam, da se kmalu spet vidiva v kavarni.",
					translation: "Goodbye and have a nice day ahead, I hope we see each other again soon at the café.",
				},
			],
		},
	],
	dialogue_glosses: [
		{ word: "kako", translation: "how" },
		{ word: "imate", translation: "you have" },
		{ word: "danes", translation: "today" },
		{ word: "lep", translation: "lovely" },
		{ word: "sončen", translation: "sunny" },
		{ word: "mestu", translation: "city" },
		{ word: "pravzaprav", translation: "actually" },
		{ word: "zelo", translation: "very" },
		{ word: "dobro", translation: "well" },
		{ word: "enainštirideset", translation: "forty-one" },
		{ word: "veliko", translation: "a lot of" },
		{ word: "pijem", translation: "I drink" },
		{ word: "vsako", translation: "every" },
		{ word: "jutro", translation: "morning" },
		{ word: "upam", translation: "I hope" },
		{ word: "kmalu", translation: "soon" },
		{ word: "vidiva", translation: "we see each other" },
		{ word: "kavarni", translation: "at the café" },
		// Full-glossed story: /api/srs/listen tracks every word, and a tracked
		// lemma without a gloss triggers an LLM lookup that misses the shared
		// cassette (7 hits, reported as "e2e LLM cassette misses"). Every other
		// listen-tracking spec glosses its full vocabulary for the same reason.
		{ word: "dober", translation: "good" },
		{ word: "dan", translation: "day" },
		{ word: "se", translation: "oneself" },
		{ word: "na", translation: "on" },
		{ word: "ta", translation: "this" },
		{ word: "v", translation: "in" },
		{ word: "hvala", translation: "thanks" },
		{ word: "lepa", translation: "beautiful" },
		{ word: "let", translation: "years" },
		{ word: "imam", translation: "I have" },
		{ word: "in", translation: "and" },
		{ word: "kavo", translation: "coffee" },
		{ word: "nasvidenje", translation: "goodbye" },
		{ word: "še", translation: "still" },
		{ word: "naprej", translation: "ahead" },
		{ word: "da", translation: "that" },
		{ word: "spet", translation: "again" },
	],
	morphology_focus: [],
};

let seeded: { curriculumId: string; lessonId: string } | null = null;

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

	// Track every word (empty ratings) the way transcript-overflow.spec.ts does:
	// untracked words carry null bands and render NO rails, and a rail-less page
	// would satisfy every geometry assertion vacuously.
	const listenRes = await request.post(`${BACKEND}/api/srs/listen`, {
		data: { content_id: lesson.id ?? lesson.lesson_id, word_ratings: {}, kp_ratings: {} },
	});
	if (!listenRes.ok())
		throw new Error(`listen failed: ${listenRes.status()} ${await listenRes.text()}`);

	seeded = { curriculumId: curriculum.id, lessonId: lesson.id ?? lesson.lesson_id };
	return seeded;
}

test.describe.configure({ mode: "serial" });

test("rails sit under their word and clear the next row and next dialogue line", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId, lessonId } = await seed(request);

	await page.addInitScript(() => localStorage.setItem("lessonMode", "read"));
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

	// Non-vacuous guard: tracked words render rails. Without this a seed that
	// stopped tracking (or bands that never arrived) would measure nothing.
	const railCount = await page.locator(".transcript-wrapper .word-rails").count();
	expect(railCount).toBeGreaterThan(5);
	const wordCount = await page.locator(".transcript-wrapper .word").count();
	expect(wordCount).toBeGreaterThan(5);

	const m = await page.evaluate(() => {
		// Per word-wrapper: pair each rail with ITS OWN word box. Wrappers can
		// hold a word without rails (untracked, or inside a collocation phrase
		// where hideRails suppresses them), so parallel arrays would misalign —
		// pairing on the same wrapper keeps every comparison honest.
		const wrappers = [...document.querySelectorAll(".transcript-wrapper .word-wrapper")];
		const wordBoxes: Array<{ left: number; right: number; top: number; bottom: number }> = [];
		const railBoxes: Array<{ rail: { left: number; right: number; top: number; bottom: number }; word: { left: number; right: number; top: number; bottom: number } }> = [];
		for (const wrap of wrappers) {
			const w = wrap.querySelector(":scope > .word");
			const r = wrap.querySelector(":scope > .word-rails");
			if (!w) continue;
			const wr = w.getBoundingClientRect();
			const word = { left: wr.left, right: wr.right, top: wr.top, bottom: wr.bottom };
			wordBoxes.push(word);
			if (r) {
				const rr = r.getBoundingClientRect();
				railBoxes.push({ rail: { left: rr.left, right: rr.right, top: rr.top, bottom: rr.bottom }, word });
			}
		}

		// Per dialogue line: every rail bottom (any kind) and word top (any kind).
		const lines = [...document.querySelectorAll(".transcript-wrapper .dialogue-line")];
		const lineRailBottoms: number[][] = [];
		const lineWordTops: number[][] = [];
		for (const line of lines) {
			lineRailBottoms.push(
				[...line.querySelectorAll(".word-rails")].map((el) => el.getBoundingClientRect().bottom),
			);
			lineWordTops.push([...line.querySelectorAll(".word")].map((el) => el.getBoundingClientRect().top));
		}

		// Within a single dialogue-words block (a line's wrapped rows): a rail
		// belongs to its own row's line box, so the FIRST word strictly BELOW the
		// rail must clear the rail's bottom — this is the moment that decided the
		// `.dialogue-words` line-height bump.
		const blocks = [...document.querySelectorAll(".transcript-wrapper .dialogue-words")];
		const rowGaps: number[] = [];
		for (const block of blocks) {
			const rails = [...block.querySelectorAll(".word-rails")].map(
				(el) => el.getBoundingClientRect(),
			);
			const words = [...block.querySelectorAll(".word")].map(
				(el) => el.getBoundingClientRect(),
			);
			for (const rail of rails) {
				const below = words
					.filter((w) => w.top > rail.top + 0.5)
					.map((w) => w.top - rail.bottom);
				if (below.length > 0) rowGaps.push(Math.min(...below));
			}
		}

		return {
			wordBoxes,
			railBoxes,
			lineRailBottoms,
			lineWordTops,
			rowGaps,
		};
	});

	// (1) Each word's rails lie within that word's own box on the horizontal
	// axis (±1px). The rail is `width: 100%` of the wrapper, whose width IS the
	// word's padded box — so any left/right excursion here is a layout bug, not a
	// rounding artifact. Pairing on the same wrapper (above) keeps this honest:
	// word and rail come from the same HTML element.
	const contain = m.railBoxes.filter(
		({ rail, word }) => rail.left < word.left - 1 || rail.right > word.right + 1,
	);
	expect(contain, "rail escaped its word's box").toEqual([]);

	// Rails sit BELOW the word (the intended rail slot), not beside/over it.
	const below = m.railBoxes.filter(({ rail, word }) => rail.top < word.bottom - 1);
	expect(below, "rail is not below its word").toEqual([]);

	// (2) Two consecutive `.dialogue-line`s: every rail in line N clears every
	// word top in line N+1. `.dialogue-line` blocks are separated by 0.3rem
	// padding + border, so this is structural — but assert it, not assume it.
	const crossGaps: number[] = [];
	const crossFailures: string[] = [];
	for (let n = 0; n < m.lineRailBottoms.length - 1; n++) {
		const railMax = Math.max(...m.lineRailBottoms[n], -Infinity);
		const wordMin = Math.min(...m.lineWordTops[n + 1], Infinity);
		if (m.lineRailBottoms[n].length === 0) continue;
		if (m.lineWordTops[n + 1].length === 0) continue;
		const gap = Math.round(wordMin * 100) / 100 - Math.round(railMax * 100) / 100;
		crossGaps.push(gap);
		if (gap <= 0) crossFailures.push(`line ${n}: rail bottom ${railMax} vs next word top ${wordMin}`);
	}
	expect(crossFailures, crossFailures.join("\n")).toEqual([]);

	// (3) Within a wrapped row: rails never touch the next row's words.
	const rowFailures = m.rowGaps.filter((g) => g <= 0);
	expect(rowFailures, "at least one rail touches the words in the next wrapped row").toEqual([]);

	// Measured gaps for the report: the within-line wrapped-row gap is the figure
	// that decides `.dialogue-words` line-height; the cross-line gap is structural.
	const crossGap = Math.min(...crossGaps);
	const rowGap = Math.min(...m.rowGaps);
	console.log(
		`[transcript-rails] measured gaps — within-row (rail→next word): ${rowGap.toFixed(2)}px; ` +
			`across dialogue lines (rail→next line word): ${crossGap.toFixed(2)}px; ` +
			`rails rendered: ${m.railBoxes.length}/${m.wordBoxes.length} words`,
	);
});

test("a no-card rail paints differently from an empty not-started track", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId, lessonId } = await seed(request);

	await page.addInitScript(() => localStorage.setItem("lessonMode", "read"));
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

	// Clone a real rail (so it carries the component's scoped class) and flip it
	// to the dashed variant, then compare what the ENGINE paints for each. A
	// class check cannot catch this: the first cut drew track-coloured dashes
	// over a track-coloured background, so "no card" rendered as a solid empty
	// track — identical to "not started".
	const styles = await page.evaluate(() => {
		const rail = document.querySelector(".transcript-wrapper .word-rails .rail");
		if (!rail) return null;
		const plain = rail.cloneNode(false) as HTMLElement;
		const dashed = rail.cloneNode(false) as HTMLElement;
		dashed.classList.add("rail-dashed");
		rail.parentElement!.append(plain, dashed);
		const cs = (el: Element) => {
			const s = getComputedStyle(el);
			return { bg: s.backgroundColor, img: s.backgroundImage };
		};
		const out = { plain: cs(plain), dashed: cs(dashed) };
		plain.remove();
		dashed.remove();
		return out;
	});
	expect(styles, "no rail found to clone").not.toBeNull();
	expect(styles!.plain.img).toBe("none");
	expect(styles!.plain.bg).not.toBe("rgba(0, 0, 0, 0)");
	expect(styles!.dashed.bg).toBe("rgba(0, 0, 0, 0)");
	expect(styles!.dashed.img).toContain("repeating-linear-gradient");
});

test("at 320px the transcript never overflows horizontally", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId, lessonId } = await seed(request);

	await page.addInitScript(() => localStorage.setItem("lessonMode", "read"));
	await page.setViewportSize({ width: 320, height: 700 });
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

	const r = await page.evaluate(() => {
		const doc = document.documentElement;
		return { scrollW: doc.scrollWidth, clientW: doc.clientWidth };
	});
	expect(r.scrollW - r.clientW, `document scrolls to ${r.scrollW} at a ${r.clientW}px viewport`).toBeLessThanOrEqual(0);
});