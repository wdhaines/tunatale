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
	<a class="back" href="/">← Lessons</a>
	<section class="curriculum-section card">
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
		margin: 1.5rem auto;
		padding: 0 1rem;
	}
	.back {
		display: inline-block;
		margin-bottom: 1rem;
		color: var(--color-muted);
		text-decoration: none;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.back:hover {
		color: var(--color-primary);
	}
	.curriculum-section {
		padding: 1.5rem;
	}
	.curriculum-section h2 {
		margin-top: 0;
		font-size: 1.5rem;
		font-weight: 800;
		letter-spacing: -0.01em;
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
