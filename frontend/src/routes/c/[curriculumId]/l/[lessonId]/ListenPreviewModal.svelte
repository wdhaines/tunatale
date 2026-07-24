<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { api, type ListenPreviewCandidate, type ListenResponse, type WordRating } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';

	let {
		lessonId,
		onDone,
	}: {
		lessonId: string;
		onDone: (result: ListenResponse | { status: 'cancelled' }) => void;
	} = $props();

	let candidates = $state<ListenPreviewCandidate[]>([]);
	let loading = $state(true);
	let error = $state('');
	let committing = $state(false);
	// Keyed by candidateKey(c) (`${kind}:${text}`), NOT c.text — a key phrase
	// can share literal text with a lemma, and c.text alone would collide two
	// unrelated rows onto one checkbox (F6).
	let selection = $state<Record<string, boolean>>({});
	let ratings = $state<Record<string, WordRating>>({});

	let countdown = $state(10);
	let countdownCancelled = $state(false);
	let countdownId: ReturnType<typeof setTimeout> | null = null;

	let overlayEl: HTMLDivElement | undefined;
	// The programmatic overlayEl.focus() call below synchronously fires our own
	// onfocusin handler (focus/focusin dispatch synchronously in both real
	// browsers and jsdom) — without this guard that self-triggered event would
	// cancel the countdown before it even starts, AND leave the interval
	// created moments later permanently uncancelable (cancelCountdown's
	// countdownCancelled guard short-circuits before reaching clearInterval).
	let suppressInitialFocus = false;

	function candidateKey(c: ListenPreviewCandidate): string {
		return `${c.kind}:${c.text}`;
	}

	function clearCountdownTimer() {
		if (countdownId !== null) {
			clearInterval(countdownId);
			countdownId = null;
		}
	}

	function cancelCountdown() {
		if (countdownCancelled) return;
		countdownCancelled = true;
		clearCountdownTimer();
	}

	// Navigating away destroys the modal without any interaction reaching the
	// overlay, so the countdown is still armed: without this the interval keeps
	// ticking on a dead component and auto-commits a listen for a lesson the
	// user has left. Clears the timer only — no $state writes after destroy.
	onDestroy(clearCountdownTimer);

	function cancel() {
		cancelCountdown();
		committing = true;
		onDone({ status: 'cancelled' });
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

	function handleFocusIn() {
		if (suppressInitialFocus) return;
		handleInteraction();
	}

	// A row's selection state and its rating are kept in lockstep by the
	// handlers below (never by mutating during commit — see doCommit): a
	// checked row carries one of the four real grades (again/hard/good/easy —
	// matching DrillCard.svelte's vocabulary); an unchecked row is always
	// "skip". The checkbox is the only UI for "skip" — there is no per-row
	// Skip button.
	let selectedCount = $derived(
		candidates.filter((c) => selection[candidateKey(c)]).length,
	);

	onMount(async () => {
		// Focus the overlay immediately so a real Escape keypress (which a
		// browser always dispatches at document.activeElement) actually reaches
		// cancelOnKeydown. Without this, focus stays on whatever button opened
		// the modal and Escape hits <body> instead.
		suppressInitialFocus = true;
		overlayEl?.focus();
		suppressInitialFocus = false;

		try {
			const preview = await api.getListenPreview(lessonId);
			candidates = preview.candidates;
			// Default: all checked, all "good"
			const sel: Record<string, boolean> = {};
			const rts: Record<string, WordRating> = {};
			for (const c of candidates) {
				const key = candidateKey(c);
				sel[key] = true;
				rts[key] = 'good';
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

	function toggle(key: string) {
		handleInteraction();
		const nextSelected = !selection[key];
		selection = { ...selection, [key]: nextSelected };
		ratings = { ...ratings, [key]: nextSelected ? 'good' : 'skip' };
	}

	function setRating(key: string, rating: WordRating) {
		handleInteraction();
		ratings = { ...ratings, [key]: rating };
		selection = { ...selection, [key]: rating !== 'skip' };
	}

	function selectAll() {
		handleInteraction();
		const sel: Record<string, boolean> = {};
		const rts: Record<string, WordRating> = { ...ratings };
		for (const c of candidates) {
			const key = candidateKey(c);
			sel[key] = true;
			if (rts[key] === 'skip') rts[key] = 'good';
		}
		selection = sel;
		ratings = rts;
	}

	function skipAll() {
		handleInteraction();
		const sel: Record<string, boolean> = {};
		const rts: Record<string, WordRating> = { ...ratings };
		for (const c of candidates) {
			const key = candidateKey(c);
			sel[key] = false;
			rts[key] = 'skip';
		}
		selection = sel;
		ratings = rts;
	}

	// Builds the commit payload purely from local reads — never assigns into
	// the $state maps (that was the F5 bug: mutating `ratings[c.text] = 'skip'`
	// during commit left a sticky "skip" behind a later re-check). Only
	// non-default entries are sent: the backend defaults an absent entry to
	// "good", so a checked+good row contributes nothing.
	function buildRatings(): {
		wordRatings: Record<string, WordRating>;
		kpRatings: Record<string, WordRating>;
	} {
		const wordRatings: Record<string, WordRating> = {};
		const kpRatings: Record<string, WordRating> = {};

		for (const c of candidates) {
			const key = candidateKey(c);
			const selected = selection[key];
			const rating = ratings[key] ?? 'good';
			const value: WordRating | null = !selected ? 'skip' : rating !== 'good' ? rating : null;
			if (value === null) continue;
			if (c.kind === 'kp') {
				kpRatings[c.text] = value;
			} else {
				wordRatings[c.text] = value;
			}
		}

		return { wordRatings, kpRatings };
	}

	async function doCommit() {
		committing = true;
		error = '';

		const { wordRatings, kpRatings } = buildRatings();

		try {
			const result = await listenedStore.markListened(lessonId, wordRatings, kpRatings);
			onDone(result);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			committing = false;
		}
	}
</script>

<div class="overlay" role="dialog" aria-modal="true" aria-label="Listen preview" tabindex="-1"
	bind:this={overlayEl}
	onpointerdown={handleInteraction}
	onfocusin={handleFocusIn}
	onkeydown={(e) => { handleInteraction(); cancelOnKeydown(e); }}
>
	<div class="modal">
		<h2>Words in this lesson</h2>

		{#if loading}
			<p class="status">Loading...</p>
		{:else if error}
			<p class="error">{error}</p>
		{:else}
			<!-- F4: the countdown must be visible whenever it's running, including
			     the zero-candidates case — otherwise it silently auto-commits with
			     no on-screen indication anything is about to happen. -->
			{#if !countdownCancelled}
				<p class="countdown">Auto-marking in {countdown}s</p>
			{/if}

			{#if candidates.length === 0}
				<p class="status">No new words to add.</p>
			{:else}
				<div class="actions">
					<button onclick={selectAll} type="button">Select All</button>
					<button onclick={skipAll} type="button">Skip All</button>
				</div>

				<ul class="list">
					{#each candidates as c (candidateKey(c))}
						<li>
							<label class="candidate">
								<input
									type="checkbox"
									checked={selection[candidateKey(c)]}
									onchange={() => toggle(candidateKey(c))}
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
										class:active={ratings[candidateKey(c)] === 'again'}
										onclick={() => setRating(candidateKey(c), 'again')}
										type="button"
									>Again</button>
									<button
										class:active={ratings[candidateKey(c)] === 'hard'}
										onclick={() => setRating(candidateKey(c), 'hard')}
										type="button"
									>Hard</button>
									<button
										class:active={ratings[candidateKey(c)] === 'good'}
										onclick={() => setRating(candidateKey(c), 'good')}
										type="button"
									>Good</button>
									<button
										class:active={ratings[candidateKey(c)] === 'easy'}
										onclick={() => setRating(candidateKey(c), 'easy')}
										type="button"
									>Easy</button>
								</div>
							</label>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}

		<div class="footer">
			<button onclick={cancel} type="button" class="cancel">Cancel</button>
			<button onclick={() => { handleInteraction(); doCommit(); }} disabled={loading || committing || !!error}>
				{committing ? 'Syncing...' : selectedCount > 0 ? `Mark ${selectedCount} as listened` : 'Mark as listened'}
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
		/* Four buttons (Again/Hard/Good/Easy) instead of three — tightened gap
		   and button padding below to keep the row inside the 400px modal on a
		   narrow phone; flex-shrink:0 keeps them from being squeezed unreadable
		   (`.text` has flex:1 + min-width:0 + ellipsis, so it absorbs the
		   pressure instead). */
		gap: 0.2rem;
		flex-shrink: 0;
	}
	.rating-btns button {
		padding: 0.15rem 0.3rem;
		font-size: 0.68rem;
		border: 1px solid var(--color-border);
		border-radius: 3px;
		background: var(--color-surface-2);
		color: var(--color-muted);
		cursor: pointer;
		flex-shrink: 0;
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
