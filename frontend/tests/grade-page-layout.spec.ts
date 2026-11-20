import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';

/**
 * Layout seam for the Grade page (tunatale-5i1p.3): on the narrowest phone the
 * page must not scroll sideways, each row's four grades must share one line,
 * and Save must stay on screen while the list scrolls under it.
 *
 * All three are browser-computed geometry (jsdom reports every rect as 0x0),
 * so they live here. The plan API is mocked: this spec is about layout, and a
 * mock lets it pin the worst realistic content (a long word, a long gloss, a
 * long line around a hidden word, many rows).
 */

const LONG = 'Nagpanguta-nguta';

function card(i: number, direction: 'recognition' | 'production') {
	return {
		item_id: i,
		direction,
		kind: 'word',
		text: i === 1 ? LONG : `pulong${i}`,
		gloss: 'a deliberately long meaning that has to wrap inside its row on a narrow phone',
		due_at: new Date(Date.now() + i * 3_600_000).toISOString(),
		grade_class: 'ahead',
		line_key: null,
		blank_start: null,
		blank_end: null,
		drill: null,
	};
}

async function mockPlan(page: Page) {
	const cards = [
		...Array.from({ length: 12 }, (_, i) => card(i + 1, 'recognition')),
		card(40, 'production'),
	];
	await page.route('**/api/srs/content/*/grade-plan*', (route) =>
		route.fulfill({
			json: {
				studied: true,
				listen_count: 2,
				range_days: 3,
				cards,
				later: { recognition: 3, production: 0 },
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
						words: ['Nagpanguta-nguta', 'siya', 'sa', 'akong', 'amigo', 'dinhi', 'karon', 'gabii']
							.map((surface, i) => ({
								surface,
								lemma: surface,
								srs_state: 'review',
								srs_item_id: i === 4 ? 40 : 100 + i,
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

const PHONES = [
	{ name: '320x640', width: 320, height: 640 },
	{ name: '390x664', width: 390, height: 664 },
];

for (const phone of PHONES) {
	test(`grade page fits the phone @ ${phone.name}`, async ({ page }) => {
		await page.setViewportSize({ width: phone.width, height: phone.height });
		await mockPlan(page);
		await page.goto('/grade/g1?back=%2F&title=A+long+lesson+title+that+wraps');
		await expect(page.locator(`[data-row-text="${LONG}"]`)).toBeVisible();

		const measure = () =>
			page.evaluate(() => {
				const root = document.documentElement;
				const rows = [...document.querySelectorAll('[data-row-text]')];
				const rowTops = rows.map((row) =>
					[...row.querySelectorAll('.grade-btn')].map((b) => Math.round(b.getBoundingClientRect().top)),
				);
				const save = document.querySelector('.save')!.getBoundingClientRect();
				return {
					overflow: root.scrollWidth - root.clientWidth,
					rowLines: rowTops.map((tops) => new Set(tops).size),
					rowButtons: rowTops.map((tops) => tops.length),
					saveBottom: save.bottom,
					saveTop: save.top,
				};
			});

		const r = await measure();
		expect(r.overflow, 'the page scrolls sideways').toBe(0);
		expect(r.rowButtons.every((n) => n === 4), `rows without four grades: ${r.rowButtons}`).toBe(true);
		expect(r.rowLines.every((n) => n === 1), `a grade row wrapped: ${r.rowLines}`).toBe(true);
		expect(r.saveBottom, 'Save is below the fold').toBeLessThanOrEqual(phone.height);

		// Scrolled to the middle of the list, Save is still on screen.
		await page.mouse.wheel(0, 600);
		await page.waitForTimeout(150);
		const mid = await measure();
		expect(mid.saveBottom, 'Save scrolled away').toBeLessThanOrEqual(phone.height);
		expect(mid.saveTop, 'Save scrolled away').toBeGreaterThanOrEqual(0);

		// Say it: the line around the hidden word wraps inside the page.
		await page.getByRole('tab', { name: /Say it/ }).click();
		await page.getByRole('button', { name: 'Show the hidden word' }).click();
		const say = await measure();
		expect(say.overflow, 'Say it scrolls sideways').toBe(0);
		expect(say.rowLines, 'Say it grade row wrapped').toEqual([1]);
	});
}
