import { test, expect } from './fixtures';
import { BACKEND, backendAvailable, resetSRSItems } from './helpers';

// Core journey (test-tiers.md door 1): a lesson's drill card, minted by the
// real listen route and served by the real review queue, reviewed on the real
// Review page. PR #421 shipped the drill payload with no component reading it,
// so this card showed a bare "[...]" while every backend test was green
// (fixed in #427). A new card kind adds its card to this journey (AGENTS.md
// § "Work the learner sees is done at the screen").
//
// Invented Norwegian drill (the course text never enters the public repo); the
// strings are opaque to the drill card, so the Slovene curriculum the e2e
// backend serves carries it unchanged.

const SUBSTITUTION = {
	kind: 'substitution',
	model: 'Han er her.',
	speaker: 'male-1',
	steps: [
		{ cue: 'hun', answer: 'Hun er her.' },
		{ cue: 'de', answer: 'De er her.' },
		{ cue: 'der', answer: 'De er der.' },
		{ cue: 'vi', answer: 'Vi er der.', english: 'We are there.' },
	],
};

test.beforeEach(async ({ request }) => {
	if (await backendAvailable(request)) await resetSRSItems(request);
});

test('review: a drill card shows its step and cue, then its answer', async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	const topic = 'e2e drill card review';
	const curr = await request.post(`${BACKEND}/api/curriculum/import`, {
		data: {
			topic,
			language_code: 'sl',
			cefr_level: 'A1',
			days: [
				{
					day: 1,
					title: 'Drills',
					focus: topic,
					collocations: ['Han er her.'],
					learning_objective: 'drill',
					story_guidance: 'drill',
				},
			],
		},
	});
	expect(curr.ok(), await curr.text()).toBe(true);
	const { id: curriculumId } = await curr.json();

	const story = {
		title: 'Drills',
		drills: [SUBSTITUTION],
		cards: { set: 'e2e-drill', items: [{ kind: 'drill', drill: 1, role: 'male-1' }] },
	};
	const imp = await request.post(`${BACKEND}/api/story/import`, {
		data: { curriculum_id: curriculumId, day: 1, story },
	});
	expect(imp.ok(), await imp.text()).toBe(true);
	const { id: lessonId } = await imp.json();

	// The listen mints the lesson's card list.
	const listen = await request.post(`${BACKEND}/api/srs/listen`, { data: { content_id: lessonId } });
	expect(listen.ok(), await listen.text()).toBe(true);

	await page.goto('/review');
	const card = page.locator('.drill-card');

	// Front: the step, the sentence before it and the cue; never the answer, never a bare blank.
	await expect(card.getByText('step 1 of 4')).toBeVisible({ timeout: 10000 });
	await expect(card.getByText('Han er her.')).toBeVisible();
	await expect(card.getByText('+ hun')).toBeVisible();
	await expect(card).not.toContainText('Hun er her.');
	await expect(card).not.toContainText('[...]');

	// Back: the answer.
	await page.getByRole('button', { name: 'Show' }).click();
	await expect(card.locator('.drill-answer')).toHaveText('Hun er her.');
});
