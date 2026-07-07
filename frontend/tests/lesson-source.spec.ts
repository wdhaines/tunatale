import { test, expect } from '@playwright/test';
import { backendAvailable, seedWithCannedStory, CANNED_STORY } from './helpers';

// Real-backend round-trip for the lesson source panel: the source GET and both
// imports hit the actual API (import needs no LLM). Only the pipeline endpoint
// is route-mocked — every real import enqueues a render job that never runs
// under PIPELINE_AUTOSTART=false, and an eternally-queued badge is not what
// this spec is about.

test.use({
	permissions: ['clipboard-read', 'clipboard-write'],
});

test('lesson source panel: copy, warned import defers navigation, clean import navigates to the new lesson', async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	const { curriculumId, lessonId } = await seedWithCannedStory(request, 'source-panel-e2e');

	await page.route(`**/api/curriculum/${curriculumId}/pipeline`, async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ active: false, days: [] }),
		});
	});

	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	await expect(page.getByRole('button', { name: 'Render Audio' })).toBeVisible({ timeout: 15000 });

	// Open the tools card, then the source panel — the REAL source endpoint returns the seeded story.
	await page.getByText('Lesson tools').click();
	await page.getByText('Edit Source').click();
	await expect(page.locator('.source-view')).toContainText('"title": "Ordering Coffee"', {
		timeout: 10000,
	});

	// Copy JSON and verify the clipboard holds the real exported story.
	await page.getByTestId('copy-json').click();
	await expect(page.getByText('Copied ✓')).toBeVisible();
	const jsonClip = await page.evaluate(() => navigator.clipboard.readText());
	expect(JSON.parse(jsonClip).title).toBe('Ordering Coffee');

	// Copy prompt — schema reminder + the story JSON.
	await page.getByTestId('copy-prompt').click();
	const promptClip = await page.evaluate(() => navigator.clipboard.readText());
	expect(promptClip).toContain('SCHEMA REMINDER');
	expect(promptClip).toContain('Ordering Coffee');

	// 1) Import a story with an unknown speaker → REAL backend returns a
	// speaker warning; navigation is deferred behind the Continue button.
	const warnedStory = {
		...CANNED_STORY,
		title: 'Ordering Coffee warned',
		scenes: [
			{
				label: 'At the Café',
				lines: [{ speaker: 'alien-9', text: 'Dober dan!', translation: 'Good day!' }],
			},
		],
	};
	await page.locator('textarea').fill(JSON.stringify(warnedStory));
	await page.getByTestId('import-btn').click();
	await expect(page.getByText(/speaker 'alien-9' is not in the sl voice map/)).toBeVisible({
		timeout: 15000,
	});
	await expect(page).toHaveURL(new RegExp(lessonId)); // still on the original lesson
	await expect(page.getByTestId('continue-btn')).toBeVisible();

	// 2) Re-paste a clean edited story (known speakers) → REAL import returns
	// no warnings and the page navigates straight to the new lesson.
	const editedStory = {
		...CANNED_STORY,
		title: 'Ordering Coffee v2',
		scenes: CANNED_STORY.scenes.map((s) => ({
			...s,
			lines: s.lines.map((l) =>
				l.text === 'Dober dan!' ? { ...l, text: 'Dober večer!', translation: 'Good evening!' } : l,
			),
		})),
	};
	await page.locator('textarea').fill(JSON.stringify(editedStory));
	await expect(page.getByTestId('continue-btn')).not.toBeVisible(); // editing resets the warned state
	await page.getByTestId('import-btn').click();

	// The real lesson page for the freshly minted lesson loads.
	await expect(page.getByRole('heading', { name: 'Ordering Coffee v2' })).toBeVisible({
		timeout: 15000,
	});
	expect(page.url()).not.toContain(lessonId);
});
