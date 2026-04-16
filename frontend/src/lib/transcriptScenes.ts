import type { DialogueLine, LessonDetail, WordToken } from './api';

export interface UnifiedLine {
	role: string;
	words: WordToken[];
	naturalText: string;
	slowText: string;
	translatedText: string;
	transcriptIndex: number;
}

export interface Scene {
	title: string | null;
	lines: UnifiedLine[];
}

const SECTION_TITLES = new Set(['Natural Speed', 'Slow Speed', 'Translated', 'Key Phrases']);

function extractL2Texts(phrases: LessonDetail['sections'][number]['phrases'], languageCode: string): string[] {
	return phrases.filter((p) => p.language_code === languageCode).map((p) => p.text);
}

function extractTranslations(
	phrases: LessonDetail['sections'][number]['phrases'],
	languageCode: string
): string[] {
	const out: string[] = [];
	let awaiting = false;
	for (const p of phrases) {
		if (p.language_code === languageCode) {
			if (awaiting) out.push('');
			awaiting = true;
		} else if (p.role === 'narrator' && awaiting && !SECTION_TITLES.has(p.text)) {
			out.push(p.text);
			awaiting = false;
		}
	}
	if (awaiting) out.push('');
	return out;
}

export function buildScenes(lesson: LessonDetail, dialogueLines: DialogueLine[]): Scene[] {
	const languageCode = lesson.language_code;
	const natural = lesson.sections.find((s) => s.type === 'natural_speed');
	if (!natural) return [];

	const slow = lesson.sections.find((s) => s.type === 'slow_speed');
	const translated = lesson.sections.find((s) => s.type === 'translated');

	const slowTexts = slow ? extractL2Texts(slow.phrases, languageCode) : [];
	const translatedTexts = translated ? extractTranslations(translated.phrases, languageCode) : [];

	const scenes: Scene[] = [];
	let currentScene: Scene = { title: null, lines: [] };
	let lineIndex = 0;

	for (const p of natural.phrases) {
		const isNarratorL1 = p.language_code !== languageCode && p.role === 'narrator';
		if (isNarratorL1) {
			if (SECTION_TITLES.has(p.text)) continue;
			if (currentScene.lines.length > 0 || currentScene.title !== null) {
				scenes.push(currentScene);
			}
			currentScene = { title: p.text, lines: [] };
		} else if (p.language_code === languageCode) {
			currentScene.lines.push({
				role: p.role,
				words: dialogueLines[lineIndex]?.words ?? [],
				naturalText: p.text,
				slowText: slowTexts[lineIndex] ?? '',
				translatedText: translatedTexts[lineIndex] ?? '',
				transcriptIndex: lineIndex
			});
			lineIndex++;
		}
	}
	if (currentScene.lines.length > 0 || currentScene.title !== null) {
		scenes.push(currentScene);
	}
	return scenes;
}

export function fallbackScenes(dialogueLines: DialogueLine[]): Scene[] {
	return [
		{
			title: null,
			lines: dialogueLines.map((dl, idx) => ({
				role: dl.role,
				words: dl.words,
				naturalText: '',
				slowText: '',
				translatedText: '',
				transcriptIndex: idx
			}))
		}
	];
}
