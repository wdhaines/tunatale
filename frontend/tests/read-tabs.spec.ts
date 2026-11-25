/**
 * The lesson's Read side as one tab bar, on a phone (tunatale-cs3p slice 3, the
 * owner's Option A of 2026-11-24). Oracle rows R1, R3 and L3 of
 * .beads-tasks/briefs/design-curriculum-page-2026-11.md, in a real browser with
 * real taps:
 *
 * - R1: a lesson's first open is Read on the Outline tab; a reload keeps the side chosen.
 * - R3: a practice lesson has the switch, an Outline of its drills, and a tab per
 *   drill that jumps the ONE drill recording to that drill's first cue.
 * - L3: the Read/Listen switch toggles, and Listen's position survives a round trip.
 *
 * The lesson is a real seeded one, served as the shape of the live Wolff
 * practice lesson l01p1 (one affix_drill section, its cues as the story-drill
 * builder emits them), and its recording is a silent WAV so seeks are real.
 */
import { test, expect } from "./fixtures";
import { backendAvailable, seedWithCannedStory } from "./helpers";

test.use({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });

const SECONDS = 30;

/** A silent mono 8 kHz 8-bit WAV, long enough to seek around in. */
function silentWav(seconds: number): Buffer {
	const rate = 8000;
	const n = rate * seconds;
	const b = Buffer.alloc(44 + n, 0x80);
	b.write("RIFF", 0);
	b.writeUInt32LE(36 + n, 4);
	b.write("WAVE", 8);
	b.write("fmt ", 12);
	b.writeUInt32LE(16, 16);
	b.writeUInt16LE(1, 20); // PCM
	b.writeUInt16LE(1, 22); // mono
	b.writeUInt32LE(rate, 24);
	b.writeUInt32LE(rate, 28); // byte rate
	b.writeUInt16LE(1, 32); // block align
	b.writeUInt16LE(8, 34); // bits
	b.write("data", 36);
	b.writeUInt32LE(n, 40);
	return b;
}

// l01p1's shape: title, then per drill one narrator instruction (its ref is the
// drill's first item), a model, and prompt/answer pairs sharing a ref.
let at = 0;
function cue(role: string, text: string, target: number | null, language_code = "ceb") {
	const i = at++;
	return {
		index: i,
		start_ms: i * 2000,
		end_ms: i * 2000 + 1500,
		section_index: 0,
		section_type: "affix_drill",
		phrase_index: i,
		role,
		language_code,
		text,
		ref: target === null ? { kind: "narration" } : { kind: "drill", target_index: target },
	};
}
const CUES = [
	cue("narrator", "Drill", null, "en"),
	cue("narrator", "Change the sentence.", 0, "en"), // drill 1 opens at 0:02
	cue("model", "Nia siya dinhi.", 0),
	cue("prompt", "si Huwan", 1),
	cue("answer", "Nia si Huwan dinhi.", 1),
	cue("prompt", "sila", 2),
	cue("answer", "Nia sila dinhi.", 2),
	cue("narrator", "Change the sentence.", 3, "en"), // drill 2 opens at 0:14
	cue("model", "Ibilin ko kining akong amigo dinhi.", 3),
	cue("prompt", "kana", 4),
	cue("answer", "Ibilin ko kanang akong amigo dinhi.", 4),
];

async function stubPractice(page: import("@playwright/test").Page) {
	await page.route("**/api/story/*", async (route) => {
		if (route.request().method() !== "GET") return route.continue();
		const real = await (await route.fetch()).json();
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				...real,
				title: "Who Is Here (Wolff 1, practice 1)",
				key_phrases: [],
				sections: [{ type: "affix_drill", phrases: [] }],
				drill: null,
				gloss_entry_count: 0,
			}),
		});
	});
	await page.route("**/api/audio/lesson/*", (route) =>
		route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				audio_id: "stub-audio",
				lesson_id: "any",
				sections: [
					{ audio_id: "stub-drill", section_index: 0, section_type: "affix_drill", title: "Drill", cues: CUES },
				],
				cues: CUES,
			}),
		}),
	);
	// With Range support: Chromium cannot seek in media served whole, and a seek
	// to 0:14 would silently land on 0:00.
	const wav = silentWav(SECONDS);
	await page.route("**/api/audio/stub-drill", (route) => {
		const range = /bytes=(\d+)-(\d*)/.exec(route.request().headers()["range"] ?? "");
		if (!range) return route.fulfill({ status: 200, contentType: "audio/wav", body: wav, headers: { "Accept-Ranges": "bytes" } });
		const start = Number(range[1]);
		const end = range[2] ? Math.min(Number(range[2]), wav.length - 1) : wav.length - 1;
		return route.fulfill({
			status: 206,
			contentType: "audio/wav",
			body: wav.subarray(start, end + 1),
			headers: {
				"Accept-Ranges": "bytes",
				"Content-Range": `bytes ${start}-${end}/${wav.length}`,
			},
		});
	});
}

let seeded: { curriculumId: string; lessonId: string } | null = null;

const overflowX = (page: import("@playwright/test").Page) =>
	page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test("a practice lesson on a phone: Outline first, a tab per drill that seeks, and Listen kept across the switch", async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), "Backend not available");
	// Seeded once per worker: a second import of the same topic is refused, and
	// each test's fresh browser context still opens it for the first time (R1).
	seeded ??= await seedWithCannedStory(request, "read-tabs-e2e");
	const { curriculumId, lessonId } = seeded;
	await stubPractice(page);
	await page.goto(`/c/${curriculumId}/l/${lessonId}`);

	// R1: first open, on a phone (where the old default was Listen): Read, Outline.
	const read = page.getByRole("button", { name: "Read", exact: true });
	const listen = page.getByRole("button", { name: "Listen", exact: true });
	await expect(page.getByRole("tab", { name: "Outline" })).toHaveAttribute("aria-selected", "true", {
		timeout: 15000,
	});
	await expect(read).toHaveClass(/active/);

	// R3: the Outline lists each drill: its count, model and instruction.
	const first = page.getByRole("button", { name: /^Drill 1/ });
	await expect(first).toContainText("Drill 1 · 2 steps");
	await expect(first).toContainText("Nia siya dinhi.");
	await expect(page.getByRole("button", { name: /^Drill 2/ })).toContainText("Drill 2 · 1 step");
	expect(await overflowX(page)).toBe(0);

	// A drill tab jumps the one recording to that drill's first cue.
	// Read's player keeps the same clock as Listen's unless it is collapsed.
	const clock = page.locator(".time-labels span").first();
	await expect(page.locator(".time-labels span").nth(1)).toHaveText(`0:${SECONDS}`, { timeout: 15000 });
	await page.getByRole("button", { name: /^Drill 2/ }).tap();
	await expect(page.getByRole("tab", { name: "Drill 2" })).toHaveAttribute("aria-selected", "true");
	await expect(clock).toHaveText("0:14");
	await page.getByRole("tab", { name: "Drill 1" }).tap();
	await expect(clock).toHaveText("0:02");

	// Every step written out, the answer blurred until tapped.
	await expect(page.getByText("si Huwan", { exact: true })).toBeVisible();
	const hidden = page.getByRole("button", { name: "Show the answer" }).first();
	await expect(hidden).toHaveText("Nia si Huwan dinhi.");
	await hidden.tap();
	await expect(page.locator(".steps .answer.ceb").first()).toHaveText("Nia si Huwan dinhi.");
	expect(await overflowX(page)).toBe(0);

	// L3: Listen, move, back to Read and to Listen again: the position holds.
	await listen.tap();
	await expect(page.getByRole("tablist")).toHaveCount(0);
	await expect(clock).toHaveText("0:02");
	await page.getByTitle("Forward 10s").tap();
	await expect(clock).toHaveText("0:12");
	await read.tap();
	await expect(page.getByRole("tablist")).toBeVisible();
	await expect(clock).toHaveText("0:12");
	await listen.tap();
	await expect(clock).toHaveText("0:12");

	// R1: the side chosen is the one the lesson reopens on.
	await page.reload();
	await expect(listen).toHaveClass(/active/, { timeout: 15000 });
	await expect(page.getByRole("tablist")).toHaveCount(0);
});
