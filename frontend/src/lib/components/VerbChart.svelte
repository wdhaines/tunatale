<script lang="ts">
	/**
	 * The verb chart (tunatale-h0q3.47). Desktop: the ten-column grid laid out by
	 * `layoutChart`. Phone: one card per chart row, affixes always shown and the
	 * examples a tap away (owner's option A, 2026-11-09). Both are rendered and CSS picks
	 * one, so the switch needs no script and a resize never loses state.
	 */
	import type { VerbChart, VerbChartExample } from '#lib/api.js';
	import { cardEntries, layoutChart, rowSummary, visibleRows } from '#lib/verbChartLayout.js';
	import Marks from '#lib/components/Marks.svelte';
	import { t } from '#lib/i18n/i18n.svelte.js';

	let { chart, targetRow = null }: { chart: VerbChart; targetRow?: string | null } = $props();

	let showLiterary = $state(false);
	// The phone cards a tap has opened; the row a link targets starts open.
	let opened = $state<Record<string, boolean>>({});

	const grid = $derived(layoutChart(chart, showLiterary));
	const volitional = $derived(chart.modes[0]);
	const potential = $derived(chart.modes[1]);
	const sourceTag = (s: VerbChartExample['source']) => (s === 'P' ? 'Wp' : s);

	function isOpen(rowId: string): boolean {
		return opened[rowId] ?? rowId === targetRow;
	}
	function toggle(rowId: string): void {
		opened = { ...opened, [rowId]: !isOpen(rowId) };
	}
</script>

{#snippet affixChip(label: string, kind: string)}
	<!-- A chip breaks only between alternatives ("mi-, ni-"), never inside an affix. -->
	<span class="chip {kind}"
		>{#each label.split(', ') as part, i (i)}{i > 0 ? ', ' : ''}<span class="nb">{part}</span>{/each}</span
	>
{/snippet}

{#snippet example(ex: VerbChartExample)}
	<span class="ex" title={ex.source_spelling}
		><em class="ceb">{ex.ceb}</em> <span class="en">{ex.en}</span><abbr class="src" title={chart.sources[ex.source]}
			>{sourceTag(ex.source)}</abbr
		></span
	>
{/snippet}

{#snippet fn(n: number | null)}{#if n !== null}<sup class="fn"><a href="#fn-{n}">{n}</a></sup>{/if}{/snippet}

<div class="verb-chart">
	<p class="intro"><Marks text={chart.intro} /></p>

	<div class="tools">
		<!-- Twice, because the target differs: the grid's trigger cell on a desktop, the
		     card heading on a phone, and an anchor into a display:none box scrolls nowhere. -->
		<nav class="jump wide" aria-label={t('reference.jumpTo')}>
			{#each chart.groups as group (group.id)}
				<a class="jump-link g-{group.id}" href="#group-{group.id}">{group.label}</a>
			{/each}
		</nav>
		<nav class="jump narrow" aria-label={t('reference.jumpTo')}>
			{#each chart.groups as group (group.id)}
				<a class="jump-link g-{group.id}" href="#card-group-{group.id}">{group.label}</a>
			{/each}
		</nav>
		<label class="lit-toggle">
			<input type="checkbox" bind:checked={showLiterary} />
			{t('reference.showLiterary')}
		</label>
	</div>

	<div class="grid-box">
		<table class="grid">
			<thead>
				<tr>
					<th rowspan="2">{t('reference.verbType')}</th>
					<th rowspan="2">{t('reference.trigger')}</th>
					<th rowspan="2">{t('reference.aspect')}</th>
					{#each chart.modes as mode, mi (mode.id)}
						<th class="mode" class:pstart={mi > 0} colspan={mode.columns.length}
							>{mode.label}{@render fn(mode.footnote)}<small><Marks text={mode.hint} /></small></th
						>
					{/each}
				</tr>
				<tr>
					{#each chart.modes as mode, mi (mode.id)}
						{#each mode.columns as col, ci (col.id)}
							<th class:pstart={mi > 0 && ci === 0}
								>{col.label}{@render fn(col.footnote)}<small><Marks text={col.hint} /></small></th
							>
						{/each}
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each grid as row (row.key)}
					<tr
						class="line-{row.line} g-{row.group}"
						class:group-start={row.groupStart}
						class:target={row.rowId === targetRow}
						data-row={row.rowId}
					>
						{#each row.cells as cell, ci (ci)}
							{#if cell.kind === 'vtype'}
								<th class="vtype" rowspan={cell.rowspan} scope="rowgroup"
									><b>{cell.label}</b><small>{cell.hint}</small></th
								>
							{:else if cell.kind === 'trigger'}
								<th
									class="trigger"
									id="group-{cell.group}"
									rowspan={cell.rowspan}
									colspan={cell.colspan}
									scope="rowgroup"><b>{cell.label}{@render fn(cell.footnote)}</b><small><Marks text={cell.hint} /></small></th
								>
							{:else if cell.kind === 'aspect'}
								<th class="aspect" rowspan={cell.rowspan} scope="row"
									><b>{cell.label}{@render fn(cell.footnote)}</b>{#if cell.hint}<small>{cell.hint}</small>{/if}</th
								>
							{:else if cell.kind === 'gloss'}
								<td class="gloss" class:pstart={cell.pstart}><Marks text={cell.text} /></td>
							{:else if cell.kind === 'chips'}
								<td class="chips" class:pstart={cell.pstart} colspan={cell.colspan}
									>{#each cell.affixes as a, ai (ai)}{@render affixChip(a.label, a.kind)}{/each}</td
								>
							{:else if cell.kind === 'examples'}
								<td class="examples" class:pstart={cell.pstart}
									>{#each cell.examples as ex, ei (ei)}{@render example(ex)}{/each}</td
								>
							{:else if cell.kind === 'above'}
								<td class="same pstart" colspan="3" rowspan={cell.rowspan}>{t('reference.sameAsAbove')}</td>
							{:else}
								<td class="none" class:pstart={cell.pstart} colspan={cell.colspan} rowspan={cell.rowspan}
									title={t('reference.noForm')}>—</td
								>
							{/if}
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	<div class="cards">
		{#each chart.groups as group (group.id)}
			<h3 class="card-group g-{group.id}" id="card-group-{group.id}">
				{group.label}{@render fn(group.footnote)} <small><Marks text={group.hint} /></small>
			</h3>
			{#each visibleRows(group.rows, showLiterary) as row (row.id)}
				<section class="card g-{group.id}" class:target={row.id === targetRow} data-row={row.id}>
					<button type="button" class="card-head" aria-expanded={isOpen(row.id)} onclick={() => toggle(row.id)}>
						<span><b>{row.aspect}</b>{#if row.hint}<small> {row.hint}</small>{/if}</span>
						<span aria-hidden="true">{isOpen(row.id) ? '▾' : '▸'}</span>
					</button>
					<div class="summary">
						{#each rowSummary(row, showLiterary) as a (a.label)}{@render affixChip(a.label, a.kind)}{/each}
					</div>
					{#if isOpen(row.id)}
						{#each [{ side: 'volitional' as const, mode: volitional }, { side: 'potential' as const, mode: potential }] as part (part.side)}
							{@const entries = cardEntries(chart, row, part.side, showLiterary)}
							{#if row[part.side] === 'above'}
								<p class="side-note">{part.mode.label}: {t('reference.sameAsAbove')}</p>
							{:else if entries.length > 0}
								<h4 class="side">{part.mode.label} · <Marks text={part.mode.hint} /></h4>
								{#each entries as entry (entry.column)}
									<div class="entry">
										<div class="entry-label">{entry.label}</div>
										<div class="entry-body">
											<div>{#each entry.affixes as a, ai (ai)}{@render affixChip(a.label, a.kind)}{/each}</div>
											{#each entry.examples as ex, ei (ei)}{@render example(ex)}{/each}
										</div>
									</div>
								{/each}
							{/if}
						{/each}
					{/if}
				</section>
			{/each}
		{/each}
	</div>

	<ul class="legend">
		{#each chart.legend as item (item.kind)}
			<li>{@render affixChip(item.example, item.kind)} {item.label}{@render fn(item.footnote)}</li>
		{/each}
	</ul>

	<ol class="footnotes">
		{#each chart.footnotes as note (note.n)}
			<li id="fn-{note.n}" value={note.n}><b>{note.title}.</b> <Marks text={note.text} /></li>
		{/each}
	</ol>

	<p class="sources">
		{#each Object.entries(chart.sources) as [tag, name] (tag)}
			<span><abbr class="src">{tag === 'P' ? 'Wp' : tag}</abbr> {name}</span>
		{/each}
	</p>
</div>

<style>
	.verb-chart {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		--trig: var(--color-secondary);
	}
	.g-agent {
		--trig: var(--color-primary);
	}
	.g-patient {
		--trig: var(--color-brand);
	}
	.g-circumstance {
		--trig: var(--color-success);
	}
	.g-instrument {
		--trig: #7a52a8;
	}
	:global(:root[data-theme='dark']) .g-instrument {
		--trig: #b597de;
	}
	.intro {
		margin: 0;
		max-width: 48rem;
		color: var(--color-text);
	}
	.tools {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		align-items: center;
		justify-content: space-between;
	}
	.jump {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.jump-link {
		padding: 0.35rem 0.8rem;
		border-radius: var(--radius-pill);
		font-size: 0.85rem;
		font-weight: 700;
		text-decoration: none;
		color: color-mix(in srgb, var(--trig) 75%, var(--color-text));
		background: color-mix(in srgb, var(--trig) 14%, var(--color-surface));
	}
	.lit-toggle {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.9rem;
		color: var(--color-muted);
		min-height: 44px;
	}
	.chip {
		display: inline-block;
		margin: 2px 4px 2px 0;
		padding: 2px 8px;
		border-radius: var(--radius-sm);
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.82rem;
		font-weight: 600;
		color: color-mix(in srgb, var(--trig) 75%, var(--color-text));
		background: color-mix(in srgb, var(--trig) 14%, var(--color-surface));
	}
	.nb {
		white-space: nowrap;
	}
	.chip.variant {
		background: transparent;
		border: 1px solid var(--color-border);
		color: var(--color-muted);
		font-weight: 500;
	}
	.chip.literary {
		opacity: 0.7;
		font-style: italic;
	}
	.chip.inferred {
		background: transparent;
		border: 1px dashed currentColor;
	}
	.ex {
		display: block;
		margin-bottom: 0.4rem;
		font-size: 0.85rem;
	}
	.ceb {
		font-weight: 700;
	}
	.en {
		color: var(--color-muted);
	}
	.src {
		display: inline-block;
		margin-left: 4px;
		padding: 0 3px;
		border: 1px solid var(--color-border);
		border-radius: 4px;
		font-size: 0.65rem;
		font-weight: 700;
		text-decoration: none;
		color: var(--color-muted);
		vertical-align: 1px;
	}
	.fn {
		font-size: 0.7em;
		margin-left: 1px;
	}

	/* Desktop grid */
	.grid-box {
		overflow-x: auto;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
	}
	.grid {
		width: 100%;
		min-width: 70rem;
		border-collapse: separate;
		border-spacing: 0;
		font-size: 0.82rem;
		line-height: 1.35;
	}
	.grid th,
	.grid td {
		padding: 0.3rem 0.45rem;
		text-align: left;
		vertical-align: top;
	}
	.grid thead th {
		vertical-align: bottom;
		border-bottom: 1px solid var(--color-border);
	}
	.grid th small {
		display: block;
		font-weight: 400;
		font-size: 0.72rem;
		color: var(--color-muted);
	}
	.grid th.mode {
		text-align: center;
	}
	.grid .pstart {
		border-left: 2px solid var(--color-border);
	}
	.grid .line-caption td,
	.grid .line-caption th {
		border-top: 1px solid var(--color-border);
		padding-top: 0.7rem;
	}
	.grid .gloss {
		font-size: 0.75rem;
		color: var(--color-muted);
	}
	.grid .vtype,
	.grid .trigger {
		background: color-mix(in srgb, var(--trig) 8%, var(--color-surface));
	}
	.grid .trigger b {
		color: color-mix(in srgb, var(--trig) 75%, var(--color-text));
	}
	.grid .same,
	.grid .none {
		color: var(--color-muted);
		font-style: italic;
	}
	.grid tr.target td {
		background: var(--color-highlight);
	}

	/* Phone cards (owner's option A) */
	.cards {
		display: none;
		flex-direction: column;
		gap: 0.6rem;
	}
	.card-group {
		margin: 0.6rem 0 0;
		font-size: 0.95rem;
		color: color-mix(in srgb, var(--trig) 75%, var(--color-text));
	}
	.card-group small {
		font-weight: 400;
		color: var(--color-muted);
	}
	.card {
		padding: 0.6rem 0.9rem;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
	}
	.card.target {
		border: 2px solid var(--color-accent);
	}
	.card-head {
		all: unset;
		box-sizing: border-box;
		display: flex;
		justify-content: space-between;
		align-items: center;
		width: 100%;
		min-height: 44px;
		cursor: pointer;
	}
	.card-head small {
		color: var(--color-muted);
	}
	.side {
		margin: 0.6rem 0 0.2rem;
		font-size: 0.72rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--color-muted);
	}
	.side-note {
		margin: 0.5rem 0 0;
		font-size: 0.85rem;
		color: var(--color-muted);
		font-style: italic;
	}
	.entry {
		display: grid;
		grid-template-columns: 5.5rem minmax(0, 1fr);
		gap: 0.6rem;
		padding: 0.5rem 0;
		border-top: 1px solid var(--color-border);
	}
	.entry-label {
		font-size: 0.8rem;
		font-weight: 700;
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem 1.2rem;
		margin: 0;
		padding: 0;
		list-style: none;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.footnotes {
		margin: 0;
		padding-left: 1.4rem;
		font-size: 0.85rem;
		color: var(--color-text);
		max-width: 52rem;
	}
	.footnotes li {
		margin-bottom: 0.4rem;
	}
	.sources {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem 1rem;
		margin: 0;
		font-size: 0.78rem;
		color: var(--color-muted);
	}

	.jump.narrow {
		display: none;
	}
	@media (max-width: 760px) {
		.jump.wide {
			display: none;
		}
		.jump.narrow {
			display: flex;
		}
		.grid-box {
			display: none;
		}
		.cards {
			display: flex;
		}
		.jump {
			flex-wrap: nowrap;
			overflow-x: auto;
		}
		.jump-link {
			flex: none;
		}
	}
</style>
