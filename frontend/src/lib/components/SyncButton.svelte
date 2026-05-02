<script lang="ts">
	import { api } from '$lib/api';

	let { deckName = '0. Slovene', modelName = 'Slovene Vocabulary' }: { deckName?: string; modelName?: string } = $props();

	let syncLoading = $state(false);
	let syncResult = $state<{ created: number; updated: number; skipped: number } | null>(null);
	let error = $state('');

	async function handleSync() {
		syncLoading = true;
		syncResult = null;
		error = '';
		try {
			const result = await api.syncCreateNew(deckName, modelName);
			syncResult = result;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			syncLoading = false;
		}
	}
</script>

<button onclick={handleSync} disabled={syncLoading}>
	{syncLoading ? 'Syncing…' : 'Sync New Cards to Anki'}
</button>

{#if syncResult}
	<p class="sync-result">
		Created {syncResult.created} new card{syncResult.created === 1 ? '' : 's'}
	</p>
{/if}

{#if error}
	<p class="error">{error}</p>
{/if}
