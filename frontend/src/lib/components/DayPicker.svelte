<script lang="ts">
	import type { CurriculumSummary } from '#lib/api.js';
	import { listenedStore } from '#lib/stores/listened.svelte.js';
	import { t } from '#lib/i18n/i18n.svelte.js';

	interface Props {
		curriculum: CurriculumSummary;
		onSelectDay: (day: number) => void | Promise<void>;
		progress?: Map<number, string>;
		pipelineStates?: Map<number, string>;
		/** Per lesson id, how many cards its Grade page shows for the remembered range. */
		dueCounts?: Map<string, number>;
	}

	let {
		curriculum,
		onSelectDay,
		progress = new Map(),
		pipelineStates = new Map(),
		dueCounts = new Map()
	}: Props = $props();

	function dueCount(day: number): number {
		const lessonId = progress.get(day);
		return lessonId ? (dueCounts.get(lessonId) ?? 0) : 0;
	}

	let loadingDay: number | null = $state(null);

	function dayState(day: number): 'empty' | 'generated' | 'listened' {
		const lessonId = progress.get(day);
		if (!lessonId) return 'empty';
		if (listenedStore.has(lessonId)) return 'listened';
		return 'generated';
	}

	function pipelineClass(day: number): string {
		const ps = pipelineStates.get(day);
		if (ps === 'queued' || ps === 'generating' || ps === 'rendering') return 'pulse';
		if (ps === 'failed') return 'danger';
		return '';
	}

	type Day = CurriculumSummary['days'][number];

	// The backend sends days in display order (a book's own order when the plan
	// carries one), so a block is a consecutive run of one group. An ungrouped
	// curriculum is one block with no heading (tunatale-cs3p).
	const blocks = $derived.by(() => {
		const out: { group: string; title: string; days: Day[] }[] = [];
		for (const d of curriculum.days) {
			const last = out.at(-1);
			if (last && last.group === d.group) last.days.push(d);
			else out.push({ group: d.group, title: d.group_title || d.group, days: [d] });
		}
		return out;
	});

	function listenedIn(days: Day[]): number {
		return days.filter(d => dayState(d.day) === 'listened').length;
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

{#each blocks as block, i (i)}
<section class="block">
	{#if block.group}
		<h3 class="block-head">
			<span>{block.title}</span>
			<span class="block-progress">{t('curriculum.blockProgress', { done: listenedIn(block.days), total: block.days.length })}</span>
		</h3>
	{/if}
<div class="days">
	{#each block.days as d (d.day)}
		{@const state = dayState(d.day)}
		{@const pClass = pipelineClass(d.day)}
		<button
			class="day-btn state-{state}"
			class:pulse={pClass === 'pulse'}
			class:danger={pClass === 'danger'}
			onclick={() => handleClick(d.day)}
			disabled={loadingDay !== null}
		>
			{loadingDay === d.day
				? '…'
				: state === 'listened'
					? `✓ Day ${d.position} · ${d.title}`
					: `Day ${d.position} · ${d.title}`}
			{#if loadingDay !== d.day && dueCount(d.day) > 0}
				<span
					class="due-badge"
					data-testid="due-badge"
					aria-label={t('curriculum.dueBadge', { count: dueCount(d.day) })}>{dueCount(d.day)}</span
				>
			{/if}
		</button>
	{/each}
</div>
</section>
{/each}

<style>
	.block-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 0.75rem;
		margin: 1.25rem 0 0;
		font-size: 1rem;
		font-weight: 700;
	}
	.block-progress {
		color: var(--color-muted);
		font-size: 0.85rem;
		font-weight: 600;
		white-space: nowrap;
	}
	.due-badge {
		display: inline-block;
		margin-left: 0.5rem;
		padding: 0 0.45rem;
		min-width: 1.25rem;
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-primary);
		font-size: 0.75rem;
		font-weight: 700;
		text-align: center;
		line-height: 1.25rem;
	}
	.days {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.75rem;
	}
	.day-btn {
		padding: 0.5rem 1.25rem;
		/* A <button>'s UA stylesheet centres its text. Harmless on one line, but
		   a long title wraps and every wrapped line then centres — the ragged
		   block reported for "Day 7 · <long title>". Left-aligning changes
		   nothing for single-line labels: with min-width 5rem and 1.25rem of
		   padding each side, essentially every label already exceeds the
		   min-width, so there is no slack for alignment to act on. */
		text-align: left;
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
	.day-btn.pulse {
		animation: pulse 1.2s ease-in-out infinite;
	}
	@keyframes pulse {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.4; }
	}
	.day-btn.danger {
		background: var(--color-danger);
		color: var(--color-on-primary);
	}
</style>
