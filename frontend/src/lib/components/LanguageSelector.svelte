<script lang="ts">
	import { languageStore } from '$lib/stores/language.svelte';
	import { t } from '$lib/i18n/i18n.svelte';

	// Changing the active language re-points every API request (X-TT-Language) at
	// the other language's connection. A full reload is the simplest correct way to
	// refetch all the per-page data + nav badges under the new language.
	function onChange(event: Event): void {
		const code = (event.currentTarget as HTMLSelectElement).value;
		if (code === languageStore.code) return;
		languageStore.set(code);
		window.location.reload();
	}
</script>

<!-- Header pill (tunatale-hi5y). The visible face is only the language CODE, so
     it fits beside the Settings link on a 320px row; the real <select> lies
     transparent over the whole pill, so one tap opens the platform's own picker,
     which lists the full names. A native select cannot show one text closed and
     another open, hence the overlay. -->
{#if languageStore.options.length > 1}
	<span class="language-pill">
		<span class="language-code" aria-hidden="true">{languageStore.code.toUpperCase()}</span>
		<select
			class="language-selector"
			aria-label={t('languageSelector.activeLanguage')}
			title={t('languageSelector.activeLanguage')}
			value={languageStore.code}
			onchange={onChange}
		>
			{#each languageStore.options as option (option.code)}
				<option value={option.code}>{option.name}</option>
			{/each}
		</select>
	</span>
{/if}

<style>
	/* Same height, pill shape and tokens as the sibling .settings-link. */
	.language-pill {
		position: relative;
		box-sizing: border-box;
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		height: 34px;
		padding: 0 0.6rem 0 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.82rem;
		font-weight: 700;
		letter-spacing: 0.03em;
		transition:
			border-color 0.15s ease,
			background-color 0.15s ease;
	}
	/* Token-aware caret: currentColor follows [data-theme] with no second SVG. */
	.language-pill::after {
		content: '';
		width: 0.38rem;
		height: 0.38rem;
		margin-top: -0.2rem;
		border-right: 1.5px solid currentColor;
		border-bottom: 1.5px solid currentColor;
		transform: rotate(45deg);
		opacity: 0.7;
	}
	.language-pill:hover {
		border-color: var(--color-primary);
		background-color: var(--color-surface-2);
	}
	.language-pill:focus-within {
		outline: 2px solid var(--color-primary);
		outline-offset: 1px;
	}
	.language-selector {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		opacity: 0;
		font: inherit;
		/* iOS zooms into a focused control under 16px; the face is the span. */
		font-size: 16px;
		cursor: pointer;
	}
</style>
