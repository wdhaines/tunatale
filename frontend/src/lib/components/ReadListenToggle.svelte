<script lang="ts">
	import { onMount } from 'svelte';
	import { lessonModePref } from '$lib/stores/lessonModePref.svelte';

	// The Read/Listen switch, shared by the lesson page and the review-session
	// reader (bd tunatale-9p9d). It carries no props: the mode is a PERSISTED,
	// viewport-defaulted preference on one store, so both surfaces are already
	// the same switch — extracting it just stops the markup and CSS from being
	// two copies that can drift.
	const mode = $derived(lessonModePref.mode);
	onMount(() => lessonModePref.init());
</script>

<div class="mode-row">
	<div class="toggle-pill">
		<button class:active={mode === 'read'} onclick={() => lessonModePref.set('read')}>Read</button>
		<button class:active={mode === 'listen'} onclick={() => lessonModePref.set('listen')}>
			Listen
		</button>
	</div>
</div>

<style>
	.mode-row {
		display: flex;
		justify-content: flex-end;
		flex-shrink: 0;
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
			padding: 0.35rem 0.6rem;
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
