<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { SvelteDate, SvelteSet } from 'svelte/reactivity';
	import { api, type ListenPreviewCandidate, type ListenResponse, type WordRating } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import { listenCountdownPref } from '$lib/stores/listenCountdownPref.svelte';
	import { masteryBackgroundColor, masteryColor } from '$lib/mastery';
	import Tooltip from '$lib/components/Tooltip.svelte';

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
	// Rows the user graded by hand. A confirmed grade is a review they
	// performed, so the backend APPLIES it instead of staging it into the
	// "Check your work" bucket — asking again would be asking twice. Everything
	// else is the listen's own assumption and keeps the safety net.
	//
	// Only a direct per-row grade click confirms. Grade All deliberately does
	// NOT: it is a bulk skip↔grade toggle, and letting one click commit reviews
	// for 136 rows — including well-known ones behind a collapsed disclosure —
	// would defeat the pending bucket entirely.
	let confirmed = new SvelteSet<string>();

	let countdown = $state(10);
	let countdownCancelled = $state(false);
	// The total the countdown started from — drives the progress fill's width.
	// 0 when the pref is "off" (never started), which keeps the fill empty.
	let countdownTotal = $state(0);
	// The timer handle. $state now, unlike the pre-option-C layout: the template
	// reads `countdownRunning`, which derives from countdownId, and a plain
	// `let` would render that derived once and never flip it — the running→idle
	// transition is exactly what the F-7 oracle asserts on. onDestroy
	// (clearCountdownTimer) still nulls it after teardown; Svelte 5 has no
	// post-unmount state-mutation warning.
	let countdownId: ReturnType<typeof setTimeout> | null = $state(null);
	// The countdown is "running" iff armed and not cancelled. The option-C
	// button derives everything from this: the fill drains, the tick shows,
	// `data-countdown` reads it, and the armed border/aria-label follow it.
	let countdownRunning = $derived(!countdownCancelled && countdownId !== null);
	// The seconds text. Empty when not running — the .tick box is held open by
	// its min-width (with `visibility: hidden`), so emptying the text cannot
	// change the button's width (F-7, one row up).
	let tickText = $derived(countdownRunning ? `${countdown}s` : '');
	// Progress fill, 0–100, driven by ELAPSED time — it grows left→right from
	// 0% while the countdown runs (F-15a). Gated on countdownRunning, not just
	// the counters: cancelCountdown resets neither countdown nor countdownTotal,
	// so a width derived only from those two would freeze mid-fill on cancel;
	// gated on the running flag it reads 0 the moment the countdown is
	// cancelled (F-15b). Driven by the existing 1-second interval, so it steps
	// rather than animates (no transition; reduced-motion users get the same
	// stepwise fill either way).
	let pctElapsed = $derived(
		countdownRunning && countdownTotal > 0
			? Math.max(0, Math.min(100, ((countdownTotal - countdown) / countdownTotal) * 100))
			: 0,
	);

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

	// The footer count is what a commit would actually create, so it must
	// exclude tail rows. Counting ratings ENTRIES rather than candidates does
	// that for free: an over-budget tail row carries no entry at all, so it can
	// never inflate the count.
	let selectedCount = $derived(Object.values(ratings).filter((r) => r !== 'skip').length);

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
			// Default: everything "good" — except DEFERRED rows (known or
			// learning), which start "skip" and collapsed inside their group.
			// One `deferred_reason` test rather than one per population: these
			// rows invert the meaning of absence from word_ratings, and the
			// inversion is stated once here and once in buildRatings. Adding a
			// third category must not add a third pair of branches.
			// Over-budget create
			// rows get NO entry at all: for a create, absent from word_ratings
			// means the backend defaults to "good" and CREATES the card, so
			// seeding a tail row would create a card the budget does not cover.
			const rts: Record<string, WordRating> = {};
			for (const c of candidates) {
				const key = candidateKey(c);
				if (c.deferred_reason) {
					rts[key] = 'skip';
				} else if (c.will_create === false) {
					// Tail rows (outside the shared introduction budget) get NO entry
					// at all — over-budget create rows AND NEW-state rows alike.
					continue;
				} else {
					rts[key] = 'good';
				}
			}
			ratings = rts;

			// Start countdown only when the pref is not "off".
			const prefValue = listenCountdownPref.value;
			if (prefValue !== 'off') {
				countdown = parseInt(prefValue, 10);
				countdownTotal = countdown;
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

	// The grade a row carried at the moment it was skipped. Skip All is a bulk
	// action, not a decision about any individual row, so it must not destroy
	// the grades the user set one at a time — Skip All → Grade All is a round
	// trip, and repeating it is stable. Deliberately NOT $state: it is only
	// ever read inside handlers, never from the template, so it needs no
	// reactivity (and a mutated const object raises no non_reactive_update
	// warning the way a reassigned let would).
	const rememberedGrade: Record<string, WordRating> = {};

	/** Stash a row's current grade before "skip" overwrites it. */
	function rememberGrade(key: string) {
		const current = ratings[key];
		if (current !== undefined && current !== 'skip') rememberedGrade[key] = current;
	}

	function setRating(key: string, rating: WordRating) {
		handleInteraction();
		if (rating === 'skip') rememberGrade(key);
		else confirmed.add(key);
		ratings = { ...ratings, [key]: rating };
	}

	/** True when a row still carries the grade the listen assumed for it. */
	function isAuto(key: string): boolean {
		return ratings[key] !== 'skip' && !confirmed.has(key);
	}

	function revealGloss(key: string) {
		handleInteraction();
		revealed.add(key);
	}

	// Restores each skipped row to the grade it last held, falling back to
	// "good" for a row that never carried one. Rows already on a real grade
	// are untouched. Iterates every candidate EXCEPT tail rows and well-known
	// rows: the known group is opt-in per row only, so a bulk action must
	// never pull a well-known row out of 'skip' (F-3), just as a display-only
	// tail row must not be pulled above the divider.
	function gradeAll() {
		handleInteraction();
		const rts: Record<string, WordRating> = { ...ratings };
		const tailKeys = new Set(tailCandidates.map((c) => candidateKey(c)));
		for (const c of candidates) {
			const key = candidateKey(c);
			if (tailKeys.has(key)) continue;
			// A bulk action is not an opt-in. A deferred row leaves its group
			// only by a per-row grade — that is what "opt-in" means, and it is
			// the same rule for known and learning rows.
			if (c.deferred_reason) continue;
			if (rts[key] === 'skip') rts[key] = rememberedGrade[key] ?? 'good';
		}
		ratings = rts;
	}

	// Bulk-skip the LIVE rows only. Tail rows are never written: they have no
	// ratings entry and must keep none. The live/tail split is static (the
	// server's `will_create` flag), so no reconciliation is needed here.
	function skipAll() {
		handleInteraction();
		const rts: Record<string, WordRating> = { ...ratings };
		for (const c of liveCandidates) {
			const key = candidateKey(c);
			rememberGrade(key);
			rts[key] = 'skip';
		}
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
		// A NEW-state card reads exactly like a create row: both are "not yet
		// introduced", and the difference (card already exists) is not something
		// the learner needs to see. Stated explicitly rather than leaning on the
		// `?? c.grade_class` fallback below, which would also produce "new" for
		// a null due_at but only by coincidence.
		if (c.kind === 'create' || c.grade_class === 'new') return 'new';
		// "learning", not "learn" — matches the lesson stats line's bucket names.
		if (c.grade_class === 'learning') return 'learning';
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
		// NEW-state rows join create rows in the unknown colour. They carry
		// `progress: null`, and the `?? 0` below would otherwise paint them the
		// red of 0% mastery — which reads as "you keep failing this" rather than
		// "you haven't started it".
		if (c.kind === 'create' || c.grade_class === 'new') {
			return `color: ${UNKNOWN_COLOR}; background: color-mix(in srgb, ${UNKNOWN_COLOR} 18%, transparent);`;
		}
		const p = c.progress ?? 0;
		return `color: ${masteryColor(p)}; background: ${masteryBackgroundColor(p)};`;
	}

	// The hue above is the ONLY mastery channel on the row, and a continuous
	// ramp is not readable to a number — 37% and 42% are the same colour to the
	// eye. The hover names it (F-4). Vocabulary mirrors WordSpan's
	// `masteryLabel` deliberately, including its well-known carve-out: the user
	// asked for "the exact redness like I have on the hover on the transcript",
	// so the two surfaces must not describe the same card with different words.
	function masteryLabel(c: ListenPreviewCandidate): string {
		if (c.kind === 'create') return 'not tracked';
		// Scheduled past the listen horizon. WordSpan suppresses the percentage
		// here because this flow has already stopped asking about the card;
		// quoting a work-in-progress number would contradict the row's own
		// "known" grouping in the disclosure below.
		if (c.deferred_reason === 'known') return 'known';
		// `progress: null` IS the server's "no mastery to report" signal — it
		// stamps null on NEW-state rows and a real float on every other tracked
		// row. Testing the null rather than `grade_class === 'new'` keeps this
		// total over the payload's own type instead of leaving a fallback arm
		// that the contract makes unreachable.
		if (c.progress == null) return 'not started';
		return `${Math.round(c.progress * 100)}%`;
	}

	// ── Over-budget creation tail ──────────────────────────────────────────
	// A skip no longer frees its slot for promotion (the server consumes the
	// slot instead — see mark_lesson_listened), so `will_create` is correct no
	// matter what the user checks. The live/tail split is therefore a STATIC
	// filter on the server's flag, not a re-derivation from current ratings.
	// Two populations overflow here now: create rows past the budget, and
	// NEW-state rows whose INTRODUCTION the budget cannot afford (releasing one
	// spends the same daily new-card allowance a creation does). `will_create`
	// is false on exactly those rows and defaults true everywhere else, so the
	// flag alone partitions them — no need to enumerate kinds.
	let tailCandidates = $derived(candidates.filter((c) => c.will_create === false));

	// How many tail rows the user has opted PAST the cap by grading them. A
	// skip is the undo, not an opt-in, so it does not count. The opted rows
	// move the summary's "now"/"later" counts and drive the over-cap caption
	// (selectedCount already counts opt-ins, because an opted-in tail row
	// carries a ratings entry).
	let optedTailCount = $derived(
		tailCandidates.filter((c) => {
			const rating = ratings[candidateKey(c)];
			return rating !== undefined && rating !== 'skip';
		}).length,
	);

	// The gradeable set: every tracked (word/kp) row that is not deferred and
	// not over budget, plus every create row the server will actually create.
	// Tail and deferred rows are rendered separately inside their disclosures.
	let liveCandidates = $derived([
		...candidates.filter((c) => c.kind !== 'create' && !c.deferred_reason && c.will_create !== false),
		...candidates.filter((c) => c.kind === 'create' && c.will_create !== false),
	]);

	// One collapsed group per deferred reason. They stay SEPARATE rather than
	// merging into one "deferred" group: "known" means the flow has stopped
	// asking, "learning" means it is asking on a schedule a listen must not
	// pre-empt. Same treatment, different reasons — and the user reads the
	// reason off the group's own summary line.
	let learningCandidates = $derived(candidates.filter((c) => c.deferred_reason === 'learning'));
	let wellKnownCandidates = $derived(candidates.filter((c) => c.deferred_reason === 'known'));

	// Builds the commit payload purely from local reads — never assigns into
	// the $state map (that was the F5 bug: mutating `ratings[c.text] = 'skip'`
	// during commit left a sticky "skip" behind a later re-grade). Only
	// non-default entries are sent: the backend defaults an absent entry to
	// "good", so an ordinary good row contributes nothing. Well-known rows are
	// an exception: the backend skips a well-known lemma absent from
	// word_ratings, so a graded well-known row MUST emit its explicit rating.
	// ⚠️ Polarity is INVERTED for the tail: a create row absent from
	// word_ratings is the backend's "good" → it creates the card. So an
	// untouched tail row must emit NOTHING — the opt-in is the only thing that
	// earns an entry, and it is carried by over_cap_words / over_cap_kps so the
	// backend knows it was a deliberate choice past the daily cap. A tail row
	// rated `skip` also emits nothing: it is the undo for a mis-tapped opt-in.
	function buildRatings(): {
		wordRatings: Record<string, WordRating>;
		kpRatings: Record<string, WordRating>;
		confirmedWords: string[];
		confirmedKps: string[];
		overCapWords: string[];
		overCapKps: string[];
	} {
		const wordRatings: Record<string, WordRating> = {};
		const kpRatings: Record<string, WordRating> = {};
		const confirmedWords: string[] = [];
		const confirmedKps: string[] = [];
		const overCapWords: string[] = [];
		const overCapKps: string[] = [];

		const tailKeys = new Set(tailCandidates.map((c) => candidateKey(c)));
		for (const c of candidates) {
			const key = candidateKey(c);
			if (tailKeys.has(key)) {
				// A tail row is opted in ONLY by a real grade. Untouched and
				// skip both emit nothing at all — exactly as if never touched,
				// which is what makes skip the route back to unset.
				const rating = ratings[key];
				if (rating !== undefined && rating !== 'skip') {
					if (c.kind === 'kp') {
						kpRatings[c.text] = rating;
						overCapKps.push(c.text);
					} else {
						wordRatings[c.text] = rating;
						overCapWords.push(c.text);
					}
				}
				continue;
			}
			const rating = ratings[key] ?? 'good';
			// Confirmation rides its own list rather than being inferred from
			// presence in the ratings map: a deferred row has to appear there
			// for the backend to consider it at all, so "present" cannot also
			// mean "reviewed". A skipped row is graded by nobody, so it is never
			// confirmed.
			if (rating !== 'skip' && confirmed.has(key)) {
				(c.kind === 'kp' ? confirmedKps : confirmedWords).push(c.text);
			}
			// ⚠️ The inverted-polarity edge, stated once. For a DEFERRED row
			// (known or learning) the backend skips anything absent from
			// word_ratings, so an opted-in "good" must be sent EXPLICITLY —
			// omitting it as "the default" would silently discard the grade.
			// For every other row "good" IS the default and sending it is
			// redundant. Keyed off deferred_reason so the two populations
			// cannot drift apart.
			const isDeferred = c.deferred_reason != null;
			const value: WordRating | null =
				rating === 'skip' ? 'skip' : isDeferred || rating !== 'good' ? rating : null;
			if (value === null) continue;
			if (c.kind === 'kp') {
				kpRatings[c.text] = value;
			} else {
				wordRatings[c.text] = value;
			}
		}

		return { wordRatings, kpRatings, confirmedWords, confirmedKps, overCapWords, overCapKps };
	}

	async function doCommit() {
		committing = true;
		error = '';

		const {
			wordRatings,
			kpRatings,
			confirmedWords,
			confirmedKps,
			overCapWords,
			overCapKps,
		} = buildRatings();

		try {
			const result = await listenedStore.markListened(
				lessonId,
				wordRatings,
				kpRatings,
				confirmedWords,
				confirmedKps,
				overCapWords,
				overCapKps,
			);
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
			<!-- F4: the countdown is visible whenever it's running — including the
			     zero-candidates case — so the auto-commit can never fire with no
			     on-screen indication anything is about to happen. Option C (user
			     choice) puts it ON the Grade All button: a draining fill plus a
			     seconds tick. F-7's constraint is satisfied structurally: the tick
			     stays mounted (its box held open by min-width, `visibility:
			     hidden` when not running) and the fill is absolutely positioned, so
			     the button's box is identical whether the countdown is running,
			     cancelled, or was never started — nothing can reflow under a
			     pointer that is still down. -->
			<div class="actions">
				<button
					class="grade-all"
					class:armed={countdownRunning}
					data-countdown={countdownRunning ? 'running' : 'idle'}
					data-countdown-pref={listenCountdownPref.value}
					aria-label={countdownRunning ? `Grade All — auto-grading in ${countdown} seconds` : undefined}
					onclick={gradeAll}
					type="button"
				>
					<span class="fill" style:width={`${pctElapsed}%`} aria-hidden="true"></span>
					<span class="label">Grade All <span class="tick">{tickText}</span></span>
				</button>
				<button onclick={skipAll} type="button">Skip All</button>
			</div>

			{#if candidates.length === 0}
				<p class="status">No new words to add.</p>
			{:else}

				<!-- One tag, both row kinds. It used to be written out twice, and the
				     two copies had already drifted (only the live one carried
				     `.overdue`) — harmlessly, since `isOverdue` is false for every
				     row the tail can hold, but that is luck, not a contract.
				     F-16's rule: the shared thing lives in one place. -->
				{#snippet dayTag(c: ListenPreviewCandidate)}
					<!-- The grid placement lives on this wrapper, NOT on `.tag.day`:
					     Tooltip's own `.tt-wrap` sits between them, so the tag is no
					     longer the grid item. `listen-preview-layout.spec.ts` measures
					     this cell's left edge against the header's to the pixel. -->
					<span class="day-cell">
						<Tooltip masteryLabel={masteryLabel(c)}>
							<span class="tag day" class:overdue={isOverdue(c)} style={dueStyle(c)}>
								{dueLabel(c)}
							</span>
						</Tooltip>
					</span>
				{/snippet}

				{#snippet gradeControl(c: ListenPreviewCandidate)}
					{@const key = candidateKey(c)}
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
								{@const label = g[0].toUpperCase() + g.slice(1)}
								{@const auto = ratings[key] === g && isAuto(key)}
								<button
									class={g}
									class:active={ratings[key] === g}
									class:auto
									data-candidate={key}
									data-grade={g}
									aria-pressed={ratings[key] === g}
									aria-label={auto
										? `${label} for ${c.text} — auto-graded, tap to confirm`
										: `${label} for ${c.text}`}
									onclick={() => setRating(key, g)}
									type="button"
								>{label}</button>
							{/each}
						</div>
					</div>
				{/snippet}

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

						{@render dayTag(c)}

						{@render gradeControl(c)}
					</li>
				{/snippet}

				{#snippet tailRow(c: ListenPreviewCandidate)}
					{@const key = candidateKey(c)}
					{@const opted = ratings[key] !== undefined && ratings[key] !== 'skip'}
					<!-- Held back by the budget, not by a choice — but gradeable now.
					     The control is the opt-in affordance: it starts with NO rating
					     set (the preview loader never seeds tail rows), and explicitly
					     grading the row carries it past the daily new-card cap. The tag
					     keeps the row's status on its own face. `.tail` stays on an
					     opted-in row — it is partition identity, and removing it would
					     re-create promote-on-uncheck through the back door — so
					     `.tail:not(.opted)` is what carries the dimming. -->
					<li class="candidate tail" class:opted={opted}>
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

						{@render dayTag(c)}

						{@render gradeControl(c)}
					</li>
				{/snippet}

				<!-- F-20 (revised 2026-08-09, user request): a three-count summary
				     above the list (replacing the <li class="cut-line"> that used to
				     render after every row it explained). `now` is everything that
				     will actually be graded/created when this listen is committed —
				     liveCandidates (every rendered, gradeable row) plus whatever tail
				     rows were opted PAST the cap. `known` and `later` stay separate:
				     well-known rows are not graded by default, and an un-opted tail
				     row is explicitly NOT happening now — that is what makes it
				     "later". The three counts sum to the row count exactly when there
				     is no opted-tail overlap accounting to do (now + later + known ==
				     total), unlike the original narrower "introduction budget only"
				     definition this replaced. -->
				<div class="partition">
					<span class="seg now"><span class="n">{liveCandidates.length + optedTailCount}</span><span class="l">now</span></span>
					<span class="seg"><span class="n">{tailCandidates.length - optedTailCount}</span><span class="l">later</span></span>
					<!-- Renders only when it has content, so a deck with nothing
					     mid-acquisition keeps the three-count line F-20 shipped.
					     Without it the counts would stop summing to the row count
					     the moment a learning card appeared — the partition's whole
					     claim is that every row is in exactly one of these buckets. -->
					{#if learningCandidates.length > 0}
						<span class="seg"><span class="n">{learningCandidates.length}</span><span class="l">learning</span></span>
					{/if}
					<span class="seg"><span class="n">{wellKnownCandidates.length}</span><span class="l">known</span></span>
				</div>
				{#if optedTailCount > 0}
					<!-- The over-cap opt-in keeps its voice: opted rows ARE being
					     introduced now, so the counts above moved with them, and the
					     caption says the overage out loud rather than quietly moving
					     the denominator. Renders ONLY when something was opted in. -->
					<p class="over-cap-caption">+{optedTailCount} past today's limit</p>
				{/if}

				<div class="list-head" aria-hidden="true">
					<span>Word</span><span>Due</span><span>Proposed grade</span>
				</div>

				<ul class="list">
					{#each liveCandidates as c (candidateKey(c))}
						{@render candidateRow(c)}
					{/each}
				</ul>

				{#if tailCandidates.length > 0}
					<!-- F-13: the tail collapses behind a disclosure mirroring the
					     well-known group — one idiom for the modal, and one tap to
					     reach the whole partition instead of a tag repeated on every
					     row. The count is tailCandidates.length: the tail holds create
					     rows AND NEW-state cards sharing the intro budget, so a create
					     count would under-report it. Tail rows stay gradeable inside
					     the group (the over-cap opt-in is per row and unchanged). -->
					<details class="tail-group disclosure-group">
						<summary>{tailCandidates.length} words for subsequent listens</summary>
						<ul class="list">
							{#each tailCandidates as c (candidateKey(c))}
								{@render tailRow(c)}
							{/each}
						</ul>
					</details>
				{/if}

				{#if learningCandidates.length > 0}
					<!-- F-5: mid-acquisition cards get the known group's treatment,
					     not the known group itself. `hage` was introduced at 10:09,
					     due at 10:20, and a listen rated it "good" in between — the
					     learning step exists to test recall at a specific interval
					     and a listen is not that test. Visible, skipped by default,
					     opt-in per row. -->
					<details class="learning-group disclosure-group">
						<summary>{learningCandidates.length} learning word{learningCandidates.length !== 1 ? 's' : ''}</summary>
						<ul class="list">
							{#each learningCandidates as c (candidateKey(c))}
								{@render candidateRow(c)}
							{/each}
						</ul>
					</details>
				{/if}

				{#if wellKnownCandidates.length > 0}
					<details class="well-known-group disclosure-group">
						<!-- "known", matching the lesson stats line's bucket. The API field
					     is still `well_known`; only the label changed. -->
					<summary>{wellKnownCandidates.length} known word{wellKnownCandidates.length !== 1 ? 's' : ''}</summary>
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
	/* box-sizing is explicit because this app has NO global border-box reset.
	   Under the default content-box, `width: 90%` + `max-width: 420px` sized the
	   CONTENT box, so the real border-box was 50px wider (48 padding + 2 border);
	   it exceeded the viewport at every phone width, flexbox shrank it to exactly
	   the viewport, and the modal rendered edge-to-edge with zero gutters — and
	   430px wide on a 430px screen, past its own max-width.

	   470px = the old 420 content + 48 + 2, so desktop keeps the box it always
	   had; only the phone arm moves. `min()` folds width and max-width into one
	   declaration so the two can no longer disagree about which box they mean. */
	.modal {
		box-sizing: border-box;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-md);
		padding: 1.5rem;
		width: min(94%, 470px);
		max-height: 80vh;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		/* Makes the row layout answer to the space the modal actually has, not to
		   the viewport — and, as a side effect, removes the flex `min-width: auto`
		   escape hatch that let content-based minimums push the box past 94%. */
		container-type: inline-size;
	}
	/* Phone: buy the gutters back out of the horizontal padding instead of out of
	   the rows. At 390px this leaves the row content 340.6px — within a pixel of
	   the 340px it had when the modal was edge-to-edge. */
	@media (max-width: 430px) {
		.modal {
			padding: 1.25rem 0.75rem;
		}
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
	/* Option C: the countdown lives ON the Grade All button. The tick box is
	   always mounted and held open by min-width (empty → `visibility: hidden`,
	   never `display: none`), so the button's box is pixel-identical whether
	   the countdown is running, cancelled, or never armed — F-7's invariant.
	   The fill is absolutely positioned behind the label so it animates the
	   progress without ever moving text. */
	.grade-all {
		position: relative;
		overflow: hidden;
		font-weight: 600;
	}
	.grade-all .fill {
		position: absolute;
		inset: 0 auto 0 0;
		background: color-mix(in srgb, var(--color-accent, #2f6fed) 18%, transparent);
		pointer-events: none;
	}
	.grade-all .label {
		position: relative;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 0.25rem;
	}
	.grade-all .tick {
		/* Sized for the largest tick the pref can produce — "60s" at pref "60",
		   the widest of the 10/30/60 the pref offers. Holding the box open to
		   that width keeps the label rock-steady while it drains and across
		   cancellation (F-7); the stale "one full 10s is the widest" premise
		   hid the 30s/60s overflow for two releases. */
		min-width: 2.2em;
		text-align: left;
		font-variant-numeric: tabular-nums;
		visibility: hidden;
	}
	.grade-all[data-countdown="running"] .tick {
		visibility: visible;
	}
	/* Pref off → the countdown can never run, so the reserved box is dead
	   weight that pushes the visible words permanently off-centre. Dropped via
	   display:none gated on the PREF, never on countdownRunning: the pref
	   cannot change while the modal is open, so the box is stable for the
	   modal's entire lifetime and nothing reflows under a pointer still down
	   (F-7). */
	.grade-all[data-countdown-pref="off"] .tick {
		display: none;
	}
	.grade-all.armed {
		border-color: var(--color-accent, #2f6fed);
		box-shadow: 0 0 0 1px var(--color-accent, #2f6fed);
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
		/* The Due track is 3.5rem = 56px against a 51.6px `learning` pill — the
		   widest label this column can emit — leaving ~4.4px. It was 3.25rem
		   (52px), which passed on 0.4px of headroom: green, oracle-guarded, and
		   one font substitution away from failing (a fallback face on a device
		   without ours, or a browser default bump). The track is deliberately
		   FIXED rather than `auto` — an `auto` track resolves per-container and
		   desynchronises the header from the rows, which is the bug
		   `listen-preview-layout.spec.ts` exists for — so the fix is to size it
		   for its content, never to make it elastic. Keep both arms in step:
		   the container-query arm below carries the same value. */
		grid-template-columns: minmax(0, 1fr) 3.5rem 11rem;
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
	/* The Due cell. Since F-4 the tag hangs inside a Tooltip, so the grid item
	   is this wrapper and the tag is two levels down — the placement has to
	   live here or the tag falls back to auto-placement and lands in the wrong
	   column. `justify-self: start` still sizes the cell to the tag, so the
	   tag's own left edge is unchanged (what the layout spec measures). */
	.day-cell {
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
	/* An auto-graded row is the listen's assumption, not the user's review, and
	   it goes to the pending bucket rather than being applied. It reads as
	   provisional by reusing the idiom Skip already established in this
	   control: dashed outline = "not your choice". There is no room for the
	   words "auto good" at this track width, and none is needed — the column
	   header says "Proposed grade", so dashed is proposed and solid is
	   confirmed. Tapping it makes it solid, and commits it. */
	.grades button.active.auto {
		background: color-mix(in srgb, var(--color-success) 30%, transparent);
		color: var(--color-text);
		border: 1px dashed color-mix(in srgb, var(--color-success) 70%, transparent);
	}
	.grades button.again.active.auto {
		background: color-mix(in srgb, var(--color-danger) 30%, transparent);
		border-color: color-mix(in srgb, var(--color-danger) 70%, transparent);
	}
	.grades button.hard.active.auto {
		background: color-mix(in srgb, var(--color-warning) 30%, transparent);
		border-color: color-mix(in srgb, var(--color-warning) 70%, transparent);
	}
	.grades button.easy.active.auto {
		background: color-mix(in srgb, var(--color-primary) 30%, transparent);
		border-color: color-mix(in srgb, var(--color-primary) 70%, transparent);
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
	/* F-16: BOTH disclosures (the tail group and the well-known group) carry
	   this ONE shared class. They are the same idiom — a partition that
	   collapses behind a counting disclosure — and one shared rule set is what
	   keeps them from drifting into different treatments again. This is
	   deliberate where the original defect was an omission: the tail group
	   mirrored the well-known group in markup, but never received the styling,
	   so it rendered as a default <details>. */
	.disclosure-group {
		border-top: 1px solid var(--color-border);
		padding-top: 0.5rem;
		margin-top: 0.25rem;
	}
	.disclosure-group summary {
		font-size: 0.8rem;
		color: var(--color-muted);
		cursor: pointer;
		padding: 0.25rem 0;
	}
	/* F-20: the three-count summary that replaced the cut line — the loudest
	   thing on the sheet, so the number reads and the label recedes. */
	.partition {
		display: flex;
		gap: 1rem;
		padding: 0.25rem 0 0.4rem;
	}
	.seg {
		display: inline-flex;
		align-items: baseline;
		gap: 0.3rem;
	}
	.seg .n {
		font-size: 0.9rem;
		font-weight: 600;
		line-height: 1;
	}
	.seg .l {
		font-size: 0.7rem;
		color: var(--color-muted);
	}
	.over-cap-caption {
		margin: 0 0 0.35rem;
		font-size: 0.7rem;
		color: var(--color-warning);
	}
	/* Held back by the budget, not by a choice: dimmed and non-interactive —
	   until the user opts one in, when `.tail.opted` lifts the dim. Never
	   collapsed. Cursor stays default so the row does not invite a click it
	   cannot honour — the gloss inside is the one exception, and sets its own
	   pointer. `.tail` stays on an opted-in row (partition identity); only the
	   dimming moves to the `:not(.opted)` form. */
	.candidate.tail:not(.opted) {
		opacity: 0.62;
		cursor: default;
	}
	.candidate.tail:not(.opted):hover {
		background: transparent;
	}
	/* The tail's over-cap opt-in control sits in the grade column's own grid
	   row so it never collides with the fixed grade control. */
	.candidate.tail .grade {
		grid-row: 2;
	}

	/* Below the width where `1fr 3rem 11rem` stops leaving the word a readable
	   column, the grade control drops to its own full-width line instead of the
	   tracks squeezing until the modal outgrows the screen. 18rem, not 288px:
	   the two fixed tracks are in `rem`, so an Android font-size setting scales
	   the layout's hard minimum and the trigger has to scale with it (at 320px
	   this restacks at a 17px root; the three-column form survives to 360px at
	   the default 16). Header and rows are re-tracked together — they are
	   separate grid containers and the pixel-alignment invariant documented
	   above depends on their track lists staying identical.

	   LAST in the sheet on purpose: a container query adds no specificity, so
	   `.grade { grid-column: 3 }` above would otherwise win on source order. */
	@container (max-width: 18rem) {
		.list-head,
		.candidate {
			/* Same 3.5rem as the three-column arm above — the pill is the same
			   width whichever arm applies. */
			grid-template-columns: minmax(0, 1fr) 3.5rem;
		}
		/* Nothing occupies a third column any more, so the header must stop
		   advertising one. */
		.list-head span:last-child {
			display: none;
		}
		.grade {
			grid-column: 1 / -1;
			grid-row: 3;
		}
		.candidate.tail .grade {
			grid-row: 3;
		}
	}
</style>
