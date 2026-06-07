<script lang="ts">
	import { api } from '$lib/api';
	import type { PeerSyncResult } from '$lib/api';

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

	async function handleSync() {
		syncLoading = true;
		syncResult = null;
		error = '';
		try {
			const result = await api.peerSync(false);
			syncResult = result;
			if (onSyncResult) onSyncResult(result);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			syncLoading = false;
		}
	}
</script>

<button
	onclick={handleSync}
	disabled={syncLoading}
	title="Sync TunaTale with AnkiWeb (Anki can stay open; changes reach AnkiDroid)."
>
	{syncLoading ? 'Syncing…' : 'Sync to AnkiWeb'}
</button>

{#if syncResult && !onSyncResult}
	<span class="sync-summary">Synced with AnkiWeb</span>
{/if}

{#if error}
	<p class="error">{error}</p>
{/if}
