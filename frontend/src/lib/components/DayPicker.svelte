<script lang="ts">
	import type { CurriculumSummary } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';

	interface Props {
		curriculum: CurriculumSummary;
		onSelectDay: (day: number) => void | Promise<void>;
		progress?: Map<number, string>;
	}

	let { curriculum, onSelectDay, progress = new Map() }: Props = $props();

	let loadingDay: number | null = $state(null);

	function dayState(day: number): 'empty' | 'generated' | 'listened' {
		const lessonId = progress.get(day);
		if (!lessonId) return 'empty';
		if (listenedStore.has(lessonId)) return 'listened';
		return 'generated';
	}

	async function handleClick(day: number) {
		if (loadingDay !== null) return;
		loadingDay = day;
		try {
			await onSelectDay(day);
		} finally {
			loadingDay = null;
		}
	}
</script>

<div class="days">
	{#each Array(curriculum.days) as _, i}
		{@const day = i + 1}
		{@const state = dayState(day)}
		<button
			class="day-btn state-{state}"
			onclick={() => handleClick(day)}
			disabled={loadingDay !== null}
		>
			{loadingDay === day ? '…' : state === 'listened' ? `✓ Day ${day}` : `Day ${day}`}
		</button>
	{/each}
</div>

<style>
	.days {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.75rem;
	}
	.day-btn {
		padding: 0.5rem 1.25rem;
		color: white;
		border: none;
		border-radius: 4px;
		cursor: pointer;
		min-width: 5rem;
	}
	.day-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.state-empty {
		background: white;
		color: var(--color-primary);
		border: 2px solid var(--color-primary);
	}
	.state-generated {
		background: var(--color-primary);
		color: white;
	}
	.state-listened {
		background: var(--color-success);
		color: white;
	}
</style>
