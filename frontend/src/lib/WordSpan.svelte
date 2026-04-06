<script lang="ts">
	import type { WordToken, WordRating } from './api';

	interface Props {
		word: WordToken;
		rating?: WordRating | null;
		onRatingChange?: (lemma: string, rating: WordRating | null) => void;
	}

	let { word, rating = null, onRatingChange }: Props = $props();

	const CYCLE: (WordRating | null)[] = ['hard', 'easy', null];

	function handleClick() {
		const currentIndex = CYCLE.indexOf(rating ?? null);
		const next = CYCLE[(currentIndex + 1) % CYCLE.length];
		onRatingChange?.(word.lemma, next);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			handleClick();
		}
	}

	const colorClass = $derived(
		rating === 'hard'
			? 'word-hard'
			: rating === 'easy'
				? 'word-easy'
				: word.srs_state === 'unknown' || word.srs_state === 'new'
					? 'word-new'
					: word.srs_state === 'learning' || word.srs_state === 'relearning'
						? 'word-learning'
						: 'word-review'
	);
</script>

<span
	class="word {colorClass}"
	role="button"
	tabindex="0"
	title={rating ? `Flagged: ${rating}` : word.srs_state}
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
	.word-hard {
		color: #ea580c;
		font-weight: 600;
	}
	.word-easy {
		color: #7c3aed;
		font-weight: 600;
	}
</style>
