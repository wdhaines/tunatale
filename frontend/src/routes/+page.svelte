<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import CardList from '$lib/components/CardList.svelte';
	import type { CardRow } from '$lib/components/CardList.svelte';
	import ReviewSessionCards from '$lib/components/ReviewSessionCards.svelte';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import { languageStore } from '$lib/stores/language.svelte';
	import { pinReviewWords, pinnedReviewWords, unpinReviewWords } from '$lib/reviewDraft';
	import ManualStoryPanel from '$lib/components/ManualStoryPanel.svelte';
	import { t } from '$lib/i18n/i18n.svelte';
	import { orderSessions } from '$lib/reading/nextReviewSession';
	import { leadCurriculum, recentLessons } from '$lib/reading/recentLessons';

	// How many recent lessons and sessions home lists before "All …" takes over.
	const RECENT_COUNT = 3;

	// Tagline names the active L2 (falls back to a generic line before the language
	// list has loaded, or in a single-language deployment that hasn't resolved yet).
	const tagline = $derived(
		languageStore.name
			? t('home.taglineNamed', { name: languageStore.name })
			: t('home.taglineGeneric')
	);

	interface CardProgress {
		listenedCount: number;
		totalDays: number;
		percent: number;
		allListened: boolean;
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
	// Warnings the auto-generate route returns in its 201 body (unmapped speaker
	// voice, no hover glosses, …). Same shape and presentation as the paste
	// paths' importWarnings — this used to be dropped at the call site.
	let sessionWarnings: string[] = $state([]);

	// C3: raw day-lists fetched once per curriculum; progress is derived from
	// listenedStore so it reacts to late hydration / in-session markListened.
	let daysById: Record<string, Array<{ day: number; position: number; lesson_id: string }>> =
		$state({});
	let progressById: Record<string, CardProgress> = $derived.by(() => {
		const next: Record<string, CardProgress> = {};
		for (const [id, days] of Object.entries(daysById)) {
			const progress = computeProgress(days);
			if (progress) next[id] = progress;
		}
		return next;
	});

	// Lesson titles come from each curriculum's own GET (bd tunatale-e6fq):
	// keyed by the day key so a deleted day leaves the titles beside it intact.
	let titlesById: Record<string, Record<number, string>> = $state({});

	// The ONE curriculum home leads with: the one listened to most recently,
	// else the first. Null only when there is no curriculum at all, which is
	// exactly the empty state.
	const lead = $derived(
		leadCurriculum(curricula, daysById, (id) => listenedStore.lastListenedAt(id))
	);
	// Every other curriculum drops to a one-line list below the lead. Identity,
	// not `lead?.id`: when there is no lead the list is empty anyway.
	const others = $derived(curricula.filter((c) => c !== lead));

	// The three newest sessions, in the ONE order the index uses, newest first.
	const recentSessions = $derived(orderSessions(sessions).reverse().slice(0, RECENT_COUNT));

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

		// Titles come from each curriculum's own GET. `overview` carries the day
		// list with its titles; keyed by day so a deleted day leaves the titles
		// beside it intact. A failure leaves this curriculum title-less, and the
		// rows fall back to "Day N" — a row still has to be reachable.
		const titleEntries = await Promise.all(
			curricula.map(async (c) => {
				try {
					const summary = await api.getCurriculum(c.id);
					return [c.id, summary.days] as const;
				} catch {
					return [c.id, null] as const;
				}
			})
		);
		const titles: Record<string, Record<number, string>> = {};
		for (const [id, days] of titleEntries) {
			if (days) titles[id] = Object.fromEntries(days.map((d) => [d.day, d.title]));
		}
		titlesById = titles;
	});

	/**
	 * Same contract as ManualStoryPanel's importKey (bd tunatale-rwkz.1): minted
	 * when a generation starts, kept while it keeps failing so a retry joins the
	 * first attempt, dropped on success so the next tap is a new session.
	 *
	 * It matters MORE here than on the paste path: a lost response on this route
	 * means the retry burns a second story call, the most expensive thing TT
	 * does, for a session nobody asked for.
	 */
	let sessionKey: string | null = null;

	async function handleNewReviewSession() {
		creatingSession = true;
		sessionKey ??= crypto.randomUUID();
		sessionError = '';
		nothingDue = '';
		sessionWarnings = [];
		try {
			const created = await api.createReviewSession(sessionKey);
			// Only a success ends the intent; a failure keeps the key so the
			// retry is recognisably the same attempt.
			sessionKey = null;
			sessionWarnings = created.warnings;
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
				nothingDue = t('home.nothingDue');
			} else {
				sessionError = e instanceof Error ? e.message : String(e);
			}
		} finally {
			creatingSession = false;
		}
	}

	// Manual mode. The words are pinned, not just displayed: nothing server-side
	// remembers the draft prompt, so the import has to be told them — and pinned
	// outside this component, because the learner leaves the page to write the
	// story and comes back to a fresh mount ($lib/reviewDraft).
	async function copyDraftPrompt() {
		const r = await api.getReviewSessionDraftPrompt();
		pinReviewWords(r.review_words);
		return r.system_prompt + '\n\n' + r.user_prompt;
	}

	async function importDraft(raw: string, idempotencyKey: string) {
		const words = pinnedReviewWords();
		// Said here rather than sent: the server can only answer an empty list
		// with a validator message about a field the learner never typed.
		if (words.length === 0) throw new Error(t('home.copyPromptFirst'));
		const created = await api.createReviewSessionFromPaste(raw, words, idempotencyKey);
		// Only a success unpins, so a retry of a failed import still has them.
		unpinReviewWords();
		return created;
	}

	function computeProgress(
		days: Array<{ day: number; position: number; lesson_id: string }>
	): CardProgress | null {
		if (days.length === 0) return null;

		const totalDays = days.length;
		const listenedCount = days.filter((d) => listenedStore.has(d.lesson_id)).length;
		const percent = Math.round((listenedCount / totalDays) * 100);
		const allListened = listenedCount === totalDays;

		return { listenedCount, totalDays, percent, allListened };
	}

	/**
	 * The lesson rows under the lead curriculum. Titles come from that
	 * curriculum's own GET; a day whose title never arrived (the fetch failed,
	 * or the day is not in the summary) is labelled by position and carries no
	 * meta, so it is still one click away.
	 */
	function lessonRowsFor(c: { id: string }): CardRow[] {
		return recentLessons(
			daysById[c.id] ?? [],
			(id) => listenedStore.has(id),
			RECENT_COUNT
		).map((lesson): CardRow => {
			const label = t('home.dayN', { position: lesson.position });
			const title = (titlesById[c.id] ?? {})[lesson.day];
			const note = lesson.next ? t('home.upNext') : t('home.listened');
			return title === undefined
				? {
						id: lesson.lesson_id,
						href: `/c/${c.id}/l/${lesson.lesson_id}`,
						title: label,
						meta: '',
						note,
						emphasis: lesson.next
					}
				: {
						id: lesson.lesson_id,
						href: `/c/${c.id}/l/${lesson.lesson_id}`,
						title,
						meta: label,
						note,
						emphasis: lesson.next
					};
		});
	}
</script>

<main>
	<header class="page-head">
		<div>
			<h1>{t('home.pageTitle')}</h1>
			<p class="tagline">{tagline}</p>
		</div>
		<button class="new-btn" onclick={() => (showForm = !showForm)} aria-expanded={showForm}>
			{showForm ? t('home.cancel') : t('home.newCurriculum')}
		</button>
	</header>

	{#if showForm}
		<section class="plan-form card">
			<h2>{t('home.planHeading')}</h2>
			<label>
				{t('home.topicLabel')}
				<input bind:value={planTopic} placeholder={t('home.topicPlaceholder')} />
			</label>
			<label>
				{t('home.cefrLabel')}
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
				{planStarting ? t('home.starting') : t('home.startPlanning')}
			</button>
			{#if planError}
				<p class="error">{planError}</p>
			{/if}
		</section>
	{/if}

	{#if listLoading}
		<p class="muted">{t('home.loading')}</p>
	{:else if listError}
		<p class="error">{listError}</p>
	{:else if !lead}
		<div class="empty card">
			<p class="muted">{t('home.noCurricula')}</p>
			<p class="muted small">{t('home.noCurriculaHint')}</p>
		</div>
	{:else}
		{@const p = progressById[lead.id]}
		<div class="curric-head" data-testid="lead-curriculum">
			<a class="curric-topic" href="/c/{lead.id}">{lead.topic}</a>
			{#if p}
				<span class="progress-line"
					>{p.allListened
						? t('home.allListened', { count: p.totalDays })
						: t('home.daysListened', { listened: p.listenedCount, total: p.totalDays })}</span
				>
			{/if}
		</div>
		{#if p}
			<div class="progress-bar lead-bar">
				<div class="progress-fill" style="width: {p.percent}%"></div>
			</div>
		{/if}
		<CardList rows={lessonRowsFor(lead)} testid="recent-lesson-row" />
		<a class="all-link" href="/c/{lead.id}">{t('home.allLessons')} →</a>
		{#if others.length > 0}
			<h2 class="other-head">{t('home.otherCurricula')}</h2>
			<ul class="other-list">
				{#each others as c (c.id)}
					{@const p = progressById[c.id]}
					<li data-testid="other-curriculum-row">
						<a href="/c/{c.id}">{c.topic}</a>
						{#if p}
							<span class="progress-line"
								>{t('home.daysListened', {
									listened: p.listenedCount,
									total: p.totalDays
								})}</span
							>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{/if}

	<!--
		Review sessions live UNDER the curricula and outside them. Dated, never
		numbered — a session has no position in a sequence to number (tunatale-9p9d).
	-->
	<section class="review-sessions">
		<div class="rs-head">
			<h2>{t('home.reviewSessions')}</h2>
			<button
				type="button"
				class="new-btn"
				onclick={handleNewReviewSession}
				disabled={creatingSession}
			>
				{creatingSession ? t('home.working') : t('home.newReviewSession')}
			</button>
		</div>
		<!--
			Manual mode is a fold-away beside the button, not a second button: the
			auto path stays the one-click default, and writing a session by hand is
			the deliberate detour (bd tunatale-jmwb). Before this, reaching it meant
			generating a session and discarding its dialogue — a wasted story call,
			the most expensive thing TT does.
		-->
		<details class="manual-session">
			<summary>{t('home.writeByHand')}</summary>
			<ManualStoryPanel
				copyPrompt={copyDraftPrompt}
				importRaw={importDraft}
				onImported={(id) => goto(`/review-sessions/${id}`)}
			/>
		</details>
		<p class="muted small rs-blurb">
			{t('home.rsBlurb')}
		</p>

		{#if nothingDue}
			<p class="muted rs-nothing-due">{nothingDue}</p>
		{/if}
		{#if sessionError}
			<p class="error" role="alert">{sessionError}</p>
		{/if}
		{#if sessionWarnings.length > 0}
			<ul class="warnings">
			{#each sessionWarnings as w (w)}
				<li>{w}</li>
			{/each}
			</ul>
		{/if}

		{#if sessions.length === 0}
			<p class="muted small">{t('home.noReviewSessions')}</p>
		{:else}
			<ReviewSessionCards sessions={recentSessions} />
			<!-- The index is the counterpart of a curriculum's day page, and the
			     page a session's back link and delete return to. Absent when there
			     are no sessions, so it is never a link to an empty page. -->
			<a class="all-link" href="/review-sessions">{t('home.allReviewSessions')} →</a>
		{/if}
	</section>
</main>

<style>
	.manual-session {
		margin-top: 0.75rem;
	}
	.manual-session summary {
		cursor: pointer;
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--color-muted);
		padding: 0.25rem 0;
		border-radius: 4px;
		user-select: none;
	}
	.manual-session summary:hover {
		color: var(--color-text);
	}
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
	.warnings {
		margin: 0 0 0.75rem;
		padding: 0 0 0 1.25rem;
		font-size: 0.82rem;
		color: var(--color-warning, #b8860b);
	}
	.warnings li {
		margin: 0.15rem 0;
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
		/* At a large Android root font the label outgrows a 320px column (18px of
		   page overflow in CI's Linux fonts at a 24px root); wrap it instead. */
		max-width: 100%;
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
	.curric-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.5rem 1rem;
		flex-wrap: wrap;
	}
	.curric-topic {
		font-size: 1.15rem;
		font-weight: 700;
		color: var(--color-text);
		text-decoration: none;
	}
	.curric-topic:hover {
		color: var(--color-primary);
	}
	.lead-bar {
		margin: 0.6rem 0 0.9rem;
	}
	.other-head {
		margin: 1.5rem 0 0.5rem;
		font-size: 0.9rem;
		font-weight: 600;
		color: var(--color-muted);
	}
	.other-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.35rem;
	}
	.other-list li {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
	}
	.other-list a {
		color: var(--color-text);
		font-weight: 600;
		text-decoration: none;
	}
	.other-list a:hover {
		color: var(--color-primary);
	}
	.progress-line {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	/* One step below the rows above it: the rows are the content, this is the way
	   to the page that lists them. */
	.all-link {
		display: inline-block;
		margin-top: 0.75rem;
		color: var(--color-muted);
		font-size: 0.85rem;
		text-decoration: none;
	}
	.all-link:hover {
		color: var(--color-primary);
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
	}
</style>
