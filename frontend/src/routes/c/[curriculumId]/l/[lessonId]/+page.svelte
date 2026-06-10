<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import type { LessonAudio, TranscriptData } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import Transcript from '$lib/components/Transcript.svelte';
	import TranscriptPlaceholder from '$lib/components/TranscriptPlaceholder.svelte';
	import { syncStore } from '$lib/stores/sync.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const SECTION_TITLES: Record<string, string> = {
		key_phrases: 'Key Phrases',
		natural_speed: 'Natural Speed',
		slow_speed: 'Slow Speed',
		translated: 'Translated'
	};

	let mode = $state<'read' | 'listen'>('read');

	// untrack: intentionally snapshot load data as mutable local state
	let audio: LessonAudio | null = $state(untrack(() => data.audio));
	let transcript: TranscriptData | null = $state(untrack(() => data.transcript));
	// Starts true when load didn't supply a transcript (production: we fetch it
	// client-side below) so the section shows the spinner from first paint.
	let transcriptLoading = $state(untrack(() => data.transcript === null));
	let listenLoading = $state(false);
	let listenResult: { registered: number } | null = $state(null);
	let audioLoading = $state(false);
	let regenLoading = $state(false);
	let syncStatus = $state('');
	let error = $state('');

	let isListened = $derived(listenedStore.has(data.lesson.id));

	// SvelteKit reuses this component on same-route param changes (e.g. the
	// Regenerate button's goto, or lesson→lesson nav). The untracked local
	// copies above must follow `data` instead of staying frozen on the prior
	// lesson — otherwise audio/transcript show stale content after navigation.
	$effect(() => {
		audio = data.audio;
		const provided = data.transcript;
		if (provided !== null) {
			// Supplied by load (or passed directly in a test) — render it as-is.
			transcript = provided;
			return;
		}
		// Not preloaded: fetch client-side so the lesson shell renders immediately
		// instead of blocking on the (classla-backed) transcript endpoint, which can
		// take many seconds on a cold backend.
		const lessonId = data.lesson.id;
		transcript = null;
		transcriptLoading = true;
		error = '';
		api.getLessonTranscript(lessonId)
			.then((t) => {
				transcript = t;
			})
			.catch((e) => {
				error = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				transcriptLoading = false;
			});
	});

	async function handleRenderAudio() {
		audioLoading = true;
		error = '';
		try {
			audio = await api.renderAudio(data.lesson.id);
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			audioLoading = false;
		}
	}

	async function handleRegenerate() {
		const confirmed = window.confirm(
			`Regenerate Day ${data.lesson.day}? This creates a new version of the dialogue using the ` +
				`current generation prompt. Your existing cards are kept; new vocabulary and ` +
				`morphology drills are added on the next listen + sync.`
		);
		if (!confirmed) return;
		regenLoading = true;
		error = '';
		try {
			const summary = await api.generateStory(data.curriculum.id, data.lesson.day);
			await goto(`/c/${data.curriculum.id}/l/${summary.id}`);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			regenLoading = false;
		}
	}

	// A sync changes per-word due/known states in the backend, but nothing else on
	// this page tracks that. Re-fetch the transcript so the rendered states reflect
	// the sync, and surface a short summary (SyncButton hides its own once a
	// callback is supplied).
	async function handleSyncResult() {
		syncStatus = 'Synced with AnkiWeb';
		error = '';
		try {
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	$effect(() => {
		if (syncStore.lastResult) handleSyncResult();
	});

	async function handleMarkListened() {
		listenLoading = true;
		error = '';
		try {
			const result = await api.markAsListened(data.lesson.id, {});
			listenResult = result;
			listenedStore.add(data.lesson.id);
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			listenLoading = false;
		}
	}

	async function handleWordClick(word: import('$lib/api').WordToken, lineIndex: number) {
		error = '';
		try {
			if (word.active_state === 'unknown') {
				const sentence = transcript!.dialogue_lines[lineIndex]?.sentence ?? '';
				await api.createBaseCard({
					surface: word.surface,
					lemma: word.lemma,
					sentence,
					language_code: data.lesson.language_code,
					translation: word.translation ?? ''
				});
			} else if (word.is_due && word.active_direction && word.srs_item_id != null) {
				await api.submitDrill(
					word.srs_item_id,
					word.active_direction as 'recognition' | 'production',
					'good'
				);
			} else {
				return;
			}
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	async function handleCollocationStateChange(span_id: number) {
		error = '';
		try {
			await api.submitDrill(span_id, 'recognition', 'good');
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

<main>
	<a class="breadcrumb" href="/c/{data.curriculum.id}">← {data.curriculum.topic}</a>

	<section class="card">
		<h1>{data.lesson.title}</h1>
		<p class="section-meta">
			{#each data.lesson.sections as section, i (i)}
				{SECTION_TITLES[section.type] ?? section.type} — {section.phrases.length} phrase{section.phrases.length === 1 ? '' : 's'}{i < data.lesson.sections.length - 1 ? ' · ' : ''}
			{/each}
		</p>

		<div class="toggle-pill">
			<button class:active={mode === 'read'} onclick={() => mode = 'read'}>Read</button>
			<button class:active={mode === 'listen'} onclick={() => mode = 'listen'}>Listen</button>
		</div>

		{#if !audio}
			<button onclick={handleRenderAudio} disabled={audioLoading}>
				{audioLoading ? 'Rendering…' : 'Render Audio'}
			</button>
		{/if}
		{#if syncStatus}
			<p class="sync-status">{syncStatus}</p>
		{/if}
		{#if error}
			<p class="error">{error}</p>
		{/if}
	</section>

	{#if audio}
		<AudioPlayer {audio} />
	{/if}

	{#if mode === 'read'}
		<section class="card">
			{#if transcript}
				<Transcript
					{transcript}
					lesson={data.lesson}
					onWordClick={handleWordClick}
					onCollocationStateChange={handleCollocationStateChange}
					onCreatePhrase={handleCreatePhrase}
					tooltipActions={tooltipActions}
				/>
			{:else if transcriptLoading}
				<TranscriptPlaceholder lesson={data.lesson} />
			{:else}
				<p class="muted">No transcript available.</p>
			{/if}
		</section>
	{:else}
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

	<section class="card">
		<p class="muted">
			Regenerating rewrites this day's dialogue with the current prompt (better declension &amp;
			conjugation coverage). Existing cards stay; new vocabulary and morphology drills are added when
			you next listen and sync.
		</p>
		<button class="regen-btn" onclick={handleRegenerate} disabled={regenLoading}>
			{regenLoading ? 'Regenerating…' : `Regenerate Day ${data.lesson.day}`}
		</button>
	</section>
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
		margin-top: 0;
		font-size: 1.4rem;
		font-weight: 800;
		letter-spacing: -0.01em;
	}
	.section-meta {
		margin: 0.5rem 0 0;
		font-size: 0.9rem;
		color: var(--color-muted);
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
		margin-top: 0.5rem;
	}
	.sync-status {
		color: var(--color-muted);
		font-size: 0.85rem;
		margin-top: 0.5rem;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.9rem;
	}
	.regen-btn {
		background: transparent;
		color: var(--color-danger);
		border: 1px solid var(--color-danger);
	}
	.regen-btn:not(:disabled):hover {
		background: color-mix(in srgb, var(--color-danger) 12%, transparent);
	}
	.toggle-pill {
		display: flex;
		gap: 0;
		margin-top: 0.75rem;
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
</style>
