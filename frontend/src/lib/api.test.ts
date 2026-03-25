/**
 * TunaTaleAPI client unit tests.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TunaTaleAPI } from './api';

const BASE = 'http://test-backend';

describe('TunaTaleAPI', () => {
	let api: TunaTaleAPI;

	beforeEach(() => {
		api = new TunaTaleAPI(BASE);
		vi.restoreAllMocks();
	});

	it('generateCurriculum calls POST /api/curriculum/generate', async () => {
		const mockResponse = { id: 'abc', topic: 'coffee', language_code: 'sl', days: 3 };
		global.fetch = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => mockResponse
		} as Response);

		const result = await api.generateCurriculum('coffee', 'A2', 3);

		expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum/generate`, expect.objectContaining({ method: 'POST' }));
		expect(result.id).toBe('abc');
		expect(result.topic).toBe('coffee');
	});

	it('generateCurriculum throws on non-ok response', async () => {
		global.fetch = vi.fn().mockResolvedValue({
			ok: false,
			statusText: 'Internal Server Error'
		} as Response);

		await expect(api.generateCurriculum('coffee')).rejects.toThrow('Failed to generate curriculum');
	});

	it('listCurricula calls GET /api/curriculum', async () => {
		global.fetch = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => [{ id: '1', topic: 'coffee' }]
		} as Response);

		const result = await api.listCurricula();

		expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum`);
		expect(result).toHaveLength(1);
	});

	it('getCurriculum calls GET /api/curriculum/:id', async () => {
		global.fetch = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ id: 'abc', topic: 'coffee', language_code: 'sl', days: 3 })
		} as Response);

		const result = await api.getCurriculum('abc');

		expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum/abc`);
		expect(result.id).toBe('abc');
	});

	it('getCurriculum throws on 404', async () => {
		global.fetch = vi.fn().mockResolvedValue({
			ok: false,
			statusText: 'Not Found'
		} as Response);

		await expect(api.getCurriculum('missing')).rejects.toThrow('Curriculum not found');
	});

	it('generateStory calls POST /api/story/generate', async () => {
		const mockLesson = { id: 'l1', title: 'Day 1', sections: [] };
		global.fetch = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => mockLesson
		} as Response);

		const result = await api.generateStory('abc', 1, 'WIDER');

		expect(fetch).toHaveBeenCalledWith(
			`${BASE}/api/story/generate`,
			expect.objectContaining({ method: 'POST' })
		);
		expect(result.id).toBe('l1');
	});

	it('renderAudio calls POST /api/audio/render', async () => {
		global.fetch = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ audio_id: 'audio-1', lesson_id: 'l1' })
		} as Response);

		const result = await api.renderAudio('l1');

		expect(fetch).toHaveBeenCalledWith(`${BASE}/api/audio/render`, expect.objectContaining({ method: 'POST' }));
		expect(result.audio_id).toBe('audio-1');
	});

	it('audioUrl returns correct URL', () => {
		const url = api.audioUrl('audio-1');
		expect(url).toBe(`${BASE}/api/audio/audio-1`);
	});

	it('getSRSDue calls GET /api/srs/due', async () => {
		global.fetch = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ due: [] })
		} as Response);

		const result = await api.getSRSDue();

		expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/due`);
		expect(result.due).toEqual([]);
	});

	it('getSRSStats calls GET /api/srs/stats', async () => {
		global.fetch = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ total: 10, due_today: 3 })
		} as Response);

		const result = await api.getSRSStats();

		expect(result.total).toBe(10);
		expect(result.due_today).toBe(3);
	});
});
