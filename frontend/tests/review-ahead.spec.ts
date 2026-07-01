import { test, expect } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { backendAvailable, seedCurriculumWithLesson } from './helpers';

const BACKEND = 'http://localhost:8001';

type TranscriptWord = {
	surface: string;
	lemma: string;
	prefix_punct?: string;
	suffix_punct?: string;
	srs_item_id: number | null;
	collocation_span_id: number | null;
	active_state: string;
	is_due: boolean;
	recognition_reviewable?: boolean;
};

async function getTranscript(request: APIRequestContext, lessonId: string) {
	const res = await request.get(`${BACKEND}/api/srs/lesson/${lessonId}/transcript`);
	expect(res.ok()).toBe(true);
	const data = await res.json();
	return (data.dialogue_lines ?? []).flatMap(
		(l: { words: TranscriptWord[] }) => l.words
	) as TranscriptWord[];
}

/**
 * Reuse a lesson another spec already generated (lesson-navigation runs earlier
 * under the shared workers:1 backend), falling back to generating one only if the
 * store is empty. Generating here would collide on the single-use "ordering
 * coffee" cassette, so reuse is the robust path.
 */
async function existingCurriculumWithLesson(request: APIRequestContext) {
	const listRes = await request.get(`${BACKEND}/api/curriculum`);
	if (listRes.ok()) {
		const curricula = (await listRes.json()) as Array<{ id: string }>;
		for (const c of curricula) {
			const lessonRes = await request.get(`${BACKEND}/api/curriculum/${c.id}/days/1/lesson`);
			if (lessonRes.ok()) {
				const lesson = await lessonRes.json();
				return { curriculumId: c.id, lessonId: lesson.id as string };
			}
		}
	}
	return seedCurriculumWithLesson(request, { topic: 'ordering coffee' });
}

// Read-ahead: a word whose recognition direction is not terminal (NEW included)
// can be reviewed by reading it in the interface, even though the SRS hasn't
// surfaced it yet. Seed such a card onto a real lesson word, open the lesson in
// Read mode, tap the word, and confirm the review is recorded (the popover flips
// to the Undo affordance, which only appears after a successful grade).
test('review-ahead: reading a not-due recognition word records a review', async ({
	page,
	request
}) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	const { curriculumId, lessonId } = await existingCurriculumWithLesson(request);

	// Pick a resolvable, punctuation-free single word from the actual transcript so
	// the card we create matches the lemma the transcript resolves, and the DOM
	// button's accessible name equals the surface exactly.
	const before = await getTranscript(request, lessonId);
	const candidate = before.find(
		(w) =>
			w.collocation_span_id == null &&
			w.srs_item_id == null &&
			!w.prefix_punct &&
			!w.suffix_punct &&
			/^[\p{L}]+$/u.test(w.surface)
	);
	test.skip(!candidate, 'No standalone unknown word in the lesson transcript');
	const { lemma, surface } = candidate!;

	// Create a NEW base card for the word. NEW is review-ahead eligible (reading it
	// is a valid early introduction) and never "due", so the word gets "Review ✓".
	const createRes = await request.post(`${BACKEND}/api/srs/items`, {
		data: { text: lemma, language_code: 'sl', word_count: 1, translation: 'x' }
	});
	expect(createRes.ok() || createRes.status() === 409).toBe(true);

	const after = await getTranscript(request, lessonId);
	const word = after.find((w) => w.surface === surface && w.srs_item_id != null);
	expect(word, 'seeded card should resolve in the transcript').toBeTruthy();
	expect(word!.is_due).toBe(false);
	expect(word!.active_state).toBe('new');
	expect(word!.recognition_reviewable).toBe(true);

	// Read mode is the lesson page default. Open the word's popover and review it.
	// The word may occur several times; scope to the first occurrence's popover
	// (all occurrences share the same card, so any one records the review).
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);
	const wordButton = page.getByRole('button', { name: surface, exact: true }).first();
	await expect(wordButton).toBeVisible({ timeout: 15000 });
	const wrapper = wordButton.locator(
		'xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " tt-wrap ")][1]'
	);
	await wordButton.click();

	const reviewAhead = wrapper.getByRole('button', { name: 'Review ✓' });
	await expect(reviewAhead).toBeVisible();
	await reviewAhead.click();

	// Proof the read-review was recorded: the recognition direction advances off
	// NEW (NEW + good → learning), which the transcript reflects after the refetch.
	await expect
		.poll(
			async () => {
				const words = await getTranscript(request, lessonId);
				return words.find((w) => w.srs_item_id === word!.srs_item_id)?.active_state;
			},
			{ timeout: 10000 }
		)
		.not.toBe('new');
});
