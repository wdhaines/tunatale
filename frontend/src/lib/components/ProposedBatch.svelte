<script lang="ts">
	import type { ProposedBatch } from '$lib/api';
	import { batchRange } from '$lib/planner';

	interface Props {
		proposed: ProposedBatch;
		pending: boolean;
		onCommit: () => void | Promise<void>;
		onRevise: () => void;
	}

	let { proposed, pending, onCommit, onRevise }: Props = $props();

	const range = $derived(batchRange(proposed));
	const header = $derived(
		range.start === range.end
			? `Proposed: Day ${range.start}`
			: `Proposed: Days ${range.start}–${range.end}`
	);
</script>

<section class="proposed card">
	<h3>{header}</h3>
	<div class="day-cards">
		{#each proposed.days as d (d.day)}
			<article class="day-card">
				<header>
					<span class="day-num">Day {d.day}</span>
					<span class="title">{d.title}</span>
				</header>
				<p class="focus">{d.focus}</p>
				<div class="chips">
					{#each d.collocations as c (c)}
						<span class="chip">{c}</span>
					{/each}
				</div>
				<p class="objective">{d.learning_objective}</p>
				{#if d.story_guidance}
					<p class="guidance">{d.story_guidance}</p>
				{/if}
			</article>
		{/each}
	</div>
	<div class="actions">
		<button class="commit" onclick={onCommit} disabled={pending}>Commit batch</button>
		<button class="revise" onclick={onRevise} disabled={pending}>Revise</button>
	</div>
</section>

<style>
	.proposed {
		padding: 1rem 1.25rem;
	}
	h3 {
		margin: 0 0 0.75rem;
		font-size: 1.05rem;
		font-weight: 700;
	}
	.day-cards {
		display: grid;
		gap: 0.75rem;
	}
	.day-card {
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 0.75rem 0.9rem;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.day-card header {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
	}
	.day-num {
		font-size: 0.75rem;
		font-weight: 700;
		color: var(--color-primary);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.title {
		font-weight: 600;
	}
	.focus {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	.chip {
		padding: 0.15rem 0.6rem;
		border-radius: var(--radius-pill);
		background: var(--color-surface-2);
		font-size: 0.8rem;
	}
	.objective {
		margin: 0;
		font-size: 0.85rem;
	}
	.guidance {
		margin: 0;
		font-size: 0.8rem;
		font-style: italic;
		color: var(--color-muted);
	}
	.actions {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.9rem;
	}
	.commit {
		padding: 0.5rem 1.1rem;
		border: none;
		border-radius: var(--radius-pill);
		background: var(--color-success);
		color: var(--color-on-primary);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.revise {
		padding: 0.5rem 1.1rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.commit:disabled,
	.revise:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
