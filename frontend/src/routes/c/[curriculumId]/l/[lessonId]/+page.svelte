<script lang="ts">
	import { untrack } from 'svelte';
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

	const STATE_CYCLE: Record<string, string> = {
		unknown: 'learning',
		new: 'learning',
		learning: 'known',
		review: 'known',
		relearning: 'known',
		known: 'ignored',
		ignored: 'new',
		suspended: 'new'
	};

	// untrack: intentionally snapshot load data as mutable local state
	let audio: LessonAudio | null = $state(untrack(() => data.audio));
	let transcript: TranscriptData | null = $state(untrack(() => data.transcript));
	let listenLoading = $state(false);
	let listenResult: { registered: number } | null = $state(null);
	let audioLoading = $state(false);
	let error = $state('');

	let isListened = $derived(listenedStore.has(data.lesson.id));

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

	async function handleStateChange(lemma: string, srs_item_id: number | null) {
		error = '';
		try {
			let itemId = srs_item_id;
			let currentState = 'new';

			for (const line of transcript?.dialogue_lines ?? []) {
				const word = line.words.find((w) => w.lemma === lemma);
				if (word) {
					currentState = word.srs_state;
					break;
				}
			}

			const nextState = STATE_CYCLE[currentState] ?? 'learning';

			if (itemId === null) {
				const created = await api.createSRSItem({
					text: lemma,
					language_code: data.lesson.language_code,
					word_count: 1
				});
				itemId = created.id;
			}

			await api.setSRSItemState(itemId, nextState);
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	async function handleCollocationStateChange(
		_lemma: string,
		span_id: number,
		current_state: string
	) {
		error = '';
		try {
			const nextState = STATE_CYCLE[current_state] ?? 'learning';
			await api.setSRSItemState(span_id, nextState);
			transcript = await api.getLessonTranscript(data.lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

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
			{#each data.lesson.sections as section}
				<li>{SECTION_TITLES[section.type] ?? section.type} — {section.phrases.length} phrase{section.phrases.length === 1 ? '' : 's'}</li>
			{/each}
		</ul>

		{#if !audio}
			<button onclick={handleRenderAudio} disabled={audioLoading}>
				{audioLoading ? 'Rendering…' : 'Render Audio'}
			</button>
		{/if}
		<SyncButton deckName="0. Slovene" modelName="Slovene Vocabulary" />
		{#if error}
			<p class="error">{error}</p>
		{/if}
	</section>

	{#if audio}
		<AudioPlayer {audio} />

		<section class="transcript-section">
			{#if transcript}
				<Transcript
					{transcript}
					lesson={data.lesson}
					{isListened}
					{listenLoading}
					{listenResult}
					{error}
					onStateChange={handleStateChange}
					onCollocationStateChange={handleCollocationStateChange}
					onMarkListened={handleMarkListened}
					onCreatePhrase={handleCreatePhrase}
				/>
			{:else}
				<p class="muted">Transcript loading…</p>
			{/if}
		</section>
	{/if}
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
</style>
