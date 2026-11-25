import { devices } from '@playwright/test';
import { test, expect } from './fixtures';
import { BACKEND, CANNED_STORY, backendAvailable, resetSRSItems, readDialogue } from "./helpers";
import { openLessonsOn } from "./helpers";

// Core journey (test-tiers.md door 1), oracle row E2 of
// design-sentence-mining-2026-11.md (tunatale-h0q3.69): Read › tap a word › More… ›
// Cloze… › Save makes that word a NEW blank card of its line. The real backend maps the
// transcript line to its lesson line and applies the edit through Grade's sentence
// sequence; the real page draws the editor under the line. Real touch, as the owner
// reads on Android (reader-phrases.spec.ts says why a tap must be a real tap).
//
// The list's own gloss of the tapped word ("please") is the card's meaning, so nothing
// asks the model (a cassette miss would fail the run).

const STORY = {
	...CANNED_STORY,
	cards: {
		set: 'e2e-read-cloze',
		items: [
			{
				kind: 'sentence',
				line: 2,
				speaker: 'Per',
				role: 'male-1',
				text: 'Prosim kavo.',
				spoken: 'Prosim kavo.',
				english: 'A coffee please.',
				glosses: [{ word: 'Prosim', gloss: 'please' }],
				cue: null,
				whole: true,
				blanks: [{ form: 'kavo', gloss: 'coffee' }],
			},
		],
	},
};

test.beforeEach(async ({ request }) => {
	if (await backendAvailable(request)) await resetSRSItems(request);
});
test.afterEach(async ({ request }) => {
	if (await backendAvailable(request)) await resetSRSItems(request);
});

test.describe('Read › Cloze…, real touch', () => {
	const PIXEL = devices['Pixel 7'];
	test.use({
		viewport: PIXEL.viewport,
		userAgent: PIXEL.userAgent,
		deviceScaleFactor: PIXEL.deviceScaleFactor,
		isMobile: PIXEL.isMobile,
		hasTouch: PIXEL.hasTouch,
	});

	test('a word clozed from Read becomes a new card of its line', async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), 'Backend not available');

		const topic = 'e2e read cloze';
		const curr = await request.post(`${BACKEND}/api/curriculum/import`, {
			data: {
				topic,
				language_code: 'sl',
				cefr_level: 'A2',
				days: [
					{
						day: 1,
						title: 'Day 1',
						focus: topic,
						collocations: ['dober dan'],
						learning_objective: 'order',
						story_guidance: 'order',
					},
				],
			},
		});
		expect(curr.ok(), await curr.text()).toBe(true);
		const curriculumId = (await curr.json()).id;
		const imp = await request.post(`${BACKEND}/api/story/import`, {
			data: { curriculum_id: curriculumId, day: 1, story: STORY },
		});
		expect(imp.ok(), await imp.text()).toBe(true);
		const { id: lessonId } = await imp.json();
		const linesUrl = `${BACKEND}/api/srs/content/${lessonId}/sentence-lines`;

		await page.goto(`/c/${curriculumId}`);
		await page.getByTestId('day-1').click();
		await expect(page.getByRole('button', { name: 'Render Audio' })).toBeVisible({ timeout: 15000 });
		await readDialogue(page);

		// 1. Open "Prosim"'s popover; Cloze… is a utility, behind More… when the popover has a primary action.
		const prosim = page
			.locator('.dialogue-words .tt-wrap')
			.filter({ has: page.locator('.word:text-is("Prosim")') });
		await expect(prosim).toHaveCount(1, { timeout: 15000 });
		await prosim.locator('.word').tap();
		const tip = prosim.locator('> .tt');
		await expect(tip).toBeVisible();
		const more = tip.getByRole('button', { name: 'More…' });
		if (await more.isVisible()) await more.tap();
		await tip.getByRole('button', { name: 'Cloze…' }).tap();

		// 2. The editor opens under the line with the tapped word on, its gloss beside it.
		const editor = page.locator('.read-cloze');
		await expect(editor).toBeVisible();
		const line = editor.locator('[data-line-key="sent:e2e-read-cloze:2"]');
		await expect(line.getByText('Prosim+ please')).toBeVisible();

		// 3. Save. The editor closes and the line says what was made.
		await editor.getByRole('button', { name: 'Save' }).tap();
		await expect(editor).toBeHidden();
		await expect(page.locator('.cloze-notice')).toHaveText('Saved: 1 new card');

		// The card exists: mined, in the deck. The lesson was never marked studied by Read.
		const after = await (await request.get(linesUrl)).json();
		const listed = after.lines.find((l: { line_key: string }) => l.line_key === 'sent:e2e-read-cloze:2');
		expect([listed.tokens[0].state, listed.tokens[0].origin, listed.tokens[0].in_deck]).toEqual([
			'on',
			'mine',
			true,
		]);
		const plan = await (await request.get(`${BACKEND}/api/srs/content/${lessonId}/grade-plan`)).json();
		expect(plan.studied).toBe(false);
	});
});
