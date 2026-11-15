<script lang="ts" module>
	import type { GradeRating } from '#lib/api.js';
	export const GRADE_RATINGS: readonly GradeRating[] = ['again', 'hard', 'good', 'easy'];
</script>

<script lang="ts">
	/**
	 * The four grades, the one control every grading surface uses (tunatale-5i1p.3):
	 * the review card (DrillCard), the Read popover (Tooltip) and the Grade page.
	 * Each colour means the same rating wherever it is given.
	 *
	 * - `card`: the review card's big pills. The root is `display: contents`, so
	 *   the buttons are flex items of the card's own `.ratings` bar (which docks
	 *   to the bottom of a phone screen; the card owns that, not this).
	 * - `popover`: the Read popover's small buttons, on its dark bubble.
	 * - `row`: the Grade page's segmented row. A grade there is a choice, not an
	 *   action: buttons are outlined until pressed, `selected` is shown with
	 *   aria-pressed, and the page clears a grade when its button is tapped again.
	 */
	import { t } from '#lib/i18n/i18n.svelte.js';

	interface Props {
		onRate: (rating: GradeRating) => void;
		variant?: 'card' | 'popover' | 'row';
		/** `row` only: the grade chosen so far, null for none. */
		selected?: GradeRating | null;
		/** `card` only: a provisional grade, ringed rather than filled. */
		suggested?: GradeRating | null;
		disabled?: boolean;
	}
	let { onRate, variant = 'card', selected = null, suggested = null, disabled = false }: Props = $props();
	const pressable = $derived(variant === 'row');
</script>

<span class="grade-buttons grade-{variant}" role="group" aria-label={t('grade.group')}>
	{#each GRADE_RATINGS as rating (rating)}
		<button
			type="button"
			class="grade-btn btn-{rating}"
			class:suggested={suggested === rating}
			aria-pressed={pressable ? selected === rating : undefined}
			{disabled}
			onclick={() => onRate(rating)}>{t(`grade.${rating}`)}</button
		>
	{/each}
</span>

<style>
	/* ── card: the review card's pills ── */
	.grade-card {
		display: contents;
	}
	.grade-card .grade-btn {
		margin-top: 0;
		padding: 0.5rem 1.25rem;
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		font-weight: 600;
		cursor: pointer;
		/* Mobile-first: a 2-up grid with big touch targets. */
		flex: 1 1 calc(50% - 0.5rem);
		min-height: 44px;
		font-size: 1rem;
	}
	.grade-card .btn-again {
		background: var(--color-danger);
	}
	.grade-card .btn-hard {
		background: var(--color-warning);
	}
	.grade-card .btn-good {
		background: var(--color-success);
	}
	.grade-card .btn-easy {
		background: var(--color-primary);
	}
	/* The listen preview's provisional grade — a subtle ring, not a color swap,
	   so it reads as a suggestion rather than a state change. */
	.grade-card .grade-btn.suggested {
		outline: 2px solid var(--color-primary);
		outline-offset: 2px;
	}
	@media (min-width: 641px) {
		.grade-card .grade-btn {
			flex: 0 1 auto;
		}
	}

	/* ── popover: small buttons on the Read popover's dark bubble ── */
	.grade-popover {
		display: flex;
		flex-wrap: nowrap;
		gap: 4px;
		margin-top: 4px;
	}
	.grade-popover .grade-btn {
		font-size: 11px;
		padding: 2px 6px;
		color: #f9fafb;
		border: 1px solid;
		border-radius: 3px;
		cursor: pointer;
		white-space: nowrap;
	}
	.grade-popover .grade-btn:hover {
		filter: brightness(1.12);
	}
	.grade-popover .btn-again {
		background: var(--color-danger);
		border-color: var(--color-danger);
	}
	.grade-popover .btn-hard {
		background: var(--color-warning);
		border-color: var(--color-warning);
	}
	.grade-popover .btn-good {
		background: var(--color-success);
		border-color: var(--color-success);
	}
	.grade-popover .btn-easy {
		background: var(--color-primary);
		border-color: var(--color-primary);
	}
	@media (pointer: coarse) {
		/* Finger-sized targets — 11px/2px buttons are untappable. */
		.grade-popover {
			gap: 8px;
			margin-top: 8px;
		}
		.grade-popover .grade-btn {
			font-size: 14px;
			padding: 8px 10px;
		}
	}

	/* ── row: the Grade page's segmented choice ── */
	.grade-row {
		display: flex;
	}
	.grade-row .grade-btn {
		flex: 1;
		margin: 0 0 0 -1px;
		font: inherit;
		font-size: 0.85rem;
		min-height: 40px;
		padding: 0.4rem 0;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-muted);
		cursor: pointer;
		border-radius: 0;
	}
	.grade-row .grade-btn:first-child {
		margin-left: 0;
		border-radius: var(--radius-sm) 0 0 var(--radius-sm);
	}
	.grade-row .grade-btn:last-child {
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
	}
	.grade-row .grade-btn[aria-pressed='true'] {
		color: var(--color-on-primary);
		font-weight: 650;
	}
	.grade-row .btn-again[aria-pressed='true'] {
		background: var(--color-danger);
		border-color: var(--color-danger);
	}
	.grade-row .btn-hard[aria-pressed='true'] {
		background: var(--color-warning);
		border-color: var(--color-warning);
	}
	.grade-row .btn-good[aria-pressed='true'] {
		background: var(--color-success);
		border-color: var(--color-success);
	}
	.grade-row .btn-easy[aria-pressed='true'] {
		background: var(--color-primary);
		border-color: var(--color-primary);
	}
	.grade-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
