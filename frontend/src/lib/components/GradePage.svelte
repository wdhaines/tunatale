<script lang="ts">
	/**
	 * The Grade page (tunatale-5i1p.3): one lesson's cards due within a range,
	 * graded ahead of schedule, the same night, to bring the review load down.
	 * Replaces the "Mark as Listened" popup once it is live (slice 7); until
	 * then the two sit side by side.
	 *
	 * Nothing is pre-graded: a row is graded only when tapped, and Save sends
	 * only those rows (`POST .../grade`, where an absent row is untouched).
	 * Design: .beads-tasks/briefs/design-grade-page-2026-11.md § The page.
	 */
	import { onMount } from 'svelte';
	import { api } from '#lib/api.js';
	import type { GradePlan, GradePlanCard, GradeRating, GradeResponse, TranscriptData } from '#lib/api.js';
	import { t } from '#lib/i18n/i18n.svelte.js';
	import { listenedStore } from '#lib/stores/listened.svelte.js';
	import { queueStatsStore } from '#lib/stores/queueStats.svelte.js';
	import { gradeRangePref } from '#lib/stores/gradeRangePref.svelte.js';
	import {
		dueTag,
		englishIsTheCue,
		gradeKey,
		RANGE_PRESETS,
		sayLine,
		stepRange,
		type GradeRange
	} from '#lib/grading/grade.js';
	import GradeButtons from './GradeButtons.svelte';
	import SentenceLineEditor from './SentenceLineEditor.svelte';
	import BackLink from './BackLink.svelte';
	import {
		countOn,
		initStage,
		lineEdit,
		newCards,
		sentenceRefusal,
		turnedOff as lineTurnedOff,
		type LineStage
	} from '#lib/grading/sentenceStage.js';

	interface Props {
		contentId: string;
		/** The lesson's (or review session's) name, under the heading. */
		title: string;
		/** Where Back goes: the page the Grade button was pressed on. */
		backHref: string;
	}
	let { contentId, title, backHref }: Props = $props();

	type Tab = 'understand' | 'say' | 'new';
	let tab = $state<Tab>('understand');
	let range = $state<GradeRange>(gradeRangePref.value);
	let plan = $state<GradePlan | null>(null);
	let transcript = $state<TranscriptData | null>(null);
	let error = $state('');
	let notice = $state('');
	let saving = $state(false);

	/** Chosen grades, by `item_id:direction`. Kept across a range change. */
	let grades = $state<Record<string, GradeRating>>({});
	/** Meanings (Understand) and hidden words (Say it) the learner has tapped open. */
	let opened = $state<Record<string, boolean>>({});
	/** Suggestions flipped from their default, by `kind:key`. */
	let flipped = $state<Record<string, boolean>>({});
	let studiedAgain = $state(false);
	/** Each lesson line's cards as the learner has staged them, by line key; kept across a range change. */
	let stages = $state<Record<string, LineStage>>({});
	let showAllLines = $state(false);
	/** Blanks the last Save could not make: named loudly (the owner wants loud failures). */
	let notMade = $state('');

	const now = new Date();

	async function load(r: GradeRange) {
		try {
			const next = await api.getGradePlan(contentId, r);
			// A slower response for a range since replaced must not win.
			if (r !== range) return;
			plan = next;
			for (const line of next.suggestions.sentences) {
				stages[line.line_key] ??= initStage(line);
			}
			error = '';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	onMount(() => {
		void load(range);
		// Say it places a word in its line; without the transcript it still
		// works, from the card's own text, so a failure here is not an error.
		api.getTranscript(contentId).then(
			(tr) => (transcript = tr),
			() => {}
		);
	});

	function setRange(r: GradeRange) {
		range = r;
		gradeRangePref.set(r);
		void load(r);
	}

	const understandRows = $derived(plan?.cards.filter((c) => c.direction === 'recognition') ?? []);
	const sayRows = $derived(plan?.cards.filter((c) => c.direction === 'production') ?? []);

	function rate(card: GradePlanCard, rating: GradeRating) {
		const key = gradeKey(card);
		if (grades[key] === rating) delete grades[key];
		else grades[key] = rating;
		notice = '';
	}

	/** What a hidden answer blurs: one fixed stand-in for every answer, so the blur shows
	 * neither its length nor its letters (owner, 2026-11-21: a blur of the real text kept both;
	 * the blur itself stays). */
	const VEIL = 'xxxxx xxxx';

	function open(key: string) {
		opened[key] = true;
	}

	// ── New cards ──
	type Suggestion = { kind: 'word' | 'contrast'; key: string; text: string; gloss: string; fits: boolean };
	const suggestions = $derived.by((): Suggestion[] => {
		const s = plan?.suggestions;
		if (!s) return [];
		return [
			...s.words.map((w) => ({ kind: 'word' as const, key: w.key, text: w.text, gloss: w.gloss, fits: w.will_create })),
			...s.contrast.map((c) => ({ kind: 'contrast' as const, key: c.form, text: c.form, gloss: c.english, fits: c.will_create }))
		];
	});
	const sugKey = (s: Suggestion) => `${s.kind}:${s.key}`;
	// On by default when it fits the day's budget, off past it (owner: new cards on by default).
	const isOn = (s: Suggestion) => s.fits !== Boolean(flipped[sugKey(s)]);
	function toggle(s: Suggestion) {
		flipped[sugKey(s)] = !flipped[sugKey(s)];
		notice = '';
	}
	// ── Sentences ──
	const sentences = $derived(plan?.suggestions.sentences ?? []);
	const stageOf = (line: (typeof sentences)[number]) => stages[line.line_key] ?? initStage(line);
	// A line with cards is open; the rest fold behind one button (owner, 2026-11-20).
	const openLines = $derived(sentences.filter((l) => showAllLines || countOn(initStage(l)) > 0 || countOn(stageOf(l)) > 0));
	const foldedLines = $derived(sentences.length - openLines.length);
	const sentenceEdits = $derived(sentences.flatMap((l) => lineEdit(l, stageOf(l)) ?? []));
	const sentencesNew = $derived(sentences.reduce((n, l) => n + newCards(l, stageOf(l), plan?.studied ?? false), 0));
	const sentencesOff = $derived(sentences.reduce((n, l) => n + lineTurnedOff(l, stageOf(l)), 0));
	const sentenceCards = $derived(sentences.reduce((n, l) => n + countOn(stageOf(l)), 0));

	const newOn = $derived(suggestions.filter(isOn).length + sentencesNew);
	const declined = $derived(suggestions.filter((s) => s.fits && !isOn(s)).map((s) => ({ kind: s.kind, key: s.key })));
	const overCap = $derived(suggestions.filter((s) => !s.fits && isOn(s)).map((s) => ({ kind: s.kind, key: s.key })));

	// ── Save ──
	const cardGrades = $derived(
		(plan?.cards ?? []).flatMap((c) => {
			const rating = grades[gradeKey(c)];
			return rating ? [{ item_id: c.item_id, direction: c.direction, rating }] : [];
		})
	);
	const saveLabel = $derived(
		t('gradePage.save', {
			grades: t('gradePage.grades', { count: cardGrades.length }),
			cards: t('gradePage.newCards', { count: newOn })
		})
	);
	// The first Save of a lesson is its study mark, so it is never empty.
	const canSave = $derived(
		plan !== null &&
			!saving &&
			(!plan.studied ||
				cardGrades.length > 0 ||
				newOn > 0 ||
				declined.length > 0 ||
				sentenceEdits.length > 0 ||
				studiedAgain)
	);

	function savedText(r: GradeResponse, turnedOff: number): string {
		const parts: string[] = [];
		if (r.applied > 0) parts.push(t('gradePage.savedGraded', { count: r.applied }));
		if (r.created > 0) parts.push(t('gradePage.newCards', { count: r.created }));
		// A turned-off word went on the ignore list: say so, or it vanishes silently.
		if (turnedOff > 0) parts.push(t('gradePage.savedTurnedOff', { count: turnedOff }));
		if (r.recorded_study) parts.push(t('gradePage.savedStudied'));
		return parts.length ? `${t('gradePage.saved')}: ${parts.join(' · ')}` : t('gradePage.saved');
	}

	async function saveAll() {
		saving = true;
		notice = '';
		error = '';
		notMade = '';
		const turnedOff = declined.length + sentencesOff;
		try {
			const result = await api.saveGrades(contentId, {
				card_grades: cardGrades,
				declined,
				over_cap: overCap,
				studied: studiedAgain,
				sentence_edits: sentenceEdits
			});
			grades = {};
			opened = {};
			flipped = {};
			stages = {};
			studiedAgain = false;
			notice = savedText(result, turnedOff);
			if (result.not_made.length > 0) {
				notMade = t('gradePage.notMade', { words: result.not_made.map((b) => b.word).join(', ') });
			}
			await load(range);
			void listenedStore.refresh();
			queueStatsStore.refresh();
		} catch (e) {
			const refusal = sentenceRefusal(e);
			if (refusal !== null) {
				// A sentence edit the server refused: not a stale plan, so a refetch would not help (review F4).
				error = t('gradePage.sentenceRefused', { reason: refusal });
			} else if ((e as { status?: number }).status === 409) {
				notice = t('gradePage.stale');
				await load(range);
			} else {
				error = e instanceof Error ? e.message : String(e);
			}
		} finally {
			saving = false;
		}
	}

	function rangeChipLabel(r: GradeRange): string {
		if (r === null) return t('gradePage.rangeAll');
		return r === 0 ? t('gradePage.rangeNow') : t('gradePage.rangeDays', { count: r });
	}
	const laterCount = $derived(
		plan ? (tab === 'understand' ? plan.later.recognition : plan.later.production) : 0
	);
	const rows = $derived(tab === 'understand' ? understandRows : sayRows);
</script>

<div class="grade-page">
	<header class="top">
		<BackLink href={backHref} label={t('gradePage.back')} />
		{#if title}
			<p class="eyebrow">{t('gradePage.title')}</p>
			<h1>{title}</h1>
		{:else}
			<h1 class="solo">{t('gradePage.title')}</h1>
		{/if}
	</header>

	<div class="tabs" role="tablist">
		<button role="tab" aria-selected={tab === 'understand'} onclick={() => (tab = 'understand')}
			>{t('gradePage.tabUnderstand', { count: understandRows.length })}</button
		>
		<button role="tab" aria-selected={tab === 'say'} onclick={() => (tab = 'say')}
			>{t('gradePage.tabSay', { count: sayRows.length })}</button
		>
		<button role="tab" aria-selected={tab === 'new'} onclick={() => (tab = 'new')}
			>{t('gradePage.tabNew', { count: newOn })}</button
		>
	</div>

	{#if tab !== 'new'}
		<div class="range" role="group" aria-label={t('gradePage.rangeLabel')}>
			<span class="range-label">{t('gradePage.due')}</span>
			{#each RANGE_PRESETS as preset (preset)}
				<button
					class="chip"
					data-range={preset ?? 'all'}
					aria-pressed={range === preset}
					onclick={() => setRange(preset)}>{rangeChipLabel(preset)}</button
				>
			{/each}
			<span class="stepper">
				<button class="chip" aria-label={t('gradePage.rangeLess')} onclick={() => setRange(stepRange(range, -1))}
					>−</button
				>
				<span data-testid="range-value"
					>{range === null ? t('gradePage.rangeAll') : t('gradePage.rangeValue', { count: range })}</span
				>
				<button class="chip" aria-label={t('gradePage.rangeMore')} onclick={() => setRange(stepRange(range, 1))}
					>+</button
				>
			</span>
		</div>
	{/if}

	{#if error}<p class="error" role="alert">{error}</p>{/if}

	<div class="body">
		{#if plan === null}
			{#if !error}<p class="muted">{t('gradePage.loading')}</p>{/if}
		{:else if tab === 'new'}
			{#if suggestions.length === 0 && sentences.length === 0}
				<p class="muted">{t('gradePage.newNone')}</p>
			{/if}
			{#each [['word', 'gradePage.newWords'], ['contrast', 'gradePage.newContrast']] as const as [kind, heading] (kind)}
				{@const group = suggestions.filter((s) => s.kind === kind)}
				{#if group.length > 0}
					<section class="group">
						<h2>{t(heading)}</h2>
						{#each group as s (sugKey(s))}
							<div class="row add-row" data-row-text={s.text}>
								<div>
									<span class="word">{s.text}</span>
									<span class="gloss">{s.gloss}</span>
								</div>
								<button class="toggle" aria-pressed={isOn(s)} onclick={() => toggle(s)}
									>{isOn(s) ? t('gradePage.add') : s.fits ? t('gradePage.off') : t('gradePage.overBudget')}</button
								>
							</div>
						{/each}
					</section>
				{/if}
			{/each}
			{#if sentences.length > 0}
				<section class="group sentences">
					<div class="group-head">
						<h2>{t('gradePage.newSentences')}</h2>
						<span class="group-count"
							>{t('gradePage.sentencesCount', {
								count: sentences.length,
								cards: t('sentenceLine.cards', { count: sentenceCards })
							})}</span
						>
					</div>
					<p class="hint">{t('gradePage.sentencesHint')}</p>
					{#each openLines as line (line.line_key)}
						<SentenceLineEditor
							{line}
							stage={stageOf(line)}
							onchange={() => {
								stages[line.line_key] ??= stageOf(line);
								notice = '';
							}}
						/>
					{/each}
					{#if foldedLines > 0}
						<button class="disclose" onclick={() => (showAllLines = true)}
							>{foldedLines === sentences.length
								? t('gradePage.showLines', { count: foldedLines })
								: t('gradePage.showMoreLines', { count: foldedLines })}</button
						>
					{/if}
				</section>
			{/if}
			{#if plan.waiting_new > 0}
				<p class="muted">{t('gradePage.waitingNew', { count: plan.waiting_new })}</p>
			{/if}
		{:else}
			{#if rows.length === 0}
				<p class="muted">{t('gradePage.empty')}</p>
			{/if}
			{#each rows as card (gradeKey(card))}
				{@const key = gradeKey(card)}
				{@const due = dueTag(card, now)}
				{#if tab === 'understand'}
					<div class="row" data-row-text={card.text}>
						<div class="row-main">
							<span class="word">{card.text}</span>
							<button
								class="gloss"
								class:veiled={!opened[key]}
								aria-label={opened[key] ? card.gloss : t('gradePage.showMeaning')}
								onclick={() => open(key)}>{opened[key] ? card.gloss : VEIL}</button
							>
						</div>
						<span class="due" class:over={due.over}>{due.label}</span>
						<div class="row-grades">
							<GradeButtons variant="row" selected={grades[key] ?? null} onRate={(r) => rate(card, r)} />
						</div>
					</div>
				{:else}
					{@const line = sayLine(card, transcript)}
					{@const englishShown = opened[key] || opened[`${key}:en`] || englishIsTheCue(line)}
					<div class="row say-row" data-row-text={card.text}>
						<div class="row-main">
							{#if card.drill}
								<span class="step">{t('gradePage.step', { step: card.drill.step, of: card.drill.of })}</span>
							{/if}
							{#each line.prompt as p, i (i)}
								<span class="prompt">{p}</span>
							{/each}
							<span class="say"
								>{line.before}<button
									class="hidden-word"
									class:shown={opened[key]}
									aria-label={opened[key] ? line.hidden : t('gradePage.showHidden')}
									onclick={() => open(key)}>{opened[key] ? line.hidden : VEIL}</button
								>{line.after}</span
							>
							{#if line.english}
								<button
									class="english"
									class:veiled={!englishShown}
									aria-label={englishShown ? line.english : t('gradePage.showEnglish')}
									onclick={() => open(`${key}:en`)}>{englishShown ? line.english : VEIL}</button
								>
							{/if}
						</div>
						<span class="due" class:over={due.over}>{due.label}</span>
						{#if opened[key]}
							<div class="row-grades">
								<GradeButtons variant="row" selected={grades[key] ?? null} onRate={(r) => rate(card, r)} />
							</div>
						{/if}
					</div>
				{/if}
			{/each}
			{#if laterCount > 0}
				<p class="muted later">{t('gradePage.later', { count: laterCount })}</p>
			{/if}
		{/if}
	</div>

	<footer class="foot">
		{#if notice}<p class="notice" role="status">{notice}</p>{/if}
		{#if notMade}<p class="not-made" role="alert">{notMade}</p>{/if}
		{#if plan && !plan.studied}
			<p class="hint">{t('gradePage.firstSaveStudies')}</p>
		{/if}
		{#if plan?.studied}
			<label class="again">
				<input type="checkbox" bind:checked={studiedAgain} />
				{t('gradePage.studiedAgain', { count: plan.listen_count })}
			</label>
		{/if}
		<button class="save" disabled={!canSave} onclick={saveAll}>{saving ? t('gradePage.saving') : saveLabel}</button>
	</footer>
</div>

<style>
	.grade-page {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		max-width: 40rem;
		margin: 0 auto;
		padding: 0 0.75rem 1rem;
	}
	/* Layout A (owner, 2026-11-21): the way back on its own line, then the lesson
	   as the heading with "Grade" a small label above it. */
	.top {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		min-width: 0;
		padding-top: 0.75rem;
	}
	.eyebrow {
		margin: 0.5rem 0 0;
		font-size: 0.72rem;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--color-muted);
	}
	h1 {
		font-size: 1.2rem;
		line-height: 1.25;
		margin: 0.1rem 0 0;
		text-wrap: balance;
		overflow-wrap: anywhere;
	}
	h1.solo {
		margin-top: 0.5rem;
	}
	.tabs {
		display: flex;
		border-bottom: 1px solid var(--color-border);
	}
	.tabs button {
		flex: 1;
		margin: 0;
		font: inherit;
		font-size: 0.9rem;
		padding: 0.55rem 0.2rem;
		border: 0;
		border-bottom: 2px solid transparent;
		border-radius: 0;
		background: none;
		color: var(--color-muted);
		cursor: pointer;
	}
	.tabs button[aria-selected='true'] {
		color: var(--color-primary);
		border-bottom-color: var(--color-primary);
		font-weight: 650;
	}
	.range {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem;
	}
	.range-label {
		font-size: 0.7rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--color-muted);
	}
	.chip {
		margin: 0;
		font: inherit;
		font-size: 0.8rem;
		padding: 0.35rem 0.7rem;
		min-height: 36px;
		border-radius: var(--radius-pill);
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		cursor: pointer;
	}
	.chip[aria-pressed='true'] {
		background: color-mix(in srgb, var(--color-primary) 14%, transparent);
		border-color: var(--color-primary);
		color: var(--color-primary);
		font-weight: 650;
	}
	.stepper {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		margin-left: auto;
		font-size: 0.8rem;
		font-variant-numeric: tabular-nums;
	}
	.stepper span {
		min-width: 2.4rem;
		text-align: center;
	}
	.body {
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		padding: 0.25rem 0.75rem;
	}
	.row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 0.35rem 0.5rem;
		align-items: center;
		padding: 0.65rem 0;
	}
	.row + .row {
		border-top: 1px solid color-mix(in srgb, var(--color-border) 60%, transparent);
	}
	.row-main {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.row-grades {
		grid-column: 1 / -1;
	}
	.word {
		font-size: 1.05rem;
	}
	.gloss {
		margin: 0;
		font: inherit;
		font-size: 0.85rem;
		color: var(--color-muted);
		background: none;
		border: 0;
		padding: 0.1rem 0;
		text-align: left;
		cursor: pointer;
	}
	/* A hidden answer: the blurred stand-in, at its own width. */
	.gloss.veiled,
	.english.veiled {
		align-self: flex-start;
		filter: blur(5px);
	}
	.due {
		justify-self: end;
		font-size: 0.75rem;
		color: var(--color-muted);
		font-variant-numeric: tabular-nums;
	}
	.due.over {
		font-weight: 700;
		color: var(--color-text);
	}
	.step {
		font-size: 0.72rem;
		color: var(--color-muted);
	}
	.prompt {
		font-size: 0.95rem;
	}
	.say {
		font-size: 1.05rem;
		line-height: 2.1;
	}
	/* The hidden word: the reader's production blur, over the fixed stand-in until tapped. */
	.hidden-word {
		margin: 0;
		font: inherit;
		border: 0;
		border-radius: 6px;
		padding: 0.15rem 0.3rem;
		cursor: pointer;
		background: color-mix(in srgb, var(--color-primary) 18%, transparent);
		color: transparent;
		text-shadow: 0 0 9px var(--color-primary);
	}
	.hidden-word.shown {
		color: var(--color-primary);
		text-shadow: none;
		font-weight: 650;
	}
	.english {
		display: block;
		margin: 0;
		font: inherit;
		font-size: 0.8rem;
		color: var(--color-muted);
		background: none;
		border: 0;
		padding: 0.1rem 0;
		text-align: left;
		cursor: pointer;
	}
	.group h2 {
		font-size: 0.85rem;
		margin: 0.6rem 0 0;
	}
	.toggle {
		margin: 0;
		font: inherit;
		font-size: 0.8rem;
		min-width: 5.5rem;
		min-height: 36px;
		padding: 0.35rem 0.6rem;
		border-radius: var(--radius-sm);
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-muted);
		cursor: pointer;
	}
	.toggle[aria-pressed='true'] {
		background: var(--color-success);
		border-color: var(--color-success);
		color: var(--color-on-primary);
		font-weight: 650;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.85rem;
		margin: 0.6rem 0;
	}
	.error {
		color: var(--color-danger);
		margin: 0;
	}
	.foot {
		position: sticky;
		bottom: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		padding: 0.5rem 0 calc(0.5rem + env(safe-area-inset-bottom, 0px));
		background: var(--color-bg);
	}
	.notice {
		margin: 0;
		color: var(--color-success);
		font-size: 0.85rem;
	}
	.not-made {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.85rem;
		font-weight: 600;
	}
	.group-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.5rem;
	}
	.group-count {
		font-size: 0.75rem;
		color: var(--color-muted);
		font-variant-numeric: tabular-nums;
		text-align: right;
	}
	.sentences .hint {
		font-size: 0.75rem;
	}
	.sentences :global(.line + .line) {
		border-top: 1px solid color-mix(in srgb, var(--color-border) 60%, transparent);
	}
	.disclose {
		font: inherit;
		font-size: 0.85rem;
		color: var(--color-primary);
		background: none;
		border: 0;
		padding: 0.55rem 0;
		cursor: pointer;
		text-align: left;
		font-weight: 600;
	}
	.hint {
		margin: 0;
		color: var(--color-muted);
		font-size: 0.85rem;
	}
	.again {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		font-size: 0.85rem;
	}
	.save {
		margin: 0;
		font: inherit;
		font-weight: 650;
		padding: 0.75rem;
		border-radius: var(--radius-pill);
		border: 0;
		background: var(--color-primary);
		color: var(--color-on-primary);
		cursor: pointer;
	}
	.save:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
