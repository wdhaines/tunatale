<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		translation?: string | null;
		state?: string | null; // raw enum: "new"|"learning"|"review"|"known"|"unknown"|"relearning"|"suspended"
		children: Snippet;
	}
	let { translation, state, children }: Props = $props();

	const STATE_LABELS: Record<string, string> = {
		unknown: 'Unknown',
		new: 'New',
		learning: 'Learning',
		relearning: 'Relearning',
		review: 'Review',
		known: 'Known',
		suspended: 'Suspended'
	};
	const STATE_HINTS: Record<string, string> = {
		unknown: 'click to start learning',
		new: 'click to start learning',
		learning: 'click to mark known',
		review: 'click to mark known',
		relearning: 'click to mark known',
		known: 'click to untrack',
		suspended: 'click to restore'
	};
	const stateLabel = $derived(state ? (STATE_LABELS[state] ?? state) : null);
	const hint = $derived(state ? (STATE_HINTS[state] ?? null) : null);
	const hasContent = $derived(Boolean(translation || stateLabel));
</script>

<span class="tt-wrap">
	{@render children()}
	{#if hasContent}
		<span class="tt" role="tooltip" aria-hidden="false">
			{#if translation}<span class="tt-translation">{translation}</span>{/if}
			{#if stateLabel}<span class="tt-state tt-state-{state}">{stateLabel}{#if hint}<span class="tt-hint"> · {hint}</span>{/if}</span>{/if}
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
		transform: translateX(-50%);
		background: #111827;
		color: #f9fafb;
		padding: 4px 8px;
		border-radius: 4px;
		font-size: 12px;
		white-space: nowrap;
		z-index: 10;
		opacity: 0;
		pointer-events: none;
		transition: opacity 0.1s;
	}
	.tt-wrap:hover > .tt,
	.tt-wrap:focus-within > .tt {
		opacity: 1;
	}
	.tt-translation {
		font-weight: 500;
	}
	.tt-state {
		margin-left: 6px;
		opacity: 0.75;
		font-size: 11px;
	}
	.tt-hint {
		font-style: italic;
	}
</style>
