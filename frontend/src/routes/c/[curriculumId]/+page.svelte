<script lang="ts">
	import BackLink from '#lib/components/BackLink.svelte';
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { api, type DayOutline } from '#lib/api.js';
	import DayPicker from '#lib/components/DayPicker.svelte';
	import NextUp from '#lib/components/NextUp.svelte';
	import LlmActivityLog from '#lib/components/LlmActivityLog.svelte';
	import ManualStoryPanel from '#lib/components/ManualStoryPanel.svelte';
	import { pipelineStore } from '#lib/stores/pipeline.svelte.js';
	import { llmActivityStore } from '#lib/stores/llmActivity.svelte.js';
	import { rateLimitStore } from '#lib/stores/rateLimit.svelte.js';
	import { t } from '#lib/i18n/i18n.svelte.js';
	import { gradeRangePref } from '#lib/stores/gradeRangePref.svelte.js';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let error = $state('');
	let progress: Map<number, string> = $state(new Map());
	let dueCounts: Map<string, number> = $state(new Map());
	let outlines: Map<number, DayOutline> = $state(new Map());
	let manualStoryDay: number | null = $state(null);

	const isManual = $derived((data.curriculum.generation_mode ?? 'auto') === 'manual');

	const pipelineStatus = $derived(pipelineStore.status);
	const pipelineStates = $derived(new Map(
		(pipelineStatus?.days ?? []).map(d => [d.day, d.state]),
	));

	// Two-click delete, the same pattern the day panel below uses: the first
	// click arms, the second deletes, and blur disarms so a click elsewhere can't
	// leave a primed button behind. It lives with the curriculum it removes, as a
	// session's delete lives on the session's own page (bd tunatale-e6fq).
	let confirmingDelete = $state(false);
	let deleting = $state(false);

	async function handleDelete() {
		confirmingDelete = false;
		deleting = true;
		error = '';
		try {
			await api.deleteCurriculum(data.curriculum.id);
			await goto('/');
		} catch (e) {
			// A failure leaves the button enabled and disarmed, so the learner can
			// try again without a second arm.
			error = e instanceof Error ? e.message : String(e);
		} finally {
			deleting = false;
		}
	}

	function handleDeleteClick() {
		if (confirmingDelete) {
			handleDelete();
		} else {
			confirmingDelete = true;
		}
	}

	async function refreshProgress() {
		try {
			const days = await api.getCurriculumProgress(data.curriculum.id);
			progress = new Map(days.map(d => [d.day, d.lesson_id]));
		} catch { /* non-critical */ }
		void refreshDueCounts();
		void refreshOutlines();
	}

	// What each row opens to (tunatale-cs3p). A failure leaves rows closed-only.
	async function refreshOutlines() {
		try {
			const days = await api.getCurriculumOutline(data.curriculum.id);
			outlines = new Map(days.map(d => [d.day, d]));
		} catch { /* non-critical: no outlines */ }
	}

	// Each lesson's badge counts what its Grade page shows at the range the
	// learner last chose there (tunatale-5i1p.3).
	async function refreshDueCounts() {
		const ids = [...progress.values()];
		if (ids.length === 0) return;
		try {
			dueCounts = new Map(Object.entries(await api.getDueCounts(ids, gradeRangePref.value)));
		} catch { /* non-critical: no badges */ }
	}

	onMount(async () => {
		// The LLM activity log below reads the rate-limit store; seed it so it
		// doesn't sit empty while the pipeline is idle. (The budget chip itself
		// moved into Lesson tools, tunatale-cs3p.)
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
	<div class="back-row"><BackLink href="/" label={t('curriculum.backToLessons')} /></div>
	<section class="curriculum-section card">
		<h2>{data.curriculum.topic}</h2>
		<p class="meta">
			{t('curriculum.dayCount', { count: data.curriculum.days.length })} · {data.curriculum.language_code.toUpperCase()}
		</p>
		<NextUp curriculum={data.curriculum} {progress} onSelectDay={handleSelectDay} />
		<DayPicker curriculum={data.curriculum} onSelectDay={handleSelectDay} {progress} {dueCounts} {outlines} pipelineStates={pipelineStates} />
		{#if manualStoryDay != null}
			<ManualStoryPanel
				copyPrompt={async () => {
					const r = await api.getStoryPrompt(data.curriculum.id, manualStoryDay!);
					return r.system_prompt + '\n\n' + r.user_prompt;
				}}
				importRaw={async (raw) =>
					api.importStory({ curriculum_id: data.curriculum.id, day: manualStoryDay!, raw })}
				onImported={(id) => goto(`/c/${data.curriculum.id}/l/${id}`)}
				onDelete={async () => {
					// Two-click confirm lives in the panel (same pattern as the plan
					// page's Reset chat button); the OPERATION lives here, because it
					// is curriculum-shaped and a review session has no equivalent.
					// Deletes this day's lessons/audio server-side (there may be none
					// yet) so a wrongly planned day can be removed; existing SRS/Anki
					// cards are untouched, and there is no renumbering.
					await api.deleteCurriculumDay(data.curriculum.id, manualStoryDay!);
					manualStoryDay = null;
					refreshProgress();
				}}
			/>
		{/if}
		<a class="plan-link" href="/c/{data.curriculum.id}/plan">{t('curriculum.planLink')} →</a>
		<button
			type="button"
			class="delete-btn"
			class:confirming={confirmingDelete}
			aria-label={confirmingDelete
				? t('curriculum.confirmDeleteTopic', { topic: data.curriculum.topic })
				: t('curriculum.deleteTopic', { topic: data.curriculum.topic })}
			onclick={handleDeleteClick}
			onblur={() => (confirmingDelete = false)}
			disabled={deleting}
		>
			{confirmingDelete ? t('curriculum.confirmDelete') : t('curriculum.delete')}
		</button>
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
	.back-row {
		margin-bottom: 1rem;
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
	.delete-btn {
		display: block;
		margin-top: 1rem;
		padding: 0.4rem 0.9rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-muted);
		font-size: 0.8rem;
		font-weight: 600;
		cursor: pointer;
	}
	.delete-btn:hover {
		color: var(--color-danger);
		border-color: var(--color-danger);
	}
	.delete-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.delete-btn.confirming {
		border-color: var(--color-danger);
		color: var(--color-danger);
	}
</style>
