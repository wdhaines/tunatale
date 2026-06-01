<script lang="ts">
	import type { WordToken } from './api';
	import Tooltip from '$lib/components/Tooltip.svelte';

	interface Props {
		word: WordToken;
		onStateChange?: (lemma: string, srs_item_id: number | null) => void;
		requireModifier?: boolean;
		altHover?: boolean;
		lineIndex?: number;
		wordIndex?: number;
		selected?: boolean;
	}

	let {
		word,
		onStateChange,
		requireModifier = false,
		altHover = false,
		lineIndex,
		wordIndex,
		selected = false
	}: Props = $props();

	function fire() {
		onStateChange?.(word.lemma, word.srs_item_id);
	}

	// Distinguish a tap from a drag-to-select by how far the pointer moved
	// between press and release: a tap cycles state, a drag selects/copies text.
	// (Checking for a present selection regressed cycling — a double-click while
	// rapidly cycling selects the word, which then blocked the following click.)
	let downX = 0;
	let downY = 0;

	function handleMouseDown(e: MouseEvent) {
		downX = e.clientX;
		downY = e.clientY;
	}

	function handleClick(e: MouseEvent) {
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

	const colorClass = $derived(
		word.srs_state === 'known'
			? 'word-known'
			: word.srs_state === 'suspended'
				? 'word-ignored'
				: word.srs_state === 'unknown'
					? 'word-unknown'
					: word.srs_state === 'learning' || word.srs_state === 'relearning'
						? 'word-learning'
						: word.srs_state === 'review'
							? 'word-review'
							: 'word-new'
	);

	// Show tooltip when: not inside a collocation, OR alt-hover mode is active
	const showTooltip = $derived(!requireModifier || altHover);
</script>

{#if showTooltip}
	<Tooltip translation={word.translation} state={word.srs_state}>
		<span
			class="word {colorClass}"
			class:word-selected={selected}
			role="button"
			tabindex="0"
			data-line-index={lineIndex}
			data-word-index={wordIndex}
			onmousedown={handleMouseDown}
			onclick={handleClick}
			onkeydown={handleKeydown}
		>{word.surface}</span>
	</Tooltip>
{:else}
	<span
		class="word {colorClass}"
		class:word-selected={selected}
		role="button"
		tabindex="0"
		data-line-index={lineIndex}
		data-word-index={wordIndex}
		onmousedown={handleMouseDown}
		onclick={handleClick}
		onkeydown={handleKeydown}
	>{word.surface}</span>
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
	.word-new {
		color: #2563eb;
	}
	.word-unknown {
		color: #818cf8;
		text-decoration: underline dotted;
		text-underline-offset: 2px;
	}
	.word-learning {
		color: #ca8a04;
	}
	.word-review {
		color: #16a34a;
	}
	.word-known {
		color: #9ca3af;
	}
	.word-ignored {
		color: #9ca3af;
		text-decoration: line-through;
	}
	.word-selected {
		background-color: rgba(99, 102, 241, 0.2);
	}
</style>
