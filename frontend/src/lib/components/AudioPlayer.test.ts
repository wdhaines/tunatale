/**
 * Tests for AudioPlayer.svelte.
 */
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import AudioPlayer from './AudioPlayer.svelte';
import type { LessonAudio } from '$lib/api';

vi.mock('$lib/api', () => ({
	api: {
		audioUrl: vi.fn((id: string) => `/api/audio/${id}`)
	}
}));

const audioWithNoSections: LessonAudio = { audio_id: 'a1', lesson_id: 'l1', sections: [] };
const audioWithSections: LessonAudio = {
	audio_id: 'a1',
	lesson_id: 'l1',
	sections: [
		{ audio_id: 's1', section_index: 0, section_type: 'key_phrases', title: 'Key Phrases' },
		{ audio_id: 's2', section_index: 1, section_type: 'natural_speed', title: 'Natural Speed' }
	]
};

describe('AudioPlayer', () => {
	it('renders the audio heading', () => {
		const { getByText } = render(AudioPlayer, { props: { audio: audioWithNoSections } });
		expect(getByText('Audio Player')).toBeTruthy();
	});

	it('renders an audio element with the correct src', () => {
		const { container } = render(AudioPlayer, { props: { audio: audioWithNoSections } });
		const audioEl = container.querySelector('audio');
		expect(audioEl).toBeTruthy();
		expect(audioEl!.src).toContain('/api/audio/a1');
	});

	it('does not render Download Sections when sections is empty', () => {
		const { queryByText } = render(AudioPlayer, { props: { audio: audioWithNoSections } });
		expect(queryByText('Download Sections')).toBeFalsy();
	});

	it('renders Download Sections when sections are present', () => {
		const { getByText } = render(AudioPlayer, { props: { audio: audioWithSections } });
		expect(getByText('Download Sections')).toBeTruthy();
	});

	it('renders one download link per section', () => {
		const { getByText, getAllByRole } = render(AudioPlayer, { props: { audio: audioWithSections } });
		expect(getByText('Key Phrases')).toBeTruthy();
		expect(getByText('Natural Speed')).toBeTruthy();
		const links = getAllByRole('link');
		expect(links.length).toBe(2);
	});

	it('section download links use the correct audioUrl', () => {
		const { getAllByRole } = render(AudioPlayer, { props: { audio: audioWithSections } });
		const links = getAllByRole('link') as HTMLAnchorElement[];
		expect(links[0].href).toContain('/api/audio/s1');
		expect(links[1].href).toContain('/api/audio/s2');
	});
});
