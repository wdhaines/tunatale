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
	let { transcript }: { transcript: TranscriptData | null } = $props();

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
		{#each segments as seg, i (seg.key)}{#if i > 0}<span class="mastery-sep">·</span>{/if}{#if seg.lemmas.length > 0}<Tooltip translation={formatLemmaTooltip(seg.lemmas)}><span class="mastery-segment" role="button" tabindex="0" onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') (e.currentTarget as HTMLElement).click(); }}>{seg.count} {seg.label}</span></Tooltip>{:else}<span class="mastery-segment">{seg.count} {seg.label}</span>{/if}{/each}
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
</style>
