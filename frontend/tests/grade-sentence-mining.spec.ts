import { test, expect } from './fixtures';
import { BACKEND, CANNED_STORY, backendAvailable, resetSRSItems } from './helpers';

// Core journey (test-tiers.md door 1), oracle row E1 of
// design-sentence-mining-2026-11.md: Grade › New cards › tap a word of a lesson
// line › Save makes that blank a NEW card, and the line says it has one card
// more. The real backend computes the line's tokens and applies the edit; the
// real page draws them (AGENTS.md § "Work the learner sees is done at the screen").
//
// The line's own gloss of the tapped word ("please") is the card's meaning, so
// nothing asks the model; the first Save is sent through the API with every word
// suggestion declined for the same reason (a cassette miss fails the e2e run).

const STORY = {
	...CANNED_STORY,
	cards: {
		set: 'e2e-mine',
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

test('grade: a word tapped in New cards › Sentences becomes a new card of its line', async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	const topic = 'e2e grade sentence mining';
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
	const imp = await request.post(`${BACKEND}/api/story/import`, {
		data: { curriculum_id: (await curr.json()).id, day: 1, story: STORY },
	});
	expect(imp.ok(), await imp.text()).toBe(true);
	const { id: lessonId } = await imp.json();
	const planUrl = `${BACKEND}/api/srs/content/${lessonId}/grade-plan`;

	// The study mark, with nothing that would ask the model for a gloss.
	const before = await (await request.get(planUrl)).json();
	const declined = [
		...before.suggestions.words.map((w: { key: string }) => ({ kind: 'word', key: w.key })),
		...before.suggestions.contrast.map((c: { form: string }) => ({ kind: 'contrast', key: c.form })),
	];
	const first = await request.post(`${BACKEND}/api/srs/content/${lessonId}/grade`, { data: { declined } });
	expect(first.ok(), await first.text()).toBe(true);
	const studied = await (await request.get(planUrl)).json();

	await page.goto(`/grade/${lessonId}`);
	await page.getByRole('tab', { name: /New cards/ }).click();
	const line = page.locator('[data-line-key="sent:e2e-mine:2"]');
	await expect(line.getByText('2 cards')).toBeVisible({ timeout: 10000 });

	await line.getByRole('button', { name: 'Prosim', exact: true }).click();
	await expect(line.getByText('Prosim+ please')).toBeVisible();
	await expect(page.getByRole('tab', { name: 'New cards · 1' })).toBeVisible();
	await page.getByRole('button', { name: /^Save/ }).click();

	await expect(page.getByRole('status')).toContainText('1 new card');
	await expect(line.getByText('3 cards')).toBeVisible();

	// The card exists, mined (mine) and NEW: one direction more is waiting to be introduced.
	const after = await (await request.get(planUrl)).json();
	const prosim = after.suggestions.sentences[0].tokens[0];
	expect([prosim.state, prosim.origin]).toEqual(['on', 'mine']);
	expect(after.waiting_new).toBe(studied.waiting_new + 1);
});
