import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';

/**
 * The Say it tab keeps its English translation hidden until tapped
 * (tunatale-nb8p): practice is the language only; the English teaches once.
 * Hidden is a blur of one fixed stand-in, not of the real text, whose length was a
 * cue (owner, 2026-11-21); that the text is absent is pinned in GradePage.test.ts.
 * The owner tests on Android, so reveal is a tap, never a hover — this spec
 * taps. Geometry is a browser fact (jsdom reports 0x0 rects), so it lives here.
 */

test.use({ viewport: { width: 360, height: 740 }, hasTouch: true });

async function mockPlan(page: Page) {
	await page.route('**/api/srs/content/*/grade-plan*', (route) =>
		route.fulfill({
			json: {
				studied: true,
				listen_count: 1,
				range_days: 3,
				cards: [
					{
						item_id: 40,
						direction: 'production',
						kind: 'word',
						text: 'Nia',
						gloss: 'is here',
						due_at: new Date().toISOString(),
						grade_class: 'ahead',
						line_key: null,
						blank_start: null,
						blank_end: null,
						drill: null,
					},
				],
				later: { recognition: 0, production: 0 },
				waiting_new: 0,
				suggestions: { words: [], contrast: [], sentences: [] },
			},
		}),
	);
	await page.route('**/api/srs/content/*/transcript', (route) =>
		route.fulfill({
			json: {
				lesson_id: 'g1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'male-1',
						sentence: '',
						words: ['Nia', 'mi', 'dinhi'].map((surface, i) => ({
							surface,
							lemma: surface,
							srs_state: 'review',
							srs_item_id: i === 0 ? 40 : 100 + i,
							translation: null,
							collocation_span_id: null,
							collocation_start: false,
							collocation_srs_state: null,
							collocation_lemma: null,
							collocation_translation: null,
							card_type: 'vocab',
							active_state: 'review',
							active_direction: null,
							is_due: false,
							progress: null,
							inflectable: false,
							inflection_feature: null,
							known_marked: false,
						})),
					},
				],
			},
		}),
	);
}

test('Say it blurs a fixed stand-in for the English until tapped, and it sits below the sentence', async ({
	page
}) => {
	await mockPlan(page);
	await page.goto('/grade/g1?back=%2F&title=Wolff+1');
	await page.getByRole('tab', { name: /Say it/ }).click();

	const row = page.locator('[data-row-text="Nia"]');
	const english = row.locator('.english');
	await expect(english).toBeVisible();
	await expect(english).toHaveText('xxxxx xxxx');
	const filter = () => english.evaluate((el) => getComputedStyle(el).filter);
	await expect.poll(filter).toContain('blur');

	// Its own width, not a full-width bar: the flex column must not stretch it.
	const chip = await english.boundingBox();
	const main = await row.locator('.row-main').boundingBox();
	expect(chip!.width).toBeLessThan(main!.width / 2);

	await english.tap();
	await expect(english).toHaveText('is here');
	await expect.poll(filter).toBe('none');

	// Under the sentence as a line of its own, never inline beside it.
	const say = await row.locator('.say').boundingBox();
	const box = await english.boundingBox();
	expect(box!.y).toBeGreaterThan(say!.y + say!.height - 1);
});
