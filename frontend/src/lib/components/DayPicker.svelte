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
	{#each curriculum.days as d (d.day)}
		{@const state = dayState(d.day)}
		<button
			class="day-btn state-{state}"
			onclick={() => handleClick(d.day)}
			disabled={loadingDay !== null}
		>
			{loadingDay === d.day ? '…' : state === 'listened' ? `✓ Day ${d.day} · ${d.title}` : `Day ${d.day} · ${d.title}`}
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
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		cursor: pointer;
		min-width: 5rem;
		font-weight: 600;
		font-size: 0.9rem;
		transition: transform 0.1s ease, box-shadow 0.15s ease, background 0.15s ease;
	}
	.day-btn:not(:disabled):hover {
		box-shadow: var(--shadow-sm);
		transform: translateY(-1px);
	}
	.day-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.state-empty {
		background: var(--color-surface);
		color: var(--color-primary);
		border: 2px solid var(--color-primary);
	}
	.state-generated {
		background: var(--color-primary);
		color: var(--color-on-primary);
	}
	.state-listened {
		background: var(--color-success);
		color: var(--color-on-primary);
	}
</style>
