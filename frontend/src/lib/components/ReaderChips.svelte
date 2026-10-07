<script lang="ts">
	import { onMount } from 'svelte';
	import { t } from '$lib/i18n/i18n.svelte';
	import type { MessageKey } from '$lib/i18n/i18n.svelte';
	import { readerEnglishPref, type ReaderEnglish } from '$lib/stores/readerEnglishPref.svelte';
	import { readerProductionPref } from '$lib/stores/readerProductionPref.svelte';

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
	<button
		type="button"
		class="setting-chip solo"
		class:active={readerProductionPref.enabled}
		data-testid="reader-recall-chip"
		aria-pressed={readerProductionPref.enabled}
		title={t('readerChips.recallHint')}
		onclick={() => readerProductionPref.set(!readerProductionPref.enabled)}
	>
		<span class="chip-label">{t('readerChips.recall')}</span>
		<span class="chip-value">{t(recallValueKey)}</span>
	</button>
{:else}
	<div class="reader-chips">
		<button
			type="button"
			class="setting-chip"
			class:active={englishLabel !== 'off'}
			data-testid="reader-english-chip"
			onclick={() => readerEnglishPref.next()}
		>
			<span class="chip-label">{t('readerChips.english')}</span>
			<span class="chip-value">{t(englishValueKey[englishLabel])}</span>
		</button>
		<button
			type="button"
			class="setting-chip"
			class:active={readerProductionPref.enabled}
			data-testid="reader-recall-chip"
			aria-pressed={readerProductionPref.enabled}
			title={t('readerChips.recallHint')}
			onclick={() => readerProductionPref.set(!readerProductionPref.enabled)}
		>
			<span class="chip-label">{t('readerChips.recall')}</span>
			<span class="chip-value">{t(recallValueKey)}</span>
		</button>
	</div>
{/if}

<style>
	/* The look of the player's setting chips (LessonPlayer.svelte::.setting-chip),
	   copied because Svelte styles are component-scoped. Two copies of one design,
	   not two designs: change both or neither. */
	.reader-chips {
		display: flex;
		justify-content: center;
		flex-wrap: wrap;
		gap: 0.5rem 0.4rem;
	}

	.setting-chip {
		margin: 0;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.05rem;
		flex: 1 1 auto;
		min-width: 0;
		max-width: 9rem;
		min-height: 44px;
		padding: 0.3rem 0.5rem;
		background: transparent;
		color: var(--color-text);
		border: 1px solid var(--color-border, #ddd);
		border-radius: 10px;
		cursor: pointer;
		transition: border-color 0.15s ease, background 0.15s ease;
	}

	.setting-chip:hover {
		border-color: var(--color-muted);
	}

	.chip-label {
		font-size: 0.6rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--color-muted);
		white-space: nowrap;
	}

	.chip-value {
		font-size: 0.85rem;
		font-weight: 700;
		line-height: 1.1;
		white-space: nowrap;
	}

	.setting-chip.active {
		border-color: var(--color-primary);
		background: color-mix(in srgb, var(--color-primary) 10%, transparent);
	}

	.setting-chip.active .chip-value {
		color: var(--color-primary);
	}

	/* Collapsed player: one line, and exactly as tall as the button beside it
	   (the action row stretches both). */
	.setting-chip.solo {
		flex: 0 0 auto;
		flex-direction: row;
		align-items: center;
		align-self: stretch;
		gap: 0.4rem;
		min-height: 0;
		padding: 0 0.8rem;
		border-radius: var(--radius-pill);
	}

	/* Too narrow for one line beside the button: back to two lines, and the
	   button stretches to the chip instead. */
	@media (max-width: 359px) {
		.setting-chip.solo {
			flex-direction: column;
			align-items: flex-start;
			justify-content: center;
			gap: 0.05rem;
			padding: 0.3rem 0.6rem;
			border-radius: 10px;
		}
	}
</style>
