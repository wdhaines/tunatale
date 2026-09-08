<script lang="ts">
	import { onMount } from 'svelte';
	import { lessonModePref } from '$lib/stores/lessonModePref.svelte';
	import { playerCollapsedPref } from '$lib/stores/playerCollapsedPref.svelte';

	// The Read/Listen switch, shared by the lesson page and the review-session
	// reader (bd tunatale-9p9d). The mode is a PERSISTED, viewport-defaulted
	// preference on one store, so both surfaces are already the same switch —
	// extracting it just stops the markup and CSS from being two copies that can
	// drift.
	//
	// It also hosts the player's collapse control, which is why `collapsible`
	// exists. That control used to be a full-width strip at the foot of the
	// player, and a strip costs a whole 44px ROW to save 100 — beside the pill it
	// costs nothing, and it sits where the eye already goes for card-level
	// controls. Only the caller knows whether there is a player to collapse, so
	// the flag comes in rather than being inferred here.
	let { collapsible = false }: { collapsible?: boolean } = $props();

	const mode = $derived(lessonModePref.mode);
	// Read-only, matching the player's own gate: in Listen the player IS the
	// content and there is nothing to get out of the way of.
	const showCollapse = $derived(collapsible && mode === 'read');
	onMount(() => {
		lessonModePref.init();
		playerCollapsedPref.init();
	});
</script>

<div class="mode-row">
	<div class="toggle-pill">
		<button class:active={mode === 'read'} onclick={() => lessonModePref.set('read')}>Read</button>
		<button class:active={mode === 'listen'} onclick={() => lessonModePref.set('listen')}>
			Listen
		</button>
	</div>
	{#if showCollapse}
		<button
			class="collapse-toggle"
			aria-expanded={!playerCollapsedPref.collapsed}
			aria-label={playerCollapsedPref.collapsed ? 'Show player controls' : 'Hide player controls'}
			title={playerCollapsedPref.collapsed ? 'Show controls' : 'Hide controls'}
			onclick={() => playerCollapsedPref.set(!playerCollapsedPref.collapsed)}
		>
			<svg viewBox="0 0 16 16" width="0.85em" height="0.85em" aria-hidden="true"
			     style="transform: rotate({playerCollapsedPref.collapsed ? 0 : 180}deg); transition: transform 0.15s">
				<polygon points="3,6 13,6 8,11.5" fill="currentColor" />
			</svg>
		</button>
	{/if}
</div>

<style>
	.mode-row {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		justify-content: flex-end;
		flex-shrink: 0;
	}
	/* Sized and surfaced like the pill beside it so the two read as one control
	   cluster rather than a stray icon — that grouping is what carries the
	   affordance now that the button has no room for a word. */
	.collapse-toggle {
		display: grid;
		place-items: center;
		width: 2rem;
		height: 2rem;
		padding: 0;
		border: none;
		border-radius: var(--radius-pill);
		background: var(--color-surface-2);
		color: var(--color-muted);
		cursor: pointer;
	}
	.collapse-toggle:hover {
		color: var(--color-text);
	}
	.collapse-toggle:focus-visible {
		outline: 2px solid var(--color-accent, currentColor);
		outline-offset: 2px;
	}
	@media (prefers-reduced-motion: reduce) {
		.collapse-toggle svg {
			transition: none !important;
		}
	}
	.toggle-pill {
		display: flex;
		gap: 0;
		background: var(--color-surface-2);
		border-radius: var(--radius-pill);
		padding: 2px;
		width: fit-content;
	}
	.toggle-pill button {
		margin: 0;
		padding: 0.35rem 1rem;
		border: none;
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--color-muted);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition:
			background 0.15s ease,
			color 0.15s ease;
	}
	/* On a phone this pill sits in column two of the card header, and its `auto`
	   width is taken out of the TITLE's column. Measured on a 390px screen: the
	   pill claimed ~160px and squeezed "The Rain and the Party at the Sports
	   Club" into 164px — three lines, the tallest single thing in the card.
	   Trimming the horizontal padding gives those pixels back to the title. The
	   label text is untouched; only the padding shrinks, and the control keeps
	   its full height, so the touch target is unchanged. */
	@media (max-width: 430px) {
		.toggle-pill button {
			padding: 0.35rem 0.5rem;
		}
		.collapse-toggle {
			width: 1.75rem;
			height: 1.75rem;
		}
	}
	.toggle-pill button.active {
		background: var(--color-bg, #fff);
		color: var(--color-text);
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
	}
	.toggle-pill button:not(.active):hover {
		color: var(--color-text);
	}
</style>
