<script lang="ts">
	import LanguageSelector from '$lib/components/LanguageSelector.svelte';
	import { themeStore, type ThemePref } from '$lib/stores/theme.svelte';
	import { prefetchPrefStore } from '$lib/stores/prefetchPref.svelte';
	import { languageStore } from '$lib/stores/language.svelte';

	// The header used to carry these controls inline; they're set-and-forget
	// preferences, so they live here and only critical CTAs stay in the nav.
	const THEME_OPTIONS: { value: ThemePref; label: string; icon: string }[] = [
		{ value: 'system', label: 'System', icon: '🖥️' },
		{ value: 'light', label: 'Light', icon: '☀️' },
		{ value: 'dark', label: 'Dark', icon: '🌙' }
	];
</script>

<svelte:head>
	<title>Settings · TunaTale</title>
</svelte:head>

<main class="settings">
	<h1>Settings</h1>

	<section class="card setting">
		<div class="setting-head">
			<h2>Appearance</h2>
			<p>Choose a theme, or follow your device's setting.</p>
		</div>
		<div class="segmented" role="group" aria-label="Theme">
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
			<h2>Downloads</h2>
			<p>Cache lessons on wifi so they replay offline for free.</p>
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
			Auto-download on wifi: {prefetchPrefStore.enabled ? 'On' : 'Off'}
		</button>
	</section>

	{#if languageStore.options.length > 1}
		<section class="card setting">
			<div class="setting-head">
				<h2>Language</h2>
				<p>Switch the active learning language. The app reloads to refetch your decks.</p>
			</div>
			<LanguageSelector />
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
</style>
