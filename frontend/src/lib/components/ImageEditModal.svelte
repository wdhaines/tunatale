<script lang="ts">
	import type { SRSItemDetail, ImageCandidate, ImageCandidatesResponse } from '$lib/api';
	import { api } from '$lib/api';

	let { item, onclose, onupdated }: {
		item: SRSItemDetail;
		onclose: () => void;
		onupdated: () => void;
	} = $props();

	let candidates = $state<ImageCandidate[]>([]);
	let candidateQuery = $state('');
	let candidateLoading = $state(false);
	let candidateError = $state<string | null>(null);
	let noApiKey = $state(false);

	let pasteUrl = $state('');
	let pasteLoading = $state(false);

	let busy = $state(false);
	let error = $state<string | null>(null);

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) onclose();
	}

	async function loadCandidates() {
		candidateLoading = true;
		candidateError = null;
		noApiKey = false;
		candidates = [];
		try {
			const resp = await api.fetchImageCandidates(item.id, candidateQuery || undefined);
			if (resp.status === 'rate_limited') {
				candidateError = 'Rate limited — try again shortly';
				return;
			}
			if (resp.status === 'api_error') {
				candidateError = 'Pixabay unavailable — try again shortly';
				return;
			}
			candidates = resp.candidates;
		} catch (e) {
			const msg = e instanceof Error ? e.message : String(e);
			if (msg.includes('409')) {
				noApiKey = true;
			} else {
				candidateError = msg || 'Failed to fetch candidates';
			}
		} finally {
			candidateLoading = false;
		}
	}

	async function selectCandidate(url: string) {
		busy = true;
		error = null;
		try {
			await api.setItemImageFromUrl(item.id, url);
			onupdated();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}

	async function removeImage() {
		busy = true;
		error = null;
		try {
			await api.removeItemImage(item.id);
			onupdated();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}

	async function setFromPaste() {
		if (!pasteUrl.trim()) return;
		pasteLoading = true;
		error = null;
		try {
			await api.setItemImageFromUrl(item.id, pasteUrl.trim());
			pasteUrl = '';
			onupdated();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			pasteLoading = false;
		}
	}

	async function handleFileUpload(e: Event) {
		const input = e.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		busy = true;
		error = null;
		try {
			await api.uploadItemImage(item.id, file);
			onupdated();
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			busy = false;
			input.value = '';
		}
	}

	$effect(() => {
		loadCandidates();
	});
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="backdrop" role="dialog" tabindex="-1" aria-label="Edit image" onclick={handleBackdropClick} onkeydown={handleKeydown}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="modal" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()} tabindex="-1">
		<div class="modal-header">
			<h2>Edit Image</h2>
			<button class="close-btn" onclick={onclose} aria-label="Close">&times;</button>
		</div>

		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}

		{#if busy}
			<p class="muted">Working…</p>
		{/if}

		<section class="current-image">
			<h3>Current image</h3>
			{#if item.image_url}
				<div class="current-img-wrap">
					<img src={item.image_url} alt={item.text} class="current-img" />
					<button class="danger" onclick={removeImage} disabled={busy}>Remove</button>
				</div>
			{:else}
				<p class="muted">No image</p>
			{/if}
		</section>

		{#if !noApiKey}
		<section class="candidates-section">
			<h3>Pixabay candidates</h3>
			<div class="candidate-controls">
				<input
					type="text"
					placeholder="Search query"
					bind:value={candidateQuery}
					onkeydown={(e) => { if (e.key === 'Enter') loadCandidates(); }}
				/>
				<button onclick={loadCandidates} disabled={candidateLoading}>
					{candidateLoading ? 'Searching…' : 'Search'}
				</button>
			</div>
			{#if candidateError}
				<p class="muted">{candidateError}</p>
			{:else if candidates.length > 0}
				<div class="candidate-grid">
					{#each candidates as c (c.webformat_url)}
						<button class="candidate-btn" onclick={() => selectCandidate(c.webformat_url)} disabled={busy} title={c.tags}>
							<img src={c.preview_url} alt={c.tags} />
						</button>
					{/each}
				</div>
			{:else if !candidateLoading}
				<p class="muted">No results</p>
			{/if}
		</section>
		{/if}

		<section class="paste-section">
			<h3>Paste URL</h3>
			<div class="paste-controls">
				<input
					type="url"
					placeholder="https://example.com/image.jpg"
					bind:value={pasteUrl}
					onkeydown={(e) => { if (e.key === 'Enter') setFromPaste(); }}
				/>
				<button onclick={setFromPaste} disabled={pasteLoading || !pasteUrl.trim()}>
					Set
				</button>
			</div>
		</section>

		<section class="upload-section">
			<h3>Upload file</h3>
			<input type="file" accept="image/*" onchange={handleFileUpload} disabled={busy} />
		</section>
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		z-index: 100;
		display: flex;
		align-items: center;
		justify-content: center;
		background: rgba(0, 0, 0, 0.5);
	}
	.modal {
		background: var(--color-surface);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow);
		padding: 1.5rem;
		max-width: 600px;
		width: 90vw;
		max-height: 80vh;
		overflow-y: auto;
	}
	.modal-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 1rem;
	}
	.modal-header h2 {
		margin: 0;
		font-size: 1.2rem;
	}
	.close-btn {
		background: none;
		border: none;
		font-size: 1.5rem;
		cursor: pointer;
		padding: 0;
		line-height: 1;
		color: var(--color-muted);
	}
	.close-btn:hover {
		color: var(--color-text);
	}
	section {
		margin-bottom: 1rem;
	}
	section h3 {
		margin: 0 0 0.5rem;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.85rem;
	}
	.error {
		color: var(--color-danger);
		font-size: 0.85rem;
		padding: 0.4rem;
		border: 1px solid var(--color-danger);
		border-radius: var(--radius);
		margin-bottom: 0.5rem;
	}
	.current-img-wrap {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}
	.current-img {
		width: 6rem;
		height: 6rem;
		object-fit: cover;
		border-radius: var(--radius);
	}
	.candidate-controls, .paste-controls {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 0.5rem;
	}
	.candidate-controls input, .paste-controls input {
		flex: 1;
		padding: 0.35rem 0.6rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
	}
	.candidate-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(5rem, 1fr));
		gap: 0.5rem;
	}
	.candidate-btn {
		padding: 0;
		border: 2px solid transparent;
		border-radius: var(--radius);
		overflow: hidden;
		cursor: pointer;
		background: none;
		aspect-ratio: 1;
	}
	.candidate-btn:hover {
		border-color: var(--color-primary);
	}
	.candidate-btn img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	button.danger {
		border-color: var(--color-danger);
		color: var(--color-danger);
	}
	button.danger:hover {
		background: color-mix(in srgb, var(--color-danger) 12%, transparent);
	}
</style>
