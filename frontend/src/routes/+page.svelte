<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import { languageStore } from '$lib/stores/language.svelte';

	// Tagline names the active L2 (falls back to a generic line before the language
	// list has loaded, or in a single-language deployment that hasn't resolved yet).
	const tagline = $derived(
		languageStore.name
			? `AI-powered ${languageStore.name}, tuned to what you know.`
			: 'AI-powered language learning, tuned to what you know.'
	);

	interface CardProgress {
		listenedCount: number;
		totalDays: number;
		percent: number;
		allListened: boolean;
		continueLabel: string;
		continueHref: string;
	}

	let curricula: Array<{ id: string; topic: string; created_at: string }> = $state([]);
	let listLoading = $state(true);
	let listError = $state('');
	let showForm = $state(false);

	// ── Review sessions ────────────────────────────────────────────────────
	// Their own dated list, under the curricula. NOT a curriculum day: no theme,
	// no position in a sequence, content drawn from the whole language deck.
	interface ReviewSession {
		id: string;
		session_date: string;
		title: string;
		review_requested: string[] | null;
		review_used: string[] | null;
	}
	let sessions: ReviewSession[] = $state([]);
	let creatingSession = $state(false);
	let sessionError = $state('');
	// Held apart from sessionError on purpose: a 409 is not a failure. With
	// nothing due there is genuinely nothing to review today, and styling that as
	// an error trains the learner to read a working feature as broken.
	let nothingDue = $state('');

	// C3: raw day-lists fetched once per curriculum; progress is derived from
	// listenedStore so it reacts to late hydration / in-session markListened.
	let daysById: Record<string, Array<{ day: number; position: number; lesson_id: string }>> =
		$state({});
	let progressById: Record<string, CardProgress> = $derived.by(() => {
		const next: Record<string, CardProgress> = {};
		for (const [id, days] of Object.entries(daysById)) {
			const progress = computeProgress(id, days);
			if (progress) next[id] = progress;
		}
		return next;
	});

	// Two-click delete, one card at a time (same pattern as the lesson page's
	// "Delete day"): the first click arms that card, the second deletes. Blur
	// disarms, so a click elsewhere can't leave a primed button behind.
	let confirmingDeleteId: string | null = $state(null);
	let deletingId: string | null = $state(null);
	let deleteError = $state('');

	async function handleDelete(id: string) {
		confirmingDeleteId = null;
		deletingId = id;
		deleteError = '';
		try {
			await api.deleteCurriculum(id);
			curricula = curricula.filter((c) => c.id !== id);
		} catch (e) {
			deleteError = e instanceof Error ? e.message : String(e);
		} finally {
			deletingId = null;
		}
	}

	function handleDeleteClick(id: string) {
		if (confirmingDeleteId === id) {
			handleDelete(id);
		} else {
			confirmingDeleteId = id;
		}
	}

	// Mini-form for starting a new plan (chat-based; replaces one-shot generation)
	let planTopic = $state('');
	let planCefr = $state('A2');
	let planStarting = $state(false);
	let planError = $state('');

	async function handleStartPlan() {
		planStarting = true;
		planError = '';
		try {
			const created = await api.startPlan(planTopic.trim(), planCefr);
			curricula = [
				{ id: created.id, topic: created.topic, created_at: new Date().toISOString() },
				...curricula
			];
			await goto(`/c/${created.id}/plan`);
		} catch (e) {
			planError = e instanceof Error ? e.message : String(e);
		} finally {
			planStarting = false;
		}
	}

	onMount(async () => {
		try {
			curricula = await api.listCurricula();
		} catch (e) {
			listError = e instanceof Error ? e.message : String(e);
		} finally {
			listLoading = false;
		}

		try {
			sessions = await api.listReviewSessions();
		} catch {
			// A failed session list must not take the curricula down with it —
			// they are independent surfaces that happen to share a page.
			sessions = [];
		}

		const entries = await Promise.all(
			curricula.map(async (c) => {
				try {
					const days = await api.getCurriculumProgress(c.id);
					return [c.id, days] as const;
				} catch {
					return [c.id, null] as const;
				}
			})
		);
		const next: Record<string, Array<{ day: number; position: number; lesson_id: string }>> = {};
		for (const [id, days] of entries) {
			if (days) next[id] = days;
		}
		daysById = next;
	});

	const MONTHS = [
		'January',
		'February',
		'March',
		'April',
		'May',
		'June',
		'July',
		'August',
		'September',
		'October',
		'November',
		'December'
	];

	/**
	 * "2 September" from "2026-09-02", without going through Date.
	 *
	 * ⚠️ `new Date('2026-09-02')` is parsed as UTC MIDNIGHT, which renders as
	 * 1 September in every negative-offset timezone — a wrong date for half the
	 * world, and a bug that passes every test run in London. The value is a
	 * calendar date, not an instant, so it is formatted as one.
	 */
	function formatSessionDate(iso: string): string {
		const [, month, day] = iso.split('-').map(Number);
		return `${day} ${MONTHS[month - 1]}`;
	}

	function coverageLine(s: ReviewSession): string | null {
		// null is "never measured" and gets NO line. [] is a measured zero and
		// gets one — "reused 0 of 5" is a real observation.
		if (s.review_requested === null || s.review_used === null) return null;
		return `reused ${s.review_used.length} of ${s.review_requested.length}`;
	}

	async function handleNewReviewSession() {
		creatingSession = true;
		sessionError = '';
		nothingDue = '';
		try {
			const created = await api.createReviewSession();
			sessions = [
				{
					id: created.id,
					session_date: created.session_date,
					title: created.title,
					review_requested: created.review_requested,
					review_used: created.review_used
				},
				...sessions
			];
		} catch (e) {
			const status = (e as Error & { status?: number }).status;
			if (status === 409) {
				nothingDue = 'Nothing to review right now — no vocabulary is due in this language today.';
			} else {
				sessionError = e instanceof Error ? e.message : String(e);
			}
		} finally {
			creatingSession = false;
		}
	}

	function computeProgress(
		curriculumId: string,
		days: Array<{ day: number; position: number; lesson_id: string }>
	): CardProgress | null {
		if (days.length === 0) return null;

		const sorted = [...days].sort((a, b) => a.day - b.day);
		const totalDays = sorted.length;
		const listenedCount = sorted.filter((d) => listenedStore.has(d.lesson_id)).length;
		const percent = Math.round((listenedCount / totalDays) * 100);
		const firstUnlistened = sorted.find((d) => !listenedStore.has(d.lesson_id));
		const allListened = !firstUnlistened;
		const target = firstUnlistened ?? sorted[sorted.length - 1];
		const continueLabel = allListened
			? `Revisit Day ${target.position}`
			: `Continue → Day ${target.position}`;
		const continueHref = `/c/${curriculumId}/l/${target.lesson_id}`;

		return { listenedCount, totalDays, percent, allListened, continueLabel, continueHref };
	}

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		});
	}
</script>

<main>
	<header class="page-head">
		<div>
			<h1>Lessons</h1>
			<p class="tagline">{tagline}</p>
		</div>
		<button class="new-btn" onclick={() => (showForm = !showForm)} aria-expanded={showForm}>
			{showForm ? 'Cancel' : '+ New curriculum'}
		</button>
	</header>

	{#if showForm}
		<section class="plan-form card">
			<h2>Plan a curriculum</h2>
			<label>
				Topic
				<input bind:value={planTopic} placeholder="e.g. ordering coffee in Ljubljana" />
			</label>
			<label>
				CEFR Level
				<select bind:value={planCefr}>
					<option>A1</option>
					<option>A2</option>
					<option>B1</option>
					<option>B2</option>
				</select>
			</label>
			<button
				class="start-btn"
				onclick={handleStartPlan}
				disabled={planStarting || !planTopic.trim()}
			>
				{planStarting ? 'Starting…' : 'Start planning'}
			</button>
			{#if planError}
				<p class="error">{planError}</p>
			{/if}
		</section>
	{/if}

	{#if listLoading}
		<p class="muted">Loading…</p>
	{:else if listError}
		<p class="error">{listError}</p>
	{:else if curricula.length === 0}
		<div class="empty card">
			<p class="muted">No curricula yet.</p>
			<p class="muted small">Use “+ New curriculum” above to generate your first one.</p>
		</div>
	{:else}
		<ul class="library">
			{#each curricula as c (c.id)}
				<li>
					<div class="curric-card card">
						<a class="card-link" href="/c/{c.id}">
							<span class="topic">{c.topic}</span>
							<span class="meta">{formatDate(c.created_at)}</span>
						</a>
						{#if progressById[c.id]}
							{@const p = progressById[c.id]}
							<div class="progress-info">
								<p class="progress-line">{p.listenedCount} of {p.totalDays} days listened</p>
								<div class="progress-bar">
									<div class="progress-fill" style="width: {p.percent}%"></div>
								</div>
								{#if p.allListened}
									<p class="all-done">All {p.totalDays} days listened ✓</p>
								{/if}
								<a class="continue-link" href={p.continueHref}>{p.continueLabel}</a>
							</div>
						{/if}
						<button
							type="button"
							class="delete-btn"
							class:confirming={confirmingDeleteId === c.id}
							aria-label={confirmingDeleteId === c.id
								? `Confirm delete ${c.topic}`
								: `Delete ${c.topic}`}
							onclick={() => handleDeleteClick(c.id)}
							onblur={() => (confirmingDeleteId = null)}
							disabled={deletingId === c.id}
						>
							{confirmingDeleteId === c.id ? 'Confirm delete' : 'Delete'}
						</button>
					</div>
				</li>
			{/each}
		</ul>
		{#if deleteError}
			<p class="error delete-error">{deleteError}</p>
		{/if}
	{/if}

	<!--
		Review sessions live UNDER the curricula and outside them. Dated, never
		numbered — a session has no position in a sequence to number (tunatale-9p9d).
	-->
	<section class="review-sessions">
		<div class="rs-head">
			<h2>Review sessions</h2>
			<button
				type="button"
				class="new-btn"
				onclick={handleNewReviewSession}
				disabled={creatingSession}
			>
				{creatingSession ? 'Working…' : '+ New review session'}
			</button>
		</div>
		<p class="muted small rs-blurb">
			Built from the words you are closest to forgetting, across everything you have learned —
			not from any one curriculum.
		</p>

		{#if nothingDue}
			<p class="muted rs-nothing-due">{nothingDue}</p>
		{/if}
		{#if sessionError}
			<p class="error" role="alert">{sessionError}</p>
		{/if}

		{#if sessions.length === 0}
			<p class="muted small">No review sessions yet.</p>
		{:else}
			<!--
				The SAME card shape the curricula above use — .library / .curric-card /
				.card-link / .topic / .meta. A session is a different KIND of thing, not
				a different kind of list item, and giving it bespoke markup made one page
				look like two.
			-->
			<ul class="library">
				{#each sessions as s (s.id)}
					{@const line = coverageLine(s)}
					<li data-testid="review-session-row">
						<div class="curric-card card">
							<a class="card-link" href="/review-sessions/{s.id}">
								<span class="topic">{s.title}</span>
								<span class="meta">{formatSessionDate(s.session_date)}</span>
							</a>
							{#if line}
								<p class="progress-line">{line} words you were forgetting</p>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
</main>

<style>
	.review-sessions {
		margin-top: 2.5rem;
		padding-top: 1.5rem;
		border-top: 1px solid var(--border, #ddd);
	}
	.rs-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
		flex-wrap: wrap;
	}
	.rs-head h2 {
		margin: 0;
		font-size: 1.15rem;
	}
	.rs-blurb {
		margin: 0.35rem 0 1rem;
		max-width: 46ch;
	}
	.rs-nothing-due {
		margin: 0 0 0.75rem;
	}

	main {
		max-width: 760px;
		margin: 1rem auto;
		padding: 0 1rem;
	}
	.page-head {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.75rem;
		margin-bottom: 1.25rem;
	}
	h1 {
		margin: 0;
		font-size: 1.9rem;
		font-weight: 800;
		letter-spacing: -0.02em;
	}
	.tagline {
		color: var(--color-muted);
		margin: 0.25rem 0 0;
		font-size: 0.95rem;
	}
	.new-btn {
		flex-shrink: 0;
		align-self: flex-start;
		padding: 0.55rem 1rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		font-size: 0.9rem;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease, transform 0.1s ease;
	}
	.new-btn:hover {
		background: var(--color-primary-hover);
	}
	.new-btn:active {
		transform: translateY(1px);
	}
	.library {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.75rem;
	}
	.curric-card {
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		padding: 1rem 1.25rem;
		transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.1s ease;
	}
	.curric-card:hover {
		border-color: var(--color-primary);
		box-shadow: var(--shadow);
		transform: translateY(-1px);
	}
	.card-link {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		text-decoration: none;
		color: var(--color-text);
	}
	.topic {
		font-size: 1.05rem;
		font-weight: 600;
	}
	.meta {
		color: var(--color-muted);
		font-size: 0.8rem;
		flex-shrink: 0;
	}
	.progress-info {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.progress-line {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.progress-bar {
		height: 6px;
		border-radius: var(--radius-pill);
		background: var(--color-surface-2);
		overflow: hidden;
	}
	.progress-fill {
		height: 100%;
		border-radius: var(--radius-pill);
		background: var(--color-primary);
	}
	.all-done {
		margin: 0;
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--color-success);
	}
	.continue-link {
		align-self: flex-start;
		padding: 0.4rem 0.9rem;
		border-radius: var(--radius-pill);
		background: var(--color-surface-2);
		color: var(--color-primary);
		font-size: 0.8rem;
		font-weight: 600;
		text-decoration: none;
		transition: background 0.15s ease, color 0.15s ease;
	}
	.continue-link:hover {
		background: var(--color-primary);
		color: var(--color-on-primary);
	}
	.delete-btn {
		align-self: flex-start;
		flex-shrink: 0;
		padding: 0.4rem 0.9rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-muted);
		font-size: 0.8rem;
		font-weight: 600;
		cursor: pointer;
	}
	.delete-btn:hover {
		color: var(--color-danger);
		border-color: var(--color-danger);
	}
	.delete-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.delete-btn.confirming {
		border-color: var(--color-danger);
		color: var(--color-danger);
	}
	.delete-error {
		margin-top: 0.75rem;
	}
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.75rem;
		text-align: center;
		padding: 2.5rem 1.25rem;
	}
	.empty .muted {
		margin: 0;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.95rem;
	}
	.muted.small {
		font-size: 0.85rem;
	}
	.error {
		color: var(--color-danger);
		margin: 0;
	}
	.plan-form {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		padding: 1.25rem;
		margin-bottom: 1.25rem;
	}
	.plan-form h2 {
		margin: 0;
		font-size: 1.2rem;
		font-weight: 700;
	}
	.plan-form label {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.plan-form input,
	.plan-form select {
		padding: 0.5rem 0.65rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		font: inherit;
		font-size: 0.92rem;
		background: var(--color-surface);
		color: var(--color-text);
	}
	.start-btn {
		align-self: flex-start;
		padding: 0.55rem 1.1rem;
		border: none;
		border-radius: var(--radius-pill);
		background: var(--color-primary);
		color: var(--color-on-primary);
		font-size: 0.9rem;
		font-weight: 600;
		cursor: pointer;
	}
	.start-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	@media (min-width: 641px) {
		main {
			margin: 2rem auto;
		}
		.page-head {
			flex-direction: row;
			justify-content: space-between;
			gap: 1rem;
		}
		.curric-card {
			flex-direction: row;
			align-items: center;
			justify-content: space-between;
			gap: 1.5rem;
		}
		.card-link {
			flex: 1 1 auto;
			flex-direction: row;
			align-items: baseline;
			justify-content: space-between;
			gap: 1rem;
		}
		.progress-info {
			flex: 0 0 auto;
			width: 14rem;
		}
	}
</style>
