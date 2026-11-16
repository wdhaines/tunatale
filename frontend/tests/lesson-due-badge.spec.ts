import { test, expect } from './fixtures';
import { BACKEND, backendAvailable, resetSRSItems, seedWithCannedStory } from './helpers';

// Core journey (test-tiers.md door 1): the lesson list's due badge
// (tunatale-5i1p.3), counted by the real backend and drawn by the real page.
// The component tests feed DayPicker a hand-written map and the backend tests
// stop at the JSON, so this is the row that reads the screen (AGENTS.md
// § "Work the learner sees is done at the screen"). The oracle is the Grade
// page's own plan for the same lesson and range: the badge is its row count.

test.beforeEach(async ({ request }) => {
	if (await backendAvailable(request)) await resetSRSItems(request);
});

// And after: the two learning cards below outlive the test in the worker's
// backend, and the next spec on it inherits them. On main 3437909e that was
// lesson-navigation (its story prompt then named kavo and dan, a cassette miss)
// and listen-preview-layout (a "learning" pill where it laid out "new").
test.afterEach(async ({ request }) => {
	if (await backendAvailable(request)) await resetSRSItems(request);
});

test('lesson list: a day is badged with its Grade page row count, at the remembered range', async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	const { curriculumId, lessonId } = await seedWithCannedStory(request, 'e2e lesson due badge');

	// Cards for two of the lesson's words, made directly (a listen would ask
	// the LLM for glosses) and promoted: a learning step that is already ripe
	// is a row at every range.
	for (const [text, translation] of [
		['kavo', 'coffee'],
		['dan', 'day'],
	]) {
		const created = await request.post(`${BACKEND}/api/srs/items`, {
			data: { text, translation, language_code: 'sl', word_count: 1 },
		});
		expect(created.ok(), await created.text()).toBe(true);
		const promoted = await request.post(`${BACKEND}/api/srs/items/${(await created.json()).id}/state`, {
			data: { state: 'learning' },
		});
		expect(promoted.ok(), await promoted.text()).toBe(true);
	}

	const plan = await (
		await request.get(`${BACKEND}/api/srs/content/${lessonId}/grade-plan?range_days=0`)
	).json();
	const rows: number = plan.cards.length;
	expect(rows).toBeGreaterThan(0);

	// A remembered range other than the default, so the badge is seen to follow it.
	await page.addInitScript(() => localStorage.setItem('gradeRange', '0'));
	const asked = page.waitForRequest((r) => r.url().includes('/api/srs/due-counts'));
	await page.goto(`/c/${curriculumId}`);
	expect(new URL((await asked).url()).searchParams.get('range_days')).toBe('0');

	const badge = page.getByTestId('due-badge');
	await expect(badge).toHaveCount(1, { timeout: 10000 });
	await expect(badge).toHaveText(String(rows));
	await expect(badge).toBeVisible();
});
