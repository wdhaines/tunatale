<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import type { LessonAudio, TranscriptData } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import Transcript from '$lib/components/Transcript.svelte';
	import SyncButton from '$lib/components/SyncButton.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const SECTION_TITLES: Record<string, string> = {
		key_phrases: 'Key Phrases',
		natural_speed: 'Natural Speed',
		slow_speed: 'Slow Speed',
		translated: 'Translated'
	};

	// untrack: intentionally snapshot load data as mutable local state
	let audio: LessonAudio | null = $state(untrack(() => data.audio));
	let transcript: TranscriptData | null = $state(untrack(() => data.transcript));
	let listenLoading = $state(false);
	let listenResult: { registered: number } | null = $state(null);
	let audioLoading = $state(false);
	let regenLoading = $state(false);
	let error = $state('');

	let isListened = $derived(listenedStore.has(data.lesson.id));

	// SvelteKit reuses this component on same-route param changes (e.g. the
	// Regenerate button's goto, or lesson→lesson nav). The untracked local
	// copies above must follow `data` instead of staying frozen on the prior
	// lesson — otherwise audio/transcript show stale content after navigation.
	$effect(() => {
		audio = data.audio;
		transcript = data.transcript;
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
				const sentence = transcript!.dialogue_lines[lineIndex].sentence ?? '';
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
					language_code: data.lesson.language_code
				});
				transcript = await api.getLessonTranscript(data.lesson.id);
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		},
		onSetState: async (id: number, state: string) => {
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
	<h1><a href="/c/{data.curriculum.id}">← {data.curriculum.topic}</a></h1>

	<section class="lesson-header">
		<h2>{data.lesson.title}</h2>
		<ul>
			{#each data.lesson.sections as section, i (i)}
				<li>{SECTION_TITLES[section.type] ?? section.type} — {section.phrases.length} phrase{section.phrases.length === 1 ? '' : 's'}</li>
			{/each}
		</ul>

		{#if !audio}
			<button onclick={handleRenderAudio} disabled={audioLoading}>
				{audioLoading ? 'Rendering…' : 'Render Audio'}
			</button>
		{/if}
		<SyncButton />
		{#if error}
			<p class="error">{error}</p>
		{/if}
	</section>

	{#if audio}
		<AudioPlayer {audio} />
	{/if}

	<section class="transcript-section">
		{#if transcript}
			<Transcript
				{transcript}
				lesson={data.lesson}
				{isListened}
				{listenLoading}
				{listenResult}
				{error}
				onWordClick={handleWordClick}
				onCollocationStateChange={handleCollocationStateChange}
				onMarkListened={handleMarkListened}
				onCreatePhrase={handleCreatePhrase}
				tooltipActions={tooltipActions}
			/>
		{:else}
			<p class="muted">Transcript loading…</p>
		{/if}
	</section>

	<section class="regenerate-section">
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
		margin: 2rem auto;
		font-family: system-ui, sans-serif;
		padding: 0 1rem;
	}
	h1 a {
		color: inherit;
		font-size: 1rem;
		text-decoration: none;
		opacity: 0.7;
	}
	h1 a:hover {
		opacity: 1;
	}
	.lesson-header {
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	.lesson-header ul {
		padding-left: 1.25rem;
		margin: 0.5rem 0;
		font-size: 0.9rem;
		color: var(--color-muted);
	}
	button {
		margin-top: 0.75rem;
		padding: 0.5rem 1.25rem;
		background: var(--color-primary);
		color: white;
		border: none;
		border-radius: 4px;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.error {
		color: var(--color-danger);
		margin-top: 0.5rem;
	}
	.transcript-section {
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.9rem;
	}
	.regenerate-section {
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	.regen-btn {
		background: transparent;
		color: var(--color-danger);
		border: 1px solid var(--color-danger);
	}
</style>
