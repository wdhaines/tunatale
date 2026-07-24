<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type ListenPreviewCandidate, type WordRating } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';

	let {
		lessonId,
		onDone,
	}: {
		lessonId: string;
		onDone: (result: { status: string; created: number; staged: number; remaining_candidates: number; listen_count: number }) => void;
	} = $props();

	let candidates = $state<ListenPreviewCandidate[]>([]);
	let loading = $state(true);
	let error = $state('');
	let committing = $state(false);
	let selection = $state<Record<string, boolean>>({});
	let ratings = $state<Record<string, WordRating>>({});

	let countdown = $state(10);
	let countdownCancelled = $state(false);
	let countdownId: ReturnType<typeof setTimeout> | null = null;

	function cancelCountdown() {
		if (countdownCancelled) return;
		countdownCancelled = true;
		if (countdownId !== null) {
			clearInterval(countdownId);
			countdownId = null;
		}
	}

	function cancel() {
		cancelCountdown();
		committing = true;
		onDone({ status: 'cancelled', created: 0, staged: 0, remaining_candidates: 0, listen_count: 0 });
	}

	function cancelOnKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			cancel();
		}
	}

	function handleInteraction() {
		cancelCountdown();
	}

	let selectedCount = $derived(
		candidates.filter((c) => selection[c.text]).length,
	);

	onMount(async () => {
		try {
			const preview = await api.getListenPreview(lessonId);
			candidates = preview.candidates;
			// Default: all checked, all "good"
			const sel: Record<string, boolean> = {};
			const rts: Record<string, WordRating> = {};
			for (const c of candidates) {
				sel[c.text] = true;
				rts[c.text] = 'good';
			}
			selection = sel;
			ratings = rts;

			// Start 10-second countdown
			countdownId = setInterval(() => {
				countdown -= 1;
				if (countdown <= 0) {
					cancelCountdown();
					void doCommit();
				}
			}, 1000);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	function toggle(text: string) {
		handleInteraction();
		selection = { ...selection, [text]: !selection[text] };
	}

	function setRating(text: string, rating: WordRating) {
		handleInteraction();
		ratings = { ...ratings, [text]: rating };
	}

	function selectAll() {
		handleInteraction();
		const sel: Record<string, boolean> = {};
		for (const c of candidates) sel[c.text] = true;
		selection = sel;
	}

	function skipAll() {
		handleInteraction();
		const sel: Record<string, boolean> = {};
		for (const c of candidates) sel[c.text] = false;
		selection = sel;
	}

	async function doCommit() {
		committing = true;
		error = '';

		const wordRatings: Record<string, WordRating> = {};
		const kpRatings: Record<string, WordRating> = {};

		for (const c of candidates) {
			if (!selection[c.text]) {
				ratings[c.text] = 'skip';
			}
			const r = ratings[c.text] ?? 'good';
			if (c.kind === 'kp') {
				kpRatings[c.text] = r;
			} else {
				wordRatings[c.text] = r;
			}
		}

		try {
			const result = await listenedStore.markListened(lessonId, wordRatings, kpRatings);
			onDone({
				status: result.status,
				created: result.created,
				staged: result.staged,
				remaining_candidates: result.remaining_candidates,
				listen_count: result.listen_count,
			});
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			committing = false;
		}
	}
</script>

<div class="overlay" role="dialog" aria-modal="true" aria-label="Listen preview" tabindex="-1"
	onpointerdown={handleInteraction}
	onfocus={handleInteraction}
	onkeydown={(e) => { handleInteraction(); cancelOnKeydown(e); }}
>
	<div class="modal">
		<h2>Words in this lesson</h2>

		{#if loading}
			<p class="status">Loading...</p>
		{:else if error}
			<p class="error">{error}</p>
		{:else if candidates.length === 0}
			<p class="status">No new words to add.</p>
		{:else}
			{#if !countdownCancelled}
				<p class="countdown">Auto-marking in {countdown}s</p>
			{/if}

			<div class="actions">
				<button onclick={selectAll} type="button">Select All</button>
				<button onclick={skipAll} type="button">Skip All</button>
			</div>

			<ul class="list">
				{#each candidates as c (c.text)}
					<li>
						<label class="candidate">
							<input
								type="checkbox"
								checked={selection[c.text]}
								onchange={() => toggle(c.text)}
							/>
							<span class="text">{c.text}</span>
							{#if c.translation}
								<span class="translation">{c.translation}</span>
							{/if}
							{#if c.kind === 'kp'}
								<span class="tag kp">key phrase</span>
							{/if}
							{#if c.grade_class && c.kind !== 'create'}
								<span class="tag">{c.grade_class}</span>
							{/if}
							<div class="rating-btns">
								<button
									class:active={ratings[c.text] === 'easy'}
									onclick={() => setRating(c.text, 'easy')}
									type="button"
								>Easy</button>
								<button
									class:active={ratings[c.text] === 'good'}
									onclick={() => setRating(c.text, 'good')}
									type="button"
								>Good</button>
								<button
									class:active={ratings[c.text] === 'skip'}
									onclick={() => setRating(c.text, 'skip')}
									type="button"
								>Skip</button>
							</div>
						</label>
					</li>
				{/each}
			</ul>
		{/if}

		<div class="footer">
			<button onclick={cancel} type="button" class="cancel">Cancel</button>
			<button onclick={() => { handleInteraction(); doCommit(); }} disabled={!selectedCount || committing}>
				{committing ? 'Syncing...' : `Mark ${selectedCount} as listened`}
			</button>
		</div>
	</div>
</div>

<style>
	.overlay {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 1000;
	}
	.modal {
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-md);
		padding: 1.5rem;
		max-width: 400px;
		width: 90%;
		max-height: 80vh;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	h2 {
		margin: 0;
		font-size: 1.1rem;
		font-weight: 700;
	}
	.status {
		color: var(--color-muted);
		font-size: 0.9rem;
		text-align: center;
		padding: 1rem 0;
	}
	.error {
		color: var(--color-danger);
		font-size: 0.9rem;
	}
	.countdown {
		color: var(--color-muted);
		font-size: 0.8rem;
		text-align: center;
		margin: 0;
	}
	.actions {
		display: flex;
		gap: 0.5rem;
	}
	.actions button {
		flex: 1;
		padding: 0.4rem;
		font-size: 0.8rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		background: var(--color-surface-2);
		color: var(--color-text);
		cursor: pointer;
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		overflow-y: auto;
		max-height: 50vh;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.candidate {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.3rem 0.4rem;
		border-radius: var(--radius-sm);
		font-size: 0.85rem;
	}
	.candidate:hover {
		background: var(--color-surface-2);
	}
	.text {
		flex: 1;
		min-width: 0;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.translation {
		font-size: 0.75rem;
		color: var(--color-muted);
		white-space: nowrap;
	}
	.tag {
		font-size: 0.7rem;
		padding: 0.1rem 0.3rem;
		border-radius: 3px;
		background: color-mix(in srgb, var(--color-info) 14%, transparent);
		color: var(--color-muted);
		white-space: nowrap;
	}
	.tag.kp {
		background: color-mix(in srgb, var(--color-warning) 14%, transparent);
		color: var(--color-warning);
	}
	.rating-btns {
		display: flex;
		gap: 0.25rem;
	}
	.rating-btns button {
		padding: 0.2rem 0.35rem;
		font-size: 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: 3px;
		background: var(--color-surface-2);
		color: var(--color-muted);
		cursor: pointer;
	}
	.rating-btns button.active {
		background: var(--color-primary);
		color: #fff;
		border-color: var(--color-primary);
	}
	.footer {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
		padding-top: 0.5rem;
		border-top: 1px solid var(--color-border);
	}
	.footer button {
		padding: 0.5rem 1rem;
		border-radius: var(--radius-sm);
		background: var(--color-primary);
		color: #fff;
		border: none;
		font-weight: 600;
		cursor: pointer;
	}
	.footer button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.footer button.cancel {
		background: var(--color-surface-2);
		color: var(--color-text);
		border: 1px solid var(--color-border);
	}
</style>
