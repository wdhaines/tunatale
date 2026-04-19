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

export interface DayProgress { day: number; lesson_id: string; }

export type WordRating = 'hard' | 'easy' | 'again';

export interface WordToken {
	surface: string;
	lemma: string;
	srs_state: string;
	srs_item_id: number | null;
	translation: string | null;
	collocation_span_id: number | null;
	collocation_start: boolean;
	collocation_srs_state: string | null;
	collocation_lemma: string | null;
	collocation_translation: string | null;
}

export interface CreateSRSItemRequest {
	text: string;
	language_code: string;
	word_count: number;
	translation?: string;
}

export interface DialogueLine {
	role: string;
	words: WordToken[];
}

export interface TranscriptData {
	lesson_id: string;
	key_phrases: KeyPhrase[];
	dialogue_lines: DialogueLine[];
}

export interface ListenResponse {
	status: string;
	registered: number;
}

export interface SectionAudio {
	audio_id: string;
	section_index: number;
	section_type: string;
	title: string;
}

export interface LessonAudio {
	audio_id: string;
	lesson_id: string;
	sections: SectionAudio[];
}

export interface DirectionState {
	state: string;
	due_date: string;
	stability: number;
	difficulty: number;
	reps: number;
	lapses: number;
	last_review: string | null;
	anki_card_id: number | null;
}

export interface SRSItemDetail {
	id: number;
	text: string;
	translation: string;
	word_count?: number;
	state: 'new' | 'learning' | 'review' | 'relearning' | 'suspended' | 'known';
	due_date: string;
	stability: number;
	difficulty: number;
	reps: number;
	lapses: number;
	last_review: string | null;
	language_code: string;
	directions?: {
		recognition: DirectionState;
		production: DirectionState;
	};
	image_url?: string | null;
}

export interface SRSItemsPage {
	items: SRSItemDetail[];
	total: number;
}

export interface SRSListParams {
	search?: string;
	state?: SRSItemDetail['state'];
	sort?: 'text' | 'translation' | 'state' | 'due_date' | 'fsrs_difficulty' | 'reps' | 'lapses' | 'last_review';
	order?: 'asc' | 'desc';
	limit?: number;
	offset?: number;
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

	private async request<T>(path: string, init?: RequestInit): Promise<T> {
		const method = init?.method ?? 'GET';
		const res = init
			? await fetch(`${this.baseUrl}${path}`, init)
			: await fetch(`${this.baseUrl}${path}`);
		if (!res.ok) throw new Error(`${method} ${path}: ${res.statusText}`);
		return res.json();
	}

	async generateCurriculum(topic: string, cefrLevel = 'A2', numDays = 7): Promise<CurriculumSummary> {
		return this.request('/api/curriculum/generate', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ topic, cefr_level: cefrLevel, num_days: numDays })
		});
	}

	async listCurricula(): Promise<Array<{ id: string; topic: string; created_at: string }>> {
		return this.request('/api/curriculum');
	}

	async getCurriculum(id: string): Promise<CurriculumSummary> {
		return this.request(`/api/curriculum/${id}`);
	}

	async getCurriculumProgress(id: string): Promise<DayProgress[]> {
		return this.request(`/api/curriculum/${id}/progress`);
	}

	async getLessonByDay(curriculumId: string, day: number): Promise<LessonDetail> {
		return this.request(`/api/curriculum/${curriculumId}/days/${day}/lesson`);
	}

	async generateStory(curriculumId: string, day: number, strategy: ContentStrategy = 'WIDER'): Promise<LessonSummary> {
		return this.request('/api/story/generate', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ curriculum_id: curriculumId, day, strategy })
		});
	}

	async getLesson(lessonId: string): Promise<LessonDetail> {
		return this.request(`/api/story/${lessonId}`);
	}

	async renderAudio(lessonId: string): Promise<LessonAudio> {
		return this.request('/api/audio/render', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ lesson_id: lessonId })
		});
	}

	audioUrl(audioId: string): string {
		return `${this.baseUrl}/api/audio/${audioId}`;
	}

	audioZipUrl(lessonId: string): string {
		return `${this.baseUrl}/api/audio/lesson/${lessonId}/zip`;
	}

	async getLessonAudio(lessonId: string): Promise<LessonAudio> {
		return this.request(`/api/audio/lesson/${lessonId}`);
	}

	async getSRSDue(): Promise<SRSDue> {
		return this.request('/api/srs/due');
	}

	async getSRSNew(): Promise<SRSNew> {
		return this.request('/api/srs/new');
	}

	async fetchDue(direction: 'recognition' | 'production' | 'any'): Promise<SRSItemDetail[]> {
		const data = await this.request<{ due: SRSItemDetail[] }>(`/api/srs/due?direction=${direction}`);
		return data.due;
	}

	async submitDrill(
		itemId: number,
		direction: 'recognition' | 'production',
		rating: 'again' | 'hard' | 'good' | 'easy'
	): Promise<{ new_due_date: string; new_state: string }> {
		return this.request(`/api/srs/items/${itemId}/direction/${direction}/feedback`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ rating })
		});
	}

	async getSRSStats(): Promise<SRSStats> {
		return this.request('/api/srs/stats');
	}

	async postSRSFeedback(text: string, signal: FeedbackSignal): Promise<{ status: string; new_due_date?: string }> {
		return this.request('/api/srs/feedback', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ collocation_text: text, signal })
		});
	}

	async markAsListened(
		lessonId: string,
		wordRatings: Record<string, WordRating> = {}
	): Promise<ListenResponse> {
		return this.request('/api/srs/listen', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ lesson_id: lessonId, word_ratings: wordRatings })
		});
	}

	async getLessonTranscript(lessonId: string): Promise<TranscriptData> {
		return this.request(`/api/srs/lesson/${lessonId}/transcript`);
	}

	async createSRSItem(payload: CreateSRSItemRequest): Promise<SRSItemDetail> {
		return this.request('/api/srs/items', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(payload)
		});
	}

	async listSRSItems(params: SRSListParams = {}): Promise<SRSItemsPage> {
		const qs = new URLSearchParams();
		for (const [k, v] of Object.entries(params)) {
			if (v !== undefined) qs.set(k, String(v));
		}
		const query = qs.toString() ? `?${qs.toString()}` : '';
		return this.request(`/api/srs/items${query}`);
	}

	async updateSRSItem(id: number, fields: { text: string; translation: string }): Promise<SRSItemDetail> {
		return this.request(`/api/srs/items/${id}`, {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(fields)
		});
	}

	async deleteSRSItem(id: number): Promise<{ status: string }> {
		return this.request(`/api/srs/items/${id}`, { method: 'DELETE' });
	}

	async bulkDeleteSRSItems(ids: number[]): Promise<{ deleted: number }> {
		return this.request('/api/srs/items/bulk-delete', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ ids })
		});
	}

	async resetSRSItem(id: number): Promise<SRSItemDetail> {
		return this.request(`/api/srs/items/${id}/reset`, { method: 'POST' });
	}

	async suspendSRSItem(id: number, suspended: boolean): Promise<SRSItemDetail> {
		return this.request(`/api/srs/items/${id}/suspend`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ suspended })
		});
	}

	async setSRSItemState(id: number, state: string): Promise<SRSItemDetail> {
		return this.request(`/api/srs/items/${id}/state`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ state })
		});
	}
}

export const api = new TunaTaleAPI();
