<script lang="ts">
	import type { WordToken } from './api';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { TooltipActions } from '$lib/components/Tooltip.svelte';
	import { railPropsFor } from '$lib/masteryBands';

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
		hideRails?: boolean;
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
		showGloss = false,
		hideRails = false
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

	// Twin rails (bd tunatale-yh47, density): a tracked word's rails are painted
	// as background-image layers in the word's own padding-bottom — no child
	// elements, so an inline word's padding cannot change the line box and a
	// wrapped row stays exactly one line-height tall. `paintsRails` gates the
	// paint, `railProps` carries the per-word colour/width/track custom props.
	const paintsRails = $derived(!hideRails && word.understand_band != null);
	const railProps = $derived.by(
		() => (paintsRails ? railPropsFor(word) : null),
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
		// Scheduled past the listen horizon — the listen preview has already
		// stopped asking about it, so reporting a work-in-progress percentage
		// here would contradict that. Deliberately NOT the word used for an
		// explicitly known card above: `well_known` is derived from the
		// RECOGNITION direction alone (`mastery.py::is_well_known`), so it
		// cannot claim the word is known in production — which is often still
		// NEW with reps=0. `active_state === 'known'` DOES cover both
		// directions, so it keeps the stronger word. Must stay identical to
		// ListenPreviewModal's `masteryLabel`; the two surfaces are not allowed
		// to describe the same card differently.
		if (word.well_known) return 'well recognized';
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
			class:paint-rails={paintsRails}
			class:paint-untracked={word.active_state === 'unknown' && !hideRails}
			class:word-selected={selected}
			class:word-due={word.is_due}
			style={railProps ?? undefined}
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
		/* Column flex: `align-self: flex-start` keeps the word at its own
		   content width rather than stretching to the wrapper; the rails below
		   stretch instead. Without it the word's box would be the wrapper
		   width and its content crushed between the 1px paddings. */
		align-self: flex-start;
	}
	.word:hover {
		opacity: 0.8;
	}
	.word-unknown {
		color: var(--word-untracked, #1e5e86);
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
		/* Inline, not the inline-flex of the element-rails era (bd tunatale-yh47
		   density): the rails are background layers painted in the word's own
		   padding-bottom, so nothing outside the word needs a slot, and a
		   wrapped row is exactly one line-height tall. */
		display: inline;
	}
	.word-gloss {
		align-self: center;
		font-size: 0.7rem;
		color: var(--color-muted, #6b7280);
		line-height: 1.1;
		white-space: nowrap;
	}
	.word-wrapper-gloss {
		/* Gloss mode keeps its column stack (word over gloss) — only the
		   non-gloss wrapper went inline. */
		display: inline-flex;
		flex-direction: column;
		align-items: stretch;
		vertical-align: top;
		margin-bottom: 1.1rem;
	}
	.punct {
		/* Neutral foreground so punctuation stays uncolored — even when the
		   word would have carried a mastery-ramp color — and legible in dark
		   mode (was #000). */
		color: var(--color-text);
		font-weight: normal;
	}
	/* Twin rails (bd tunatale-yh47, density) — painted, not laid out. A tracked
	   word paints 3px rails in the bottom 7px of its own padding: understand on
	   top (layers at bottom 4px), produce below (bottom 0), each a 3px fill over
	   a 3px track. Inline padding never changes the line box, so the rows stay
	   exactly one `line-height` apart and the rails cannot push the next row.
	   The four layer rules live here; the per-word colour/width/track arrive as
	   `--rail-*` custom properties set inline by the component. `background-
	   color` (the selected tint) sits UNDER these image layers untouched. */
	/* An untracked word has no card on either side, so it paints the reader's
	   "no card" symbol — a dashed rail — in BOTH rail slots (user's pick,
	   2026-09-10), in the same padding the tracked words' rails use. */
	.word.paint-untracked {
		padding-bottom: 7px;
		background-repeat: no-repeat;
		background-image:
			repeating-linear-gradient(90deg, var(--color-muted, #6b7280) 0 3px, transparent 3px 6px),
			repeating-linear-gradient(90deg, var(--color-muted, #6b7280) 0 3px, transparent 3px 6px);
		background-size:
			100% 3px,
			100% 3px;
		background-position:
			left bottom 4px,
			left bottom 0;
	}
	.word.paint-rails {
		padding-bottom: 7px;
		background-repeat: no-repeat;
		background-image:
			linear-gradient(var(--rail-u-fill, transparent), var(--rail-u-fill, transparent)),
			var(--rail-u-track, var(--band-track, #e4e9e6)),
			linear-gradient(var(--rail-p-fill, transparent), var(--rail-p-fill, transparent)),
			var(--rail-p-track, var(--band-track, #e4e9e6));
		background-size:
			var(--rail-u-pct, 0%) 3px,
			100% 3px,
			var(--rail-p-pct, 0%) 3px,
			100% 3px;
		background-position:
			left bottom 4px,
			left bottom 4px,
			left bottom 0,
			left bottom 0;
	}
</style>
