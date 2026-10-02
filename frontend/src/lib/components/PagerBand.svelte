<script lang="ts">
	/**
	 * The previous/next band a reader pages through its sequence with.
	 *
	 * Shared by the lesson page (days) and the session reader (dates). Both
	 * bands are the same affordance and the same geometry — previous hard left,
	 * next hard right — so the markup and its rules exist once.
	 *
	 * Nothing is rendered when both neighbours are null: an empty `<nav>` still
	 * costs a band and its gap in the reader's header.
	 */
	interface Props {
		prev: { href: string; label: string } | null;
		next: { href: string; label: string } | null;
		ariaLabel: string;
	}

	let { prev, next, ariaLabel }: Props = $props();
</script>

{#if prev || next}
	<nav class="pager-band" aria-label={ariaLabel}>
		{#if prev}
			<a class="pager-link" href={prev.href}>← {prev.label}</a>
		{/if}
		{#if next}
			<a class="pager-link pager-next" href={next.href}>{next.label} →</a>
		{/if}
	</nav>
{/if}

<style>
	/* Prev at the left edge, next at the right — the pager's own width IS the
	   affordance. `margin-left: auto` on the next link (rather than
	   space-between) keeps it hard right when prev is absent on day one. */
	.pager-band {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		/* The pager sits between two muted lines that both start with an arrow;
		   without this it reads as a second breadcrumb glued to the first. */
		margin: 0.15rem 0 0.35rem;
	}
	.pager-next {
		margin-left: auto;
	}
	/* Same treatment as .breadcrumb, one step smaller — these are secondary to the
	   "back to curriculum" link they sit opposite. */
	.pager-link {
		color: var(--color-muted);
		font-size: 0.8rem;
		/* One notch lighter than .breadcrumb (600): sibling navigation is
		   secondary to the way back out. */
		font-weight: 500;
		text-decoration: none;
		white-space: nowrap;
	}
	.pager-link:hover {
		color: var(--color-primary);
	}
</style>