import { test, expect } from '@playwright/test';

// Phase-2 Norwegian smoke: import a Norwegian curriculum + generate a lesson
// against the dedicated TARGET_LANGUAGE=no backend (port 8002) and assert the
// lesson comes back as Bokmål with nb-NO voices. Backed by the Norwegian
// cassettes recorded in backend/tests/cassettes/e2e.json (story: day 1, WIDER).
// API-level only — the frontend isn't yet language-switchable (Phase 5), so we
// hit the backend directly.

const NO_API = 'http://localhost:8002';

/**
 * Byte-identical copy of what POST /api/curriculum/generate produces for
 * topic="ordering coffee" with the Norwegian cassettes — must stay byte-identical
 * so the e2e.json story cassette hash keeps hitting.
 */
const NO_DAY_CAPTURE = {
	day: 1,
	title: "Kaffe på norsk",
	focus: "Basic coffee ordering",
	collocations: [
		"Jeg vil gjerne en kaffe",
		"En espresso takk",
		"Kaffen er for varm",
		"Jeg tar sukkerpilen",
		"En cappuccino bitte",
	],
	learning_objective: "Order a coffee and express simple preferences",
	story_guidance: "Learner visits a busy café in Oslo for the first time",
};

test('Norwegian curriculum + lesson generate with nb-NO voices (mock cassette)', async ({
	request
}) => {
	const health = await request.get(`${NO_API}/api/health`);
	test.skip(!health.ok(), 'Norwegian backend not available');

	// 1. Curriculum — import the captured day so the story cassette matches.
	const curRes = await request.post(`${NO_API}/api/curriculum/import`, {
		data: {
			topic: 'ordering coffee',
			language_code: 'no',
			cefr_level: 'A2',
			days: [NO_DAY_CAPTURE],
		},
	});
	expect(curRes.ok()).toBe(true);
	const cur = await curRes.json();
	expect(cur.language_code).toBe('no');

	// 2. Lesson (story) for day 1 — exercises the Phase-2 story prompt +
	//    section builders (syllabifier + nb-NO voices).
	const storyRes = await request.post(`${NO_API}/api/story/generate`, {
		data: { curriculum_id: cur.id, day: 1 }
	});
	expect(storyRes.ok()).toBe(true);
	const story = await storyRes.json();
	expect(story.id).toBeTruthy();

	// 3. Fetch the rendered lesson and assert Norwegian content + voices.
	const lessonRes = await request.get(`${NO_API}/api/story/${story.id}`);
	expect(lessonRes.ok()).toBe(true);
	const lesson = await lessonRes.json();
	expect(lesson.language_code).toBe('no');

	const l2Phrases = lesson.sections
		.flatMap((s: { phrases: { language_code: string; voice_id: string }[] }) => s.phrases)
		.filter((p: { language_code: string }) => p.language_code === 'no');
	expect(l2Phrases.length).toBeGreaterThan(0);
	// Every Norwegian phrase must use an nb-NO voice (no Slovene voice leakage).
	expect(
		l2Phrases.every((p: { voice_id: string }) => p.voice_id.startsWith('nb-NO-'))
	).toBe(true);

	// The Pimsleur dialogue sections are present.
	const sectionTypes = lesson.sections.map((s: { type: string }) => s.type);
	expect(sectionTypes).toContain('natural_speed');
});
