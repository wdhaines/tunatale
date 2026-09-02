<script lang="ts">
	import { untrack } from 'svelte';
	import { api } from '$lib/api';
	import type { LessonAudio } from '$lib/api';
	import LessonPlayer from '$lib/components/LessonPlayer.svelte';
	import { buildScenes } from '$lib/transcriptScenes';

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

	// ⚠️ buildScenes, NOT a hand-rolled filter over natural_speed.phrases.
	// Measured against the live API: that section's first phrases are NARRATOR
	// lines in English -- the literal string "Natural Speed", then the scene
	// label -- so rendering the phrase list raw opens the scene with the
	// section's own name. buildScenes already knows to drop the section title
	// and promote the next narrator line to a heading, and it pairs each L2 line
	// with its English translation for free.
	//
	// It is passed no dialogueLines: those come from the transcript endpoint,
	// which resolves against the lessons table where a session is not. Every
	// field this page renders comes from the session body itself.
	const scenes = $derived(buildScenes(data.session, []));

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
		{#each scenes as scene, si (si)}
			{#if scene.title}
				<h3 class="scene-title">{scene.title}</h3>
			{/if}
			<ol>
				{#each scene.lines as line, i (i)}
					<li>
						<span class="speaker">{line.role}</span>
						<span class="text">
							{line.naturalText}
							{#if line.translatedText}
								<span class="gloss">{line.translatedText}</span>
							{/if}
						</span>
					</li>
				{/each}
			</ol>
		{/each}
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
	.scene-title {
		font-size: 0.82rem;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		opacity: 0.6;
		margin: 1.25rem 0 0.6rem;
		font-weight: 600;
	}
	.gloss {
		display: block;
		font-size: 0.85rem;
		opacity: 0.62;
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
