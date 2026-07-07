<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import type { LessonAudio, TranscriptData } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import LessonPlayer from '$lib/components/LessonPlayer.svelte';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';
	import Transcript from '$lib/components/Transcript.svelte';
	import TranscriptPlaceholder from '$lib/components/TranscriptPlaceholder.svelte';
	import { syncStore } from '$lib/stores/sync.svelte';
	import { queueStatsStore } from '$lib/stores/queueStats.svelte';
	import { lessonModePref } from '$lib/stores/lessonModePref.svelte';
	import { pipelineStore } from '$lib/stores/pipeline.svelte';
	import { rateLimitStore } from '$lib/stores/rateLimit.svelte';
	import RateLimitWidget from '$lib/components/RateLimitWidget.svelte';
	import LessonSourcePanel from '$lib/components/LessonSourcePanel.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Read/Listen mode is a persisted preference that defaults by viewport (Listen
	// on mobile, Read on desktop) rather than an unconditional 'read' that landed
	// mobile users in the wrong mode. Seeds on mount like the theme/prefetch prefs.
	const mode = $derived(lessonModePref.mode);
	onMount(() => lessonModePref.init());

	// untrack: intentionally snapshot load data as mutable local state
	let audio: LessonAudio | null = $state(untrack(() => data.audio));
	let transcript: TranscriptData | null = $state(untrack(() => data.transcript));
	// Starts true when load didn't supply a transcript (production: we fetch it
	// client-side below) so the section shows the spinner from first paint.
	let transcriptLoading = $state(untrack(() => data.transcript === null));
	let listenLoading = $state(false);
	let listenResult: { registered: number } | null = $state(null);
	let audioLoading = $state(false);
	// Stays true from the Regenerate click until the pipeline lands the new lesson
	// (navigate) or fails — NOT just for the brief regenerateDay request, so the
	// button stays disabled while the background job runs.
	let regenerating = $state(false);
	let syncStatus = $state('');
	let error = $state('');
	let wordActionInFlight = $state(false);

	let playbackController = $state<PlaybackController | null>(null);

	// The player card sticks below the layout's sticky nav; measure the nav so
	// the offset tracks its real (wrap-dependent) height.
	let navHeight = $state(0);
	function measureNav() {
		navHeight = document.querySelector('.global-nav')?.clientHeight ?? 0;
	}
	onMount(measureNav);

	// Seed the LLM quota chip so it isn't empty when the user opens Lesson tools to
	// regenerate; the pipeline poll (pipelineStore) keeps it fresh during the run.
	onMount(() => {
		void rateLimitStore.ensureFresh();
	});

	let isListened = $derived(listenedStore.has(data.lesson.id));

	// SvelteKit reuses this component on same-route param changes (e.g. the
	// Regenerate button's goto, or lesson→lesson nav). The untracked local
	// copies above must follow `data` instead of staying frozen on the prior
	// lesson — otherwise audio/transcript show stale content after navigation.
	$effect(() => {
		audio = data.audio;
		// A render started on the previous lesson must not leave the new lesson's
		// Render button stuck on "Rendering…".
		audioLoading = false;
		const provided = data.transcript;
		if (provided !== null) {
			// Supplied by load (or passed directly in a test) — render it as-is.
			transcript = provided;
			return;
		}
		// Not preloaded: fetch client-side so the lesson shell renders immediately
		// instead of blocking on the (classla-backed) transcript endpoint, which can
		// take many seconds on a cold backend. That latency means a lesson→lesson
		// navigation can outrun the fetch — drop responses for a lesson we've left,
		// or lesson A's late transcript would clobber lesson B's.
		const lessonId = data.lesson.id;
		transcript = null;
		transcriptLoading = true;
		error = '';
		api.getLessonTranscript(lessonId)
			.then((t) => {
				if (data.lesson.id === lessonId) transcript = t;
			})
			.catch((e) => {
				if (data.lesson.id === lessonId) error = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				if (data.lesson.id === lessonId) transcriptLoading = false;
			});
	});

	async function handleRenderAudio() {
		// Rendering takes tens of seconds (full-lesson TTS); the user may navigate
		// to another lesson meanwhile. Everything after an await re-checks that
		// this lesson is still the one on screen before touching page state.
		const lessonId = data.lesson.id;
		audioLoading = true;
		error = '';
		try {
			const rendered = await api.renderAudio(lessonId);
			if (data.lesson.id !== lessonId) return;
			audio = rendered;
			const t = await api.getLessonTranscript(lessonId);
			if (data.lesson.id !== lessonId) return;
			transcript = t;
		} catch (e) {
			if (data.lesson.id === lessonId) error = e instanceof Error ? e.message : String(e);
		} finally {
			if (data.lesson.id === lessonId) audioLoading = false;
		}
	}

	async function handleRegenerate() {
		const confirmed = window.confirm(
			`Regenerate Day ${data.lesson.day}? This creates a new version of the dialogue using the ` +
				`current generation prompt. Your existing cards are kept; new vocabulary and ` +
				`morphology drills are added on the next listen + sync.`
		);
		if (!confirmed) return;
		regenerating = true;
		error = '';
		try {
			// Route through the greedy pipeline (429 wait-and-retry, sticky-failed +
			// Retry, activity-log visibility) rather than the synchronous generate
			// endpoint, which escapes an unhandled LLMError on a 429. The pipeline mints
			// a NEW lesson id for this day; the follow-effect below navigates once ready.
			await api.regenerateDay(data.curriculum.id, data.lesson.day, 'WIDER');
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			regenerating = false;
		}
	}

	// A sync changes per-word due/known states in the backend, but nothing else on
	// this page tracks that. Re-fetch the transcript so the rendered states reflect
	// the sync, and surface a short summary (SyncButton hides its own once a
	// callback is supplied).
	async function handleSyncResult() {
		syncStatus = 'Synced with AnkiWeb';
		error = '';
		const lessonId = data.lesson.id;
		try {
			const t = await api.getLessonTranscript(lessonId);
			if (data.lesson.id === lessonId) transcript = t;
		} catch (e) {
			if (data.lesson.id === lessonId) error = e instanceof Error ? e.message : String(e);
		}
	}

	$effect(() => {
		if (syncStore.lastResult) handleSyncResult();
	});

	// Pipeline lifecycle: poll render status while on this page; stop on destroy.
	$effect(() => {
		const cid = data.curriculum.id;
		pipelineStore.start(cid);
		return () => pipelineStore.stop();
	});

	let thisDayPipeline = $derived.by(() => {
		if (!pipelineStore.status?.active) return null;
		return pipelineStore.status.days.find((d) => d.day === data.lesson.day) ?? null;
	});

	// Track this lesson's day record WITHOUT the active gate so we can detect
	// transitions (e.g. rendering→ready) even after the pipeline goes inactive.
	let pipelineDayRecord = $derived.by(() => {
		if (!pipelineStore.status) return null;
		return pipelineStore.status.days.find((d) => d.day === data.lesson.day) ?? null;
	});

	// Follow the regenerated day to its NEW lesson: navigate once the pipeline day
	// record reaches ready with a different lesson id. Gated on `regenerating` so
	// merely viewing an older version (whose latest lesson differs) never navigates.
	// A failed regen just drops the flag — the failure stays visible via regenStatus
	// and the curriculum page's sticky Retry.
	$effect(() => {
		if (!regenerating) return;
		const record = pipelineDayRecord;
		if (record === null) return;
		if (record.state === 'failed') {
			regenerating = false;
			return;
		}
		const newId = record.lesson_id;
		if (record.state === 'ready' && newId !== null && newId !== data.lesson.id) {
			regenerating = false;
			goto(`/c/${data.curriculum.id}/l/${newId}`);
		}
	});

	// The render-row pipeline badge only shows when audio is absent, but a regen
	// keeps the old audio on screen — so surface progress here as a colored state
	// pill + message: the live detail (e.g. the 429 "waiting Ns for rate-limit
	// window") while running, and the sticky error once the day fails.
	let regenStatus = $derived.by((): { state: string; message: string | null } | null => {
		const record = pipelineDayRecord;
		if (record === null) return null;
		if (record.state === 'failed') return { state: 'failed', message: record.error ?? 'Regeneration failed' };
		if (regenerating) return { state: record.state, message: record.detail };
		return null;
	});

	// Plain let (not $state): we only need to remember across effect runs, not
	// trigger a re-run when it changes.
	let prevPipelineDayState: string | null = null;

	$effect(() => {
		const record = pipelineDayRecord;
		const lessonId = data.lesson.id;

		if (record) {
			const prev = prevPipelineDayState;
			prevPipelineDayState = record.state;

			if (
				// Transition from a non-ready state to ready (pipeline just finished)
				(prev != null && prev !== 'ready' && record.state === 'ready') ||
				// Page loaded after pipeline already finished — audio still missing
				(prev == null && record.state === 'ready' && record.has_audio && !audio)
			) {
				api.getLessonAudio(lessonId)
					.then((a) => {
						if (data.lesson.id === lessonId) audio = a;
					})
					.catch((e) => {
						if (data.lesson.id === lessonId) error = e instanceof Error ? e.message : String(e);
					});
			}
		} else {
			prevPipelineDayState = null;
		}
	});

	async function handleMarkListened() {
		const lessonId = data.lesson.id;
		listenLoading = true;
		error = '';
		try {
			const result = await api.markAsListened(lessonId, {});
			listenResult = result;
			listenedStore.add(lessonId);
			const t = await api.getLessonTranscript(lessonId);
			if (data.lesson.id === lessonId) transcript = t;
		} catch (e) {
			if (data.lesson.id === lessonId) error = e instanceof Error ? e.message : String(e);
		} finally {
			listenLoading = false;
		}
	}

	// Single-level undo cycle: the last drill grade (word or phrase) stays
	// reversible from its popover ("Undo ↩") until something else is graded,
	// the page reloads, or a sync hands the review to Anki (backend 409s then).
	let undoable = $state<{ itemId: number; direction: 'recognition' | 'production' } | null>(
		null
	);

	async function handleWordClick(word: import('$lib/api').WordToken, lineIndex: number) {
		if (wordActionInFlight) return;
		wordActionInFlight = true;
		error = '';
		try {
			if (word.active_state === 'unknown') {
				// Reading an untracked word introduces AND reviews it in one tap:
				// create the base card, then record a first recognition review so it
				// enters learning right away (not just parked at NEW).
				const sentence = transcript!.dialogue_lines[lineIndex]?.sentence ?? '';
				const created = await api.createBaseCard({
					surface: word.surface,
					lemma: word.lemma,
					sentence,
					language_code: data.lesson.language_code,
					translation: word.translation ?? ''
				});
				await api.submitDrill(created.id, 'recognition', 'good');
				undoable = { itemId: created.id, direction: 'recognition' };
			} else if (word.is_due && word.active_direction && word.srs_item_id != null) {
				const direction = word.active_direction as 'recognition' | 'production';
				await api.submitDrill(word.srs_item_id, direction, 'good');
				undoable = { itemId: word.srs_item_id, direction };
			} else if (word.recognition_reviewable && word.srs_item_id != null) {
				// Read-ahead: reading a not-due word is a valid RECOGNITION review.
				// Always grade the literal recognition direction — never
				// active_direction, which flips to production once recognition
				// graduates (that would silently grade the wrong card).
				await api.submitDrill(word.srs_item_id, 'recognition', 'good');
				undoable = { itemId: word.srs_item_id, direction: 'recognition' };
			} else {
				return;
			}
			transcript = await api.getLessonTranscript(data.lesson.id);
			// A grade changes the review counts; keep the shared nav badge truthful.
			queueStatsStore.refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			wordActionInFlight = false;
		}
	}

	async function handleCollocationStateChange(span_id: number) {
		if (wordActionInFlight) return;
		wordActionInFlight = true;
		error = '';
		try {
			await api.submitDrill(span_id, 'recognition', 'good');
			undoable = { itemId: span_id, direction: 'recognition' };
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			wordActionInFlight = false;
		}
	}

	async function handleUndoGrade(itemId: number, direction: 'recognition' | 'production') {
		error = '';
		// Either way the snapshot is spent: success restores it, failure means a
		// newer grade or a sync invalidated it — drop the Undo button regardless.
		undoable = null;
		try {
			await api.undoGrade(itemId, direction);
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	const tooltipActions = {
		onCreateInflection: async (word: import('$lib/api').WordToken, sentence: string) => {
			error = '';
			try {
				await api.createInflectionCloze({
					surface: word.surface,
					lemma: word.lemma,
					feature: word.inflection_feature!,
					sentence,
					language_code: data.lesson.language_code,
					lesson_id: data.lesson.id,
					translation: word.translation ?? ''
				});
				transcript = await api.getLessonTranscript(data.lesson.id);
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		},
		onSetState: async (id: number, state: string) => {
			// Reset-to-new forgets the card in Anki too (re-learn from scratch),
			// so confirm before discarding the schedule. Other states are label-only.
			if (
				state === 'new' &&
				!confirm('Reset this word? It will be forgotten in Anki too and re-learned from scratch.')
			) {
				return;
			}
			error = '';
			try {
				await api.setSRSItemState(id, state);
				transcript = await api.getLessonTranscript(data.lesson.id);
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		},
		onUntrack: async (id: number) => {
			error = '';
			try {
				await api.untrackSRSItem(id);
				transcript = await api.getLessonTranscript(data.lesson.id);
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		},
		onUnignore: async (id: number) => {
			error = '';
			try {
				await api.suspendSRSItem(id, false);
				transcript = await api.getLessonTranscript(data.lesson.id);
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		},
		onIgnoreLemma: async (lemma: string) => {
			error = '';
			try {
				await api.ignoreLemma(lemma, data.lesson.language_code);
				transcript = await api.getLessonTranscript(data.lesson.id);
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		},
		onUnignoreLemma: async (lemma: string) => {
			error = '';
			try {
				await api.unignoreLemma(lemma, data.lesson.language_code);
				transcript = await api.getLessonTranscript(data.lesson.id);
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		},
		onRestoreKnown: async (id: number) => {
			error = '';
			try {
				await api.restoreKnown(id);
				transcript = await api.getLessonTranscript(data.lesson.id);
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		},
		// Match on item id only — grading recognition can graduate it, flipping the
		// refetched word's active_direction to production; the undo must still hit
		// the direction that was actually graded (stored in `undoable`).
		isGradeUndoable: (word: import('$lib/api').WordToken) =>
			undoable != null && word.srs_item_id === undoable.itemId,
		onUndoGrade: async (_word: import('$lib/api').WordToken) => {
			if (undoable != null) await handleUndoGrade(undoable.itemId, undoable.direction);
		}
	};

	async function handleCreatePhrase({
		text,
		word_count,
		translation,
		source_sentence,
		source_lesson_id,
		source_line_index
	}: {
		text: string;
		word_count: number;
		translation: string;
		lineIndex: number;
		startIdx: number;
		endIdx: number;
		source_sentence?: string;
		source_lesson_id?: string;
		source_line_index?: number;
	}) {
		error = '';
		try {
			await api.createSRSItem({
				text,
				language_code: data.lesson.language_code,
				word_count,
				translation,
				source_sentence,
				source_lesson_id,
				source_line_index
			});
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
</script>

<svelte:window onresize={measureNav} />

<main>
	<!-- The sticky card owns everything the user reaches for mid-lesson: the
	     lesson title, Read/Listen toggle, and (once rendered) the player. It
	     sticks below the global nav so nothing the user needs scrolls away. -->
	<section class="card player-card" style="top: {navHeight}px">
		<div class="player-title-area">
			<a class="breadcrumb" href="/c/{data.curriculum.id}">← {data.curriculum.topic}</a>
			<h1>{data.lesson.title}</h1>
			{#if syncStatus}
				<p class="sync-status">{syncStatus}</p>
			{/if}
			{#if error}
				<p class="error">{error}</p>
			{/if}
		</div>
		<div class="mode-row">
			<div class="toggle-pill">
				<button class:active={mode === 'read'} onclick={() => lessonModePref.set('read')}>Read</button>
				<button class:active={mode === 'listen'} onclick={() => lessonModePref.set('listen')}>Listen</button>
			</div>
		</div>
		{#if audio}
			{#key audio.audio_id}
				<!-- ONE persistent player across modes: only the `compact` prop flips on
				     Listen↔Read, so the controller (and playback) survives the switch. -->
				<LessonPlayer {audio} compact={mode !== 'listen'} lessonTitle={data.lesson.title} bind:controller={playbackController} />
			{/key}
		{:else}
			<div class="render-row">
				<button onclick={handleRenderAudio} disabled={audioLoading}>
					{audioLoading ? 'Rendering…' : 'Render Audio'}
				</button>
				{#if thisDayPipeline && !audioLoading}
					<span class="pipeline-state state-{thisDayPipeline.state}">{thisDayPipeline.state}</span>
				{/if}
			</div>
		{/if}
	</section>

	{#if mode === 'listen'}
		<section class="card listen-card">
			<button class="listen-btn" class:listened={isListened} onclick={handleMarkListened} disabled={listenLoading}>
				{#if listenLoading}
					Registering…
				{:else if isListened}
					✓ Listened
				{:else}
					Mark as Listened
				{/if}
			</button>
			{#if listenResult && !error}
				<p class="listen-confirmation">
					{listenResult.registered}
					{listenResult.registered === 1 ? 'word' : 'words'} tracked in SRS
				</p>
			{/if}
		</section>
	{/if}

	{#if mode === 'read'}
		<section class="card">
			{#if transcript}
				<Transcript
					{transcript}
					lesson={data.lesson}
					onWordClick={handleWordClick}
					onCollocationStateChange={handleCollocationStateChange}
					undoableItemId={undoable?.itemId ?? null}
					onCollocationUndo={(spanId) => handleUndoGrade(spanId, 'recognition')}
					onCreatePhrase={handleCreatePhrase}
					controller={playbackController}
					cues={audio?.cues ?? null}
					tooltipActions={tooltipActions}
				/>
			{:else if transcriptLoading}
				<TranscriptPlaceholder lesson={data.lesson} />
			{:else}
				<p class="muted">No transcript available.</p>
			{/if}
		</section>
	{/if}

	<!-- Rare actions live folded away: downloads for offline use, regeneration
	     as the destructive-ish last resort. -->
	<details class="card tools-card">
		<summary>Lesson tools</summary>
		{#if audio}
			<div class="download-links">
				<a class="download-all-btn" href={api.audioZipUrl(audio.lesson_id)} download>Download All Sections</a>
				{#each audio.sections as sec (sec.audio_id)}
					<a class="section-dl-btn" href={api.audioUrl(sec.audio_id)} download>{sec.title}</a>
				{/each}
			</div>
		{/if}
		<p class="muted">
			Regenerating rewrites this day's dialogue with the current prompt (better declension &amp;
			conjugation coverage). Existing cards stay; new vocabulary and morphology drills are added when
			you next listen and sync.
		</p>
		<div class="regen-row">
			<button class="regen-btn" onclick={handleRegenerate} disabled={regenerating}>
				{regenerating ? 'Regenerating…' : `Regenerate Day ${data.lesson.day}`}
			</button>
			<!-- Regeneration hits the LLM, so surface the quota chip here to track usage. -->
			<RateLimitWidget />
		</div>
		{#if regenStatus}
			<p class="regen-status" data-testid="regen-status">
				<span class="pipeline-state state-{regenStatus.state}">{regenStatus.state}</span>
				{#if regenStatus.message}
					<span class="regen-detail" data-testid="regen-detail">{regenStatus.message}</span>
				{/if}
			</p>
		{/if}
	</details>

	<LessonSourcePanel
		lessonId={data.lesson.id}
		curriculumId={data.curriculum.id}
		day={data.lesson.day}
		onImported={(newLessonId) => goto(`/c/${data.curriculum.id}/l/${newLessonId}`)}
	/>
</main>

<style>
	main {
		max-width: 700px;
		margin: 1.5rem auto;
		padding: 0 1rem;
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}
	.player-title-area {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		margin-bottom: 0.5rem;
	}
	.breadcrumb {
		display: inline-block;
		color: var(--color-muted);
		font-size: 0.9rem;
		font-weight: 600;
		text-decoration: none;
	}
	.breadcrumb:hover {
		color: var(--color-primary);
	}
	h1 {
		margin: 0;
		font-size: 1.4rem;
		font-weight: 800;
		letter-spacing: -0.01em;
	}
	button {
		margin-top: 0.75rem;
		padding: 0.55rem 1.4rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease;
	}
	button:not(:disabled):hover {
		background: var(--color-primary-hover);
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.error {
		color: var(--color-danger);
		margin: 0;
	}
	.sync-status {
		color: var(--color-muted);
		font-size: 0.85rem;
		margin: 0;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.9rem;
	}
	.regen-row {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		flex-wrap: wrap;
		margin-top: 0.75rem;
	}
	.regen-row button {
		margin-top: 0;
	}
	.regen-btn {
		background: transparent;
		color: var(--color-danger);
		border: 1px solid var(--color-danger);
	}
	.regen-btn:not(:disabled):hover {
		background: color-mix(in srgb, var(--color-danger) 12%, transparent);
	}
	.regen-status {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 0.6rem 0 0;
		font-size: 0.82rem;
	}
	.regen-detail {
		color: var(--color-muted);
	}
	.mode-row {
		display: flex;
		justify-content: center;
		margin-bottom: 0.75rem;
	}
	.render-row {
		display: flex;
		justify-content: center;
		align-items: center;
		gap: 0.5rem;
	}
	.render-row button {
		margin-top: 0;
	}
	.pipeline-state {
		font-size: 0.75rem;
		font-weight: 600;
		padding: 0.15rem 0.5rem;
		border-radius: var(--radius-pill);
		text-transform: capitalize;
	}
	.state-queued {
		background: var(--color-surface-2);
		color: var(--color-muted);
	}
	.state-generating {
		background: color-mix(in srgb, var(--color-info) 14%, transparent);
		color: var(--color-info);
	}
	.state-rendering {
		background: color-mix(in srgb, var(--color-accent) 14%, transparent);
		color: var(--color-accent);
	}
	.state-ready {
		background: color-mix(in srgb, var(--color-success) 14%, transparent);
		color: var(--color-success);
	}
	.state-failed {
		background: color-mix(in srgb, var(--color-danger) 14%, transparent);
		color: var(--color-danger);
	}
	.toggle-pill {
		display: flex;
		gap: 0;
		background: var(--color-surface-2);
		border-radius: var(--radius-pill);
		padding: 2px;
		width: fit-content;
	}
	.toggle-pill button {
		margin: 0;
		padding: 0.35rem 1rem;
		border: none;
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--color-muted);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease, color 0.15s ease;
	}
	.toggle-pill button.active {
		background: var(--color-bg, #fff);
		color: var(--color-text);
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
	}
	.toggle-pill button:not(.active):hover {
		color: var(--color-text);
	}
	.player-card {
		position: sticky;
		/* Above transcript content + tooltips (z 10), below the global nav (z 50). */
		z-index: 20;
	}
	.listen-card {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.75rem;
		padding: 1.5rem;
	}
	.listen-btn {
		padding: 0.5rem 1.25rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		cursor: pointer;
		font-weight: 600;
	}
	.listen-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.listen-btn.listened {
		background: var(--color-success);
	}
	.listen-confirmation {
		color: var(--color-success);
		font-size: 0.85rem;
		margin: 0;
	}
	.tools-card summary {
		cursor: pointer;
		font-size: 0.9rem;
		font-weight: 600;
		color: var(--color-muted);
	}
	.tools-card[open] summary {
		margin-bottom: 0.75rem;
	}
	.tools-card .muted {
		margin: 0.75rem 0 0;
	}
	.download-links {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.download-all-btn {
		display: block;
		min-height: 44px;
		line-height: 44px;
		padding: 0 1.25rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border-radius: 4px;
		text-decoration: none;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.download-all-btn:hover {
		filter: brightness(0.9);
	}
	.section-dl-btn {
		padding: 0.4rem 0.9rem;
		background: var(--color-secondary);
		color: var(--color-on-primary);
		border-radius: 4px;
		text-decoration: none;
		font-size: 0.85rem;
	}
	.section-dl-btn:hover {
		filter: brightness(0.85);
	}
</style>
