<script lang="ts">
	import { untrack } from 'svelte';
	import { api } from '$lib/api';
	import type { LessonAudio } from '$lib/api';
	import LessonPlayer from '$lib/components/LessonPlayer.svelte';

	// The reader for a review session. It has no curriculum and no day, so there
	// is no day navigation, no Regenerate, and no position in a sequence to show
	// — which is most of what the lesson page's length is (bd tunatale-9p9d).
	let { data } = $props();

	// The initial read is deliberate: `audio` is this page's own state from here
	// on (Prepare audio replaces it), so it snapshots the loaded value rather
	// than tracking it. untrack marks that as intended — the same pattern
	// LessonPlayer uses for its own props.
	let audio: LessonAudio | null = $state(untrack(() => data.audio));
	let preparing = $state(false);
	let renderError = $state('');

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
	 * `new Date('2026-09-02')` is UTC midnight and renders as 1 September in
	 * every negative-offset timezone. session_date is a calendar date, not an
	 * instant. Same reasoning as the list on the index.
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

	// The dialogue, which is the whole point of reading one: with no theme, the
	// only thing left to judge is whether it is a scene real people could have.
	const dialogue = $derived(
		data.session.sections.find(
			(s: { type: string }) => s.type === 'natural_speed'
		)?.phrases ?? []
	);

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
	<a class="back" href="/">← Lessons</a>

	<header>
		<p class="date">{formatSessionDate(data.session.session_date)}</p>
		<h1>{data.session.title}</h1>
		{#if coverage}
			<p class="coverage">{coverage} words you were forgetting</p>
		{/if}
		<p class="muted">
			A review session — built from what has decayed across your whole deck, with no theme and
			no place in any curriculum.
		</p>
	</header>

	{#if audio}
		<div data-testid="session-player">
			{#key audio.audio_id}
				<LessonPlayer {audio} lessonTitle={data.session.title} />
			{/key}
		</div>
	{:else}
		<div class="prepare">
			<p class="muted">No audio yet for this session.</p>
			<button type="button" onclick={prepareAudio} disabled={preparing}>
				{preparing ? 'Preparing…' : 'Prepare audio'}
			</button>
			{#if renderError}
				<p class="error" role="alert">{renderError}</p>
			{/if}
		</div>
	{/if}

	<section class="transcript">
		<h2>The scene</h2>
		<ol>
			{#each dialogue as line, i (i)}
				<li><span class="speaker">{line.role}</span><span class="text">{line.text}</span></li>
			{/each}
		</ol>
	</section>
</main>

<style>
	main {
		max-width: 760px;
		margin: 1rem auto;
		padding: 0 1rem 4rem;
	}
	.back {
		display: inline-block;
		margin-bottom: 1rem;
		font-size: 0.9rem;
	}
	header {
		margin-bottom: 1.5rem;
	}
	.date {
		margin: 0;
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		opacity: 0.7;
	}
	h1 {
		margin: 0.15rem 0 0.4rem;
		font-size: 1.6rem;
		text-wrap: balance;
	}
	.coverage {
		margin: 0 0 0.4rem;
		font-size: 0.92rem;
	}
	.muted {
		opacity: 0.72;
		font-size: 0.9rem;
		margin: 0;
		max-width: 52ch;
	}
	.prepare {
		border: 1px solid var(--border, #ddd);
		border-radius: 6px;
		padding: 1rem;
		margin-bottom: 1.5rem;
	}
	.prepare p {
		margin: 0 0 0.6rem;
	}
	.error {
		color: #9c2f2a;
		margin: 0.6rem 0 0;
		font-size: 0.9rem;
	}
	.transcript {
		margin-top: 2rem;
	}
	.transcript h2 {
		font-size: 1.1rem;
		margin: 0 0 0.75rem;
	}
	.transcript ol {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.transcript li {
		display: grid;
		grid-template-columns: 6.5rem 1fr;
		gap: 0.75rem;
		align-items: baseline;
	}
	.speaker {
		font-size: 0.78rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		opacity: 0.6;
	}
	.text {
		min-width: 0;
	}
</style>
