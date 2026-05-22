import { test, expect } from '@playwright/test';
import { backendAvailable, seedCurriculumWithLesson } from './helpers';

test('curriculum → day picker → lesson page renders header', async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	const { curriculumId } = await seedCurriculumWithLesson(request, { topic: 'ordering coffee' });

	await page.goto(`/c/${curriculumId}`);
	await expect(page.getByRole('button', { name: 'Day 1' })).toBeVisible({ timeout: 10000 });
	await page.getByRole('button', { name: 'Day 1' }).click();

	// getLessonByDay returns the pre-seeded lesson; no LLM call needed at click time.
	await expect(page).toHaveURL(new RegExp(`/c/${curriculumId}/l/[a-z0-9-]+$`), { timeout: 15000 });
	// Lesson header renders with "Render Audio" button — proves the +page.ts loader
	// resolved both curriculumId and lessonId from the URL correctly.
	await expect(page.getByRole('button', { name: 'Render Audio' })).toBeVisible({ timeout: 10000 });
});
