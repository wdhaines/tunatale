<script lang="ts">
	// The one banner for app-level notices, so an LLM failure, a preset change
	// and a lesson with no glosses look and behave alike (asked for 2026-09-29).
	// Tone decides colour AND role: `danger` interrupts (role="alert"),
	// `warning` is a degraded-but-working state that does not (role="status").
	// At most one action; a banner that needs two is two banners.
	import type { Snippet } from 'svelte';

	interface Props {
		tone?: 'danger' | 'warning';
		children: Snippet;
		actionLabel?: string;
		busyLabel?: string;
		busy?: boolean;
		onaction?: () => void;
	}

	let { tone = 'danger', children, actionLabel, busyLabel, busy = false, onaction }: Props = $props();
</script>

<div class="banner banner-{tone}" role={tone === 'danger' ? 'alert' : 'status'}>
	<div class="banner-text">{@render children()}</div>
	{#if onaction}
		<button class="banner-btn" onclick={onaction} disabled={busy}>
			{busy && busyLabel ? busyLabel : actionLabel}
		</button>
	{/if}
</div>

<style>
	/* flex-wrap keeps a long message from squeezing the button off a 360px
	   screen; the button wraps under the text instead. */
	.banner {
		--banner-color: var(--color-danger);
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.55rem 0.75rem;
		background: color-mix(in srgb, var(--banner-color) 14%, transparent);
		border-bottom: 1px solid var(--banner-color);
		font-size: 0.85rem;
		color: var(--banner-color);
	}
	.banner-warning {
		--banner-color: var(--color-warning);
	}
	.banner-text {
		display: flex;
		flex: 1;
		min-width: 0;
		flex-direction: column;
		gap: 0.15rem;
		line-height: 1.4;
	}
	.banner-btn {
		flex-shrink: 0;
		padding: 0.3rem 0.7rem;
		border: 1px solid var(--banner-color);
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--banner-color);
		font-size: 0.82rem;
		font-weight: 600;
		cursor: pointer;
		transition:
			background 0.15s ease,
			color 0.15s ease;
	}
	.banner-btn:hover:not(:disabled) {
		background: var(--banner-color);
		color: var(--color-on-primary);
	}
	.banner-btn:disabled {
		opacity: 0.6;
		cursor: default;
	}
</style>
