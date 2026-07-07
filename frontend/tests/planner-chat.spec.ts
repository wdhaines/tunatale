import { test, expect } from '@playwright/test';

const CANNED_TURN_REPLY = "Here are two ideas for your coffee curriculum!";
const CANNED_DAYS = [
	{
		day: 1,
		title: "First Sip",
		focus: "Ordering at the counter",
		collocations: ["Ena kava prosim", "Kavo z mlekom", "Koliko stane"],
		learning_objective: "Order a coffee and ask the price",
		story_guidance: "Standing at the counter of a busy Ljubljana café",
	},
	{
		day: 2,
		title: "Pastry Pairing",
		focus: "Ordering food alongside coffee",
		collocations: ["Ena kava in rogljiček", "Kaj priporočate", "Račun prosim"],
		learning_objective: "Order coffee with a pastry and ask for the bill",
		story_guidance: "Sitting at a table after ordering, ready to add food",
	},
];

test('planner chat: route-mocked turn + commit render the full propose/commit UI loop', async ({ page, request }) => {
	const health = await request.get('http://localhost:8001/api/health');
	test.skip(!health.ok(), 'Backend not available');

	// 1. Create a curriculum plan (LLM-free)
	const planRes = await request.post('http://localhost:8001/api/curriculum/plan', {
		data: { topic: 'ordering coffee', cefr_level: 'A2' },
	});
	test.skip(!planRes.ok(), 'Failed to create plan');
	const plan = await planRes.json();
	const curriculumId: string = plan.id;

	// 2. Navigate to the plan page
	await page.goto(`/c/${curriculumId}/plan`);

	// Wait for hydration
	await expect(page.getByPlaceholder('Message the planner…')).toBeVisible({ timeout: 10000 });

	// 3. Route-intercept the turn and commit endpoints (no proposal stored on backend,
	// so the real commit would 409).
	await page.route(`**/api/curriculum/${curriculumId}/plan/turn`, async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				reply: CANNED_TURN_REPLY,
				proposed: { start_day: 1, days: CANNED_DAYS },
			}),
		});
	});
	await page.route(`**/api/curriculum/${curriculumId}/plan/commit`, async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ id: curriculumId, days: 2 }),
		});
	});

	// 4. Send a message (click the quick action button)
	await page.getByRole('button', { name: /Plan the next \d+ days/ }).click();

	// 5. Assert reply and proposal cards render
	await expect(page.getByText(CANNED_TURN_REPLY)).toBeVisible();

	for (const day of CANNED_DAYS) {
		await expect(page.getByText(`Day ${day.day}`, { exact: true })).toBeVisible();
		await expect(page.getByText(day.title)).toBeVisible();
		await expect(page.getByText(day.focus)).toBeVisible();
		await expect(page.getByText(day.learning_objective)).toBeVisible();
	}

	// 6. Commit batch (route-mocked — the real backend has no proposal state
	// because the turn was intercepted, so a real commit would 409).
	await page.getByRole('button', { name: 'Commit batch' }).click();

	// 7. Assert event line appears (local commitEvent mirror)
	await expect(page.getByText('Committed days 1-2.')).toBeVisible();
	// The header meta updates from the mocked commit response
	await expect(page.getByText('2 days committed')).toBeVisible();

	// 8. Proposal is cleared from the UI
	await expect(page.getByRole('button', { name: 'Commit batch' })).not.toBeVisible();
});
