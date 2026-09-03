<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api } from '$lib/api';
	import type { LessonAudio, TranscriptData } from '$lib/api';
	import LessonReader from '$lib/components/LessonReader.svelte';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';
	import { createReadingActions } from '$lib/reading/readingActions.svelte';


	// The reader for a review session.
	//
	// ⚠️ IT RENDERS THROUGH THE SAME COMPONENTS AS A LESSON — LessonPlayer,
	// Transcript, TranscriptPlaceholder — and that is the point. An earlier
	// version hand-rolled its own list of dialogue lines because the transcript
	// endpoint looked a session up in the lessons table and missed. A lookup is
	// not a reason to fork a UI: the second one was worse immediately (it opened
	// the scene with the narrator's "Natural Speed" section header) and would
	// have drifted further every time either changed. The endpoint now has a
	// session-shaped twin and this page reuses everything.
	//
	// What it does NOT have is the lesson page's day navigation, Regenerate or
	// delete-day — a session has no day for those to act on.
	let { data } = $props();

	let audio: LessonAudio | null = $state(untrack(() => data.audio));
	let transcript: TranscriptData | null = $state(null);
	let transcriptLoading = $state(true);
	let preparing = $state(false);
	let renderError = $state('');
	let playbackController: PlaybackController | null = $state(null);
	let error = $state('');

	// ⚠️ THE SAME ACTIONS THE LESSON PAGE USES, from one implementation. Tapping
	// a word grades it, the popovers create cards and cloze inflections, undo
	// works — none of it re-implemented here. Only the SOURCE differs, which is
	// the whole claim of this page.
	const reading = createReadingActions({
		get contentId() {
			return data.session.id;
		},
		get languageCode() {
			return data.session.language_code;
		},
		getTranscript: () => transcript,
		setTranscript: (t) => (transcript = t),
		setError: (m) => (error = m)
	});

	const MONTHS = [
		'January',
		'February',
		'March',
		'April',
		'May',
		'June',
		'July',
		'August',
		'September',
		'October',
		'November',
		'December'
	];

	/**
	 * ⚠️ Formatted from the ISO parts, never through `new Date()`.
	 * `new Date('2026-09-02')` is UTC midnight and renders as the previous day in
	 * every negative-offset timezone. session_date is a calendar date, not an
	 * instant.
	 */
	function formatSessionDate(iso: string): string {
		const [, month, day] = iso.split('-').map(Number);
		return `${day} ${MONTHS[month - 1]}`;
	}

	// Empty means UNMEASURABLE, not zero: no line at all rather than "reused 0 of
	// 0", which would read as a grade instead of an observation.
	const coverage = $derived(
		data.session.review_requested.length > 0
			? `reused ${data.session.review_used.length} of ${data.session.review_requested.length}`
			: null
	);

	onMount(async () => {
		// Client-side, not in `load`: the transcript runs the lemmatizer and can
		// take seconds on a cold backend. The lesson page does the same, for the
		// same reason — the shell renders at once and the words arrive after.
		try {
			transcript = await api.getTranscript(data.session.id);
		} catch {
			transcript = null;
		} finally {
			transcriptLoading = false;
		}
	});

	async function prepareAudio() {
		preparing = true;
		renderError = '';
		try {
			await api.renderReviewSession(data.session.id);
			audio = await api.getLessonAudio(data.session.id);
		} catch (e) {
			renderError = e instanceof Error ? e.message : String(e);
		} finally {
			preparing = false;
		}
	}
</script>

<main>
	<LessonReader
		title={data.session.title}
		content={data.session}
		{audio}
		{transcript}
		{transcriptLoading}
		{reading}
		bind:controller={playbackController}
	>
		{#snippet headerAbove()}
			<a class="back" href="/">← Lessons</a>
		{/snippet}
		{#snippet header()}
			<div class="title-area">
				<p class="date">{formatSessionDate(data.session.session_date)}</p>
				<h1>{data.session.title}</h1>
				{#if error}
					<p class="error" role="alert">{error}</p>
				{/if}
			</div>
		{/snippet}
		{#snippet headerBelow()}
			{#if coverage}
				<p class="coverage">{coverage} words you were forgetting</p>
			{/if}
			<p class="muted">
				A review session — built from what has decayed across your whole deck, with no theme
				and no place in any curriculum.
			</p>
		{/snippet}
		{#snippet noAudio()}
			<div class="prepare">
				<button type="button" onclick={prepareAudio} disabled={preparing}>
					{preparing ? 'Preparing…' : 'Prepare audio'}
				</button>
				{#if renderError}
					<p class="error" role="alert">{renderError}</p>
				{/if}
			</div>
		{/snippet}
	</LessonReader>
</main>

<style>
	/* Only what THIS page's snippets need. The sticky card, the header grid, the
	   player, the mode gate and the transcript block all live in LessonReader —
	   shared with the lesson page so the two cannot drift again. */
	.back {
		display: inline-block;
		color: var(--color-muted);
		font-size: 0.9rem;
		font-weight: 600;
		text-decoration: none;
	}
	.back:hover {
		color: var(--color-primary);
	}
	.title-area {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		min-width: 0;
	}
	.date {
		margin: 0;
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		color: var(--color-muted);
		font-size: 0.9rem;
	}
	h1 {
		margin: 0;
		font-size: 1.35rem;
		text-wrap: balance;
	}
	.coverage {
		margin: 0;
		font-size: 0.9rem;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.85rem;
		margin: 0.15rem 0 0;
		max-width: 52ch;
	}
	.prepare {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: center;
		gap: 0.5rem 0.9rem;
	}
	.error {
		color: var(--color-danger, #9c2f2a);
		margin: 0;
		font-size: 0.9rem;
	}
</style>
