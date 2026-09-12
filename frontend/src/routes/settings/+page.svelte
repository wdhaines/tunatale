<script lang="ts">
	import LanguageSelector from '$lib/components/LanguageSelector.svelte';
	import { themeStore, type ThemePref } from '$lib/stores/theme.svelte';
	import { prefetchPrefStore } from '$lib/stores/prefetchPref.svelte';
	import { listenCountdownPref, type CountdownValue } from '$lib/stores/listenCountdownPref.svelte';
	import { languageStore } from '$lib/stores/language.svelte';
	import { authStore } from '$lib/stores/auth.svelte';
	import {
		mediaTraceEnabled,
		readMediaTrace,
		clearMediaTrace,
		setMediaTraceEnabled
	} from '$lib/mediaTrace';
	import { t } from '$lib/i18n/i18n.svelte';

	// The header used to carry these controls inline; they're set-and-forget
	// preferences, so they live here and only critical CTAs stay in the nav.
	const THEME_OPTIONS: { value: ThemePref; label: string; icon: string }[] = [
		{ value: 'system', label: t('settings.themeSystem'), icon: '🖥️' },
		{ value: 'light', label: t('settings.themeLight'), icon: '☀️' },
		{ value: 'dark', label: t('settings.themeDark'), icon: '🌙' }
	];

	const COUNTDOWN_OPTIONS: { value: CountdownValue; label: string }[] = [
		{ value: 'off', label: t('settings.countdownOff') },
		{ value: '10', label: t('settings.countdown10s') },
		{ value: '30', label: t('settings.countdown30s') },
		{ value: '60', label: t('settings.countdown60s') },
	];

	// Media button log — the on-device trace is read HERE, on the phone, after
	// the drive. Hidden unless it was switched on (`?mediatrace=on`), and a
	// snapshot of the buffer is taken at mount.
	let mediaTraceOn = $state(mediaTraceEnabled());
	let mediaTraceEntries: string[] = $state(mediaTraceEnabled() ? readMediaTrace() : []);

	function copyMediaTrace() {
		const text = mediaTraceEntries.join('\n');
		const clipboard = navigator.clipboard;
		if (!clipboard) return;
		clipboard.writeText(text).catch(() => {});
	}

	function clearMediaTraceView() {
		clearMediaTrace();
		mediaTraceEntries = [];
	}

	function turnOffMediaTrace() {
		setMediaTraceEnabled(false);
		mediaTraceOn = false;
	}
</script>

<svelte:head>
	<title>{t('settings.heading')} · TunaTale</title>
</svelte:head>

<main class="settings">
	<h1>{t('settings.heading')}</h1>

	<section class="card setting">
		<div class="setting-head">
			<h2>{t('settings.appearance')}</h2>
			<p>{t('settings.appearanceDesc')}</p>
		</div>
		<div class="segmented" role="group" aria-label={t('settings.themeAriaLabel')}>
			{#each THEME_OPTIONS as option (option.value)}
				<button
					type="button"
					class="segment"
					class:active={themeStore.pref === option.value}
					aria-pressed={themeStore.pref === option.value}
					onclick={() => themeStore.set(option.value)}
				>
					<span aria-hidden="true">{option.icon}</span>
					{option.label}
				</button>
			{/each}
		</div>
	</section>

	<section class="card setting">
		<div class="setting-head">
			<h2>{t('settings.listenPreview')}</h2>
			<p>{t('settings.listenPreviewDesc')}</p>
		</div>
		<div class="segmented" role="group" aria-label={t('settings.autoMarkAriaLabel')}>
			{#each COUNTDOWN_OPTIONS as option (option.value)}
				<button
					type="button"
					class="segment"
					class:active={listenCountdownPref.value === option.value}
					aria-pressed={listenCountdownPref.value === option.value}
					onclick={() => listenCountdownPref.set(option.value)}
				>
					{option.label}
				</button>
			{/each}
		</div>
	</section>

	<section class="card setting">
		<div class="setting-head">
			<h2>{t('settings.downloads')}</h2>
			<p>{t('settings.downloadsDesc')}</p>
		</div>
		<button
			type="button"
			class="toggle"
			class:on={prefetchPrefStore.enabled}
			role="switch"
			aria-checked={prefetchPrefStore.enabled}
			onclick={() => prefetchPrefStore.toggle()}
		>
			<span class="toggle-track"><span class="toggle-thumb"></span></span>
			{t('settings.autoDownload')}{prefetchPrefStore.enabled ? t('settings.on') : t('settings.off')}
		</button>
	</section>

	{#if languageStore.options.length > 1}
		<section class="card setting">
			<div class="setting-head">
				<h2>{t('settings.language')}</h2>
				<p>{t('settings.languageDesc')}</p>
			</div>
			<LanguageSelector />
		</section>
	{/if}

	{#if authStore.enabled}
		<section class="card setting">
			<div class="setting-head">
				<h2>{t('settings.account')}</h2>
				<p>{t('settings.signedInAs', { email: authStore.email ?? '' })}</p>
			</div>
			<button class="signout" onclick={() => authStore.logout()}>{t('settings.signOut')}</button>
		</section>
	{/if}

	{#if mediaTraceOn}
		<section class="card setting">
			<div class="setting-head">
				<h2>{t('settings.mediaTraceHeading')}</h2>
				<p>{t('settings.mediaTraceCount', { count: mediaTraceEntries.length })}</p>
			</div>
			<div class="media-trace-body">
				<pre class="media-trace">{mediaTraceEntries.slice().reverse().join('\n')}</pre>
				<div class="media-trace-actions">
					<button type="button" class="media-trace-button" onclick={copyMediaTrace}>{t('settings.mediaTraceCopy')}</button>
					<button type="button" class="media-trace-button" onclick={clearMediaTraceView}>{t('settings.mediaTraceClear')}</button>
					<button type="button" class="media-trace-button" onclick={turnOffMediaTrace}>{t('settings.mediaTraceTurnOff')}</button>
				</div>
			</div>
		</section>
	{/if}
</main>

<style>
	.settings {
		max-width: 42rem;
		margin: 0 auto;
		padding: 1.5rem 1rem 3rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	h1 {
		margin: 0 0 0.25rem;
		font-size: 1.6rem;
		letter-spacing: -0.01em;
	}
	.setting {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}
	.setting-head h2 {
		margin: 0;
		font-size: 1.05rem;
	}
	.setting-head p {
		margin: 0.2rem 0 0;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.segmented {
		display: inline-flex;
		padding: 0.2rem;
		gap: 0.2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface-2);
	}
	.segment {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.35rem 0.75rem;
		border: none;
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--color-secondary);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease, color 0.15s ease;
	}
	.segment:hover {
		color: var(--color-text);
	}
	.segment.active {
		background: var(--color-surface);
		color: var(--color-primary);
		box-shadow: var(--shadow-sm);
	}
	.toggle {
		display: inline-flex;
		align-items: center;
		gap: 0.55rem;
		padding: 0.4rem 0.75rem 0.4rem 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.toggle-track {
		display: inline-flex;
		align-items: center;
		width: 38px;
		height: 22px;
		padding: 2px;
		border-radius: var(--radius-pill);
		background: var(--color-border);
		transition: background 0.15s ease;
	}
	.toggle.on .toggle-track {
		background: var(--color-success);
	}
	.toggle-thumb {
		width: 18px;
		height: 18px;
		border-radius: 50%;
		background: #fff;
		box-shadow: var(--shadow-sm);
		transition: transform 0.15s ease;
	}
	.toggle.on .toggle-thumb {
		transform: translateX(16px);
	}
	.signout {
		padding: 0.4rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.media-trace-body {
		width: 100%;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.6rem;
	}
	.media-trace {
		width: 100%;
		box-sizing: border-box;
		margin: 0;
		padding: 0.6rem 0.75rem;
		max-height: 20rem;
		overflow: auto;
		background: var(--color-surface-2);
		border-radius: var(--radius-sm);
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.75rem;
		line-height: 1.45;
		white-space: pre-wrap;
		word-break: break-word;
	}
	.media-trace-actions {
		display: inline-flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.media-trace-button {
		padding: 0.4rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
</style>
