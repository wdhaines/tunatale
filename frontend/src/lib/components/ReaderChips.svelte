<script lang="ts">
	import { onMount } from 'svelte';
	import { t } from '$lib/i18n/i18n.svelte';
	import type { MessageKey } from '$lib/i18n/i18n.svelte';
	import { readerEnglishPref, type ReaderEnglish } from '$lib/stores/readerEnglishPref.svelte';
	import { readerProductionPref } from '$lib/stores/readerProductionPref.svelte';
	import SettingChip from './SettingChip.svelte';

	interface Props {
		inline?: boolean;
	}

	let { inline = false }: Props = $props();

	const englishLabel = $derived(readerEnglishPref.value);

	const englishValueKey: Record<ReaderEnglish, MessageKey> = {
		off: 'readerChips.off',
		idiomatic: 'readerChips.idiomatic',
		literal: 'readerChips.literal',
		both: 'readerChips.both'
	};

	const recallValueKey: MessageKey = $derived(
		readerProductionPref.enabled ? 'readerChips.blurred' : 'readerChips.off'
	);

	onMount(() => {
		readerEnglishPref.init();
		readerProductionPref.init();
	});
</script>

{#if inline}
	<SettingChip
		type="button"
		solo
		active={readerProductionPref.enabled}
		data-testid="reader-recall-chip"
		aria-pressed={readerProductionPref.enabled}
		title={t('readerChips.recallHint')}
		onclick={() => readerProductionPref.set(!readerProductionPref.enabled)}
		label={t('readerChips.recall')}
		value={t(recallValueKey)}
	/>
{:else}
	<div class="reader-chips">
		<SettingChip
			type="button"
			active={englishLabel !== 'off'}
			data-testid="reader-english-chip"
			onclick={() => readerEnglishPref.next()}
			label={t('readerChips.english')}
			value={t(englishValueKey[englishLabel])}
		/>
		<SettingChip
			type="button"
			active={readerProductionPref.enabled}
			data-testid="reader-recall-chip"
			aria-pressed={readerProductionPref.enabled}
			title={t('readerChips.recallHint')}
			onclick={() => readerProductionPref.set(!readerProductionPref.enabled)}
			label={t('readerChips.recall')}
			value={t(recallValueKey)}
		/>
	</div>
{/if}

<style>
	.reader-chips {
		display: flex;
		justify-content: center;
		flex-wrap: wrap;
		gap: 0.5rem 0.4rem;
	}
</style>
