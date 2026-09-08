<script lang="ts">
	import type { TranscriptData } from '$lib/api';
	import { lessonMastery, masteryColor } from '$lib/mastery';
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
	const pct = $derived(mastery?.pct ?? null);
	const counts = $derived(mastery?.counts ?? null);

	const segments = $derived(
		!counts
			? []
			: [
					{ key: 'new', count: counts.new, label: 'new', lemmas: mastery?.lemmas?.new ?? [] },
					{ key: 'learning', count: counts.learning, label: 'learning', lemmas: mastery?.lemmas?.learning ?? [] },
					{ key: 'due', count: counts.due, label: 'due', lemmas: mastery?.lemmas?.due ?? [] },
					{ key: 'review', count: counts.review, label: 'review', lemmas: mastery?.lemmas?.review ?? [] },
					{ key: 'known', count: counts.known, label: 'known', lemmas: mastery?.lemmas?.known ?? [] }
				].filter((s) => s.count > 0 || s.key === 'known')
	);

	const LEMMA_TOOLTIP_MAX = 15;
	function formatLemmaTooltip(lemmas: string[]): string {
		if (lemmas.length <= LEMMA_TOOLTIP_MAX) return lemmas.join(', ');
		return lemmas.slice(0, LEMMA_TOOLTIP_MAX).join(', ') + ` … +${lemmas.length - LEMMA_TOOLTIP_MAX} more`;
	}
</script>

{#if mastery && pct !== null}
	<p class="mastery-line">
		<span class="mastery-pct" style:color={masteryColor(pct)}>{Math.round(pct * 100)}%</span>
		{#each segments as seg, i (seg.key)}{#if i > 0}<span class="mastery-sep">·</span>{/if}{#if seg.lemmas.length > 0}<Tooltip translation={formatLemmaTooltip(seg.lemmas)}><span class="mastery-segment" role="button" tabindex="0" onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') (e.currentTarget as HTMLElement).click(); }}>{seg.count} {seg.label}</span></Tooltip>{:else}<span class="mastery-segment">{seg.count} {seg.label}</span>{/if}{/each}{#if extra}<span class="mastery-sep">·</span><Tooltip translation={extra.tooltip}><span class="mastery-segment mastery-extra" role="button" tabindex="0" onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') (e.currentTarget as HTMLElement).click(); }}>{extra.text}</span></Tooltip>{/if}
	</p>
{/if}

<style>
	.mastery-line {
		color: var(--color-muted);
		font-size: 0.82rem;
		margin: 0;
	}
	.mastery-pct {
		font-weight: 700;
	}
	.mastery-segment {
		cursor: default;
	}
	.mastery-sep {
		margin: 0 0.3em;
	}
	/* On a phone this line is the width budget for every segment on it, and the
	   separators alone were ~39px of it — five gaps at 0.6em. Tightening them,
	   plus a slightly smaller face, buys back enough room for the trailing
	   segment to carry a WORD instead of a bare number. Desktop keeps the roomier
	   spacing.

	   ⚠️ IT IS A TIGHT FIT AND THAT IS DELIBERATE, NOT AN OVERSIGHT: measured at
	   323px of 327 available on a 390px screen, so ~4px of slack. If a future
	   session carries a wider figure (a four-digit review count, a longer
	   locale), the line WRAPS — which costs ~15px and is exactly the state this
	   merge replaced. The failure mode is bounded and self-correcting, so it is
	   preferred to dropping a segment or shrinking the type further. Re-measure
	   with the whitespace:nowrap clone probe before adding anything to this
	   line. */
	@media (max-width: 430px) {
		.mastery-line {
			font-size: 0.78rem;
		}
		.mastery-sep {
			margin: 0 0.12em;
		}
	}
</style>
