<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DayPicker from '$lib/components/DayPicker.svelte';
	import RateLimitWidget from '$lib/components/RateLimitWidget.svelte';
	import LlmActivityLog from '$lib/components/LlmActivityLog.svelte';
	import ManualStoryPanel from '$lib/components/ManualStoryPanel.svelte';
	import { pipelineStore } from '$lib/stores/pipeline.svelte';
	import { llmActivityStore } from '$lib/stores/llmActivity.svelte';
	import { rateLimitStore } from '$lib/stores/rateLimit.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let error = $state('');
	let progress: Map<number, string> = $state(new Map());
	let manualStoryDay: number | null = $state(null);

	const isManual = $derived((data.curriculum.generation_mode ?? 'auto') === 'manual');

	const pipelineStatus = $derived(pipelineStore.status);
	const pipelineStates = $derived(new Map(
		(pipelineStatus?.days ?? []).map(d => [d.day, d.state]),
	));

	async function refreshProgress() {
		try {
			const days = await api.getCurriculumProgress(data.curriculum.id);
			progress = new Map(days.map(d => [d.day, d.lesson_id]));
		} catch { /* non-critical */ }
	}

	onMount(async () => {
		// The rate-limit widget lives on this page now (not the global nav);
		// seed its store so it doesn't sit empty while the pipeline is idle.
		rateLimitStore.ensureFresh();
		await refreshProgress();
		pipelineStore.start(data.curriculum.id);
	});

	onDestroy(() => {
		pipelineStore.stop();
	});

	async function handleSelectDay(day: number) {
		error = '';

		const pipelineDay = pipelineStatus?.days.find(d => d.day === day);
		if (pipelineDay) {
			if (pipelineDay.state === 'ready' && pipelineDay.lesson_id) {
				await goto(`/c/${data.curriculum.id}/l/${pipelineDay.lesson_id}`);
				return;
			}
			if (pipelineDay.state === 'failed' && pipelineDay.retryable) {
				try {
					await api.retryPipelineDay(data.curriculum.id, day);
					// A failed pipeline polls at the idle 10s cadence — restart the
					// store so the retried state shows up immediately.
					pipelineStore.start(data.curriculum.id);
				} catch (e) {
					error = e instanceof Error ? e.message : String(e);
				}
				return;
			}
			// queued/generating/rendering: fall through to the cached-lesson
			// check — a render-only job (lesson exists, audio pending) must not
			// block opening the lesson page.
		}

		// Cached lesson (pre-pipeline days, or a lesson whose audio is still rendering)
		try {
			const lesson = await api.getLessonByDay(data.curriculum.id, day);
			await goto(`/c/${data.curriculum.id}/l/${lesson.id}`);
		} catch {
			// No lesson yet — in manual mode, offer story authoring
			if (isManual) {
				manualStoryDay = day;
			}
		}
	}
</script>

<main>
	<a class="back" href="/">← Lessons</a>
	<section class="curriculum-section card">
		<h2>{data.curriculum.topic}</h2>
		<p class="meta">
			{data.curriculum.days.length}
			{data.curriculum.days.length === 1 ? 'day' : 'days'} · {data.curriculum.language_code.toUpperCase()}
		</p>
		<RateLimitWidget />
		<DayPicker curriculum={data.curriculum} onSelectDay={handleSelectDay} {progress} pipelineStates={pipelineStates} />
		{#if manualStoryDay != null}
			<ManualStoryPanel
				curriculumId={data.curriculum.id}
				day={manualStoryDay}
				onImported={(id) => goto(`/c/${data.curriculum.id}/l/${id}`)}
				onDeleted={() => {
					manualStoryDay = null;
					refreshProgress();
				}}
			/>
		{/if}
		<a class="plan-link" href="/c/{data.curriculum.id}/plan">Plan next days →</a>
		{#if error}
			<p class="error">{error}</p>
		{/if}
	</section>
	<LlmActivityLog
		events={llmActivityStore.events}
		currentLine={llmActivityStore.currentLine}
		rateLimitStatus={rateLimitStore.status}
	/>
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
	.plan-link {
		display: inline-block;
		margin-top: 1rem;
		color: var(--color-primary);
		text-decoration: none;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.plan-link:hover {
		text-decoration: underline;
	}
</style>
