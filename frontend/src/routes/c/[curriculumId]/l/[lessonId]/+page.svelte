<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import type { LessonAudio, TranscriptData, ListenResponse, PeerSyncResult, DayProgress } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import LessonPlayer from '$lib/components/LessonPlayer.svelte';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';
	import Transcript from '$lib/components/Transcript.svelte';
	import TranscriptPlaceholder from '$lib/components/TranscriptPlaceholder.svelte';
	import { syncStore } from '$lib/stores/sync.svelte';
	import { queueStatsStore } from '$lib/stores/queueStats.svelte';
	import { createReadingActions } from '$lib/reading/readingActions.svelte';
	import LessonReader from '$lib/components/LessonReader.svelte';
	import MasteryLine from '$lib/components/MasteryLine.svelte';
	import ListenActions from '$lib/components/ListenActions.svelte';
	import { createListenActions } from '$lib/reading/listenActions.svelte';
	import { pipelineStore } from '$lib/stores/pipeline.svelte';
	import { rateLimitStore } from '$lib/stores/rateLimit.svelte';
	import RateLimitWidget from '$lib/components/RateLimitWidget.svelte';
	import LessonSourcePanel from '$lib/components/LessonSourcePanel.svelte';
	import ListenPreviewModal from '$lib/components/ListenPreviewModal.svelte';
	import { lessonMastery, masteryColor } from '$lib/mastery';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { confirmDialog } from '$lib/components/ConfirmDialog.svelte';
	import type { WordRating } from '$lib/api';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();


	// untrack: intentionally snapshot load data as mutable local state
	let audio: LessonAudio | null = $state(untrack(() => data.audio));
	let transcript: TranscriptData | null = $state(untrack(() => data.transcript));
	// Starts true when load didn't supply a transcript (production: we fetch it
	// client-side below) so the section shows the spinner from first paint.
	let transcriptLoading = $state(untrack(() => data.transcript === null));

	// ONE binding, both factories. The accessors are created once — which also
	// means the reading tests exercise the very same closures the listen path
	// uses, rather than each page carrying two identical copies.
	const contentBinding = {
		get contentId() {
			return data.lesson.id;
		},
		get languageCode() {
			return data.lesson.language_code;
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
	const listen = createListenActions(contentBinding);
	// Which lesson the three above describe. Plain `let`, not $state: it is the
	// follow-`data` effect's own bookkeeping and must not be a dependency of it.
	let resultsLessonId: string | null = untrack(() => data.lesson.id);
	let audioLoading = $state(false);
	// Stays true from the Regenerate click until the pipeline lands the new lesson
	// (navigate) or fails — NOT just for the brief regenerateDay request, so the
	// button stays disabled while the background job runs.
	let regenerating = $state(false);
	let syncStatus = $state('');
	let error = $state('');
	let confirmingDeleteDay = $state(false);
	let deletingDay = $state(false);
	let wordActionInFlight = $state(false);
	let showRegenHelp = $state(false);

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


	// `lesson.day` is the stable key and goes gappy as days are deleted; the plan's
	// position is what the day picker shows, so label with that. Falls back to the
	// key for a lesson whose day is no longer in the plan (deleted in another tab).
	let dayPosition = $derived(
		data.curriculum.days.find((d) => d.day === data.lesson.day)?.position ?? data.lesson.day
	);

	// Prev/next links need the day→lesson map, which `data.curriculum.days`
	// (day + position) does not carry — it comes from the progress endpoint
	// (DayProgress: day + position + lesson_id). onMount, not $effect, is
	// deliberate: prev/next navigation stays inside one curriculum, so the map
	// does not need to refetch per lesson.
	let dayLessons: DayProgress[] = $state([]);
	onMount(async () => {
		try {
			dayLessons = await api.getCurriculumProgress(data.curriculum.id);
		} catch {
			// Non-critical chrome: a failed side fetch must never blank the lesson
			// over the nav links, so leave dayLessons empty and do NOT set `error`.
		}
	});

	// Order by position (the plan's display order), not array order and not by
	// `day` — day is the stable key and goes gappy when days are deleted.
	const orderedLessons = $derived([...dayLessons].sort((a, b) => a.position - b.position));
	const currentIndex = $derived(orderedLessons.findIndex((d) => d.day === data.lesson.day));
	const prevLesson = $derived(currentIndex > 0 ? orderedLessons[currentIndex - 1] : null);
	const nextLesson = $derived(
		currentIndex >= 0 && currentIndex < orderedLessons.length - 1
			? orderedLessons[currentIndex + 1]
			: null
	);

	// SvelteKit reuses this component on same-route param changes (e.g. the
	// Regenerate button's goto, or lesson→lesson nav). The untracked local
	// copies above must follow `data` instead of staying frozen on the prior
	// lesson — otherwise audio/transcript show stale content after navigation.
	$effect(() => {
		audio = data.audio;
		// A render started on the previous lesson must not leave the new lesson's
		// Render button stuck on "Rendering…".
		audioLoading = false;
		// Everything the previous listen produced is scoped to the lesson it ran
		// on. `listenResult` is the confirmation banner ("1 graded · 10 remaining
		// — listen again to add more"), and queueCount/hasUnreviewedListen back
		// the "Check your work" link; left standing, all three report day N-1's
		// numbers on day N's page. Guarded on the id rather than reset on every
		// `data` change: a same-lesson re-load (invalidate) would otherwise blank
		// a queue count that the fetch effect below has no reason to re-fetch.
		if (data.lesson.id !== resultsLessonId) {
			resultsLessonId = data.lesson.id;
			listen.reset();
		}
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
		api.getTranscript(lessonId)
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
			const t = await api.getTranscript(lessonId);
			if (data.lesson.id !== lessonId) return;
			transcript = t;
		} catch (e) {
			if (data.lesson.id === lessonId) error = e instanceof Error ? e.message : String(e);
		} finally {
			if (data.lesson.id === lessonId) audioLoading = false;
		}
	}

	// What the generating prompt asked this lesson to reuse, and what it actually
	// did. Hidden entirely when nothing was requested: most early lessons request
	// nothing, and an imported lesson with no recorded request is unmeasurable
	// rather than bad — a permanent "0 of 0" would read as a standing failure.
	const reviewRequested = $derived(data.lesson.review_requested ?? []);
	const reviewUsed = $derived(data.lesson.review_used ?? []);

	async function handleRegenerate() {
		const confirmed = await confirmDialog(
			`Regenerate Day ${dayPosition}? This creates a new version of the dialogue using the ` +
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

	// Two-click confirm (same pattern as the plan page's Reset chat button):
	// first click arms it, second click deletes. Deletes the day's lessons and
	// audio server-side; existing SRS/Anki cards are untouched. No renumbering.
	async function handleDeleteDay() {
		confirmingDeleteDay = false;
		deletingDay = true;
		error = '';
		try {
			await api.deleteCurriculumDay(data.curriculum.id, data.lesson.day);
			goto(`/c/${data.curriculum.id}`);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			deletingDay = false;
		}
	}

	function handleDeleteDayClick() {
		if (confirmingDeleteDay) {
			handleDeleteDay();
		} else {
			confirmingDeleteDay = true;
		}
	}

	function handleDeleteDayBlur() {
		confirmingDeleteDay = false;
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
			const t = await api.getTranscript(lessonId);
			if (data.lesson.id === lessonId) transcript = t;
		} catch (e) {
			if (data.lesson.id === lessonId) error = e instanceof Error ? e.message : String(e);
		}
	}

	// syncStore.lastResult is a session-lifetime singleton, so its PRESENCE is not
	// an event — it stays set for the rest of the session after one sync. Treating
	// it as an event meant every lesson opened afterwards re-announced "Synced
	// with AnkiWeb" under its title and fired a redundant transcript refetch.
	// Seed from the current value so a result that predates this mount counts as
	// already handled; plain `let`, not $state, or writing it would re-run us.
	let handledSyncResult: PeerSyncResult | null = syncStore.lastResult;
	$effect(() => {
		const result = syncStore.lastResult;
		if (result !== null && result !== handledSyncResult) {
			handledSyncResult = result;
			handleSyncResult();
		}
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


	// Fetch the review queue when the page loads with already-listened content
	// (so "Check your work" shows the right count from first paint). Depends on
	// the ID, not just `isListened`: navigating between two already-listened
	// lessons leaves that boolean true, so a $derived equality check swallows the
	// change and the link keeps the previous lesson's count.
	$effect(() => {
		if (listenedStore.has(data.lesson.id)) listen.fetchQueue();
	});

	// Fully acquired: no remaining candidates AND no words in the review queue
	// No "fully acquired" terminal state any more (dropped 2026-07-27). It was
	// `remaining_candidates === 0 && queueCount === 0` and swapped the listen
	// button for a DISABLED "✓ Listened (n×)". Once queueCount came to mean
	// "pending autogrades" rather than "the lesson's study queue", it hit 0
	// whenever a listen staged nothing — skipping every row, say — so the page
	// declared the lesson finished and locked the one control that re-listening
	// needs. The mastery line below already reports how well the lesson is
	// known; that is the honest signal, and the button stays a button.


	// ⚠️ ONE IMPLEMENTATION, SHARED WITH THE REVIEW-SESSION READER. These ~200
	// lines used to live here and were nearly copied into that page when its
	// transcript endpoint missed on a session id. Only the SOURCE differs
	// between the two — see $lib/reading/readingActions.svelte.ts.

</script>

<svelte:window onresize={measureNav} />

<main>
	<LessonReader
		title={data.lesson.title}
		content={data.lesson}
		{audio}
		{transcript}
		{transcriptLoading}
		{reading}
		{navHeight}
		bind:controller={playbackController}
	>
		{#snippet headerAbove()}
				<a class="breadcrumb" href="/c/{data.curriculum.id}">← {data.curriculum.topic}</a>
				<!-- Day pager: its own full-width band directly under the curriculum link,
				     prev hard left and next hard right, so it reads as one axis of travel
				     rather than two links fighting the breadcrumb for the same corner.
				     Hidden entirely (not rendered empty) when there is no neighbour — an
				     empty nav would still cost a grid row and its gap. -->
				{#if prevLesson || nextLesson}
					<nav class="lesson-nav" aria-label="Lesson navigation">
						{#if prevLesson}
							<a class="lesson-nav-link" href="/c/{data.curriculum.id}/l/{prevLesson.lesson_id}">← Day {prevLesson.position}</a>
						{/if}
						{#if nextLesson}
							<a class="lesson-nav-link lesson-nav-next" href="/c/{data.curriculum.id}/l/{nextLesson.lesson_id}">Day {nextLesson.position} →</a>
						{/if}
					</nav>
				{/if}
		{/snippet}
		{#snippet header()}
				<div class="player-title-area">
					<h1>{data.lesson.title}</h1>
					{#if syncStatus}
						<p class="sync-status">{syncStatus}</p>
					{/if}
					{#if error}
						<p class="error">{error}</p>
					{/if}
				</div>
		{/snippet}
		{#snippet headerBelow()}
				<!-- Stats read as lesson metadata under the title rather than a third
				     stacked line in the action row — same information, no extra row. -->
				<MasteryLine {transcript} />
		{/snippet}
		{#snippet noAudio()}
				<div class="render-row">
					<button onclick={handleRenderAudio} disabled={audioLoading}>
						{audioLoading ? 'Rendering…' : 'Render Audio'}
					</button>
					{#if thisDayPipeline && !audioLoading}
						<span class="pipeline-state state-{thisDayPipeline.state}">{thisDayPipeline.state}</span>
					{/if}
				</div>
		{/snippet}
		{#snippet actions()}
			<ListenActions
				{listen}
				reviewHref="/review?lesson={data.lesson.id}&back=/c/{data.curriculum.id}/l/{data.lesson.id}"
				hasError={error !== ''}
			/>
		{/snippet}
	</LessonReader>

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
		{#if reviewRequested.length > 0}
			<!-- Deliberately neutral: at the default pressure the prompt tells the
			     model that using none of these is a correct answer, so a low number
			     is an observation, not a score with a bad end. -->
			<p class="review-coverage" data-testid="review-coverage">
				Reused {reviewUsed.length} of {reviewRequested.length} words you were forgetting{#if reviewUsed.length > 0}: <span class="review-words">{reviewUsed.join(', ')}</span>{/if}
			</p>
		{/if}
		<div class="regen-row">
			<button class="regen-btn" onclick={handleRegenerate} disabled={regenerating}>
				{regenerating ? 'Regenerating…' : `Regenerate Day ${dayPosition}`}
			</button>
			<!-- Regeneration hits the LLM, so surface the quota chip here to track usage. -->
			<RateLimitWidget />
			<button type="button" class="help-toggle"
				aria-label="What does regenerate do?"
				aria-expanded={showRegenHelp}
				onclick={() => (showRegenHelp = !showRegenHelp)}>?</button>
		</div>
		{#if showRegenHelp}
			<p class="help-panel">Regenerating rewrites this day's dialogue with the current prompt (better declension &amp; conjugation coverage). Existing cards stay; new vocabulary and morphology drills are added when you next listen and sync.</p>
		{/if}
		{#if regenStatus}
			<p class="regen-status" data-testid="regen-status">
				<span class="pipeline-state state-{regenStatus.state}">{regenStatus.state}</span>
				{#if regenStatus.message}
					<span class="regen-detail" data-testid="regen-detail">{regenStatus.state === 'failed' ? 'Last regeneration failed: ' : ''}{regenStatus.message}</span>
				{/if}
			</p>
		{/if}
		<hr />
		<LessonSourcePanel
			lessonId={data.lesson.id}
			curriculumId={data.curriculum.id}
			day={data.lesson.day}
			onImported={(newLessonId) => goto(`/c/${data.curriculum.id}/l/${newLessonId}`)}
		/>
		<hr />
		<div class="delete-day-row">
			<button
				type="button"
				class="delete-day-btn"
				class:confirming={confirmingDeleteDay}
				onclick={handleDeleteDayClick}
				onblur={handleDeleteDayBlur}
				disabled={deletingDay}
			>
				{confirmingDeleteDay ? 'Confirm delete' : `Delete day ${dayPosition}`}
			</button>
		</div>
	</details>
</main>

{#if listen.showPreview}
	<ListenPreviewModal {...listen.previewProps} />
{/if}

<style>
	main {
		max-width: 700px;
		margin: 1.5rem auto;
		padding: 0 1rem;
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}
	/* Four stacked bands, narrowing from context to content: curriculum link,
	   day pager, then the title sharing ONE row with the mode toggle (they are
	   the two things the eye lands on), then the stats. Only that third band is
	   two-column; the rest span the full width so the pager's arrows can sit at
	   the card's outer edges. */
	/* Level with the title, not the top of its column: `align-items: start` would
	   hang the pill off the band's top edge and the h1 is the taller box. */
	.player-title-area {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		min-width: 0;
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
	/* Prev at the left edge, next at the right — the pager's own width IS the
	   affordance. `margin-left: auto` on the next link (rather than
	   space-between) keeps it hard right when prev is absent on day one. */
	.lesson-nav {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		/* The pager sits between two muted lines that both start with an arrow;
		   without this it reads as a second breadcrumb glued to the first. */
		margin: 0.15rem 0 0.35rem;
	}
	.lesson-nav-next {
		margin-left: auto;
	}
	/* Same treatment as .breadcrumb, one step smaller — these are secondary to the
	   "back to curriculum" link they sit opposite. */
	.lesson-nav-link {
		color: var(--color-muted);
		font-size: 0.8rem;
		/* One notch lighter than .breadcrumb (600): sibling navigation is
		   secondary to the way back out. */
		font-weight: 500;
		text-decoration: none;
		white-space: nowrap;
	}
	.lesson-nav-link:hover {
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
		font-weight: 700;
		cursor: pointer;
		line-height: 1;
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
	.review-coverage {
		margin: 0 0 0.6rem;
		font-size: 0.8rem;
		color: var(--color-muted);
	}
	.review-words {
		color: var(--color-text);
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
	.delete-day-row {
		display: flex;
		justify-content: flex-end;
	}
	.delete-day-btn {
		margin-top: 0;
		padding: 0.5rem 1.1rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.delete-day-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.delete-day-btn.confirming {
		border-color: var(--color-danger);
		color: var(--color-danger);
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
	/* Single centered action row: the button keeps the card's centre line it has
	   always had, with whatever the last listen produced beside it. Wraps on
	   narrow viewports rather than squeezing the link. */
	.listen-actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: center;
		gap: 0.5rem 0.9rem;
	}
	.listen-btn {
		/* The component-wide `button` rule adds a 0.75rem top margin, which in a
		   flex row just offsets the button from its neighbours. */
		margin-top: 0;
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
	.check-work-link {
		display: inline-block;
		color: var(--color-primary);
		font-weight: 600;
		font-size: 0.9rem;
		text-decoration: none;
	}
	.check-work-link:hover {
		text-decoration: underline;
	}
	.mastery-line {
		color: var(--color-muted);
		font-size: 0.82rem;
		margin: 0;
	}
	.mastery-pct {
		font-weight: 700;
	}
	.mastery-segment {
		cursor: default;
	}
	/* Element boundary instead of a bare text node: Svelte collapses inline
	   whitespace around component tags, which ate the ` · ` separator. */
	.mastery-sep {
		margin: 0 0.3em;
	}
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
	.tools-card .muted {
		margin: 0.75rem 0 0;
	}
	.tools-card hr {
		border: none;
		border-top: 1px solid var(--color-border);
		margin: 1rem 0;
		opacity: 0.5;
	}
	.download-links {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.download-all-btn {
		display: inline-block;
		padding: 0.5rem 1.25rem;
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
