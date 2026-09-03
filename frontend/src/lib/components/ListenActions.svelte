<script lang="ts">
	import type { createListenActions } from '$lib/reading/listenActions.svelte';

	/**
	 * The action row at the foot of the sticky card: mark listened, what the last
	 * listen produced, and the way through to checking your work.
	 *
	 * ⚠️ `reviewHref` IS THE ONLY THING THAT VARIES between a lesson and a review
	 * session, and it varies for one reason: /review needs somewhere to send the
	 * learner BACK to. Everything else — the listen POST, the review queue, the
	 * transcript re-read — keys on a content id and always did.
	 */
	interface Props {
		listen: ReturnType<typeof createListenActions>;
		/** Where "Check your work" goes, including its own return path. */
		reviewHref: string;
		/** Suppressed while the page is showing an error of its own. */
		hasError?: boolean;
	}
	let { listen, reviewHref, hasError = false }: Props = $props();

	const result = $derived(listen.listenResult);
	const showConfirmation = $derived(result !== null && !hasError);

	// Built in script, not across template lines: the formatter wraps a long
	// interpolation and the rendered text node then carries a newline plus tabs
	// between the count and its noun, so "2 words" is not contiguous in
	// textContent. jsdom assertions read exactly that.
	const checkWorkLabel = $derived(
		`Check your work — review ${listen.queueCount} ${listen.queueCount === 1 ? 'word' : 'words'}`
	);
</script>

<div class="listen-actions">
	<button class="listen-btn" class:listened={listen.isListened} onclick={() => listen.open()}>
		Mark as Listened
	</button>
	{#if showConfirmation && result}
		<p class="listen-confirmation">
			{#if result.created > 0}
				{result.created} new {result.created === 1 ? 'word' : 'words'} added
			{/if}
			{#if result.created > 0 && (result.applied > 0 || result.staged > 0)} · {/if}
			{#if result.applied > 0}
				{result.applied} graded
			{/if}
			{#if result.applied > 0 && result.staged > 0} · {/if}
			{#if result.staged > 0}
				{result.staged} ready to check
			{/if}
			{#if result.remaining_candidates > 0}
				· {result.remaining_candidates} remaining — listen again to add more
			{/if}
		</p>
	{/if}
	{#if listen.showCheckWorkLink}
		<a class="check-work-link" href={reviewHref}>{checkWorkLabel}</a>
	{/if}
</div>

<style>
	/* Single centered action row: the button keeps the card's centre line, with
	   whatever the last listen produced beside it. Wraps on narrow viewports
	   rather than squeezing the link. */
	.listen-actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: center;
		gap: 0.5rem 0.9rem;
	}
	.listen-btn {
		/* The page-wide `button` rule adds a 0.75rem top margin, which in a flex
		   row just offsets the button from its neighbours. */
		margin-top: 0;
		padding: 0.5rem 1.25rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		cursor: pointer;
		font-weight: 600;
	}
	.listen-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.listen-btn.listened {
		background: var(--color-success);
	}
	.listen-confirmation {
		color: var(--color-success);
		font-size: 0.85rem;
		margin: 0;
	}
	.check-work-link {
		display: inline-block;
		color: var(--color-primary);
		font-weight: 600;
		font-size: 0.9rem;
		text-decoration: none;
	}
	.check-work-link:hover {
		text-decoration: underline;
	}
</style>
