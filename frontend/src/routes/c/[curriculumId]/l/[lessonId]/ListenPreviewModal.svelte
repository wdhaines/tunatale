<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { SvelteDate, SvelteSet } from 'svelte/reactivity';
	import { api, type ListenPreviewCandidate, type ListenResponse, type WordRating } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import { listenCountdownPref } from '$lib/stores/listenCountdownPref.svelte';
	import { masteryBackgroundColor, masteryColor } from '$lib/mastery';

	let {
		lessonId,
		languageCode,
		onDone,
	}: {
		lessonId: string;
		languageCode?: string;
		onDone: (result: ListenResponse | { status: 'cancelled' }) => void;
	} = $props();

	let candidates = $state<ListenPreviewCandidate[]>([]);
	let loading = $state(true);
	let error = $state('');
	let committing = $state(false);
	// Keyed by candidateKey(c) (`${kind}:${text}`), NOT c.text — a key phrase
	// can share literal text with a lemma, and c.text alone would collide two
	// unrelated rows onto one row of state (F6).
	//
	// This is now the ONLY per-row state. There used to be a parallel
	// `selection` map kept in lockstep with it, but the checkbox and the grade
	// buttons were two controls for one fact: a row is skipped iff its rating
	// is "skip". Deriving selection removes the class of bug where the two
	// could disagree.
	let ratings = $state<Record<string, WordRating>>({});
	// Glosses are blurred until tapped; a revealed gloss stays revealed for the
	// life of the modal (per row, not globally).
	let revealed = new SvelteSet<string>();

	let countdown = $state(10);
	let countdownCancelled = $state(false);
	// Deliberately NOT $state: this is a bare timer handle, never read from the
	// template, and onDestroy(clearCountdownTimer) writes it after teardown —
	// which is exactly why it must stay non-reactive (see onDestroy below).
	// svelte-ignore non_reactive_update
	let countdownId: ReturnType<typeof setTimeout> | null = null;

	let overlayEl: HTMLDivElement | undefined;
	// The programmatic overlayEl.focus() call below synchronously fires our own
	// onfocusin handler (focus/focusin dispatch synchronously in both real
	// browsers and jsdom) — without this guard that self-triggered event would
	// cancel the countdown before it even starts, AND leave the interval
	// created moments later permanently uncancelable (cancelCountdown's
	// countdownCancelled guard short-circuits before reaching clearInterval).
	let suppressInitialFocus = false;

	// The four real grades, in DrillCard.svelte's order. "skip" is deliberately
	// NOT in this list: it is the absence of a grade, and the UI sets it apart.
	const GRADES = ['again', 'hard', 'good', 'easy'] as const satisfies readonly WordRating[];

	// WordSpan.svelte renders an untracked word in this indigo. A `create` row
	// IS an untracked word, so its dueness pill uses the same colour rather
	// than a position on the mastery ramp it has not joined yet.
	const UNKNOWN_COLOR = '#818cf8';

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

	let selectedCount = $derived(
		candidates.filter((c) => ratings[candidateKey(c)] !== 'skip').length,
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
			// Default: everything "good" — except well-known rows, which start
			// "skip" (and collapsed inside the disclosure).
			const rts: Record<string, WordRating> = {};
			for (const c of candidates) {
				rts[candidateKey(c)] = c.well_known ? 'skip' : 'good';
			}
			ratings = rts;

			// Start countdown only when the pref is not "off".
			const prefValue = listenCountdownPref.value;
			if (prefValue !== 'off') {
				countdown = parseInt(prefValue, 10);
				countdownId = setInterval(() => {
					countdown -= 1;
					if (countdown <= 0) {
						cancelCountdown();
						void doCommit();
					}
				}, 1000);
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	function setRating(key: string, rating: WordRating) {
		handleInteraction();
		ratings = { ...ratings, [key]: rating };
	}

	function revealGloss(key: string) {
		handleInteraction();
		revealed.add(key);
	}

	function gradeAll() {
		handleInteraction();
		const rts: Record<string, WordRating> = { ...ratings };
		for (const c of candidates) {
			const key = candidateKey(c);
			if (rts[key] === 'skip') rts[key] = 'good';
		}
		ratings = rts;
	}

	function skipAll() {
		handleInteraction();
		const rts: Record<string, WordRating> = { ...ratings };
		for (const c of candidates) rts[candidateKey(c)] = 'skip';
		ratings = rts;
	}

	/** Whole days from today (UTC) until the card is due; null if unknown. */
	function dueDays(due_at: string | null): number | null {
		if (!due_at) return null;
		const dueDate = new SvelteDate(due_at);
		if (isNaN(dueDate.getTime())) return null;
		const today = new SvelteDate();
		today.setUTCHours(0, 0, 0, 0);
		const dueDay = new SvelteDate(dueDate);
		dueDay.setUTCHours(0, 0, 0, 0);
		return Math.round((dueDay.getTime() - today.getTime()) / 86400000);
	}

	function formatDueAt(due_at: string | null): string | null {
		const days = dueDays(due_at);
		if (days === null) return null;
		return days === 0 ? 'today' : `${days}d`;
	}

	// One cell for the whole dueness story. "due"/"ahead" used to be rendered
	// as a word beside the day count, but under a "Due" header the sign already
	// says overdue, so the word carried nothing. The two states that are not a
	// day count keep a word of their own.
	function dueLabel(c: ListenPreviewCandidate): string {
		if (c.kind === 'create') return 'new';
		if (c.grade_class === 'learning') return 'learn';
		return formatDueAt(c.due_at ?? null) ?? c.grade_class ?? '';
	}

	function isOverdue(c: ListenPreviewCandidate): boolean {
		if (c.kind === 'create') return false;
		const days = dueDays(c.due_at ?? null);
		return days !== null && days <= 0;
	}

	// The dialogue's own colour language, via mastery.ts's own functions —
	// red→yellow→green by progress, so a word looks the same here as it does in
	// the transcript. Dueness is weight, not hue (WordSpan's `.word-due`).
	function dueStyle(c: ListenPreviewCandidate): string {
		if (c.kind === 'create') {
			return `color: ${UNKNOWN_COLOR}; background: color-mix(in srgb, ${UNKNOWN_COLOR} 18%, transparent);`;
		}
		const p = c.progress ?? 0;
		return `color: ${masteryColor(p)}; background: ${masteryBackgroundColor(p)};`;
	}

	let mainCandidates = $derived(candidates.filter((c) => !c.well_known));
	let wellKnownCandidates = $derived(candidates.filter((c) => c.well_known));

	// Builds the commit payload purely from local reads — never assigns into
	// the $state map (that was the F5 bug: mutating `ratings[c.text] = 'skip'`
	// during commit left a sticky "skip" behind a later re-grade). Only
	// non-default entries are sent: the backend defaults an absent entry to
	// "good", so an ordinary good row contributes nothing. Well-known rows are
	// an exception: the backend skips a well-known lemma absent from
	// word_ratings, so a graded well-known row MUST emit its explicit rating.
	function buildRatings(): {
		wordRatings: Record<string, WordRating>;
		kpRatings: Record<string, WordRating>;
	} {
		const wordRatings: Record<string, WordRating> = {};
		const kpRatings: Record<string, WordRating> = {};

		for (const c of candidates) {
			const rating = ratings[candidateKey(c)] ?? 'good';
			const isWellKnown = c.well_known === true;
			const value: WordRating | null =
				rating === 'skip' ? 'skip' : isWellKnown || rating !== 'good' ? rating : null;
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
			{#if !countdownCancelled && countdownId !== null}
				<p class="countdown">Auto-marking in {countdown}s</p>
			{/if}

			{#if candidates.length === 0}
				<p class="status">No new words to add.</p>
			{:else}
				<div class="actions">
					<button onclick={gradeAll} type="button">Grade All</button>
					<button onclick={skipAll} type="button">Skip All</button>
				</div>

				{#snippet candidateRow(c: ListenPreviewCandidate)}
					{@const key = candidateKey(c)}
					<li class="candidate">
						<!-- lang drives `hyphens: auto`, so a long compound breaks at a
						     real dictionary point (etterforsknings-team) instead of
						     mid-morpheme. -->
						<span class="text" lang={languageCode}>{c.text}</span>

						<div class="sub" class:revealed={revealed.has(key)}>
							{#if c.kind === 'kp'}
								<span class="tag kp">key phrase</span>
							{/if}
							{#if c.translation}
								<button
									type="button"
									class="gloss"
									class:blurred={!revealed.has(key)}
									aria-label={revealed.has(key) ? c.translation : `Reveal gloss for ${c.text}`}
									onclick={() => revealGloss(key)}
								>{c.translation}</button>
							{:else}
								<span class="gloss empty" aria-label="No gloss available">&mdash;</span>
							{/if}
						</div>

						<span class="tag day" class:overdue={isOverdue(c)} style={dueStyle(c)}>
							{dueLabel(c)}
						</span>

						<div class="grade" role="group" aria-label={`Proposed grade for ${c.text}`}>
							<!-- Skip is the opposite of grading, not a fifth grade, so it
							     sits outside the welded segmented control. -->
							<button
								class="skip"
								class:active={ratings[key] === 'skip'}
								data-candidate={key}
								data-grade="skip"
								aria-pressed={ratings[key] === 'skip'}
								onclick={() => setRating(key, 'skip')}
								type="button"
							>Skip</button>
							<div class="grades">
								{#each GRADES as g (g)}
									<button
										class={g}
										class:active={ratings[key] === g}
										data-candidate={key}
										data-grade={g}
										aria-pressed={ratings[key] === g}
										onclick={() => setRating(key, g)}
										type="button"
									>{g[0].toUpperCase() + g.slice(1)}</button>
								{/each}
							</div>
						</div>
					</li>
				{/snippet}

				<div class="list-head" aria-hidden="true">
					<span>Word</span><span>Due</span><span>Proposed grade</span>
				</div>

				<ul class="list">
					{#each mainCandidates as c (candidateKey(c))}
						{@render candidateRow(c)}
					{/each}
				</ul>

				{#if wellKnownCandidates.length > 0}
					<details class="well-known-group">
						<summary>{wellKnownCandidates.length} well-known word{wellKnownCandidates.length !== 1 ? 's' : ''}</summary>
						<ul class="list">
							{#each wellKnownCandidates as c (candidateKey(c))}
								{@render candidateRow(c)}
							{/each}
						</ul>
					</details>
				{/if}
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
		max-width: 420px;
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
		min-width: 0;
		overflow-y: auto;
		overflow-x: hidden;
		max-height: 50vh;
	}

	/* The header and every row are SEPARATE grid containers, so the tracks must
	   be deterministic for the columns to line up — an `auto` track resolves
	   against its own container's content, which made a row with a narrow "new"
	   pill compute different columns than one with "today". The two trailing
	   tracks are fixed and the grade control fills its track rather than sizing
	   it. Verified by measuring every row's cell offsets against the header's:
	   max deviation must be 0. */
	.list-head,
	.candidate {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 3rem 11rem;
		gap: 0.1rem 0.35rem;
		padding: 0.35rem 0.15rem;
	}
	.list-head {
		align-items: center;
		font-size: 0.6rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--color-muted);
		border-bottom: 1px solid var(--color-border);
		padding-bottom: 0.35rem;
	}
	.list-head span {
		text-align: left;
	}
	.candidate + .candidate {
		border-top: 1px solid color-mix(in srgb, var(--color-border) 45%, transparent);
	}
	.candidate:hover {
		background: var(--color-surface-2);
	}

	/* The word is the item being graded, so it is never truncated. Measured
	   against the real NO+SL card corpus (3611 single-word lemmas): ellipsising
	   it beside the gloss cut 630 of them (17.4%) — every Norwegian compound
	   past ~9 chars. Stacked, with dictionary hyphenation, 2 wrap and none goes
	   past two lines. overflow-wrap is only the backstop for a word the
	   hyphenation dictionary has no break point for. */
	.text {
		grid-column: 1;
		grid-row: 1;
		font-size: 0.85rem;
		line-height: 1.25;
		hyphens: auto;
		overflow-wrap: break-word;
		min-width: 0;
	}

	.sub {
		grid-column: 1;
		grid-row: 2;
		display: flex;
		align-items: baseline;
		gap: 0.25rem;
		min-width: 0;
	}
	/* Revealed, the gloss borrows the Due column too — the extra ~50px is the
	   difference between a readable gloss and a wrapped stack of fragments. */
	.sub.revealed {
		grid-column: 1 / 3;
	}
	.gloss {
		font-size: 0.72rem;
		line-height: 1.25;
		color: var(--color-muted);
		min-width: 0;
		background: none;
		border: none;
		padding: 0;
		margin: 0;
		font-family: inherit;
		text-align: left;
		cursor: pointer;
	}
	/* Glosses are hidden by default so the lesson is a listening exercise
	   first. Blur rather than omission keeps the row's shape stable and shows
	   there IS something to reveal. */
	.gloss.blurred {
		filter: blur(4.5px);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		display: block;
		width: 100%;
		-webkit-user-select: none;
		user-select: none;
	}
	.gloss:not(.blurred) {
		white-space: normal;
		color: var(--color-text);
	}
	.gloss.empty {
		color: var(--color-border);
		cursor: default;
	}

	.tag {
		font-size: 0.66rem;
		padding: 0.12rem 0.34rem;
		border-radius: 4px;
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
		color: var(--color-muted);
	}
	.tag.day {
		grid-column: 2;
		grid-row: 1 / 3;
		align-self: center;
		justify-self: start;
	}
	/* Mirrors WordSpan's `.word-due { font-weight: bold }` — in the dialogue
	   dueness is weight, not hue, so the mastery ramp stays the only colour
	   axis. */
	.tag.overdue {
		font-weight: 700;
	}
	.tag.kp {
		background: color-mix(in srgb, var(--color-warning) 18%, transparent);
		color: var(--color-warning);
		flex-shrink: 0;
	}

	.grade {
		grid-column: 3;
		grid-row: 1 / 3;
		align-self: stretch;
		display: flex;
		gap: 0.3rem;
		align-items: stretch;
		width: 100%;
		/* Floors the tap targets: centring the due pill across both rows
		   shortened the row, which would otherwise have quietly shrunk them. */
		min-height: 2.15rem;
	}
	.grades {
		display: flex;
		flex: 1 1 auto;
		min-width: 0;
		border: 1px solid var(--color-border);
		border-radius: 6px;
		overflow: hidden;
	}
	.grade button {
		flex: 1 1 0;
		min-width: 0;
		/* 0.66rem is the smallest size at which "Again" stops clipping its
		   segment at this track width — measured, not chosen. */
		font-size: 0.66rem;
		padding: 0.1rem;
		font-family: inherit;
		border: none;
		background: var(--color-surface-2);
		color: var(--color-muted);
		cursor: pointer;
		white-space: nowrap;
	}
	.grades button {
		border-right: 1px solid var(--color-border);
	}
	.grades button:last-child {
		border-right: none;
	}
	/* An active grade wears DrillCard.svelte's colour for that rating, so a
	   grade means the same thing in both places. Inactive stays muted — four
	   saturated buttons on every row would be a wall. */
	.grades button.active {
		color: #fff;
	}
	.grades button.again.active {
		background: var(--color-danger);
	}
	.grades button.hard.active {
		background: var(--color-warning);
	}
	.grades button.good.active {
		background: var(--color-success);
	}
	.grades button.easy.active {
		background: var(--color-primary);
	}
	.grade button.skip {
		flex: 0 0 auto;
		background: transparent;
		border: 1px dashed var(--color-border);
		border-radius: 6px;
		font-style: italic;
	}
	.grade button.skip.active {
		background: color-mix(in srgb, var(--color-muted) 22%, transparent);
		border-style: solid;
		border-color: var(--color-muted);
		color: var(--color-text);
		font-style: normal;
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
	.well-known-group {
		border-top: 1px solid var(--color-border);
		padding-top: 0.5rem;
		margin-top: 0.25rem;
	}
	.well-known-group summary {
		font-size: 0.8rem;
		color: var(--color-muted);
		cursor: pointer;
		padding: 0.25rem 0;
	}
</style>
