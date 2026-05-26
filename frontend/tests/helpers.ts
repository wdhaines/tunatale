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

export async function seedCurriculumWithLesson(
	request: APIRequestContext,
	opts: { topic: string; num_days?: number },
): Promise<{ curriculumId: string; lessonId: string }> {
	const currRes = await request.post(`${BACKEND}/api/curriculum/generate`, {
		data: { topic: opts.topic, num_days: opts.num_days ?? 1 },
	});
	if (!currRes.ok())
		throw new Error(`curriculum generate failed: ${currRes.status()} ${await currRes.text()}`);
	const curriculum = await currRes.json();

	const storyRes = await request.post(`${BACKEND}/api/story/generate`, {
		data: { curriculum_id: curriculum.id, day: 1 },
	});
	if (!storyRes.ok())
		throw new Error(`story generate failed: ${storyRes.status()} ${await storyRes.text()}`);
	const story = await storyRes.json();

	return { curriculumId: curriculum.id, lessonId: story.id };
}
