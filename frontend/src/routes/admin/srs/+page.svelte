<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { SRSItemDetail, SRSListParams, QueueStats } from '$lib/api';
	import SyncButton from '$lib/components/SyncButton.svelte';

	const PAGE_SIZE = 50;

	let search = $state('');
	let stateFilter = $state<'' | SRSItemDetail['state']>('');
	let sort = $state<NonNullable<SRSListParams['sort']>>('text');
	let order = $state<'asc' | 'desc'>('asc');
	let page = $state(0);
	let items = $state<SRSItemDetail[]>([]);
	let total = $state(0);
	let selected = $state<Set<number>>(new Set());
	let editingId = $state<number | null>(null);
	let editText = $state('');
	let editTranslation = $state('');
	let loading = $state(false);
	let error = $state<string | null>(null);
	let queueStats = $state<QueueStats | null>(null);
	let syncStatus = $state<string | null>(null);

	function handleSyncResult() {
		syncStatus = 'Synced with AnkiWeb';
		loadItems();
	}

	let debounceTimer: ReturnType<typeof setTimeout>;
	let lastSearch = $state('');

	async function loadItems() {
		loading = true;
		error = null;
		try {
			const params: SRSListParams = {
				sort,
				order,
				limit: PAGE_SIZE,
				offset: page * PAGE_SIZE
			};
			if (lastSearch) params.search = lastSearch;
			if (stateFilter) params.state = stateFilter;
			const [data, stats] = await Promise.all([
				api.listSRSItems(params),
				api.fetchQueueStats().catch(() => null),
			]);
			items = data.items;
			total = data.total;
			if (stats) queueStats = stats;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		// track reactive dependencies
		const _sort = sort;
		const _order = order;
		const _page = page;
		const _stateFilter = stateFilter;
		void _sort, _order, _page, _stateFilter;
		loadItems();
	});

	function onSearchInput() {
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			lastSearch = search;
			page = 0;
			loadItems();
		}, 250);
	}

	function setSort(col: NonNullable<SRSListParams['sort']>) {
		if (sort === col) {
			order = order === 'asc' ? 'desc' : 'asc';
		} else {
			sort = col;
			order = 'asc';
		}
		page = 0;
	}

	function sortIndicator(col: string) {
		if (sort !== col) return '';
		return order === 'asc' ? ' ▲' : ' ▼';
	}

	function toggleSelect(id: number) {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- refactor to SvelteSet is a separate task
		const next = new Set(selected);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		selected = next;
	}

	function toggleSelectAll() {
		if (selected.size === items.length) {
			selected = new Set();
		} else {
			selected = new Set(items.map((i) => i.id));
		}
	}

	function startEdit(item: SRSItemDetail) {
		editingId = item.id;
		editText = item.text;
		editTranslation = item.translation;
	}

	function cancelEdit() {
		editingId = null;
	}

	async function saveEdit(id: number) {
		try {
			await api.updateSRSItem(id, { text: editText, translation: editTranslation });
			editingId = null;
			await loadItems();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	async function deleteItem(id: number) {
		if (!confirm('Delete this item?')) return;
		try {
			await api.deleteSRSItem(id);
			await loadItems();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	async function resetItem(id: number) {
		if (!confirm('Reset this item? It will be forgotten in Anki too and re-learned from scratch.')) return;
		try {
			await api.resetSRSItem(id);
			await loadItems();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	async function toggleSuspend(item: SRSItemDetail) {
		const suspending = item.state !== 'suspended';
		try {
			await api.suspendSRSItem(item.id, suspending);
			await loadItems();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	async function bulkDelete() {
		if (!confirm(`Delete ${selected.size} selected items?`)) return;
		try {
			await api.bulkDeleteSRSItems([...selected]);
			selected = new Set();
			await loadItems();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	const totalPages = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	function formatDue(iso: string | null | undefined): string {
		if (!iso) return '';
		const d = new Date(iso);
		if (isNaN(d.getTime())) return iso;
		return d.toLocaleDateString(undefined, {
			year: 'numeric',
			month: 'short',
			day: 'numeric',
			timeZone: 'UTC',
		});
	}

	onMount(() => {
		loadItems();
	});
</script>

<main>
	<div class="toolbar">
		<h1>SRS Admin <span class="muted">· {total} total{#if queueStats} · {queueStats.new} new · {queueStats.learning} learning · {queueStats.review} review{/if}</span></h1>
		<div class="controls">
			<input
				type="search"
				placeholder="Search text or translation…"
				bind:value={search}
				oninput={onSearchInput}
			/>
			<select bind:value={stateFilter} onchange={() => { page = 0; }}>
				<option value="">All states</option>
				<option value="new">new</option>
				<option value="learning">learning</option>
				<option value="review">review</option>
				<option value="relearning">relearning</option>
				<option value="suspended">suspended</option>
			</select>
			{#if selected.size > 0}
				<button class="danger" onclick={bulkDelete}>Delete selected ({selected.size})</button>
			{/if}
			<button onclick={loadItems} title="Refresh">⟳</button>
			<SyncButton onSyncResult={handleSyncResult} />
		</div>
	</div>

	{#if syncStatus}
		<p class="sync-status">{syncStatus}</p>
	{/if}

	{#if error}
		<p class="error">{error}</p>
	{/if}

	<div class="table-wrap">
		<div class="row header">
			<span class="col-check">
				<input type="checkbox" checked={selected.size === items.length && items.length > 0} onchange={toggleSelectAll} />
			</span>
			<span class="col-text"><button class="sort-btn" onclick={() => setSort('text')}>text{sortIndicator('text')}</button></span>
			<span class="col-trans"><button class="sort-btn" onclick={() => setSort('translation')}>translation{sortIndicator('translation')}</button></span>
			<span class="col-state"><button class="sort-btn" onclick={() => setSort('state')}>state{sortIndicator('state')}</button></span>
			<span class="col-due"><button class="sort-btn" onclick={() => setSort('due_at')}>due{sortIndicator('due_at')}</button></span>
			<span class="col-reps"><button class="sort-btn" onclick={() => setSort('reps')}>reps{sortIndicator('reps')}</button></span>
			<span class="col-actions">actions</span>
		</div>

		{#if loading}
			<p class="muted pulse">Loading…</p>
		{:else if items.length === 0}
			<p class="muted">No items found.</p>
		{:else}
			{#each items as item (item.id)}
				{#if editingId === item.id}
					<div class="row editing">
						<span class="col-check"></span>
						<input class="col-text" bind:value={editText} />
						<input class="col-trans" bind:value={editTranslation} />
						<span class="col-state">{item.state}</span>
						<span class="col-due">{formatDue(item.due_at)}</span>
						<span class="col-reps">{item.reps}</span>
						<span class="col-actions">
							<button onclick={() => saveEdit(item.id)}>Save</button>
							<button onclick={cancelEdit}>Cancel</button>
						</span>
					</div>
				{:else}
					<div class="row">
						<span class="col-check">
							<input type="checkbox" checked={selected.has(item.id)} onchange={() => toggleSelect(item.id)} />
						</span>
						<span class="col-text">{item.text}</span>
						<span class="col-trans">{item.translation}</span>
						<span class="col-state state-{item.state}">{item.state}</span>
						<span class="col-due">{formatDue(item.due_at)}</span>
						<span class="col-reps">{item.reps}</span>
						<span class="col-actions">
							<button onclick={() => startEdit(item)}>Edit</button>
							<button onclick={() => resetItem(item.id)}>Reset</button>
							<button onclick={() => toggleSuspend(item)}>
								{item.state === 'suspended' ? 'Unsuspend' : 'Suspend'}
							</button>
							<button class="danger" onclick={() => deleteItem(item.id)}>Delete</button>
						</span>
					</div>
				{/if}
			{/each}
		{/if}
	</div>

	<div class="pagination">
		<button disabled={page === 0} onclick={() => { page -= 1; }}>◀ prev</button>
		<span>page {page + 1} / {totalPages}</span>
		<button disabled={page >= totalPages - 1} onclick={() => { page += 1; }}>next ▶</button>
	</div>
</main>

<style>
	main {
		max-width: 1100px;
		margin: 0 auto;
		padding: 1.5rem;
	}
	h1 {
		margin: 0 0 0.5rem;
		font-size: 1.4rem;
	}
	.muted {
		color: var(--color-muted);
		font-weight: normal;
	}
	.toolbar {
		display: flex;
		align-items: flex-start;
		gap: 1rem;
		flex-wrap: wrap;
		margin-bottom: 1rem;
	}
	.controls {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
		margin-left: auto;
	}
	input[type='search'] {
		padding: 0.35rem 0.6rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		font-size: 0.9rem;
		min-width: 180px;
	}
	select {
		padding: 0.35rem 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		font-size: 0.9rem;
	}
	button {
		padding: 0.3rem 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: #fff;
		cursor: pointer;
		font-size: 0.85rem;
	}
	button:hover {
		background: #f0f0f0;
	}
	button:disabled {
		opacity: 0.4;
		cursor: default;
	}
	button.danger {
		border-color: var(--color-danger);
		color: var(--color-danger);
	}
	button.danger:hover {
		background: #fff0f0;
	}
	.error {
		color: var(--color-danger);
		padding: 0.5rem;
		border: 1px solid var(--color-danger);
		border-radius: var(--radius);
		margin-bottom: 1rem;
	}
	.anki-warning {
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.table-wrap {
		display: grid;
		grid-template-columns: 2rem 1fr 1fr 7rem 7rem 4rem auto;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		overflow: hidden;
		background: #fff;
	}
	.table-wrap > p {
		grid-column: 1 / -1;
		margin: 0;
		padding: 0.5rem 0.75rem;
	}
	.row {
		display: grid;
		grid-template-columns: subgrid;
		grid-column: 1 / -1;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 0.75rem;
		border-bottom: 1px solid var(--color-border);
	}
	.row:last-child {
		border-bottom: none;
	}
	.row.header {
		background: #f5f5f5;
		font-weight: 600;
		font-size: 0.85rem;
	}
	.row.editing {
		background: #fffde7;
	}
	.sort-btn {
		appearance: none;
		background: none;
		border: none;
		padding: 0;
		margin: 0;
		font: inherit;
		font-weight: 600;
		font-size: 0.85rem;
		cursor: pointer;
		text-align: left;
		color: inherit;
		display: block;
		width: 100%;
	}
	.row.header .col-actions { justify-self: start; }
	.col-check { justify-self: center; }
	.col-actions { display: flex; gap: 0.3rem; flex-wrap: wrap; }
	.col-text, .col-trans { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.9rem; }
	.col-state { font-size: 0.8rem; }
	.col-due, .col-reps { font-size: 0.8rem; color: var(--color-muted); }
	.col-due { white-space: nowrap; }
	.state-suspended { color: var(--color-muted); font-style: italic; }
	.state-review { color: var(--color-success); }
	.state-learning, .state-relearning { color: var(--color-warning); }
	.pagination {
		display: flex;
		align-items: center;
		gap: 1rem;
		margin-top: 1rem;
		justify-content: center;
		font-size: 0.9rem;
	}
	.row.editing input {
		width: 100%;
		padding: 0.25rem 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: 4px;
		font-size: 0.85rem;
	}

	@media (max-width: 640px) {
		main {
			padding: 1rem;
		}
		h1 {
			font-size: 1.2rem;
		}
		.toolbar {
			flex-direction: column;
			gap: 0.5rem;
		}
		.controls {
			margin-left: 0;
			width: 100%;
		}
		input[type='search'] {
			min-width: unset;
			flex: 1;
		}

		/* Drop shared grid on mobile so rows render as cards */
		.table-wrap {
			display: block;
		}

		/* Hide column header row — not meaningful in card layout */
		.row.header {
			display: none;
		}

		/* Convert each data row from grid to vertical card */
		.row {
			display: flex;
			flex-direction: column;
			gap: 0.35rem;
			padding: 0.75rem;
			position: relative;
		}

		/* Checkbox pinned to top-right corner of card */
		.col-check {
			position: absolute;
			top: 0.75rem;
			right: 0.75rem;
			justify-self: unset;
		}

		/* Allow full text to show (no truncation in card layout) */
		.col-text,
		.col-trans {
			white-space: normal;
			overflow: visible;
			font-size: 0.95rem;
		}
		.col-trans {
			color: var(--color-muted);
			font-style: italic;
		}

		/* Action buttons: share the row, touch-friendly height */
		.col-actions {
			padding-top: 0.25rem;
		}
		.col-actions button {
			min-height: 44px;
			flex: 1;
		}

		/* Pagination: bigger tap targets */
		.pagination button {
			min-height: 44px;
			padding: 0.5rem 1rem;
		}
	}
</style>
