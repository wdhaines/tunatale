<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api } from '$lib/api';
	import type { LessonAudio, TranscriptData } from '$lib/api';
	import LessonReader from '$lib/components/LessonReader.svelte';
	import MasteryLine from '$lib/components/MasteryLine.svelte';
	import ListenActions from '$lib/components/ListenActions.svelte';
	import ListenPreviewModal from '$lib/components/ListenPreviewModal.svelte';
	import { createListenActions } from '$lib/reading/listenActions.svelte';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';
	import { createReadingActions } from '$lib/reading/readingActions.svelte';
	import AudioDownloads from '$lib/components/AudioDownloads.svelte';
	import RateLimitWidget from '$lib/components/RateLimitWidget.svelte';
	import { confirmDialog } from '$lib/components/ConfirmDialog.svelte';
	import { invalidateAll } from '$app/navigation';


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
	let regenerating = $state(false);
	let showRegenHelp = $state(false);

	// ⚠️ THE SAME ACTIONS THE LESSON PAGE USES, from one implementation. Tapping
	// a word grades it, the popovers create cards and cloze inflections, undo
	// works — none of it re-implemented here. Only the SOURCE differs, which is
	// the whole claim of this page.
	// ONE binding, both factories. The accessors are created once — which also
	// means the reading tests exercise the very same closures the listen path
	// uses, rather than each page carrying two identical copies.
	const contentBinding = {
		get contentId() {
			return data.session.id;
		},
		get languageCode() {
			return data.session.language_code;
		},
		getTranscript: () => transcript,
		setTranscript: (t: TranscriptData) => {
			transcript = t;
		},
		setError: (m: string) => {
			error = m;
		}
	};

	const reading = createReadingActions(contentBinding);

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

	// The same listen flow a lesson has: nothing about it was ever day-scoped,
	// and /api/srs/content/{id}/… now resolves a session too.
	const listen = createListenActions(contentBinding);

	$effect(() => {
		if (listenedStore.has(data.session.id)) listen.fetchQueue();
	});

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

	/**
	 * Rewrite this session's dialogue, keeping the session.
	 *
	 * ⚠️ NOT `createReviewSession()`. That mints a new id at a new URL and leaves
	 * this one in the dated list — a second session, not a better one. The route
	 * this calls preserves the id and the date.
	 *
	 * ⚠️ Unlike the lesson page's Regenerate, this does NOT go through the greedy
	 * pipeline: `LessonPipeline` is keyed (language_code, curriculum_id, day) and
	 * a session has none of those. The visible cost is that a 429 arrives here as
	 * an error the user has to act on, rather than a wait-and-retry.
	 */
	async function handleRegenerate() {
		const confirmed = await confirmDialog(
			'Rewrite this session\u2019s dialogue? It keeps its date and its place in the list, ' +
				'and is rebuilt from what has decayed NOW \u2014 so the words it drills may differ ' +
				'from the ones it used before. Existing cards are kept. Any audio already ' +
				'rendered for it is discarded.'
		);
		if (!confirmed) return;
		regenerating = true;
		error = '';
		try {
			await api.regenerateReviewSession(data.session.id);
			// The server dropped the renders of the dialogue we just replaced, so
			// the player must not keep offering them.
			audio = null;
			transcriptLoading = true;
			await invalidateAll();
			transcript = await api.getTranscript(data.session.id).catch(() => null);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			regenerating = false;
			transcriptLoading = false;
		}
	}

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
			<MasteryLine {transcript} />
			<p class="muted">
				A review session — built from what has decayed across your whole deck, with no theme
				and no place in any curriculum.
			</p>
		{/snippet}
		{#snippet actions()}
			<ListenActions
				{listen}
				reviewHref="/review?lesson={data.session.id}&back=/review-sessions/{data.session.id}"
				hasError={error !== ''}
			/>
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
	<!-- Same fold-away as the lesson reader's, with only the tools a session can
	     actually have. No day pager, no curriculum breadcrumb, no delete-day and
	     no source panel: every one of those acts on a POSITION IN A CURRICULUM,
	     which a session does not have. The source panel is the near miss —
	     `/api/story/{id}/source` reads the lessons table, and its importer needs a
	     curriculumId and a day to write back to. -->
	<details class="card tools-card">
		<summary>Session tools</summary>
		<AudioDownloads {audio} />
		<div class="regen-row">
			<button class="regen-btn" onclick={handleRegenerate} disabled={regenerating}>
				{regenerating ? 'Rewriting…' : 'Rewrite dialogue'}
			</button>
			<!-- Rewriting hits the LLM, so the quota chip belongs beside the button
			     that spends it — the same placement the lesson reader uses. -->
			<RateLimitWidget />
			<button
				type="button"
				class="help-toggle"
				aria-label="What does rewriting do?"
				aria-expanded={showRegenHelp}
				onclick={() => (showRegenHelp = !showRegenHelp)}>?</button
			>
		</div>
		{#if showRegenHelp}
			<p class="help-panel">
				Rewrites this session&rsquo;s dialogue with the current prompt, keeping its date and
				its place in the list. It is rebuilt from what has decayed <em>now</em>, so the words
				it drills may differ from last time. Existing cards stay; any audio already rendered
				is discarded and can be prepared again.
			</p>
		{/if}
	</details>
</main>

{#if listen.showPreview}
	<ListenPreviewModal {...listen.previewProps} />
{/if}

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
	/* The `<details>` chrome is duplicated from the lesson reader rather than
	   shared, deliberately: a shared shell would have to style content each page
	   passes IN, and Svelte scopes a slot's styles to the parent — so it could
	   only work through `:global()` rules the compiler cannot verify. What is
	   genuinely identical and leaf-like (the download row) IS shared, as
	   AudioDownloads. See its header for the full reasoning. */
	.tools-card summary {
		cursor: pointer;
		font-size: 0.9rem;
		font-weight: 600;
		color: var(--color-muted);
		padding: 0.25rem 0;
		border-radius: 4px;
		user-select: none;
	}
	.tools-card summary:hover {
		color: var(--color-text);
	}
	.tools-card[open] summary {
		margin-bottom: 0.75rem;
	}
	.regen-row {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		flex-wrap: wrap;
		margin-top: 0.75rem;
	}
	.regen-btn {
		background: transparent;
		color: var(--color-danger);
		border: 1px solid var(--color-danger);
		border-radius: 4px;
		padding: 0.4rem 0.9rem;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.regen-btn:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.help-toggle {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.4rem;
		height: 1.4rem;
		padding: 0;
		margin: 0;
		border: 1px solid var(--color-border);
		border-radius: 50%;
		background: transparent;
		color: var(--color-muted);
		font-size: 0.8rem;
		cursor: pointer;
	}
	.help-toggle:hover {
		color: var(--color-text);
		border-color: var(--color-text);
	}
	.help-panel {
		margin: 0.5rem 0 0;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
</style>
