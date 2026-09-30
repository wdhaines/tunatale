<script lang="ts">
	import type { LessonAudio, RenderEstimate } from '$lib/api';
	import { getLocale, t } from '$lib/i18n/i18n.svelte';

	/**
	 * Re-render audio from the tools menu (bd tunatale-9paa), shared by the lesson
	 * reader and the review-session reader, which pass their own API calls.
	 *
	 * The user's calls, 2026-09-30: the sections as checkboxes (all ticked = the
	 * whole lesson, sent as `null` so the server takes the full render rather
	 * than the byte-for-byte reassemble), and the cost shown before the click.
	 * A clip already in the TTS cache is free, so a re-render after a code fix
	 * usually costs only the lines the fix changed.
	 *
	 * Owns its markup and CSS for AudioDownloads' reason: nothing crosses the
	 * component boundary.
	 */
	let {
		sections,
		estimate,
		rerender,
		onRendered
	}: {
		sections: { section_type: string; title: string }[];
		estimate: (types: string[] | null) => Promise<RenderEstimate>;
		rerender: (types: string[] | null) => Promise<LessonAudio>;
		onRendered?: (audio: LessonAudio) => void;
	} = $props();

	// One box per section TYPE: the server selects by type, so two sections of
	// one type are one choice (and a keyed each would throw on the duplicate).
	const offered = $derived(sections.filter((s, i) => sections.findIndex((o) => o.section_type === s.section_type) === i));
	// Stores the UNticked types, so a section the props add later starts ticked
	// and a new `sections` never leaves a box bound to a stale snapshot.
	let unticked = $state<Record<string, boolean>>({});
	let cost = $state<RenderEstimate | null>(null);
	let pricing = $state(false);
	let rendering = $state(false);
	let status = $state<'' | 'done'>('');
	let error = $state('');

	// Lesson order, never click order: the server gets a stable list.
	const chosen = $derived(offered.filter((s) => !unticked[s.section_type]).map((s) => s.section_type));
	const whole = $derived(chosen.length === offered.length);
	// `null` is "the whole lesson" on the wire.
	const selection = $derived(whole ? null : chosen);

	// Only the newest request may paint: a slow estimate for an older selection
	// must not land on top of the current one.
	let latest = 0;
	async function price(types: string[] | null) {
		const mine = ++latest;
		pricing = true;
		try {
			const result = await estimate(types);
			if (mine === latest) cost = result;
		} catch (e) {
			if (mine === latest) error = e instanceof Error ? e.message : String(e);
		} finally {
			if (mine === latest) pricing = false;
		}
	}

	$effect(() => {
		const types = selection;
		if (chosen.length === 0) {
			latest++;
			cost = null;
			pricing = false;
			return;
		}
		void price(types);
	});

	const number = (n: number) => new Intl.NumberFormat(getLocale()).format(n);
	const share = (e: RenderEstimate) => {
		const pct = (e.billable_chars / e.monthly_allowance) * 100;
		return pct > 0 && pct < 0.1 ? '<0.1%' : `${pct.toFixed(1)}%`;
	};

	async function run() {
		rendering = true;
		status = '';
		error = '';
		try {
			const audio = await rerender(selection);
			status = 'done';
			onRendered?.(audio);
			void price(selection);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			rendering = false;
		}
	}
</script>

<div class="rerender">
	<p class="rerender-title">{t('rerender.title')}</p>
	<div class="rerender-sections">
		{#each offered as s (s.section_type)}
			<label class="rerender-section">
				<input
					type="checkbox"
					checked={!unticked[s.section_type]}
					onchange={(e) => (unticked[s.section_type] = !e.currentTarget.checked)}
					disabled={rendering}
				/>
				{s.title}
			</label>
		{/each}
	</div>

	<p class="rerender-cost" aria-live="polite">
		{#if chosen.length === 0}
			{t('rerender.pickOne')}
		{:else if cost === null || pricing}
			{t('rerender.pricing')}
		{:else if cost.new_clips === 0}
			{t('rerender.free')}
		{:else}
			{t('rerender.estimate', { chars: number(cost.billable_chars), share: share(cost) })}
			{#if cost.gemini_usd > 0}
				{t('rerender.gemini', { usd: cost.gemini_usd.toFixed(2) })}
			{/if}
		{/if}
	</p>

	<div class="rerender-row">
		<button type="button" class="rerender-btn" onclick={run} disabled={rendering || chosen.length === 0}>
			{whole ? t('rerender.buttonWhole') : t('rerender.buttonSome', { count: chosen.length })}
		</button>
		{#if rendering}
			<span class="rerender-status">{t('rerender.rendering')}</span>
		{:else if status === 'done'}
			<span class="rerender-status">{t('rerender.done')}</span>
		{/if}
	</div>
	{#if error}
		<p class="rerender-error" role="alert">{t('rerender.failed', { message: error })}</p>
	{/if}
</div>

<style>
	.rerender {
		display: grid;
		gap: 0.5rem;
	}
	.rerender-title {
		margin: 0;
		font-weight: 600;
	}
	.rerender-sections {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem 1rem;
	}
	.rerender-section {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.9rem;
	}
	.rerender-cost {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-muted);
		font-variant-numeric: tabular-nums;
	}
	.rerender-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
	}
	.rerender-btn {
		padding: 0.45rem 1rem;
		border: 1px solid var(--color-primary);
		background: transparent;
		color: var(--color-primary);
		border-radius: 4px;
		font-weight: 600;
		cursor: pointer;
	}
	.rerender-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.rerender-status {
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.rerender-error {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.85rem;
	}
</style>
