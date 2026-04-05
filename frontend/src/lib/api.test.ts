/**
 * TunaTaleAPI client unit tests.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { TunaTaleAPI } from './api';

const BASE = 'http://test-backend';

function mockOk(json: unknown): Response {
	return { ok: true, json: async () => json } as Response;
}

function mockFail(statusText = 'Internal Server Error'): Response {
	return { ok: false, statusText } as Response;
}

describe('TunaTaleAPI', () => {
	let api: TunaTaleAPI;

	beforeEach(() => {
		api = new TunaTaleAPI(BASE);
		vi.restoreAllMocks();
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	describe('curriculum', () => {
		it('generateCurriculum calls POST /api/curriculum/generate', async () => {
			vi.stubGlobal(
				'fetch',
				vi.fn().mockResolvedValue(mockOk({ id: 'abc', topic: 'coffee', language_code: 'sl', days: 3 }))
			);

			const result = await api.generateCurriculum('coffee', 'A2', 3);

			expect(fetch).toHaveBeenCalledWith(
				`${BASE}/api/curriculum/generate`,
				expect.objectContaining({ method: 'POST' })
			);
			expect(result.id).toBe('abc');
			expect(result.topic).toBe('coffee');
		});

		it('generateCurriculum throws on non-ok response', async () => {
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockFail()));

			await expect(api.generateCurriculum('coffee')).rejects.toThrow('Failed to generate curriculum');
		});

		it('listCurricula calls GET /api/curriculum', async () => {
			vi.stubGlobal(
				'fetch',
				vi.fn().mockResolvedValue(mockOk([{ id: '1', topic: 'coffee' }]))
			);

			const result = await api.listCurricula();

			expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum`);
			expect(result).toHaveLength(1);
		});

		it('getCurriculum calls GET /api/curriculum/:id', async () => {
			vi.stubGlobal(
				'fetch',
				vi.fn().mockResolvedValue(mockOk({ id: 'abc', topic: 'coffee', language_code: 'sl', days: 3 }))
			);

			const result = await api.getCurriculum('abc');

			expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum/abc`);
			expect(result.id).toBe('abc');
		});

		it('getCurriculum throws on 404', async () => {
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockFail('Not Found')));

			await expect(api.getCurriculum('missing')).rejects.toThrow('Curriculum not found');
		});
	});

	describe('story', () => {
		it('generateStory calls POST /api/story/generate', async () => {
			vi.stubGlobal(
				'fetch',
				vi.fn().mockResolvedValue(mockOk({ id: 'l1', title: 'Day 1', sections: [] }))
			);

			const result = await api.generateStory('abc', 1, 'WIDER');

			expect(fetch).toHaveBeenCalledWith(
				`${BASE}/api/story/generate`,
				expect.objectContaining({ method: 'POST' })
			);
			expect(result.id).toBe('l1');
		});

		it('getLesson calls GET /api/story/:id', async () => {
			const mockDetail = {
				id: 'l1',
				title: 'Day 1',
				language_code: 'sl',
				sections: [
					{
						type: 'key_phrases',
						phrases: [
							{
								text: 'dober dan',
								role: 'female-1',
								language_code: 'sl',
								voice_id: 'sl-SI-PetraNeural'
							}
						]
					}
				]
			};
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockOk(mockDetail)));

			const result = await api.getLesson('l1');

			expect(fetch).toHaveBeenCalledWith(`${BASE}/api/story/l1`);
			expect(result.id).toBe('l1');
			expect(result.sections[0].phrases[0].text).toBe('dober dan');
		});

		it('getLesson throws on 404', async () => {
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockFail('Not Found')));

			await expect(api.getLesson('missing')).rejects.toThrow('Lesson not found');
		});
	});

	describe('audio', () => {
		it('renderAudio calls POST /api/audio/render', async () => {
			vi.stubGlobal(
				'fetch',
				vi.fn().mockResolvedValue(mockOk({ audio_id: 'audio-1', lesson_id: 'l1' }))
			);

			const result = await api.renderAudio('l1');

			expect(fetch).toHaveBeenCalledWith(
				`${BASE}/api/audio/render`,
				expect.objectContaining({ method: 'POST' })
			);
			expect(result.audio_id).toBe('audio-1');
		});

		it('audioUrl returns correct URL', () => {
			const url = api.audioUrl('audio-1');
			expect(url).toBe(`${BASE}/api/audio/audio-1`);
		});
	});

	describe('SRS', () => {
		it('getSRSDue calls GET /api/srs/due', async () => {
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockOk({ due: [] })));

			const result = await api.getSRSDue();

			expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/due`);
			expect(result.due).toEqual([]);
		});

		it('getSRSStats calls GET /api/srs/stats', async () => {
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockOk({ total: 10, due_today: 3 })));

			const result = await api.getSRSStats();

			expect(result.total).toBe(10);
			expect(result.due_today).toBe(3);
		});

		it('postSRSFeedback calls POST /api/srs/feedback with correct body', async () => {
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockOk({ status: 'ok' })));

			const result = await api.postSRSFeedback('dober dan', 'no_help');

			expect(fetch).toHaveBeenCalledWith(
				`${BASE}/api/srs/feedback`,
				expect.objectContaining({
					method: 'POST',
					body: JSON.stringify({ collocation_text: 'dober dan', signal: 'no_help' })
				})
			);
			expect(result.status).toBe('ok');
		});

		it('postSRSFeedback throws on non-ok response', async () => {
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockFail()));

			await expect(api.postSRSFeedback('dober dan', 'no_help')).rejects.toThrow(
				'Failed to record feedback'
			);
		});

		it('getSRSNew calls GET /api/srs/new', async () => {
			const mockResponse = { new: [{ text: 'dober dan', translation: 'good day' }] };
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockOk(mockResponse)));

			const result = await api.getSRSNew();

			expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/new`);
			expect(result.new).toEqual(mockResponse.new);
		});

		it('getSRSNew throws on non-ok response', async () => {
			vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockFail()));

			await expect(api.getSRSNew()).rejects.toThrow('Failed to get new collocations');
		});
	});
});
