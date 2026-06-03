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
		tooltipActions
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

	const dynamicStyle = $derived(
		word.active_state !== 'unknown' && word.active_state !== 'known' && word.active_state !== 'suspended'
			? `color: ${masteryColor(word.progress ?? 0)};`
			: ''
	);

	const colorClass = $derived(
		word.active_state === 'unknown'
			? 'word-unknown'
			: word.active_state === 'known'
				? 'word-known'
				: word.active_state === 'suspended'
					? 'word-ignored'
					: ''
	);

	// Show tooltip when: not inside a collocation, OR alt-hover mode is active
	const showTooltip = $derived(!requireModifier || altHover);
</script>

{#if showTooltip}
	<Tooltip translation={word.translation} state={word.srs_state} {word} {sentence} actions={tooltipActions}>
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
	</Tooltip>
{:else}
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
{/if}

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
	.word-known {
		color: #9ca3af;
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
	.punct {
		color: #000;
		font-weight: normal;
	}
</style>
