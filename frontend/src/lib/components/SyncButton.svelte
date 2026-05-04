<script lang="ts">
	import { api } from '$lib/api';
	import type { AnkiSyncResult } from '$lib/api';

	let syncLoading = $state(false);
	let syncResult = $state<AnkiSyncResult | null>(null);
	let error = $state('');

	async function handleSync() {
		syncLoading = true;
		syncResult = null;
		error = '';
		try {
			const result = await api.syncWithAnki(false);
			syncResult = result;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			syncLoading = false;
		}
	}

	function formatResult(r: AnkiSyncResult): string {
		return `Mode: ${r.mode}
Created: ${r.created}, Linked: ${r.linked}, Skipped: ${r.skipped}
Pulled: ${r.notes_pulled} notes, ${r.directions_pulled} directions
Pushed: ${r.notes_pushed} notes, ${r.directions_pushed} directions
Conflicts: ${r.conflicts}, Revlog drained: ${r.revlog_drained}`;
	}
</script>

<button onclick={handleSync} disabled={syncLoading}>
	{syncLoading ? 'Syncing…' : 'Sync with Anki'}
</button>

{#if syncResult}
	<pre class="sync-result">{formatResult(syncResult)}</pre>
{/if}

{#if error}
	<p class="error">{error}</p>
{/if}
