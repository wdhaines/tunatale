import { test, expect } from "@playwright/test";
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

async function openPreview(page: import("@playwright/test").Page, curriculumId: string) {
	await page.goto(`/c/${curriculumId}`);
	await page.getByRole("button", { name: "Day 1" }).click();
	await expect(page.getByRole("button", { name: "Render Audio" })).toBeVisible({ timeout: 15000 });
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

test("listen preview: glosses are blurred until tapped, per row", async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");

	await page.setViewportSize(PHONE);
	const modal = await openPreview(page, await curriculumId(request));

	const glosses = modal.locator(".gloss.blurred");
	const blurredBefore = await glosses.count();
	// A hard assertion, not a test.skip: a silently skipped layout test is
	// indistinguishable from a passing one, and the seed above guarantees
	// several glossed candidates.
	expect(blurredBefore).toBeGreaterThanOrEqual(2);

	// A blurred gloss really is blurred — the class carries a filter, not just a name.
	expect(await glosses.first().evaluate((el) => getComputedStyle(el).filter)).toContain("blur");

	await glosses.first().click();

	await expect(modal.locator(".gloss.blurred")).toHaveCount(blurredBefore - 1);
});
