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

	// Clicking/tapping a word never grades — it toggles the popover (Tooltip
	// handles that). Grading is the popover's grade button below, the same
	// deliberate two-step on desktop and mobile. Keyboard Enter/Space still
	// grades directly: focusing the word already shows the popover, and a key
	// press can't happen by accident the way a touch tap can.
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

	// Per-word mastery label for the tooltip popover.
	const masteryLabel = $derived.by((): string | null => {
		if (word.active_state === 'unknown') return 'not tracked';
		if (word.active_state === 'known') return 'known';
		if (word.active_state === 'ignored') return 'ignored';
		if (word.progress != null) return `${Math.round(word.progress * 100)}%`;
		// suspended, etc. — no mastery line
		return null;
	});

	// Undo cycle: when the page says THIS word holds the last (still-local)
	// grade, the grade button flips to "Undo ↩" — even though the word is no
	// longer due post-grade. Single-level, mirrors the backend snapshot.
	const undoable = $derived(Boolean(tooltipActions?.isGradeUndoable?.(word)));

	// The normal due-grade path: the active direction is due and tracked.
	const gotItApplies = $derived(
		word.is_due && word.active_direction != null && word.srs_item_id != null
	);

	// Read-ahead: a not-due word whose RECOGNITION direction is on the review ramp.
	// Reading it in the interface is a valid recognition review even though the SRS
	// wouldn't have surfaced it yet. Suppressed when the due path already applies
	// (the active direction — recognition OR production — is graded there instead;
	// reconciling a due-production graduated word is deferred).
	const readAheadApplies = $derived(
		!gotItApplies && Boolean(word.recognition_reviewable) && word.srs_item_id != null
	);

	// Grade-button label mirrors what the old direct click did (the "cycle"):
	// unknown → create a base card; due+tracked → grade Good; not-due but readable
	// → review ahead; otherwise the click was a no-op, so no button.
	const gradeLabel = $derived(
		undoable
			? 'Undo ↩'
			: onWordClick == null
				? null
				: word.active_state === 'unknown'
					? 'Start learning'
					: gotItApplies
						? 'Got it ✓'
						: readAheadApplies
							? 'Review ✓'
							: null
	);

	// Style the read-ahead grade subtler than the due "Got it ✓" so the user can
	// see it's ahead of schedule (not the card the SRS is asking for).
	const gradeVariant = $derived(!undoable && readAheadApplies ? 'ahead' : 'primary');

	const onGrade = $derived(
		undoable
			? () => void tooltipActions!.onUndoGrade!(word)
			: onWordClick
				? fire
				: null
	);
</script>

<Tooltip
	translation={word.translation}
	{word}
	{sentence}
	actions={tooltipActions}
	suppressed={!showTooltip}
	{gradeLabel}
	{gradeVariant}
	{onGrade}
	{masteryLabel}
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
		/* The 1px side padding widens the hover/tap highlight box, but it also
		   pushed adjacent glyphs ~1.5 space-widths apart (measured 6px vs a
		   4px font space, 2026-07-18 report). The negative margin refunds the
		   padding in layout: highlight keeps its box, text keeps true
		   one-space rhythm. */
		padding: 0 1px;
		margin: 0 -1px;
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
