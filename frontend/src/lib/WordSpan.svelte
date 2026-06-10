<script lang="ts">
	import type { WordToken } from './api';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { TooltipActions } from '$lib/components/Tooltip.svelte';
	import { masteryColor } from '$lib/mastery';

	interface Props {
		word: WordToken;
		onWordClick?: (word: WordToken, lineIndex: number) => void;
		requireModifier?: boolean;
		altHover?: boolean;
		lineIndex?: number;
		wordIndex?: number;
		selected?: boolean;
		sentence?: string;
		tooltipActions?: TooltipActions;
		showGloss?: boolean;
	}

	let {
		word,
		onWordClick,
		requireModifier = false,
		altHover = false,
		lineIndex,
		wordIndex,
		selected = false,
		sentence,
		tooltipActions,
		showGloss = false
	}: Props = $props();

	function fire() {
		onWordClick?.(word, lineIndex ?? 0);
	}

	function isPunctClick(e: MouseEvent | KeyboardEvent): boolean {
		const target = 'key' in e ? null : (e.target as Element | null);
		return target != null && target.closest('.punct') != null;
	}

	// Distinguish a tap from a drag-to-select by how far the pointer moved
	// between press and release: a tap cycles state, a drag selects/copies text.
	// (Checking for a present selection regressed cycling — a double-click while
	// rapidly cycling selects the word, which then blocked the following click.)
	let downX = 0;
	let downY = 0;

	function handleMouseDown(e: MouseEvent) {
		if (isPunctClick(e)) return;
		downX = e.clientX;
		downY = e.clientY;
	}

	function handleClick(e: MouseEvent) {
		if (isPunctClick(e)) return;
		if (Math.abs(e.clientX - downX) + Math.abs(e.clientY - downY) > 8) return;
		if (requireModifier) {
			if (e.altKey || e.shiftKey) {
				e.stopPropagation();
				fire();
			}
			return;
		}
		fire();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key !== 'Enter' && e.key !== ' ') return;
		if (requireModifier && !(e.altKey || e.shiftKey)) return;
		e.preventDefault();
		if (requireModifier) e.stopPropagation();
		fire();
	}

	// KNOWN renders on the green end of the mastery ramp (its progress is ~1.0),
	// NOT as a static gray — only unknown/suspended/ignored stay off the ramp.
	const dynamicStyle = $derived(
		word.active_state !== 'unknown' && word.active_state !== 'suspended' && word.active_state !== 'ignored'
			? `color: ${masteryColor(word.progress ?? 0)};`
			: ''
	);

	const colorClass = $derived(
		word.active_state === 'unknown'
			? 'word-unknown'
			: word.active_state === 'suspended' || word.active_state === 'ignored'
				? 'word-ignored'
				: ''
	);

	// Show the popover when: not inside a collocation, OR alt-hover mode is active.
	// The Tooltip wrapper is ALWAYS rendered (suppressed otherwise) so the DOM
	// structure stays stable — toggling Alt over a collocation must not reflow the
	// line (the prior if/else swap caused a visible spacing jump).
	const showTooltip = $derived(!requireModifier || altHover);
</script>

<Tooltip
	translation={word.translation}
	{word}
	{sentence}
	actions={tooltipActions}
	suppressed={!showTooltip}
>
	<span
		class="word-wrapper"
		class:word-wrapper-gloss={showGloss && word.translation}
	>
		<span
			class="word {colorClass}"
			class:word-selected={selected}
			class:word-due={word.is_due}
			style={dynamicStyle}
			role="button"
			tabindex="0"
			data-line-index={lineIndex}
			data-word-index={wordIndex}
			onmousedown={handleMouseDown}
			onclick={handleClick}
			onkeydown={handleKeydown}
		><span class="punct">{word.prefix_punct ?? ''}</span>{word.surface}<span class="punct">{word.suffix_punct ?? ''}</span></span>
		{#if showGloss && word.translation}
			<span class="word-gloss">{word.translation}</span>
		{/if}
	</span>
</Tooltip>

<style>
	.word {
		cursor: pointer;
		border-radius: 2px;
		padding: 0 1px;
		transition: background-color 0.1s;
	}
	.word:hover {
		opacity: 0.8;
	}
	.word-unknown {
		color: #818cf8;
		text-decoration: underline dotted;
		text-underline-offset: 2px;
	}
	.word-ignored {
		color: #9ca3af;
		text-decoration: line-through;
	}
	.word-due {
		font-weight: bold;
	}
	.word-selected {
		background-color: rgba(99, 102, 241, 0.2);
	}
	.word-wrapper {
		display: inline-flex;
		flex-direction: column;
		align-items: center;
		vertical-align: top;
	}
	.word-wrapper-gloss {
		margin-bottom: 1.1rem;
	}
	.word-gloss {
		font-size: 0.7rem;
		color: var(--color-muted, #6b7280);
		line-height: 1.1;
		white-space: nowrap;
	}
	.punct {
		/* Neutral foreground so punctuation stays uncolored even when the word
		   carries a mastery-ramp color — and legible in dark mode (was #000). */
		color: var(--color-text);
		font-weight: normal;
	}
</style>
