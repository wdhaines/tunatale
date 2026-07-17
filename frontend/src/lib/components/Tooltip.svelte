<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { WordToken } from '$lib/api';

	export interface TooltipActions {
		onCreateInflection?: (word: WordToken, sentence: string) => Promise<void>;
		onSetState?: (id: number, state: string) => Promise<void>;
		onRestoreKnown?: (id: number) => Promise<void>;
		onUntrack?: (id: number) => Promise<void>;
		onUnignore?: (id: number) => Promise<void>;
		onIgnoreLemma?: (lemma: string) => Promise<void>;
		onUnignoreLemma?: (lemma: string) => Promise<void>;
		// Undo cycle for the grade button: when isGradeUndoable(word) is true the
		// word's popover shows "Undo ↩" (calling onUndoGrade) instead of its grade
		// label — the page tracks which item was graded last (single-level, mirrors
		// the backend's one snapshot).
		isGradeUndoable?: (word: WordToken) => boolean;
		onUndoGrade?: (word: WordToken) => Promise<void>;
	}

	interface Props {
		translation?: string | null;
		children: Snippet;
		word?: WordToken;
		sentence?: string;
		actions?: TooltipActions;
		// When true, render the child but never the popover. Lets callers keep a
		// stable DOM structure (no layout shift) while gating the popover on, e.g.,
		// an Alt-held state — see WordSpan's collocation inner words.
		suppressed?: boolean;
		// All grading lives in the popover: the grade button (label + callback) is
		// the ONLY way to advance a word/phrase from the transcript. Callers decide
		// the label ("Start learning" / "Got it ✓" / "Undo ↩") and pass null to hide it.
		gradeLabel?: string | null;
		// 'primary' = the due "Got it ✓" grade (accent); 'ahead' = a read-ahead
		// recognition review of a not-due word (subtler, so it reads as ahead of
		// schedule rather than the card the SRS is asking for).
		gradeVariant?: 'primary' | 'ahead';
		onGrade?: (() => void) | null;
		// "Words…" — the touch path into a phrase's individual words (what
		// Alt+hover does on desktop). Only collocation popovers pass this.
		onDrillIn?: (() => void) | null;
		// Mastery line shown below translation (e.g. "not tracked", "known", "50%").
		// Caller computes the label; null hides the line.
		masteryLabel?: string | null;
	}

	let {
		translation,
		children,
		word,
		sentence,
		actions,
		suppressed = false,
		gradeLabel = null,
		gradeVariant = 'primary',
		onGrade = null,
		onDrillIn = null,
		masteryLabel = null
	}: Props = $props();

	let open = $state(false);
	let wrapEl = $state<HTMLElement | null>(null);
	let ttEl = $state<HTMLElement | null>(null);

	// A *long-press* opens the popover (so a touch user can reach the per-word
	// actions); a plain *tap* falls through to the word's own grade handler. This
	// avoids the tap-to-grade-also-toggles-the-tooltip conflict: pressing a word
	// grades it, holding it reveals its actions without grading.
	const LONG_PRESS_MS = 450;
	// Cancel only when the pointer travels beyond finger jitter. Cancelling on
	// ANY pointermove made long-press unreachable on real touchscreens — touch
	// pointermove fires for sub-pixel tremor on every hold.
	const MOVE_CANCEL_PX = 10;
	let pressTimer: ReturnType<typeof setTimeout> | null = null;
	let longPressed = false;
	let pressX = 0;
	let pressY = 0;

	function startPress(e: PointerEvent) {
		// Right/middle button never long-presses (keeps desktop right-click
		// reaching the browser context menu — see handleContextMenu).
		if (e.button !== 0) return;
		pressX = e.clientX;
		pressY = e.clientY;
		longPressed = false;
		pressTimer = setTimeout(() => {
			open = true;
			longPressed = true;
			pressTimer = null;
		}, LONG_PRESS_MS);
	}

	function cancelPress() {
		if (pressTimer !== null) {
			clearTimeout(pressTimer);
			pressTimer = null;
		}
	}

	function handlePressMove(e: PointerEvent) {
		if (pressTimer === null) return;
		if (Math.abs(e.clientX - pressX) + Math.abs(e.clientY - pressY) > MOVE_CANCEL_PX) {
			cancelPress();
		}
	}

	// Android fires contextmenu on long-press; swallow it while a press is
	// pending or just completed so the OS menu never covers the popover. A
	// desktop right-click never starts a press (button !== 0 above), so its
	// context menu is unaffected.
	function handleContextMenu(e: Event) {
		if (pressTimer !== null || longPressed) e.preventDefault();
	}

	// Swallow the click a long-press would otherwise fire (capture phase), so
	// releasing a long-press doesn't immediately toggle the popover back closed.
	function suppressClickAfterLongPress(e: MouseEvent) {
		if (longPressed) {
			e.stopPropagation();
			e.preventDefault();
			longPressed = false;
		}
	}

	// Click/tap toggles the popover — the same affordance on desktop and touch
	// (desktop hover remains a preview). Clicks never grade; grading is the
	// explicit grade button inside the popover. Popover-body clicks don't reach
	// this (the .tt onclick stops propagation).
	function handleWrapClick() {
		open = !open;
	}

	// Tap-outside: close when the pointer goes down outside THIS wrapper.
	// Scoped to wrapEl (not closest('.tt-wrap'), which matched ANY wrapper and
	// left stale popovers open when tapping the next word); pointerdown so it
	// fires immediately on touch instead of waiting for the synthesized click.
	$effect(() => {
		if (!open) return;
		function handleOutside(e: PointerEvent) {
			if (!wrapEl!.contains(e.target as Node)) open = false;
		}
		document.addEventListener('pointerdown', handleOutside);
		return () => document.removeEventListener('pointerdown', handleOutside);
	});

	// Keep the popover on-screen: when the centered position would clip at a
	// viewport edge (narrow phone screens), nudge it horizontally on open.
	const EDGE_MARGIN_PX = 8;
	let shiftX = $state(0);
	$effect(() => {
		if (!open || !ttEl) {
			shiftX = 0;
			return;
		}
		const rect = ttEl.getBoundingClientRect();
		if (rect.left < EDGE_MARGIN_PX) {
			shiftX = EDGE_MARGIN_PX - rect.left;
		} else if (rect.right > window.innerWidth - EDGE_MARGIN_PX) {
			shiftX = window.innerWidth - EDGE_MARGIN_PX - rect.right;
		} else {
			shiftX = 0;
		}
	});

	const dueLabel = $derived(word != null ? (word.is_due ? 'Due' : 'Not Due') : null);

	const showCreateInflection = $derived(Boolean(word?.inflectable && actions?.onCreateInflection));

	const hasSrsItem = $derived(word != null && word.srs_item_id != null);

	const showIgnore = $derived(
		Boolean(
			hasSrsItem &&
				word!.active_state !== 'unknown' &&
				word!.active_state !== 'suspended'
		)
	);

	const showIgnoreCardless = $derived(
		Boolean(word && !hasSrsItem && word.active_state === 'unknown' && actions?.onIgnoreLemma)
	);

	const showUnignore = $derived(
		Boolean(hasSrsItem && word!.active_state === 'suspended')
	);

	const showUnignoreCardless = $derived(
		Boolean(word && !hasSrsItem && word.active_state === 'ignored' && actions?.onUnignoreLemma)
	);

	const showMarkKnown = $derived(
		Boolean(
			hasSrsItem &&
				!word!.known_marked &&
				word!.active_state !== 'unknown' &&
				word!.active_state !== 'known' &&
				word!.active_state !== 'suspended'
		)
	);

	const showUnmarkKnown = $derived(
		Boolean(
			hasSrsItem &&
				word!.known_marked &&
				actions?.onRestoreKnown
		)
	);

	const showResetNew = $derived(
		Boolean(
			hasSrsItem &&
				word!.active_state !== 'unknown' &&
				word!.active_state !== 'new' &&
				word!.active_state !== 'suspended'
		)
	);

	const showGrade = $derived(Boolean(gradeLabel && onGrade));
	const showDrillIn = $derived(Boolean(onDrillIn));

	const hasActions = $derived(
		showGrade || showDrillIn || showCreateInflection || showIgnore || showIgnoreCardless || showUnignore || showUnignoreCardless || showMarkKnown || showUnmarkKnown || showResetNew
	);

	const hasContent = $derived(!suppressed && Boolean(translation || masteryLabel || dueLabel || hasActions));
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions, a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<span
	class="tt-wrap"
	class:open
	bind:this={wrapEl}
	onpointerdown={startPress}
	onpointerup={cancelPress}
	onpointerleave={cancelPress}
	onpointercancel={cancelPress}
	onpointermove={handlePressMove}
	oncontextmenu={handleContextMenu}
	onclickcapture={suppressClickAfterLongPress}
	onclick={handleWrapClick}
>
	{@render children()}
	{#if hasContent}
		<span
			class="tt"
			role="tooltip"
			aria-hidden="false"
			bind:this={ttEl}
			style:transform={`translateX(calc(-50% + ${shiftX}px))`}
			onclick={(e) => e.stopPropagation()}
		>
			{#if translation}<span class="tt-translation">{translation}</span>{/if}
			{#if masteryLabel}<span class="tt-mastery">{masteryLabel}</span>{/if}
			{#if dueLabel}<span class="tt-state tt-state-{word?.is_due ? 'due' : 'not-due'}">{dueLabel}</span>{/if}
			{#if hasActions}
				<span class="tt-actions">
					{#if showGrade}
						<button
							type="button"
							class="tt-btn tt-btn-grade"
							class:tt-btn-review-ahead={gradeVariant === 'ahead'}
							onclick={() => onGrade!()}
						>{gradeLabel}</button>
					{/if}
					{#if showDrillIn}
						<button
							type="button"
							class="tt-btn"
							onclick={() => onDrillIn!()}
						>Words…</button>
					{/if}
					{#if showCreateInflection}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onCreateInflection!(word!, sentence ?? '')}
						>Create inflection card</button>
					{/if}
					{#if showUnignore}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onUnignore!(word!.srs_item_id!)}
						>Un-ignore</button>
					{/if}
					{#if showUnignoreCardless}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onUnignoreLemma!(word!.lemma)}
						>Un-ignore</button>
					{/if}
					{#if showIgnore}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onUntrack!(word!.srs_item_id!)}
						>Ignore</button>
					{/if}
					{#if showIgnoreCardless}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onIgnoreLemma!(word!.lemma)}
						>Ignore</button>
					{/if}
					{#if showMarkKnown}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onSetState!(word!.srs_item_id!, 'known')}
						>Known</button>
					{/if}
					{#if showUnmarkKnown}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onRestoreKnown!(word!.srs_item_id!)}
						>Un-mark known</button>
					{/if}
					{#if showResetNew}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onSetState!(word!.srs_item_id!, 'new')}
						>Reset</button>
					{/if}
				</span>
			{/if}
		</span>
	{/if}
</span>

<style>
	.tt-wrap {
		position: relative;
		display: inline;
	}
	.tt {
		position: absolute;
		bottom: 100%;
		left: 50%;
		/* transform is set inline: translateX(-50%) plus the viewport-edge shift */
		background: #111827;
		color: #f9fafb;
		padding: 4px 8px;
		border-radius: 4px;
		font-size: 12px;
		/* Shrink-wrap to one line when short, wrap within the viewport when long
		   (the containing block is the inline word, so max-content is required
		   to avoid wrapping at the word's own width). */
		width: max-content;
		max-width: min(280px, calc(100vw - 16px));
		white-space: normal;
		z-index: 10;
		opacity: 0;
		pointer-events: none;
		transition: opacity 0.1s;
	}
	/* Hover/focus reveal is a fine-pointer affordance only. On touch, a tap
	   synthesizes hover AND focuses the tabindex word, which sticky-opened the
	   popover on every grade-tap; there, long-press (.open) is the only opener. */
	@media (hover: hover) {
		.tt-wrap:hover > .tt,
		.tt-wrap:focus-within > .tt {
			opacity: 1;
			pointer-events: auto;
		}
	}
	.tt-wrap.open > .tt {
		opacity: 1;
		pointer-events: auto;
	}
	.tt::before {
		content: '';
		position: absolute;
		top: 100%;
		left: 0;
		right: 0;
		height: 8px;
	}
	.tt-translation {
		font-weight: 500;
	}
	.tt-state {
		margin-left: 6px;
		opacity: 0.75;
		font-size: 11px;
	}
	.tt-mastery {
		margin-left: 6px;
		opacity: 0.8;
		font-size: 11px;
	}
	.tt-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 4px;
	}
	.tt-btn {
		font-size: 11px;
		padding: 2px 6px;
		background: rgba(255, 255, 255, 0.15);
		color: #f9fafb;
		border: 1px solid rgba(255, 255, 255, 0.3);
		border-radius: 3px;
		cursor: pointer;
		white-space: nowrap;
	}
	.tt-btn:hover {
		background: rgba(255, 255, 255, 0.25);
	}
	/* The grade button is the primary action — give it the accent so it reads
	   as "this is what advances the word," distinct from the utility actions. */
	.tt-btn-grade {
		background: var(--color-primary, #2563eb);
		border-color: var(--color-primary, #2563eb);
	}
	.tt-btn-grade:hover {
		background: var(--color-primary-hover, #1d4ed8);
	}
	/* Read-ahead review of a not-due word — subtler than the due grade (outlined
	   accent, not filled) so it reads as "ahead of schedule," not the SRS's ask. */
	.tt-btn-review-ahead {
		background: transparent;
		color: var(--color-primary, #2563eb);
		border-color: var(--color-primary, #2563eb);
	}
	.tt-btn-review-ahead:hover {
		background: rgba(37, 99, 235, 0.12);
	}
	@media (pointer: coarse) {
		/* Long-press must win over the OS gestures it collides with: iOS text
		   selection + callout, magnifier. Scoped to coarse pointers so desktop
		   drag-to-select-and-copy on words keeps working. */
		.tt-wrap {
			-webkit-touch-callout: none;
			-webkit-user-select: none;
			user-select: none;
		}
		/* Finger-sized targets — 11px/2px buttons are untappable. */
		.tt {
			font-size: 14px;
			padding: 8px 10px;
		}
		.tt-btn {
			font-size: 14px;
			padding: 8px 12px;
		}
		.tt-actions {
			gap: 8px;
			margin-top: 8px;
		}
	}
</style>
