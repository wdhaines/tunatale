import { test, expect } from "./fixtures";
import { backendAvailable, BACKEND } from "./helpers";

/**
 * Twin-rail density (bd tunatale-yh47, stage 3) in a REAL browser: the rails
 * are no longer elements — each tracked word PAINTS its understand/produce
 * rails as background-image layers in its own 7px padding-bottom, so an inline
 * span's padding cannot change the line box and wrapped rows stay exactly one
 * line-height apart. None of that is measurable in the unit suite: jsdom does
 * no layout and no paint, so the DOM is identical whether rows are 26px or 46px
 * apart.
 *
 * Every assertion is engine-computed geometry: row pitch and rail clearance are
 * read off layout rects, "painted" is read off the resolved padding, and the
 * no-card-vs-not-started distinction is read off the resolved background-image.
 * Same seeded/imported setup rationale as transcript-layout.spec.ts: the shared
 * cassettes have a fixed number of recorded plays, so the story is imported,
 * not generated.
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
	// untracked words carry null bands and paint NO rails, and a rail-less page
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

test("rails are painted: dense wrapped rows, clear of the next row, and non-vacuous", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId, lessonId } = await seed(request);

	await page.addInitScript(() => localStorage.setItem("lessonMode", "read"));
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

	const m = await page.evaluate(() => {
		// (3) Non-vacuous: painted words resolve a full 7px rail padding — the
		// padding is the painted box. Without it a seed that never tracked (or
		// bands that never arrived) would satisfy the geometry assertions by
		// painting nothing at all.
		const paintedWords = [...document.querySelectorAll(".transcript-wrapper .word")].filter(
			(el) => getComputedStyle(el).paddingBottom === "7px",
		);

		// Group a block's word LINE-FRAGMENTS into rows by their top: a phrase's
		// words may sit 1-3px off their row, so tops within 6px are one row. An
		// inline word at a wrap boundary splits into MULTIPLE line fragments
		// (Chromium leaves a 1px sliver on the row it wraps from), so geometry
		// is measured per fragment — a union rect would span two rows and read
		// as a bottom far past the next row's top.
		const groupRows = (
			rects: Array<{ painted: boolean; top: number; bottom: number; text: string }>,
		): Array<Array<{ painted: boolean; top: number; bottom: number; text: string }>> => {
			const sorted = [...rects].sort((a, b) => a.top - b.top);
			const rows: Array<Array<{ painted: boolean; top: number; bottom: number; text: string }>> = [];
			for (const r of sorted) {
				const last = rows[rows.length - 1];
				if (last && r.top - last[0].top <= 6) last.push(r);
				else rows.push([r]);
			}
			return rows;
		};

		const blocks = [...document.querySelectorAll(".transcript-wrapper .dialogue-words")];
		const rowPitches: number[] = [];
		const clearances: Array<{ text: string; bottom: number; nextRowTop: number }> = [];
		let wrappedLines = 0;
		let paintedFragments = 0;
		for (const block of blocks) {
			const fragments: Array<{ painted: boolean; top: number; bottom: number; text: string }> = [];
			for (const el of block.querySelectorAll(".word")) {
				const painted = getComputedStyle(el).paddingBottom === "7px";
				for (const f of el.getClientRects()) {
					fragments.push({ painted, top: f.top, bottom: f.bottom, text: (el as HTMLElement).innerText });
					if (painted) paintedFragments += 1;
				}
			}
			if (fragments.length < 2) continue;
			const rows = groupRows(fragments);
			if (rows.length < 2) continue;
			wrappedLines += 1;
			// (1) Density: consecutive wrapped-row tops are at most 27px apart.
			for (let i = 1; i < rows.length; i++) rowPitches.push(rows[i][0].top - rows[i - 1][0].top);
			// (2) No overlap: a painted fragment's box bottom (ends in the 7px
			// rail padding) clears every word-box top in the NEXT row.
			for (let i = 0; i < rows.length - 1; i++) {
				const nextRowTop = Math.min(...rows[i + 1].map((r) => r.top));
				for (const r of rows[i]) {
					if (!r.painted) continue;
					clearances.push({ text: r.text, bottom: r.bottom, nextRowTop });
				}
			}
		}

		return { paintedCount: paintedWords.length, rowPitches, clearances, wrappedLines, paintedFragments };
	});

	expect(m.paintedCount, "fewer than 6 words painted rails").toBeGreaterThan(5);
	expect(m.wrappedLines, "no dialogue line wrapped — nothing measured").toBeGreaterThan(0);
	expect(m.rowPitches.length).toBeGreaterThan(0);
	const maxPitch = Math.max(...m.rowPitches);
	expect(maxPitch, `rows ${maxPitch}px apart — the rails are adding height`).toBeLessThanOrEqual(
		27,
	);
	const worst = m.clearances.sort((a, b) => a.nextRowTop - a.bottom - (b.nextRowTop - b.bottom))[0];
	console.log(
		`[transcript-rails] painted words: ${m.paintedCount} (${m.paintedFragments} fragments); ` +
			`row pitch max ${maxPitch.toFixed(2)}px within ${m.rowPitches.length} wrapped-line gaps ` +
			`across ${m.wrappedLines} wrapped lines; worst clearance word "${worst?.text}" ` +
			`(next row ${worst?.nextRowTop.toFixed(2)} vs bottom ${worst?.bottom.toFixed(2)})`,
	);
	// Clearance = next row's first word-box top MINUS this fragment's box
	// bottom (which ends in the 7px rail padding). At or above → clearance >= 0.
	const minGap = Math.round(Math.min(...m.clearances.map((c) => c.nextRowTop - c.bottom)) * 100) / 100;
	expect(minGap, `a rail touches the next row's text (gap ${minGap}px, worst "${worst?.text}")`).toBeGreaterThanOrEqual(
		-0.5,
	);
});

test("a no-card rail paints differently from an empty not-started track", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId, lessonId } = await seed(request);

	await page.addInitScript(() => localStorage.setItem("lessonMode", "read"));
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

	// Rails are painted background layers now, so the distinction between "no
	// card" (dashed track) and "not started" (solid empty track) lives in the
	// resolved background-image, not in a class. Clone a real painted word and
	// flip its produce track between the two values, then compare what the
	// ENGINE resolves for each. A class or attribute check cannot catch this:
	// both states carry the identical `--rail-*` mark-up, differing only in the
	// track gradient.
	const styles = await page.evaluate(() => {
		const word = document.querySelector(".transcript-wrapper .word.paint-rails");
		if (!word) return null;
		const solidTrack = "linear-gradient(var(--band-track, #e4e9e6), var(--band-track, #e4e9e6))";
		const dashedTrack =
			"repeating-linear-gradient(90deg, var(--color-muted, #6b7280) 0 3px, transparent 3px 6px)";
		const mk = (produceTrack: string) => {
			const c = word.cloneNode(false) as HTMLElement;
			c.style.setProperty("--rail-u-fill", "transparent");
			c.style.setProperty("--rail-u-pct", "0%");
			c.style.setProperty("--rail-u-track", solidTrack);
			c.style.setProperty("--rail-p-fill", "transparent");
			c.style.setProperty("--rail-p-pct", "0%");
			c.style.setProperty("--rail-p-track", produceTrack);
			return c;
		};
		const noneClone = mk(dashedTrack);
		const newClone = mk(solidTrack);
		word.parentElement!.append(noneClone, newClone);
		const img = (el: HTMLElement) => getComputedStyle(el).backgroundImage;
		const out = { none: img(noneClone), fresh: img(newClone) };
		noneClone.remove();
		newClone.remove();
		return out;
	});
	expect(styles, "no painted word found to clone").not.toBeNull();
	expect(styles!.none, '"no card" rail did not paint dashes').toContain("repeating-linear-gradient");
	expect(styles!.fresh, '"not started" rail painted dashes like a no-card rail').not.toContain(
		"repeating-linear-gradient",
	);
});

test("an untracked word paints two dashed rails in the app's link blue", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId, lessonId } = await seed(request);

	await page.addInitScript(() => localStorage.setItem("lessonMode", "read"));
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

	const r = await page.evaluate(() => {
		// A standalone untracked word. One inside a multi-word phrase span is
		// deliberately rail-less (Transcript passes hideRails; the phrase's own
		// rails cover it) yet still carries .word-unknown — and whether the FIRST
		// untracked word sits in a span depends on which phrase cards other specs
		// left in the shared e2e DB. Taking the first match went red once in a
		// full gate (0 dashed layers) and green in isolation.
		const w = [...document.querySelectorAll(".transcript-wrapper .word.word-unknown")].find(
			(el) => !el.closest(".collocation-span"),
		);
		if (!w) return null;
		const s = getComputedStyle(w);
		// The link blue resolved the way the page resolves it: a probe coloured
		// with the same token, so this does not hard-code a hex.
		const probe = document.createElement("span");
		probe.style.color = "var(--color-primary)";
		document.body.append(probe);
		const primary = getComputedStyle(probe).color;
		probe.remove();
		return {
			dashedLayers: (s.backgroundImage.match(/repeating-linear-gradient/g) ?? []).length,
			paddingBottom: s.paddingBottom,
			textDecoration: s.textDecorationLine,
			color: s.color,
			primary,
		};
	});
	expect(r, "the fixture has no untracked word to measure").not.toBeNull();
	expect(r!.dashedLayers).toBe(2);
	expect(r!.paddingBottom).toBe("7px");
	expect(r!.textDecoration).toBe("none");
	expect(r!.color).toBe(r!.primary);
});

test("in dark mode the empty rail track is visible against the card", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	const { curriculumId, lessonId } = await seed(request);

	await page.addInitScript(() => {
		localStorage.setItem("lessonMode", "read");
		localStorage.setItem("theme", "dark");
	});
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	await expect(page.locator(".tt-wrap").first()).toBeVisible({ timeout: 15000 });

	// The first dark track was #2b302e on the #182f3c card: ~1.1:1, invisible.
	// Composite the track the engine resolves over the card it sits on and
	// require a contrast a reader can actually see.
	const ratio = await page.evaluate(() => {
		const parse = (c: string) => {
			const m = c.match(/[\d.]+/g)!.map(Number);
			return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 };
		};
		const resolve = (prop: string, value: string) => {
			const el = document.createElement("span");
			el.style.setProperty(prop, value);
			document.querySelector(".transcript-wrapper")!.append(el);
			const out = getComputedStyle(el).getPropertyValue(prop);
			el.remove();
			return out;
		};
		const track = parse(resolve("background-color", "var(--band-track)"));
		const card = parse(resolve("background-color", "var(--color-surface)"));
		const mix = (k: "r" | "g" | "b") => track[k] * track.a + card[k] * (1 - track.a);
		const lum = (c: { r: number; g: number; b: number }) => {
			const f = (v: number) => {
				v /= 255;
				return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
			};
			return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
		};
		const a = lum({ r: mix("r"), g: mix("g"), b: mix("b") });
		const b = lum(card);
		return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
	});
	expect(ratio, `dark track contrast ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(1.4);
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