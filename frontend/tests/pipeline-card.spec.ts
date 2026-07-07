import { test, expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';

// Pipeline card e2e — the pipeline + activity endpoints are route-mocked
// (PIPELINE_AUTOSTART=false on the test backend, and we never want real
// generation/rendering here). The plan/commit flow mirrors planner-chat.spec.ts.

const CANNED_DAY = {
	day: 1,
	title: 'First Sip',
	focus: 'Ordering at the counter',
	collocations: ['Ena kava prosim'],
	learning_objective: 'Order a coffee',
	story_guidance: 'At the counter',
};

async function createPlan(request: APIRequestContext) {
	const health = await request.get('http://localhost:8001/api/health');
	test.skip(!health.ok(), 'Backend not available');
	const planRes = await request.post('http://localhost:8001/api/curriculum/plan', {
		data: { topic: 'pipeline e2e', cefr_level: 'A2' },
	});
	test.skip(!planRes.ok(), 'Failed to create plan');
	const plan = await planRes.json();
	return plan.id as string;
}

async function mockPlannerRoutes(page: Page, curriculumId: string) {
	await page.route(`**/api/curriculum/${curriculumId}/plan/turn`, async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ reply: 'One day planned.', proposed: { start_day: 1, days: [CANNED_DAY] } }),
		});
	});
	await page.route(`**/api/curriculum/${curriculumId}/plan/commit`, async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ id: curriculumId, days: 1 }),
		});
	});
}

async function commitOneDay(page: Page) {
	await expect(page.getByPlaceholder('Message the planner…')).toBeVisible({ timeout: 10000 });
	await page.getByRole('button', { name: /Plan the next \d+ days/ }).click();
	await page.getByRole('button', { name: 'Commit batch' }).click();
	await expect(page.getByText('Committed day 1.')).toBeVisible();
}

function dayPayload(state: string) {
	const ready = state === 'ready';
	return {
		active: !ready,
		days: [
			{
				day: 1,
				state,
				lesson_id: ready ? 'lesson-e2e-1' : null,
				has_audio: ready,
				error: state === 'failed' ? 'LLM exploded' : null,
				retryable: state === 'failed' ? true : null,
				detail: state === 'generating' ? 'Generating story' : null,
			},
		],
	};
}

	test('pipeline card: committed day walks queued → generating → ready, with live activity line', async ({
	page,
	request,
}) => {
	const curriculumId = await createPlan(request);

	// Each poll (2s cadence while active) advances one step; clamps on the last.
	// The pipeline store starts polling on mount now, so pre-commit polls consume
	// some states. Reset the counter when the commit route is hit so the post-commit
	// walk starts from queued.
	const states = ['queued', 'generating', 'generating', 'generating', 'ready'];
	let call = 0;
	await page.route(`**/api/curriculum/${curriculumId}/pipeline`, async (route) => {
		const state = states[Math.min(call, states.length - 1)];
		call += 1;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(dayPayload(state)),
		});
	});
	await page.route('**/api/llm/activity*', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				latest: 1,
				events: [
					{
						seq: 1,
						kind: 'pipeline',
						timestamp: 0,
						curriculum_id: curriculumId,
						day: 1,
						state: 'generating',
						message: 'Generating story',
					},
				],
			}),
		});
	});
	await mockPlannerRoutes(page, curriculumId);
	// Register AFTER mockPlannerRoutes so this commit handler takes priority (LIFO).
	await page.route(`**/api/curriculum/${curriculumId}/plan/commit`, async (route) => {
		call = 0; // reset so post-commit pipeline walk starts from queued
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ id: curriculumId, days: 1 }),
		});
	});

	await page.goto(`/c/${curriculumId}/plan`);
	await commitOneDay(page);

	// Committing restarts the pipeline store — the card appears and walks the states.
	await expect(page.getByText('queued', { exact: true })).toBeVisible();
	await expect(page.getByText('generating', { exact: true })).toBeVisible({ timeout: 10000 });
	// The activity log shows the current line fed by the polled events.
	await expect(page.locator('.current-line')).toHaveText('[pipeline] day 1: generating — Generating story', {
		timeout: 10000,
	});
	// Once every day is ready the card retires itself.
	await expect(page.getByText('generating', { exact: true })).not.toBeVisible({ timeout: 15000 });
	await expect(page.getByText('queued', { exact: true })).not.toBeVisible();
});

test('pipeline card: failed day → Retry → queued', async ({ page, request }) => {
	const curriculumId = await createPlan(request);

	let retried = false;
	await page.route(`**/api/curriculum/${curriculumId}/pipeline`, async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(dayPayload(retried ? 'queued' : 'failed')),
		});
	});
	await page.route(`**/api/curriculum/${curriculumId}/pipeline/retry`, async (route) => {
		retried = true;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ status: 'queued' }),
		});
	});
	await page.route('**/api/llm/activity*', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ latest: 0, events: [] }),
		});
	});
	await mockPlannerRoutes(page, curriculumId);

	await page.goto(`/c/${curriculumId}/plan`);
	await commitOneDay(page);

	await expect(page.getByText('failed', { exact: true })).toBeVisible();
	await page.getByRole('button', { name: 'Retry' }).click();
	// Retry restarts polling, so the queued state lands immediately (no 10s wait).
	await expect(page.getByText('queued', { exact: true })).toBeVisible();
	await expect(page.getByText('failed', { exact: true })).not.toBeVisible();
});
