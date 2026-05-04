<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { AnkiSyncResult } from '$lib/api';

	let {
		variant = 'full',
		onSyncResult,
	}: {
		variant?: 'full' | 'compact';
		onSyncResult?: (result: AnkiSyncResult) => void;
	} = $props();

	let syncLoading = $state(false);
	let syncResult = $state<AnkiSyncResult | null>(null);
	let error = $state('');
	let ankiRunning = $state(false);

	async function refreshAnkiStatus() {
		try {
			const s = await api.fetchAnkiStatus();
			ankiRunning = s.anki_running;
		} catch {
			// non-fatal: if status endpoint is unavailable, leave button enabled
		}
	}

	async function handleSync() {
		await refreshAnkiStatus();
		if (ankiRunning) {
			return;
		}
		syncLoading = true;
		syncResult = null;
		error = '';
		try {
			const result = await api.syncWithAnki(false);
			syncResult = result;
			if (onSyncResult) onSyncResult(result);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			await refreshAnkiStatus();
		} finally {
			syncLoading = false;
		}
	}

	onMount(() => {
		refreshAnkiStatus();
		const onVisibility = () => refreshAnkiStatus();
		const onFocus = () => refreshAnkiStatus();
		document.addEventListener('visibilitychange', onVisibility);
		window.addEventListener('focus', onFocus);
		return () => {
			document.removeEventListener('visibilitychange', onVisibility);
			window.removeEventListener('focus', onFocus);
		};
	});

	function formatResult(r: AnkiSyncResult): string {
		return `Mode: ${r.mode}
Created: ${r.created}, Linked: ${r.linked}, Skipped: ${r.skipped}
Pulled: ${r.notes_pulled} notes, ${r.directions_pulled} directions
Pushed: ${r.notes_pushed} notes, ${r.directions_pushed} directions
Conflicts: ${r.conflicts}, Revlog drained: ${r.revlog_drained}`;
	}
</script>

<button
	onclick={handleSync}
	disabled={syncLoading || ankiRunning}
	title={ankiRunning
		? 'Close Anki to sync — TunaTale needs exclusive access to collection.anki2.'
		: 'Sync with Anki (Anki must stay closed during sync).'}
>
	{syncLoading ? 'Syncing…' : 'Sync with Anki'}
</button>

{#if ankiRunning}
	<span class="anki-warning">Close Anki to sync.</span>
{/if}

{#if syncResult && !onSyncResult}
	{#if variant === 'full'}
		<pre class="sync-result">{formatResult(syncResult)}</pre>
	{:else}
		<span class="sync-summary">{syncResult.created} created, {syncResult.linked} linked</span>
	{/if}
{/if}

{#if error}
	<p class="error">{error}</p>
{/if}

<style>
	.anki-warning {
		font-size: 0.85rem;
		color: var(--color-muted, #888);
	}
</style>
