<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { SRSItemDetail, SRSListParams, QueueStats } from '$lib/api';
	import { syncStore } from '$lib/stores/sync.svelte';
	import ImageEditModal from '$lib/components/ImageEditModal.svelte';

	const PAGE_SIZE = 50;

	let search = $state('');
	let stateFilter = $state<'' | SRSItemDetail['state']>('');
	let sort = $state<NonNullable<SRSListParams['sort']>>('text');
	let order = $state<'asc' | 'desc'>('asc');
	let page = $state(0);
	let items = $state<SRSItemDetail[]>([]);
	let total = $state(0);
	let fetchSeq = 0;
	let selected = $state<Set<number>>(new Set());
	let editingId = $state<number | null>(null);
	let editText = $state('');
	let editTranslation = $state('');
	let loading = $state(false);
	let error = $state<string | null>(null);
	let queueStats = $state<QueueStats | null>(null);
	let syncStatus = $state<string | null>(null);
	let openMenuId = $state<number | null>(null);
	let imageEditItem = $state<SRSItemDetail | null>(null);

	function handleSyncResult() {
		syncStatus = 'Synced with AnkiWeb';
		loadItems();
	}

	let debounceTimer: ReturnType<typeof setTimeout>;
	let lastSearch = $state('');

	async function loadItems() {
		const seq = ++fetchSeq;
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
			if (seq !== fetchSeq) return;
			items = data.items;
			total = data.total;
			if (stats) queueStats = stats;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			if (seq === fetchSeq) loading = false;
		}
	}

	$effect(() => {
		// track reactive dependencies
		const _sort = sort;
		const _order = order;
		const _page = page;
		const _stateFilter = stateFilter;
		const _lastSearch = lastSearch;
		void _sort, _order, _page, _stateFilter, _lastSearch;
		loadItems();
	});

	function onSearchInput() {
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			lastSearch = search;
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

	function toggleMenu(id: number) {
		openMenuId = openMenuId === id ? null : id;
	}

	function closeMenu() {
		openMenuId = null;
	}

	function handleDocumentClick(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (target.closest('.actions-menu')) return;
		closeMenu();
	}

	function handleDocumentKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') closeMenu();
	}

	$effect(() => {
		if (openMenuId === null) return;
		document.addEventListener('click', handleDocumentClick);
		document.addEventListener('keydown', handleDocumentKeydown);
		return () => {
			document.removeEventListener('click', handleDocumentClick);
			document.removeEventListener('keydown', handleDocumentKeydown);
		};
	});

	function startEdit(item: SRSItemDetail) {
		closeMenu();
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

	function stripSoundTags(value: string): string {
		return value.replace(/\[sound:[^\]]*\]/g, '');
	}

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

	$effect(() => {
		if (syncStore.lastResult) handleSyncResult();
	});

	onMount(() => {
		loadItems();
	});
</script>

<main>
	<div class="toolbar">
		<h1>Cards <span class="muted">· {total} total{#if queueStats} · {queueStats.new} new · {queueStats.learning} learning · {queueStats.review} review{/if}</span></h1>
		<div class="controls">
			<input
				type="search"
				placeholder="Search cards"
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
			<span class="col-img"></span>
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
						<span class="col-img"></span>
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
						<span class="col-img">
							{#if item.image_url}
								<button class="thumb-btn" onclick={() => { closeMenu(); imageEditItem = item; }} title="Change image">
									<img src={item.image_url} alt="" />
								</button>
							{:else}
								<button class="thumb-btn thumb-empty" onclick={() => { closeMenu(); imageEditItem = item; }} title="Add image">+</button>
							{/if}
						</span>
						<span class="col-text">{stripSoundTags(item.text)}</span>
						<span class="col-trans">{stripSoundTags(item.translation)}</span>
						<span class="col-state state-{item.state}">{item.state}</span>
						<span class="col-due">{formatDue(item.due_at)}</span>
						<span class="col-reps">{item.reps}</span>
						<span class="col-actions actions-menu">
							<button
								class="actions-trigger"
								aria-label="Actions for {stripSoundTags(item.text)}"
								aria-haspopup="menu"
								aria-expanded={openMenuId === item.id}
								onclick={() => toggleMenu(item.id)}
							>
								⋯
							</button>
							{#if openMenuId === item.id}
								<div class="menu" role="menu">
									<button role="menuitem" onclick={() => { closeMenu(); imageEditItem = item; }}>Change image…</button>
									<button role="menuitem" onclick={() => startEdit(item)}>Edit</button>
									<button role="menuitem" onclick={() => { closeMenu(); resetItem(item.id); }}>Reset</button>
									<button role="menuitem" onclick={() => { closeMenu(); toggleSuspend(item); }}>
										{item.state === 'suspended' ? 'Unsuspend' : 'Suspend'}
									</button>
									<div class="menu-divider"></div>
									<button role="menuitem" class="danger" onclick={() => { closeMenu(); deleteItem(item.id); }}>Delete</button>
								</div>
							{/if}
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

{#if imageEditItem}
	<ImageEditModal item={imageEditItem} onclose={() => imageEditItem = null} onupdated={() => { imageEditItem = null; loadItems(); }} />
{/if}

<style>
	main {
		max-width: 1100px;
		margin: 0 auto;
		padding: 1rem;
	}
	h1 {
		margin: 0 0 0.5rem;
		font-size: 1.3rem;
		font-weight: 800;
		letter-spacing: -0.01em;
	}
	.muted {
		color: var(--color-muted);
		font-weight: normal;
	}
	.toolbar {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}
	.controls {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
		width: 100%;
	}
	input[type='search'] {
		padding: 0.4rem 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.9rem;
		flex: 1;
	}
	select {
		padding: 0.4rem 0.6rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.9rem;
	}
	button {
		padding: 0.35rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface);
		color: var(--color-text);
		cursor: pointer;
		font-size: 0.85rem;
		transition: background 0.12s ease, border-color 0.12s ease;
	}
	button:hover {
		background: var(--color-surface-2);
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
		background: color-mix(in srgb, var(--color-danger) 12%, transparent);
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
	/* Mobile-first: rows render as stacked cards; the shared grid table is layered
	   on at the desktop breakpoint below. */
	.table-wrap {
		display: block;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		overflow: hidden;
		background: var(--color-surface);
		box-shadow: var(--shadow-sm);
	}
	.table-wrap > p {
		margin: 0;
		padding: 0.5rem 0.75rem;
	}
	.row {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding: 0.75rem;
		position: relative;
		border-bottom: 1px solid var(--color-border);
	}
	.row:last-child {
		border-bottom: none;
	}
	/* Column header row is meaningless in the stacked-card layout */
	.row.header {
		display: none;
		background: var(--color-surface-2);
		font-weight: 600;
		font-size: 0.85rem;
	}
	.row.editing {
		background: var(--color-highlight);
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
	/* Checkbox pinned to the card's top-right on mobile */
	.col-check {
		position: absolute;
		top: 0.75rem;
		right: 0.75rem;
	}
	.col-actions { display: flex; gap: 0.3rem; flex-wrap: wrap; padding-top: 0.25rem; }
	.col-actions button { min-height: 44px; flex: 1; }
	.col-img { display: flex; align-items: center; justify-content: center; }
	.thumb-btn {
		padding: 0;
		border: none;
		background: none;
		cursor: pointer;
		border-radius: 4px;
		overflow: hidden;
		width: 3rem;
		height: 3rem;
	}
	.thumb-btn img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	.thumb-empty {
		color: var(--color-muted);
		font-size: 1.2rem;
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.thumb-btn:hover { outline: 2px solid var(--color-primary); }
	.actions-menu {
		position: relative;
		justify-content: flex-end;
		flex-wrap: nowrap;
	}
	.actions-trigger {
		flex: 0 0 auto;
		min-width: 44px;
		font-size: 1.1rem;
		line-height: 1;
		font-weight: 700;
	}
	.menu {
		position: absolute;
		top: calc(100% + 0.25rem);
		right: 0;
		z-index: 10;
		display: flex;
		flex-direction: column;
		min-width: 9rem;
		padding: 0.25rem;
		gap: 0.15rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface);
		box-shadow: var(--shadow);
	}
	.menu button[role='menuitem'] {
		min-height: 44px;
		flex: 0 0 auto;
		width: 100%;
		text-align: left;
		border: none;
		background: transparent;
	}
	.menu button[role='menuitem']:hover {
		background: var(--color-surface-2);
	}
	.menu button[role='menuitem'].danger {
		color: var(--color-danger);
	}
	.menu button[role='menuitem'].danger:hover {
		background: color-mix(in srgb, var(--color-danger) 12%, transparent);
	}
	.menu-divider {
		height: 1px;
		margin: 0.2rem 0.25rem;
		background: var(--color-border);
	}
	.col-text, .col-trans { white-space: normal; overflow: visible; font-size: 0.95rem; }
	.col-trans { color: var(--color-muted); font-style: italic; }
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
	.pagination button { min-height: 44px; padding: 0.5rem 1rem; }
	.row.editing input {
		width: 100%;
		padding: 0.25rem 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: 4px;
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
	}

	@media (min-width: 641px) {
		main { padding: 1.5rem; }
		h1 { font-size: 1.6rem; }
		.toolbar { flex-direction: row; align-items: flex-start; flex-wrap: wrap; gap: 1rem; }
		.controls { margin-left: auto; width: auto; }
		input[type='search'] { min-width: 180px; flex: 0 1 auto; }

		.table-wrap {
			display: grid;
			grid-template-columns: 2rem 3rem 1fr 1fr 7rem 7rem 4rem auto;
		}
		.table-wrap > p { grid-column: 1 / -1; }
		.row {
			display: grid;
			grid-template-columns: subgrid;
			grid-column: 1 / -1;
			align-items: center;
			gap: 0.5rem;
			padding: 0.5rem 0.75rem;
			position: static;
		}
		.row.header { display: grid; }
		.row.header .col-actions { justify-self: start; }
		.col-check { position: static; justify-self: center; top: auto; right: auto; }
		.col-text, .col-trans { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.9rem; }
		.col-trans { color: inherit; font-style: normal; }
		.col-actions { padding-top: 0; }
		.col-actions button { min-height: 0; flex: 0 1 auto; }
		.pagination button { min-height: 0; padding: 0.3rem 0.7rem; }
	}
</style>
