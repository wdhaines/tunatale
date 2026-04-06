/**
 * TunaTale API client — wraps backend endpoints.
 */

export const BASE_URL = typeof window !== 'undefined' ? '' : 'http://localhost:8000';

export interface CurriculumDay {
	day: number;
	title: string;
	focus: string;
	learning_objective: string;
	story_guidance: string;
	collocations: string[];
}

export interface CurriculumSummary {
	id: string;
	topic: string;
	language_code: string;
	days: number;
}

export interface SectionSummary {
	type: string;
	phrase_count: number;
}

export interface LessonSummary {
	id: string;
	title: string;
	sections: SectionSummary[];
}

export type ContentStrategy = 'WIDER' | 'DEEPER';
export type FeedbackSignal = 'no_help' | 'slowdown' | 'translation_request' | 'fast_forward';

export interface PhraseDetail {
	text: string;
	role: string;
	language_code: string;
	voice_id: string;
}

export interface SectionDetail {
	type: string;
	phrases: PhraseDetail[];
}

export interface KeyPhrase {
	phrase: string;
	translation: string;
}

export interface LessonDetail {
	id: string;
	title: string;
	language_code: string;
	sections: SectionDetail[];
	key_phrases: KeyPhrase[];
}

export interface ListenResponse {
	status: string;
	registered: number;
}

export interface SRSDue {
	due: Array<{ text: string; translation: string }>;
}

export interface SRSNew {
	new: Array<{ text: string; translation: string }>;
}

export interface SRSStats {
	total: number;
	due_today: number;
}

export class TunaTaleAPI {
	private baseUrl: string;

	constructor(baseUrl: string = BASE_URL) {
		this.baseUrl = baseUrl;
	}

	async generateCurriculum(topic: string, cefrLevel = 'A2', numDays = 7): Promise<CurriculumSummary> {
		const res = await fetch(`${this.baseUrl}/api/curriculum/generate`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ topic, cefr_level: cefrLevel, num_days: numDays })
		});
		if (!res.ok) throw new Error(`Failed to generate curriculum: ${res.statusText}`);
		return res.json();
	}

	async listCurricula(): Promise<Array<{ id: string; topic: string }>> {
		const res = await fetch(`${this.baseUrl}/api/curriculum`);
		if (!res.ok) throw new Error(`Failed to list curricula: ${res.statusText}`);
		return res.json();
	}

	async getCurriculum(id: string): Promise<CurriculumSummary> {
		const res = await fetch(`${this.baseUrl}/api/curriculum/${id}`);
		if (!res.ok) throw new Error(`Curriculum not found: ${id}`);
		return res.json();
	}

	async generateStory(curriculumId: string, day: number, strategy: ContentStrategy = 'WIDER'): Promise<LessonSummary> {
		const res = await fetch(`${this.baseUrl}/api/story/generate`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ curriculum_id: curriculumId, day, strategy })
		});
		if (!res.ok) throw new Error(`Failed to generate story: ${res.statusText}`);
		return res.json();
	}

	async getLesson(lessonId: string): Promise<LessonDetail> {
		const res = await fetch(`${this.baseUrl}/api/story/${lessonId}`);
		if (!res.ok) throw new Error(`Lesson not found: ${lessonId}`);
		return res.json();
	}

	async renderAudio(lessonId: string): Promise<{ audio_id: string; lesson_id: string }> {
		const res = await fetch(`${this.baseUrl}/api/audio/render`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ lesson_id: lessonId })
		});
		if (!res.ok) throw new Error(`Failed to render audio: ${res.statusText}`);
		return res.json();
	}

	audioUrl(audioId: string): string {
		return `${this.baseUrl}/api/audio/${audioId}`;
	}

	async getSRSDue(): Promise<SRSDue> {
		const res = await fetch(`${this.baseUrl}/api/srs/due`);
		if (!res.ok) throw new Error('Failed to get due collocations');
		return res.json();
	}

	async getSRSNew(): Promise<SRSNew> {
		const res = await fetch(`${this.baseUrl}/api/srs/new`);
		if (!res.ok) throw new Error('Failed to get new collocations');
		return res.json();
	}

	async getSRSStats(): Promise<SRSStats> {
		const res = await fetch(`${this.baseUrl}/api/srs/stats`);
		if (!res.ok) throw new Error('Failed to get SRS stats');
		return res.json();
	}

	async postSRSFeedback(text: string, signal: FeedbackSignal): Promise<{ status: string; new_due_date?: string }> {
		const res = await fetch(`${this.baseUrl}/api/srs/feedback`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ collocation_text: text, signal })
		});
		if (!res.ok) throw new Error('Failed to record feedback');
		return res.json();
	}

	async markAsListened(lessonId: string): Promise<ListenResponse> {
		const res = await fetch(`${this.baseUrl}/api/srs/listen`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ lesson_id: lessonId })
		});
		if (!res.ok) throw new Error('Failed to mark lesson as listened');
		return res.json();
	}
}

export const api = new TunaTaleAPI();
