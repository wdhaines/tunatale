<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { LessonDrill } from '#lib/api.js';
	import type { ReadTab } from '#lib/reading/readTabs.js';
	import { t } from '#lib/i18n/i18n.svelte.js';

	/**
	 * The Read side's Outline tab (tunatale-cs3p slice 3): what the lesson holds,
	 * so it can be looked over the way the book can. Each part opens its own tab.
	 * A drill shows its instruction and model in writing, because the spoken
	 * instructions "go by fast" (the owner, 2026-11-13).
	 *
	 * `below` is what the page measures from the transcript (word counts and the
	 * Understand / Produce bars): on Read it moves here from the header.
	 */
	interface Props {
		tabs: ReadTab[];
		/** Both counts are null while the transcript loads: not known yet. */
		keyPhraseCount: number | null;
		dialogueLineCount: number | null;
		ruleDrill: LessonDrill | null;
		onopen: (tab: ReadTab) => void;
		below?: Snippet;
	}

	let { tabs, keyPhraseCount, dialogueLineCount, ruleDrill, onopen, below }: Props = $props();

	const parts = $derived(tabs.filter((tab) => tab.kind !== 'outline' && tab.kind !== 'grade'));
</script>

<h2 class="outline-title">{t('readTabs.outline')}</h2>
<ul class="outline">
	{#each parts as tab (tab.id)}
		<li>
			<button type="button" class="part" onclick={() => onopen(tab)}>
				{#if tab.kind === 'story_drill'}
					{@const drill = tab.drill!}
					<span class="head">{t('readOutline.drillHead', { n: tab.id.slice('drill-'.length), count: drill.steps.length })}</span>
					{#if drill.model}
						<span class="ceb">{drill.model.text}</span>
					{/if}
					<span class="note">{drill.instruction}</span>
				{:else}
					<span class="head">{tab.label}</span>
					{#if tab.kind === 'key_phrases' && keyPhraseCount !== null}
						<span class="note">{t('readOutline.keyPhrases', { count: keyPhraseCount })}</span>
					{:else if tab.kind === 'dialogue' && dialogueLineCount !== null}
						<span class="note">{t('readOutline.dialogue', { count: dialogueLineCount })}</span>
					{:else if tab.kind === 'drill'}
						<!-- a Drill tab exists only for a lesson with a rule-built drill -->
						<span class="note">{t('readOutline.ruleDrill', { count: ruleDrill!.roots.length })}</span>
					{/if}
				{/if}
			</button>
		</li>
	{/each}
</ul>
{#if below}
	<div class="below">{@render below()}</div>
{/if}

<style>
	.outline-title {
		margin: 0 0 0.6rem;
		font-size: 1rem;
	}
	.outline {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.part {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.2rem;
		width: 100%;
		margin: 0;
		padding: 0.7rem 0.85rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-md, 10px);
		background: var(--color-surface-2);
		color: var(--color-text);
		text-align: left;
		font: inherit;
		cursor: pointer;
	}
	.part:hover,
	.part:focus-visible {
		border-color: var(--color-primary);
	}
	.head {
		font-size: 0.75rem;
		font-weight: 600;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--color-muted);
	}
	.ceb {
		font-size: 1.05rem;
	}
	.note {
		font-size: 0.88rem;
		color: var(--color-muted);
	}
	.below {
		margin-top: 0.9rem;
	}
</style>
