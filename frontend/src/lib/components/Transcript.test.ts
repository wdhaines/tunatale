/**
 * Tests for Transcript.svelte component.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import Transcript from './Transcript.svelte';
import type { TranscriptData } from '$lib/api';

const baseTranscript: TranscriptData = {
	lesson_id: 'l1',
	key_phrases: [],
	dialogue_lines: []
};

const transcriptWithPhrases: TranscriptData = {
	lesson_id: 'l1',
	key_phrases: [
		{ phrase: 'dober dan', translation: 'good day' },
		{ phrase: 'hvala', translation: 'thank you' }
	],
	dialogue_lines: []
};

const transcriptWithDialogue: TranscriptData = {
	lesson_id: 'l1',
	key_phrases: [],
	dialogue_lines: [
		{
			role: 'Petra',
			words: [
				{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'new' }
			]
		}
	]
};

function defaultProps(overrides = {}) {
	return {
		transcript: baseTranscript,
		pendingRatings: {},
		isListened: false,
		listenLoading: false,
		listenResult: null,
		error: '',
		onRatingChange: vi.fn(),
		onMarkListened: vi.fn(),
		...overrides
	};
}

describe('Transcript', () => {
	it('renders Mark as Listened button', () => {
		const { getByText } = render(Transcript, { props: defaultProps() });
		expect(getByText('Mark as Listened')).toBeTruthy();
	});

	it('shows ✓ Listened when isListened is true', () => {
		const { getByText } = render(Transcript, { props: defaultProps({ isListened: true }) });
		expect(getByText('✓ Listened')).toBeTruthy();
	});

	it('shows Registering… when listenLoading is true', () => {
		const { getByText } = render(Transcript, { props: defaultProps({ listenLoading: true }) });
		expect(getByText('Registering…')).toBeTruthy();
	});

	it('calls onMarkListened when button is clicked', async () => {
		const onMarkListened = vi.fn();
		const { getByText } = render(Transcript, { props: defaultProps({ onMarkListened }) });
		await fireEvent.click(getByText('Mark as Listened'));
		expect(onMarkListened).toHaveBeenCalled();
	});

	it('renders key phrases when present', () => {
		const { getByText } = render(Transcript, {
			props: defaultProps({ transcript: transcriptWithPhrases })
		});
		expect(getByText('Key Phrases')).toBeTruthy();
		expect(getByText('dober dan')).toBeTruthy();
		expect(getByText('good day')).toBeTruthy();
	});

	it('does not render Key Phrases section when empty', () => {
		const { queryByText } = render(Transcript, { props: defaultProps() });
		expect(queryByText('Key Phrases')).toBeFalsy();
	});

	it('renders dialogue lines when present', () => {
		const { getByText } = render(Transcript, {
			props: defaultProps({ transcript: transcriptWithDialogue })
		});
		expect(getByText('Dialogue')).toBeTruthy();
		expect(getByText('Petra')).toBeTruthy();
	});

	it('does not render Dialogue section when empty', () => {
		const { queryByText } = render(Transcript, { props: defaultProps() });
		expect(queryByText('Dialogue')).toBeFalsy();
	});

	it('shows listen confirmation when listenResult is set and no error', () => {
		const { getByText } = render(Transcript, {
			props: defaultProps({ listenResult: { registered: 3 }, error: '' })
		});
		expect(getByText(/3.*words tracked/i)).toBeTruthy();
	});

	it('shows singular word when registered is 1', () => {
		const { getByText } = render(Transcript, {
			props: defaultProps({ listenResult: { registered: 1 }, error: '' })
		});
		expect(getByText(/1 word tracked/i)).toBeTruthy();
	});

	it('hides listen confirmation when error is set', () => {
		const { queryByText } = render(Transcript, {
			props: defaultProps({ listenResult: { registered: 3 }, error: 'something went wrong' })
		});
		expect(queryByText(/words tracked/i)).toBeFalsy();
	});

	it('shows listen confirmation after listenResult changes from null to non-null (reactive update)', async () => {
		const { rerender, findByText, queryByText } = render(Transcript, {
			props: defaultProps({ listenResult: null })
		});
		expect(queryByText(/words tracked/i)).toBeFalsy();

		await rerender(defaultProps({ listenResult: { registered: 2 }, error: '' }));

		await waitFor(() => {
			expect(queryByText(/2.*words tracked/i)).toBeTruthy();
		});
	});

	it('shows singular word after listenResult changes to registered=1', async () => {
		const { rerender, findByText } = render(Transcript, {
			props: defaultProps({ listenResult: null })
		});

		await rerender(defaultProps({ listenResult: { registered: 1 }, error: '' }));

		expect(await findByText(/1 word tracked/i)).toBeTruthy();
	});
});
