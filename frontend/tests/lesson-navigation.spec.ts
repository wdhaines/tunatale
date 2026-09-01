import { test, expect } from './fixtures';
import { backendAvailable, seedCurriculumWithLesson } from './helpers';

test('curriculum → day picker → lesson page renders header', async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	const { curriculumId } = await seedCurriculumWithLesson(request, { topic: 'ordering coffee' });

	await page.goto(`/c/${curriculumId}`);
	await expect(page.getByRole('button', { name: 'Day 1' })).toBeVisible({ timeout: 10000 });

	// Day buttons must not inherit the <button> UA stylesheet's `text-align:
	// center`. On one line that is invisible; a long title wraps and every
	// wrapped line then centres, which is the ragged block reported for
	// "Day 7 · <long title>".
	//
	// Asserted HERE, inside an existing test, rather than as its own spec:
	// the page and the seeded curriculum are already up, so this costs no extra
	// fixture and no extra Playwright slot — and E2E is the CI long pole, where
	// every addition has a visible per-push price (.claude/rules/test-tiers.md).
	//
	// It cannot live at the component tier, and that was MEASURED rather than
	// assumed: jsdom does apply the button UA rule (it reports "center"), but
	// this vitest setup injects no Svelte component CSS, so the override is
	// invisible there and the test could never go green. Resolving the real
	// cascade needs a real browser.
	await expect(page.getByRole('button', { name: 'Day 1' })).toHaveCSS('text-align', 'left');

	await page.getByRole('button', { name: 'Day 1' }).click();

	// getLessonByDay returns the pre-seeded lesson; no LLM call needed at click time.
	await expect(page).toHaveURL(new RegExp(`/c/${curriculumId}/l/[a-z0-9-]+$`), { timeout: 15000 });
	// Lesson header renders with "Render Audio" button — proves the +page.ts loader
	// resolved both curriculumId and lessonId from the URL correctly.
	await expect(page.getByRole('button', { name: 'Render Audio' })).toBeVisible({ timeout: 10000 });
});
