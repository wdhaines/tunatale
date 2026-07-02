<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { CurriculumCreated } from '$lib/api';
	import { saveFormPreferences, loadFormPreferences } from '$lib/storage';

	interface Props {
		onGenerate: (curriculum: CurriculumCreated) => void;
	}

	let { onGenerate }: Props = $props();

	let topic = $state('');
	let cefrLevel = $state('A2');
	let numDays = $state(7);
	let loading = $state(false);
	let error = $state('');

	onMount(() => {
		const prefs = loadFormPreferences();
		if (prefs) {
			topic = prefs.topic;
			cefrLevel = prefs.cefrLevel;
			numDays = prefs.numDays;
		}
	});

	$effect(() => {
		saveFormPreferences({ topic, cefrLevel, numDays });
	});

	async function handleGenerate() {
		loading = true;
		error = '';
		try {
			const curriculum = await api.generateCurriculum(topic, cefrLevel, numDays);
			onGenerate(curriculum);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}
</script>

<section class="input-section">
	<h2>Generate Curriculum</h2>
	<label>
		Topic
		<input bind:value={topic} placeholder="e.g. ordering coffee in Ljubljana" />
	</label>
	<label>
		CEFR Level
		<select bind:value={cefrLevel}>
			<option>A1</option>
			<option>A2</option>
			<option>B1</option>
			<option>B2</option>
		</select>
		<small class="cefr-hint">A1: Complete beginner · A2: Basic phrases · B1: Intermediate · B2: Complex topics</small>
	</label>
	<label>
		Days
		<input type="number" bind:value={numDays} min="1" max="30" />
	</label>
	<button onclick={handleGenerate} disabled={!topic.trim() || loading}>
		{loading ? 'Generating…' : 'Generate'}
	</button>
	{#if error}
		<p class="error">{error}</p>
	{/if}
</section>

<style>
	.input-section {
		margin-bottom: 1.25rem;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-sm);
		padding: 1.25rem;
	}
	h2 {
		margin-top: 0;
		font-size: 1.1rem;
	}
	label {
		display: block;
		margin-bottom: 0.75rem;
		font-size: 0.9rem;
		font-weight: 600;
	}
	input,
	select {
		display: block;
		margin-top: 0.3rem;
		width: 100%;
		padding: 0.5rem 0.6rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		background: var(--color-surface);
		color: var(--color-text);
		font-weight: 400;
		box-sizing: border-box;
	}
	.cefr-hint {
		display: block;
		color: var(--color-muted);
		font-size: 0.8rem;
		margin-top: 0.25rem;
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
		background: var(--color-surface-2);
		color: var(--color-muted);
		border: 1px solid var(--color-border);
		opacity: 1;
		cursor: not-allowed;
	}
	.error {
		color: var(--color-danger);
		margin-top: 0.5rem;
	}
</style>
