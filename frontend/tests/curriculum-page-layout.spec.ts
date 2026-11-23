import { test, expect } from './fixtures';
import { BACKEND, backendAvailable } from './helpers';

/**
 * Layout seam for the curriculum page (tunatale-cs3p slice 2, oracle C5): at
 * phone widths and a larger root font the page must not scroll sideways, and
 * no row title may be clipped, with a row opened to its outline.
 *
 * Browser-computed geometry (jsdom reports every rect as 0x0), so it lives
 * here. The curriculum is real (the import route, grouped as the Wolff plan
 * is); the outline is mocked so the spec can pin the worst realistic content
 * (a long drill sentence) without rendering a practice lesson.
 */

const LONG_TITLE = 'Here, There, Over There, and Somewhere Much Further Away (Wolff 2, practice 2)';
const LONG_MODEL = 'Ibilin ko kining akong amigo dinhi samtang moadto ko sa merkado ugma sa buntag.';

function day(n: number, lesson: number, order: number, title: string) {
	return {
		day: n,
		title,
		focus: 'f',
		collocations: ['dober dan'],
		learning_objective: 'o',
		story_guidance: '',
		group: `wolff-${lesson}`,
		group_title: `Lesson ${lesson}`,
		order,
	};
}

const SIZES = [
	{ width: 360, font: 16 },
	{ width: 360, font: 20 },
	{ width: 412, font: 16 },
	{ width: 412, font: 20 },
];

for (const size of SIZES) {
	test(`curriculum page fits the phone @ ${size.width}px, root ${size.font}px`, async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), 'Backend not available');
		const res = await request.post(`${BACKEND}/api/curriculum/import`, {
			data: {
				topic: `e2e curriculum layout ${size.width} ${size.font}`,
				language_code: 'sl',
				cefr_level: 'A2',
				days: [day(1, 1, 1, 'Visiting Mr. Abaya (Wolff 1, part 1)'), day(2, 2, 3, LONG_TITLE), day(3, 1, 2, 'Who Is Here (Wolff 1, practice 1)')],
			},
		});
		expect(res.ok(), await res.text()).toBe(true);
		const { id } = await res.json();
		await page.route('**/api/curriculum/*/outline', (route) =>
			route.fulfill({
				json: [
					{ day: 2, parts: ['key_phrases', 'dialogue', 'drill'], drills: [{ kind: "substitution", model: LONG_MODEL, instruction: "", steps: 16 }] },
				],
			}),
		);

		await page.setViewportSize({ width: size.width, height: 800 });
		await page.addInitScript((f) => {
			document.addEventListener('DOMContentLoaded', () => {
				document.documentElement.style.fontSize = `${f}px`;
			});
		}, size.font);
		await page.goto(`/c/${id}`);
		await page.getByRole('button', { name: `What's in ${LONG_TITLE}` }).click();
		await expect(page.getByText(LONG_MODEL, { exact: false })).toBeVisible();

		const r = await page.evaluate(() => {
			const root = document.documentElement;
			const clipped = [...document.querySelectorAll('.row .t b, .outline span, .next-up b')]
				.filter((el) => el.scrollWidth > el.clientWidth)
				.map((el) => el.textContent);
			return { overflow: root.scrollWidth - root.clientWidth, clipped, font: getComputedStyle(root).fontSize };
		});
		expect(r.font, 'the root font was not applied').toBe(`${size.font}px`);
		expect(r.overflow, 'the page scrolls sideways').toBe(0);
		expect(r.clipped, 'a title is clipped').toEqual([]);
	});
}
