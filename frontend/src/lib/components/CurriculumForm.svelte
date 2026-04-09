<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { CurriculumSummary } from '$lib/api';
	import { saveFormPreferences, loadFormPreferences } from '$lib/storage';

	interface Props {
		onGenerate: (curriculum: CurriculumSummary) => void;
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
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	label {
		display: block;
		margin-bottom: 0.5rem;
	}
	input,
	select {
		display: block;
		margin-top: 0.25rem;
		width: 100%;
		padding: 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: 4px;
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
</style>
