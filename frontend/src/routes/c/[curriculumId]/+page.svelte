<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DayPicker from '$lib/components/DayPicker.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let error = $state('');
	let progress: Map<number, string> = $state(new Map());

	onMount(async () => {
		try {
			const days = await api.getCurriculumProgress(data.curriculum.id);
			progress = new Map(days.map(d => [d.day, d.lesson_id]));
		} catch { /* non-critical */ }
	});

	async function handleSelectDay(day: number) {
		error = '';
		try {
			let lesson;
			try {
				lesson = await api.getLessonByDay(data.curriculum.id, day);
			} catch {
				// No cached lesson for this day — generate one
				const summary = await api.generateStory(data.curriculum.id, day);
				lesson = await api.getLesson(summary.id);
			}
			await goto(`/c/${data.curriculum.id}/l/${lesson.id}`);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
</script>

<main>
	<section class="curriculum-section">
		<h2>{data.curriculum.topic}</h2>
		<p class="meta">{data.curriculum.days} days · {data.curriculum.language_code.toUpperCase()}</p>
		<DayPicker curriculum={data.curriculum} onSelectDay={handleSelectDay} {progress} />
		{#if error}
			<p class="error">{error}</p>
		{/if}
	</section>
</main>

<style>
	main {
		max-width: 700px;
		margin: 2rem auto;
		font-family: system-ui, sans-serif;
		padding: 0 1rem;
	}
	.curriculum-section {
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	.meta {
		color: var(--color-muted);
		font-size: 0.9rem;
		margin-top: 0.25rem;
	}
	.error {
		color: var(--color-danger);
		margin-top: 0.75rem;
	}
</style>
