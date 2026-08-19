import { test, expect, type Page } from '@playwright/test';

/**
 * Layout guard: the four grade buttons must stay on screen on a phone.
 *
 * User-reported (tunatale-asi, 2026-08-17): on mobile, a card with an image
 * pushes the grade buttons below the fold. The image clamps that would keep the
 * card short live behind `@media (min-width: 641px)`, so the one viewport where
 * vertical space is scarcest got the LOOSEST rules — and because every clamp is
 * per-image, nothing budgeted the TOTAL of
 * prompt + divider + image + text + grammar + note + ratings.
 *
 * ⚠️ This CANNOT be a vitest/jsdom test. The value asserted here is
 * browser-computed geometry, and jsdom and happy-dom both report x=0 w=0 for
 * every rect — a component-level version passes vacuously and is worse than no
 * test (the clean-negative trap in .claude/rules/tdd.md). It is also a
 * page-level property: the overflow belongs to the whole stack, not to
 * DrillCard in isolation.
 *
 * The queue API is mocked rather than seeded because this spec is ABOUT layout,
 * not about queue logic — mocking is what lets it pin the worst realistic
 * content (image + translation + grammar + note, all present at once).
 */

// Intrinsically tall (1:3) so it is the height clamp that binds, never the
// width — an image that shrinks itself would hide the very defect under test.
const TALL_IMAGE =
	'data:image/svg+xml;base64,' +
	Buffer.from(
		'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="900">' +
			'<rect width="300" height="900" fill="#4a7"/></svg>',
	).toString('base64');

const LONG_GRAMMAR =
	'<b>fem.</b> noun, declension pattern <i>-a</i>. Genitive singular ' +
	'<i>knjige</i>, dative <i>knjigi</i>, accusative <i>knjigo</i>, ' +
	'locative <i>knjigi</i>, instrumental <i>knjigo</i>.';

const LONG_NOTE =
	'Compare with <i>zvezek</i> (notebook), which is masculine and takes a ' +
	'different pattern. In speech the locative and dative are identical, so ' +
	'context carries the case.';

function queueItem(direction: 'recognition' | 'production') {
	return {
		id: 1,
		text: 'knjiga',
		translation: 'book',
		state: 'review',
		due_at: new Date().toISOString(),
		stability: 5,
		difficulty: 5,
		reps: 3,
		lapses: 0,
		last_review: null,
		language_code: 'sl',
		card_type: 'vocab',
		image_url: TALL_IMAGE,
		grammar: LONG_GRAMMAR,
		note: LONG_NOTE,
		direction,
		pending_rating: null,
	};
}

async function mockQueue(page: Page, direction: 'recognition' | 'production') {
	await page.route('**/api/srs/queue-stats', route =>
		route.fulfill({
			json: { new: 0, learning: 0, review: 1, daily_new_cap: 20, cap_source: 'test' },
		}),
	);
	await page.route('**/api/srs/review-queue*', route =>
		route.fulfill({ json: { queue: [queueItem(direction)] } }),
	);
}

/** Open /review and reveal the answer — the state the buttons appear in. */
async function revealCard(page: Page) {
	await page.goto('/review');
	await page.getByRole('button', { name: 'Show' }).click();
	// The image is the tall element; wait for it so measurements are post-layout.
	await expect(page.locator('.drill-card img')).toBeVisible();
}

const GRADES = ['Again', 'Hard', 'Good', 'Easy'] as const;

// 390x664: a common phone with browser chrome showing. 360x560: a short
// viewport, because the defect is about vertical budget and the tallest image
// is the one that fits the least.
const PHONES = [
	{ name: '390x664', width: 390, height: 664 },
	{ name: '360x560 (short)', width: 360, height: 560 },
];

for (const phone of PHONES) {
	for (const direction of ['recognition', 'production'] as const) {
		// Both directions, because the image sits on opposite sides of the
		// divider in each — recognition puts it in .answer, production in .prompt.
		test(`grade buttons are on screen: ${direction} @ ${phone.name}`, async ({ page }) => {
			await page.setViewportSize({ width: phone.width, height: phone.height });
			await mockQueue(page, direction);
			await revealCard(page);

			for (const grade of GRADES) {
				const button = page.getByRole('button', { name: grade, exact: true });
				await expect(button).toBeInViewport();

				const box = await button.boundingBox();
				expect(box, `${grade} has no layout box`).not.toBeNull();
				expect(
					box!.y + box!.height,
					`${grade} button bottom is below the fold`,
				).toBeLessThanOrEqual(phone.height);
			}
		});
	}
}

test('a docked bar does not permanently cover the last line of the card', async ({ page }) => {
	// The failure mode a fixed footer introduces if it reserves no space: the
	// note becomes unreadable no matter how far you scroll.
	await page.setViewportSize({ width: 390, height: 664 });
	await mockQueue(page, 'recognition');
	await revealCard(page);

	await page.mouse.wheel(0, 5000);
	await page.waitForTimeout(150);

	const noteBox = await page.locator('.drill-card .note').boundingBox();
	const ratingsBox = await page.locator('.drill-card .ratings').boundingBox();
	expect(noteBox, 'note has no layout box').not.toBeNull();
	expect(ratingsBox, 'ratings has no layout box').not.toBeNull();
	expect(
		noteBox!.y + noteBox!.height,
		'scrolled to the bottom, the note is still hidden behind the grade bar',
	).toBeLessThanOrEqual(ratingsBox!.y + 1);
});

test('desktop layout is untouched — the grade row stays in normal flow', async ({ page }) => {
	// Oracle 5: this is a small-viewport fix. The >=641px branch already clamps
	// images to a 240px box and must not gain a pinned bar.
	await page.setViewportSize({ width: 1280, height: 800 });
	await mockQueue(page, 'recognition');
	await revealCard(page);

	const position = await page
		.locator('.drill-card .ratings')
		.evaluate(el => getComputedStyle(el).position);
	expect(position).toBe('static');
});

test('Show is docked in the same strip the grades appear in', async ({ page }) => {
	// User request 2026-08-18: "put show where the grades are so there's less
	// finger jumping". On a phone the thumb taps Show and then immediately taps
	// a grade; those two targets should sit in the same band, not a screen apart.
	//
	// The oracle is the distance between the Show button's centre and the
	// NEAREST grade button's centre — that is literally the travel the thumb
	// makes. Asserting "Show is near the bottom" instead would pass for a layout
	// where the grades moved somewhere else entirely.
	await page.setViewportSize({ width: 390, height: 664 });
	await mockQueue(page, 'recognition');
	await page.goto('/review');

	const showBtn = page.getByRole('button', { name: 'Show' });
	await expect(showBtn).toBeVisible();
	const showBox = await showBtn.boundingBox();
	expect(showBox, 'Show has no layout box').not.toBeNull();
	const showCentre = showBox!.y + showBox!.height / 2;

	await showBtn.click();
	await expect(page.locator('.drill-card img')).toBeVisible();

	const centres: number[] = [];
	for (const grade of GRADES) {
		const box = await page.getByRole('button', { name: grade, exact: true }).boundingBox();
		expect(box, `${grade} has no layout box`).not.toBeNull();
		centres.push(box!.y + box!.height / 2);
	}

	const travel = Math.min(...centres.map(c => Math.abs(c - showCentre)));
	expect(
		travel,
		'vertical thumb travel from Show to the nearest grade button',
	).toBeLessThanOrEqual(60);
});

test('the card surface carries no dead space below its content', async ({ page }) => {
	// User-reported 2026-08-18, twice. The docked bar's height has to be
	// reserved somewhere, and it was reserved as `padding-bottom` on
	// `.drill-card` — which lives INSIDE `.card-section`, the element that paints
	// the white card. So the reservation inflated the visible card instead of the
	// page, showing up as a large empty band inside the card on BOTH sides.
	//
	// The reservation now sits on `.card-section`'s MARGIN, which is outside the
	// border box: the page still scrolls clear of the bar, the card does not grow.
	//
	// Measured from the last in-flow content (`.key-hint`) to the card's bottom
	// edge, because that gap IS the complaint. Measuring `.drill-card`'s own box
	// would read ~0 whether or not the bug is present, since its padding is
	// inside its own border box — a control that cannot fail.
	await page.setViewportSize({ width: 390, height: 664 });
	await mockQueue(page, 'recognition');
	await page.goto('/review');

	const hint = page.locator('.drill-card .key-hint');
	await expect(hint, 'this assertion is anchored on .key-hint being rendered').toBeVisible();

	const deadSpace = async () => {
		const section = await page.locator('.card-section').boundingBox();
		const last = await hint.boundingBox();
		expect(section, 'card-section has no layout box').not.toBeNull();
		expect(last, 'key-hint has no layout box').not.toBeNull();
		return section!.y + section!.height - (last!.y + last!.height);
	};

	// `.drill-card`'s own 0.75rem padding is the legitimate remainder.
	expect(await deadSpace(), 'dead space inside the card, front').toBeLessThanOrEqual(24);

	await page.getByRole('button', { name: 'Show' }).click();
	await expect(page.locator('.drill-card img')).toBeVisible();
	expect(await deadSpace(), 'dead space inside the card, back').toBeLessThanOrEqual(24);
});
