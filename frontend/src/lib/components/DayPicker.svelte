<script lang="ts">
	import type { CurriculumSummary, DayOutline } from '#lib/api.js';
	import { listenedStore } from '#lib/stores/listened.svelte.js';
	import { t } from '#lib/i18n/i18n.svelte.js';
	import { SvelteSet } from 'svelte/reactivity';

	interface Props {
		curriculum: CurriculumSummary;
		onSelectDay: (day: number) => void | Promise<void>;
		progress?: Map<number, string>;
		pipelineStates?: Map<number, string>;
		/** Per lesson id, how many cards its Grade page shows for the remembered range. */
		dueCounts?: Map<string, number>;
		/** Per day key, what its lesson holds; a day without one has no lesson yet. */
		outlines?: Map<number, DayOutline>;
	}

	let {
		curriculum,
		onSelectDay,
		progress = new Map(),
		pipelineStates = new Map(),
		dueCounts = new Map(),
		outlines = new Map()
	}: Props = $props();

	function dueCount(day: number): number {
		const lessonId = progress.get(day);
		return lessonId ? (dueCounts.get(lessonId) ?? 0) : 0;
	}

	let loadingDay: number | null = $state(null);
	const openDays = new SvelteSet<number>();

	function toggle(day: number) {
		if (openDays.has(day)) openDays.delete(day);
		else openDays.add(day);
	}

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

	const PART_LABEL: Record<string, () => string> = {
		key_phrases: () => t('curriculum.part.key_phrases'),
		dialogue: () => t('curriculum.part.dialogue'),
		drill: () => t('curriculum.part.drill'),
	};

	// A part a newer backend adds before this build knows it shows by its token.
	function partLabel(part: string): string {
		return PART_LABEL[part]?.() ?? part;
	}

	const DRILL_KIND_LABEL: Record<string, () => string> = {
		question_answer: () => t('curriculum.drillKind.question_answer'),
	};

	// A substitution drill is named by its model sentence, a transformation by
	// its instruction; a question-and-answer drill has neither (tunatale-cs3p).
	function drillText(d: DayOutline['drills'][number]): string {
		return d.model || d.instruction || DRILL_KIND_LABEL[d.kind]?.() || d.kind || t('curriculum.part.drill');
	}

	// One line under a row's title: its parts in lesson order, with an authored
	// drill part counted ("2 drills") rather than named.
	function summary(o: DayOutline): string {
		return o.parts
			.map(p => (p === 'drill' && o.drills.length > 0 ? t('curriculum.drillCount', { count: o.drills.length }) : partLabel(p)))
			.join(', ');
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
	{@const done = listenedIn(block.days)}
	<section class="block">
		{#if block.group}
			<h3 class="block-head">
				<span>{block.title}</span>
				<span class="block-progress">{t('curriculum.blockProgress', { done, total: block.days.length })}</span>
			</h3>
			<div class="bar" aria-hidden="true"><i style="width: {Math.round((done / block.days.length) * 100)}%"></i></div>
		{/if}
		<ul class="rows">
			{#each block.days as d (d.day)}
				{@const state = dayState(d.day)}
				{@const pClass = pipelineClass(d.day)}
				{@const outline = outlines.get(d.day)}
				{@const open = openDays.has(d.day)}
				<li class="row" class:open>
					<button
						type="button"
						class="day-btn state-{state}"
						class:pulse={pClass === 'pulse'}
						class:danger={pClass === 'danger'}
						data-testid="day-{d.day}"
						onclick={() => handleClick(d.day)}
						disabled={loadingDay !== null}
					>
						<span class="dot" aria-hidden={state !== 'listened'}>{state === 'listened' ? '✓' : ''}</span>
						<span class="t">
							<b>{loadingDay === d.day ? '…' : d.title}</b>
							{#if outline}<small>{summary(outline)}</small>{/if}
						</span>
						{#if loadingDay !== d.day && dueCount(d.day) > 0}
							<span
								class="due-badge"
								data-testid="due-badge"
								aria-label={t('curriculum.dueBadge', { count: dueCount(d.day) })}>{dueCount(d.day)}</span
							>
						{/if}
					</button>
					{#if outline}
						<button
							type="button"
							class="toggle"
							aria-expanded={open}
							aria-label={t('curriculum.showOutline', { title: d.title })}
							onclick={() => toggle(d.day)}><span aria-hidden="true">{open ? '▾' : '▸'}</span></button
						>
						{#if open}
							<ul class="outline" data-testid="outline-{d.day}">
								{#each outline.parts as part (part)}
									{#if part === 'drill' && outline.drills.length > 0}
										{#each outline.drills as drill, n (n)}
											<li>
												<span>{t('curriculum.drillLine', { n: n + 1, model: drillText(drill) })}</span><em
													>{t('curriculum.stepCount', { count: drill.steps })}</em
												>
											</li>
										{/each}
									{:else}
										<li><span>{partLabel(part)}</span></li>
									{/if}
								{/each}
							</ul>
						{/if}
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/each}

<style>
	.block {
		margin-top: 0.75rem;
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		overflow: hidden;
	}
	.block-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0;
		padding: 0.7rem 0.9rem 0.4rem;
		font-size: 1rem;
		font-weight: 700;
	}
	.block-progress {
		color: var(--color-muted);
		font-size: 0.8rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.bar {
		height: 3px;
		margin: 0 0.9rem 0.35rem;
		background: var(--color-border);
	}
	.bar i {
		display: block;
		height: 100%;
		background: var(--color-success);
	}
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.row {
		display: grid;
		grid-template-columns: 1fr auto;
		align-items: center;
		border-top: 1px solid var(--color-border);
	}
	.block-head + .bar + .rows > .row:first-child,
	.rows:first-child > .row:first-child {
		border-top: none;
	}
	.row.open {
		background: var(--color-surface);
	}
	.day-btn {
		display: grid;
		grid-template-columns: 1.25rem 1fr auto;
		gap: 0.65rem;
		align-items: center;
		min-width: 0;
		padding: 0.65rem 0.9rem;
		border: none;
		background: none;
		color: var(--color-text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.day-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.t {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.t b {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.t small {
		color: var(--color-muted);
		font-size: 0.8rem;
	}
	.dot {
		display: grid;
		place-items: center;
		width: 1.15rem;
		height: 1.15rem;
		box-sizing: border-box;
		border-radius: 50%;
		border: 2px solid var(--color-primary);
		color: var(--color-on-primary);
		font-size: 0.7rem;
	}
	.state-empty .dot {
		border-style: dashed;
		border-color: var(--color-muted);
	}
	.state-listened .dot {
		background: var(--color-success);
		border-color: var(--color-success);
	}
	.day-btn.danger .dot {
		border-color: var(--color-danger);
		background: var(--color-danger);
	}
	.day-btn.pulse .dot {
		animation: pulse 1.2s ease-in-out infinite;
	}
	@keyframes pulse {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.3; }
	}
	.due-badge {
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
	.toggle {
		align-self: stretch;
		min-width: 2.75rem;
		padding: 0 0.9rem;
		border: none;
		background: none;
		color: var(--color-muted);
		font-size: 1rem;
		cursor: pointer;
	}
	.outline {
		grid-column: 1 / -1;
		list-style: none;
		margin: 0;
		padding: 0 0.9rem 0.75rem 2.8rem;
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		font-size: 0.85rem;
	}
	.outline li {
		display: flex;
		justify-content: space-between;
		gap: 0.75rem;
	}
	.outline span {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.outline em {
		font-style: normal;
		color: var(--color-muted);
		white-space: nowrap;
	}
</style>
