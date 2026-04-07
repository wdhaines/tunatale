<script lang="ts">
	import type { CurriculumSummary } from '$lib/api';

	interface Props {
		curriculum: CurriculumSummary;
		onSelectDay: (day: number) => void | Promise<void>;
	}

	let { curriculum, onSelectDay }: Props = $props();

	let loadingDay: number | null = $state(null);

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
		<button
			class="day-btn"
			onclick={() => handleClick(day)}
			disabled={loadingDay !== null}
		>
			{loadingDay === day ? '…' : `Day ${day}`}
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
		background: var(--color-primary);
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
</style>
