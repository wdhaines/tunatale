import { test, expect } from './fixtures';

const CANNED_TURN_REPLY = "Here are two ideas for your coffee curriculum!";
const CANNED_DAYS = [
	{
		day: 1,
		position: 1,
		title: "First Sip",
		focus: "Ordering at the counter",
		collocations: ["Ena kava prosim", "Kavo z mlekom", "Koliko stane"],
		learning_objective: "Order a coffee and ask the price",
		story_guidance: "Standing at the counter of a busy Ljubljana café",
	},
	{
		day: 2,
		position: 2,
		title: "Pastry Pairing",
		focus: "Ordering food alongside coffee",
		collocations: ["Ena kava in rogljiček", "Kaj priporočate", "Račun prosim"],
		learning_objective: "Order coffee with a pastry and ask for the bill",
		story_guidance: "Sitting at a table after ordering, ready to add food",
	},
];

test('planner chat: route-mocked turn + commit render the full propose/commit UI loop', async ({ backendURL, page, request }) => {
	const health = await request.get(`${backendURL}/api/health`);
	test.skip(!health.ok(), 'Backend not available');

	// 1. Create a curriculum plan (LLM-free)
	const planRes = await request.post(`${backendURL}/api/curriculum/plan`, {
		data: { topic: 'ordering coffee', cefr_level: 'A2' },
	});
	test.skip(!planRes.ok(), 'Failed to create plan');
	const plan = await planRes.json();
	const curriculumId: string = plan.id;

	// 2. Route-intercept the turn and commit endpoints (no proposal stored on
	// backend, so the real commit would 409).
	//
	// ⚠️ REGISTERED BEFORE `goto`, AND MATCHING ANY CURRICULUM ID — both matter
	// (tunatale-hvbv). This spec leaked a REAL planner turn to the backend three
	// times in CI, each time dying on a cassette miss for the same hash, because
	// there is no planner prompt recorded in e2e.json and there never should be.
	// The old form registered the routes AFTER navigation and pinned the glob to
	// the one id returned by POST /api/curriculum/plan, so anything the page
	// requested before interception was installed — or under any id but that one
	// — went straight to a real LLM call. A route this spec's whole premise
	// depends on must not be conditional on either.
	let turnMockHits = 0;
	let commitMockHits = 0;
	await page.route('**/api/curriculum/*/plan/turn', async (route) => {
		turnMockHits += 1;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				reply: CANNED_TURN_REPLY,
				proposed: { start_day: 1, days: CANNED_DAYS },
			}),
		});
	});
	await page.route('**/api/curriculum/*/plan/commit', async (route) => {
		commitMockHits += 1;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ id: curriculumId, days: 2 }),
		});
	});

	// 3. Navigate to the plan page and wait for hydration
	await page.goto(`/c/${curriculumId}/plan`);
	await expect(page.getByPlaceholder('Message the planner…')).toBeVisible({ timeout: 10000 });

	// 4. Send a message (click the quick action button)
	await page.getByRole('button', { name: /Plan the next \d+ days/ }).click();

	// 5. Assert reply and proposal cards render.
	//
	// The mock-hit check comes FIRST and is the diagnostic that was missing. When
	// the turn escaped interception, the visible symptom was this reply never
	// rendering — a 30s timeout on `element(s) not found`, which names the
	// wrong thing entirely and sent three investigations after prompt-hash
	// determinism. If the mock is ever bypassed again, THIS is what fails, and it
	// says so in one line.
	await expect
		.poll(() => turnMockHits, {
			message: 'the planner turn was NOT intercepted — it reached the real backend and the LLM'
		})
		.toBeGreaterThan(0);
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

	// Both mocks did the work — otherwise this spec silently becomes an
	// integration test against a real LLM, which is what it spent three CI runs
	// accidentally being.
	expect(turnMockHits, 'turn mock never fired').toBeGreaterThan(0);
	expect(commitMockHits, 'commit mock never fired').toBeGreaterThan(0);
});
