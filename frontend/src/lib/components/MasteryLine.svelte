<script lang="ts">
	import type { TranscriptData } from '$lib/api';
	import { lessonMastery } from '$lib/mastery';
	import type { SideResult } from '$lib/mastery';
	import Tooltip from './Tooltip.svelte';

	/**
	 * How much of this content is known, from the transcript alone.
	 *
	 * ⚠️ NOTHING HERE IS CURRICULUM- OR DAY-SCOPED, and it never was. It is pure
	 * client-side arithmetic over the transcript — no API call, no lesson id. It
	 * lived inline on the lesson page and was wrongly described (by me) as
	 * day-scoped when the review-session reader was being built; extracting it
	 * makes the truth structural.
	 */
	/**
	 * *extra* appends one more segment to the same line — same separator, same
	 * type. It exists so a caller with its own one-number observation does not
	 * need a SECOND line for it: on a phone a standalone stat line costs ~17px,
	 * and stacked stat lines are the easiest vertical space in the card to give
	 * back. `tooltip` carries the full phrasing, because the terse form has to be
	 * short enough to keep the line from wrapping — a merge that wraps saves
	 * nothing.
	 */
	let {
		transcript,
		extra = null
	}: {
		transcript: TranscriptData | null;
		/** `tooltip` is REQUIRED: the on-line text has to be terse enough not to
		 *  wrap, so the full phrasing has nowhere else to live. An optional one
		 *  produced a branch nothing ever took. */
		extra?: { text: string; tooltip: string } | null;
	} = $props();

	const mastery = $derived(transcript ? lessonMastery(transcript) : null);

	const sideRows = $derived<Array<readonly [string, SideResult]>>(
		mastery
			? [
					['Understand', mastery.sides.understand],
					['Produce', mastery.sides.produce]
				]
			: []
	);

	const BAND_ORDER = ['solid', 'months', 'weeks', 'days', 'learning', 'suspended', 'new', 'none'] as const;
	function total(bands: Record<string, number>): number {
		return Object.values(bands).reduce((a, b) => a + b, 0);
	}
	function pctText(p: number | null): string {
		return p == null ? '—' : `${Math.round(p * 100)}%`;
	}

	const segments = $derived(
		!mastery
			? []
			: [
					{ key: 'new', count: mastery.counts.new, label: 'new', lemmas: mastery.lemmas?.new ?? [] },
					{ key: 'learning', count: mastery.counts.learning, label: 'learning', lemmas: mastery.lemmas?.learning ?? [] },
					{ key: 'due', count: mastery.counts.due, label: 'due', lemmas: mastery.lemmas?.due ?? [] },
					{ key: 'review', count: mastery.counts.review, label: 'review', lemmas: mastery.lemmas?.review ?? [] },
					{ key: 'known', count: mastery.counts.known, label: 'known', lemmas: mastery.lemmas?.known ?? [] }
				].filter((s) => s.count > 0 || s.key === 'known')
	);

	const LEMMA_TOOLTIP_MAX = 15;
	function formatLemmaTooltip(lemmas: string[]): string {
		if (lemmas.length <= LEMMA_TOOLTIP_MAX) return lemmas.join(', ');
		return lemmas.slice(0, LEMMA_TOOLTIP_MAX).join(', ') + ` … +${lemmas.length - LEMMA_TOOLTIP_MAX} more`;
	}
</script>

{#if mastery && mastery.pct !== null}
	<p class="mastery-line">
		{#each segments as seg, i (seg.key)}{#if i > 0}<span class="mastery-sep">·</span>{/if}{#if seg.lemmas.length > 0}<Tooltip translation={formatLemmaTooltip(seg.lemmas)}><span class="mastery-segment" role="button" tabindex="0" onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') (e.currentTarget as HTMLElement).click(); }}>{seg.count} {seg.label}</span></Tooltip>{:else}<span class="mastery-segment">{seg.count} {seg.label}</span>{/if}{/each}{#if extra}<span class="mastery-sep">·</span><Tooltip translation={extra.tooltip}><span class="mastery-segment mastery-extra" role="button" tabindex="0" onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') (e.currentTarget as HTMLElement).click(); }}>{extra.text}</span></Tooltip>{/if}
	</p>
	<div class="mastery-sides">
		{#each sideRows as [label, side] (label)}
			<span class="side-label">{label}</span>
			<span class="side-bar" role="img" aria-label="{label}: {pctText(side.pct)}">
				{#each BAND_ORDER as band (band)}{#if (side.bands[band] ?? 0) > 0}<i class="seg seg-{band}" style:width="{(100 * side.bands[band]) / total(side.bands)}%"></i>{/if}{/each}
			</span>
			<span class="side-pct">{pctText(side.pct)}</span>
		{/each}
	</div>
{/if}

<style>
	.mastery-line {
		color: var(--color-muted);
		font-size: 0.82rem;
		margin: 0;
	}
	.mastery-segment {
		cursor: default;
	}
	.mastery-sep {
		margin: 0 0.3em;
	}
	.mastery-sides {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		gap: 3px 8px;
		align-items: center;
		margin-top: 4px;
		font-size: 0.72rem;
		color: var(--color-muted);
		font-variant-numeric: tabular-nums;
	}
	.side-bar {
		display: flex;
		height: 5px;
		background: var(--band-track);
		overflow: hidden;
	}
	.seg { display: block; height: 100%; }
	.seg-solid { background: var(--band-solid); }
	.seg-months { background: var(--band-months); }
	.seg-weeks { background: var(--band-weeks); }
	.seg-days { background: var(--band-days); }
	.seg-learning { background: var(--band-learning); }
	.seg-new, .seg-suspended { background: transparent; }
	.seg-none { background: repeating-linear-gradient(90deg, var(--color-muted) 0 3px, transparent 3px 6px); }
	.side-pct { text-align: right; min-width: 2.6em; }
	/* On a phone this line is the width budget for every segment on it, and the
	   separators alone were ~39px of it — five gaps at 0.6em. Tightening them,
	   plus a slightly smaller face, buys back enough room for the trailing
	   segment to carry a WORD instead of a bare number. Desktop keeps the roomier
	   spacing.

	   It was measured at 323px of 327 available on a 390px screen (~4px of
	   slack) while the line still led with the blended percent. That percent
	   moved to the Understand / Produce row below (bd tunatale-yh47.7), so the
	   line has more room now — but a wider figure (a four-digit review count, a
	   longer locale) still WRAPS it, costing ~15px. Re-measure with the
	   whitespace:nowrap clone probe before adding anything to this line. */
	@media (max-width: 430px) {
		.mastery-line {
			font-size: 0.78rem;
		}
		.mastery-sep {
			margin: 0 0.12em;
		}
	}
</style>
