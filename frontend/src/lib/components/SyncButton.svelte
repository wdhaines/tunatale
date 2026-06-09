<script lang="ts">
	import { api } from '$lib/api';
	import type { PeerSyncResult } from '$lib/api';
	import { syncStore } from '$lib/stores/sync.svelte';

	// Peer-sync with AnkiWeb (or a self-host server): TunaTale syncs its OWN
	// collection, so Anki can stay open and changes reach AnkiDroid. (This replaced
	// the closed-SQLite "Sync with Anki" flow, which required Anki to be quit.)
	let {
		onSyncResult,
	}: {
		onSyncResult?: (result: PeerSyncResult) => void;
	} = $props();

	let syncLoading = $state(false);
	let syncResult = $state<PeerSyncResult | null>(null);
	let error = $state('');
	let dismissTimer: ReturnType<typeof setTimeout> | undefined;

	async function handleSync() {
		syncLoading = true;
		syncResult = null;
		error = '';
		clearTimeout(dismissTimer);
		try {
			const result = await api.peerSync(false);
			syncResult = result;
			syncStore.notify(result);
			if (onSyncResult) onSyncResult(result);
			// Success is a transient confirmation — flash it, then clear so it
			// doesn't linger over the page. Errors stay until the next attempt.
			dismissTimer = setTimeout(() => {
				syncResult = null;
			}, 4000);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			syncLoading = false;
		}
	}
</script>

<div class="sync-button">
	<button
		onclick={handleSync}
		disabled={syncLoading}
		title="Sync TunaTale with AnkiWeb (Anki can stay open; changes reach AnkiDroid)."
	>
		{syncLoading ? 'Syncing…' : 'Sync to AnkiWeb'}
	</button>

	{#if error}
		<span class="sync-toast error" role="alert">{error}</span>
	{:else if syncResult && !onSyncResult}
		<span class="sync-toast success" role="status">Synced with AnkiWeb</span>
	{/if}
</div>

<style>
	.sync-button {
		position: relative;
		display: inline-flex;
	}
	.sync-button button {
		padding: 0.4rem 0.9rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
	}
	.sync-button button:hover:not(:disabled) {
		border-color: var(--color-primary);
		color: var(--color-primary);
		background: var(--color-surface-2);
	}
	.sync-button button:disabled {
		opacity: 0.6;
		cursor: default;
	}
	/* Float the result below the button so it never expands or wraps the header. */
	.sync-toast {
		position: absolute;
		top: calc(100% + 0.4rem);
		right: 0;
		z-index: 20;
		max-width: min(16rem, 80vw);
		padding: 0.35rem 0.6rem;
		border-radius: 6px;
		font-size: 0.8rem;
		font-weight: 500;
		line-height: 1.3;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
		text-align: left;
	}
	.sync-toast::before {
		margin-right: 0.3rem;
		font-weight: 700;
	}
	.sync-toast.success {
		background: #d1fae5;
		color: #065f46;
		border: 1px solid #a7f3d0;
	}
	.sync-toast.success::before {
		content: '✓';
	}
	.sync-toast.error {
		background: #fee2e2;
		color: #991b1b;
		border: 1px solid #fecaca;
	}
	.sync-toast.error::before {
		content: '⚠';
	}
</style>
