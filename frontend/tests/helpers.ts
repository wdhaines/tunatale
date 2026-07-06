import type { APIRequestContext } from '@playwright/test';

const BACKEND = 'http://localhost:8001';

export async function backendAvailable(request: APIRequestContext): Promise<boolean> {
	return (await request.get(`${BACKEND}/api/health`)).ok();
}

export async function seedSRSItems(
	request: APIRequestContext,
	items: Array<{ text: string; translation: string; language_code?: string; word_count?: number }>,
): Promise<void> {
	for (const item of items) {
		const res = await request.post(`${BACKEND}/api/srs/items`, {
			data: { language_code: 'sl', word_count: 1, ...item },
		});
		if (!res.ok() && res.status() !== 409)
			throw new Error(`seed failed: ${res.status()} ${await res.text()}`);
	}
}

/**
 * Wipes ALL SRS items from the shared test DB. Used by specs whose assertions
 * depend on a known item count (e.g., review-flow asserts "Done for today"
 * after exactly N reviews). Specs that share the DB with earlier-running
 * specs need this in `beforeEach` to undo the prior seeds.
 */
export async function resetSRSItems(request: APIRequestContext): Promise<void> {
	const res = await request.get(`${BACKEND}/api/srs/items?limit=10000`);
	if (!res.ok()) return;
	const data = await res.json();
	const ids: number[] = (data.items ?? []).map((i: { id: number }) => i.id);
	if (ids.length === 0) return;
	await request.post(`${BACKEND}/api/srs/items/bulk-delete`, {
		data: { ids },
	});
}

/**
 * Byte-identical copy of what POST /api/curriculum/generate produces for
 * topic="ordering coffee" with the Slovene cassettes — must stay byte-identical
 * so the e2e.json story cassette hash keeps hitting.
 */
const SL_DAY_CAPTURE = {
	day: 1,
	title: "Kavarna",
	focus: "ordering coffee",
	collocations: [
		"Želim kavo",
		"Kako kavo imate?",
		"Kavno pivo, prosim",
		"Želim vročo kavo",
		"Koliko stane?",
		"Ali imate sladko kavo?",
	],
	learning_objective: "to order a coffee in a café",
	story_guidance:
		"You are a tourist in Ljubljana and you want to order a coffee in a local café",
};

/**
 * Canonical minimal story (matches backend test_api.py::TestLessonAuthoringEndpoints._story).
 * No LLM needed — used by seedWithCannedStory for cassette-free seeding.
 */
export const CANNED_STORY = {
	title: 'Ordering Coffee',
	key_phrases: [{ phrase: 'dober dan', translation: 'good day' }],
	scenes: [
		{
			label: 'At the Café',
			lines: [
				{ speaker: 'female-1', text: 'Dober dan!', translation: 'Good day!' },
				{ speaker: 'male-1', text: 'Prosim kavo.', translation: 'A coffee please.' },
			],
		},
	],
	dialogue_glosses: [{ word: 'kavo', translation: 'coffee' }],
	morphology_focus: [],
};

/** One curriculum day for an e2e-specific topic (no cassette dependency). */
function makeDay(topic: string) {
	return {
		day: 1,
		title: 'Day 1',
		focus: topic,
		collocations: ['dober dan'],
		learning_objective: 'greet and order',
		story_guidance: `Practice ordering ${topic}`,
	};
}

/**
 * Seed a curriculum + lesson via direct imports (no LLM) so the test avoids
 * consuming the shared e2e.json cassette. Topic must be unique per spec to
 * prevent cross-spec data collisions.
 */
export async function seedWithCannedStory(
	request: APIRequestContext,
	topic: string,
): Promise<{ curriculumId: string; lessonId: string }> {
	const currRes = await request.post(`${BACKEND}/api/curriculum/import`, {
		data: {
			topic,
			language_code: 'sl',
			cefr_level: 'A2',
			days: [makeDay(topic)],
		},
	});
	if (!currRes.ok())
		throw new Error(`curriculum import failed: ${currRes.status()} ${await currRes.text()}`);
	const curriculum = await currRes.json();

	const impRes = await request.post(`${BACKEND}/api/story/import`, {
		data: { curriculum_id: curriculum.id, day: 1, story: CANNED_STORY },
	});
	if (!impRes.ok())
		throw new Error(`story import failed: ${impRes.status()} ${await impRes.text()}`);
	const { id: lessonId } = await impRes.json();
	return { curriculumId: curriculum.id, lessonId };
}

export async function seedCurriculumWithLesson(
	request: APIRequestContext,
	opts: { topic: string },
): Promise<{ curriculumId: string; lessonId: string }> {
	const currRes = await request.post(`${BACKEND}/api/curriculum/import`, {
		data: {
			topic: opts.topic,
			language_code: "sl",
			cefr_level: "A2",
			days: [SL_DAY_CAPTURE],
		},
	});
	if (!currRes.ok())
		throw new Error(`curriculum import failed: ${currRes.status()} ${await currRes.text()}`);
	const curriculum = await currRes.json();

	const storyRes = await request.post(`${BACKEND}/api/story/generate`, {
		data: { curriculum_id: curriculum.id, day: 1 },
	});
	if (!storyRes.ok())
		throw new Error(`story generate failed: ${storyRes.status()} ${await storyRes.text()}`);
	const story = await storyRes.json();

	return { curriculumId: curriculum.id, lessonId: story.id };
}
