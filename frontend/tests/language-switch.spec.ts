import { test, expect } from '@playwright/test';

/**
 * Switching the active language in Settings re-points the whole app at the
 * other language's database.
 *
 * ADMITTED AS A CORE JOURNEY, not as a seam (`.claude/rules/test-tiers.md`).
 * Every assertion below is app-computed — visible text, a select's value — and
 * that is fine: a journey is admitted on the PATH IT WALKS, not on what it
 * reads. The path here is localStorage → the `X-TT-Language` header on every
 * subsequent request → a different per-language connection on the backend →
 * different content rendered after a full page reload. No tier below a browser
 * can join those: `LanguageSelector.test.ts` proves the select calls
 * `languageStore.set` and reloads, and `test_multilang.py` proves the header
 * selects the connection, but only a real reload in a real browser shows that
 * the choice SURVIVES the reload and reaches the next request.
 *
 * Replaces `generate-norwegian.spec.ts`, deleted 2026-08-15 (`tunatale-vnf.10`)
 * — 78 lines that paid the full e2e boot (two backends, Vite, a browser) and
 * never opened a page: it drove a second uvicorn on :8002 with the `request`
 * fixture to assert Norwegian generation, which is a backend claim. Those
 * assertions now live in
 * `test_multilang.py::TestPerRequestIsolation::test_curriculum_imports_as_norwegian_into_the_norwegian_store`
 * and `test_story.py::TestNorwegianStoryGeneration` (voices across every
 * section, plus the `natural_speed` section builder). The spec's own header
 * comment said "the frontend isn't yet language-switchable (Phase 5)" — that
 * premise expired when /settings grew the selector this test drives.
 *
 * The Norwegian curriculum is seeded through the SAME backend the browser talks
 * to (:8001), with the same header the app itself sends. :8001 is configured
 * with both languages' DBs, so a header is all it takes to reach the Norwegian
 * one — which is why removing the :8002 server cost this suite nothing.
 */

const API = 'http://localhost:8001';

// Distinctive enough that a match cannot be some other spec's fixture leaking
// through the shared Slovene DB — which is the failure this test's first
// assertion would otherwise silently absorb.
//
// The random suffix keeps it idempotent under `--repeat-each`: the seed is a
// real row in a DB that is cleaned once per RUN, not once per test, so a fixed
// topic would match twice on the second iteration and fail Playwright's strict
// mode for a reason that has nothing to do with language switching.
const NO_TOPIC = `norsk kaffekurs (language-switch ${Math.random().toString(36).slice(2, 8)})`;

test('switching to Norwegian in Settings re-points the library at the Norwegian DB', async ({
	page,
	request
}) => {
	const health = await request.get(`${API}/api/health`);
	test.skip(!health.ok(), 'Backend not available');

	// Seed the NORWEGIAN database, via the header the app itself sends. Nothing
	// else in this suite writes to it, so this row is the discriminator.
	const seeded = await request.post(`${API}/api/curriculum/import`, {
		headers: { 'X-TT-Language': 'no' },
		data: {
			topic: NO_TOPIC,
			language_code: 'no',
			cefr_level: 'A2',
			days: [
				{
					day: 1,
					title: 'Kaffe på norsk',
					focus: 'Basic coffee ordering',
					collocations: ['Jeg vil gjerne en kaffe', 'En espresso takk'],
					learning_objective: 'Order a coffee and express simple preferences'
				}
			]
		}
	});
	expect(seeded.ok()).toBe(true);

	// CONTROL. The app starts on Slovene (:8001's TARGET_LANGUAGE, and no stored
	// choice yet), so the Norwegian curriculum must be ABSENT here. Without this
	// the final assertion proves nothing: a library that showed every curriculum
	// regardless of language would pass it just as well.
	await page.goto('/');
	await expect(page.locator('.review-badge')).toBeVisible({ timeout: 10000 });
	await expect(page.getByText(NO_TOPIC)).toHaveCount(0);

	await page.goto('/settings');
	const selector = page.getByRole('combobox', { name: 'Active language' });
	// Vacuity guard: the selector renders only when the backend reports more than
	// one configured language. A single-language backend would hide it, and every
	// assertion after this would be about a page with no control on it.
	await expect(selector).toBeVisible({ timeout: 10000 });
	await expect(selector).toHaveValue('sl');

	// The switch itself triggers window.location.reload().
	await selector.selectOption('no');
	await expect(selector).toHaveValue('no');

	// THE CLAIM: the choice survived the reload and is on the next request's
	// header, so the library now comes from the Norwegian connection.
	await page.goto('/');
	await expect(page.getByText(NO_TOPIC)).toBeVisible({ timeout: 10000 });
});
