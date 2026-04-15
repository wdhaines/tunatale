<script lang="ts">
	import type { WordToken } from './api';

	interface Props {
		word: WordToken;
		onStateChange?: (lemma: string, srs_item_id: number | null) => void;
		requireModifier?: boolean;
	}

	let { word, onStateChange, requireModifier = false }: Props = $props();

	function fire() {
		onStateChange?.(word.lemma, word.srs_item_id);
	}

	function handleClick(e: MouseEvent) {
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
				: word.srs_state === 'learning' || word.srs_state === 'relearning'
					? 'word-learning'
					: word.srs_state === 'review'
						? 'word-review'
						: 'word-new'
	);
</script>

<span
	class="word {colorClass}"
	role="button"
	tabindex="0"
	title={word.srs_state}
	onclick={handleClick}
	onkeydown={handleKeydown}
>{word.surface}</span>

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
</style>
