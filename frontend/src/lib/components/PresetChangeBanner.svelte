<script lang="ts">
	// A preset change the sync found and Anki did NOT reschedule (tunatale-c649).
	// The due dates stayed where they were while the stabilities under them moved,
	// so (due_at - last_review) / stability decouples and reviews drift out of
	// range. That was tunatale-xzp6 — Slovene at a median of 7.06 where ~1.5 was
	// expected — and it was invisible until the line reached sync.log. This is
	// that line, on screen.
	import Banner from '$lib/components/Banner.svelte';
	import { api } from '$lib/api';
	import type { PresetChange } from '$lib/api';
	import { languageStore } from '$lib/stores/language.svelte';
	import { syncStore } from '$lib/stores/sync.svelte';
	import { t } from '$lib/i18n/i18n.svelte';

	let { syncAvailable = true }: { syncAvailable?: boolean } = $props();

	let change = $state<PresetChange | null>(null);

	// An unmeasured median is "—", never 0.00: a ratio of zero is a real ratio,
	// and printing it would claim Anki scheduled every card to its last review.
	const NO_MEASUREMENT = '—';
	function fmt2(value: number): string {
		return value.toFixed(2);
	}
	function fmtRatio(value: number | null): string {
		return value === null ? NO_MEASUREMENT : value.toFixed(2);
	}

	async function refresh(): Promise<void> {
		try {
			const res = await api.getPresetChange();
			change = res.change;
		} catch {
			// A failed read is not a change, and a second thing on screen is not
			// what anyone asked for. Stay silent; the next trigger tries again.
			change = null;
		}
	}

	// Refetched on mount, on every finished sync (a sync is the only thing that
	// can CREATE this record, so it is the only moment a new one can appear) and
	// on a language switch — the alert is per-language, so the other language's
	// record is a different question.
	$effect(() => {
		void languageStore.code;
		void syncStore.lastResult;
		if (!syncAvailable) return;
		void refresh();
	});

	async function dismiss(): Promise<void> {
		change = null;
		try {
			await api.dismissPresetChange();
		} catch {
			// Hidden either way: if the write failed, the next refetch brings the
			// alert back, which is honest — better than a banner that will not go.
		}
	}

	const deckLine = $derived(
		change ? t('presetChangeBanner.deck', { name: change.deck_name }) : '',
	);
	const retentionLine = $derived(
		change
			? t('presetChangeBanner.retention', {
					old: fmt2(change.desired_retention_old),
					new: fmt2(change.desired_retention_new),
				})
			: '',
	);
	const ratioLine = $derived(
		change
			? t('presetChangeBanner.dueRatioMedian', {
					old: fmtRatio(change.due_ratio_median_old),
					new: fmtRatio(change.due_ratio_median_new),
				})
			: '',
	);
</script>

{#if change}
	<Banner tone="danger" actionLabel={t('presetChangeBanner.dismiss')} onaction={dismiss}>
		<strong>{t('presetChangeBanner.heading')}</strong>
		<span class="line">{deckLine}</span>
		<span class="line">{retentionLine}</span>
		<span class="line">{ratioLine}</span>
		<span class="line">{t('presetChangeBanner.body')}</span>
	</Banner>
{/if}
