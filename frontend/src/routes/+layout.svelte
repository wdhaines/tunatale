<script lang="ts">
	import { onMount } from 'svelte';
	import favicon from '$lib/assets/favicon.png';
	import logo from '$lib/assets/logo.png';
	import { page } from '$app/stores';
	import SyncButton from '$lib/components/SyncButton.svelte';
	import QueueStatsWidget from '$lib/components/QueueStatsWidget.svelte';
	import { languageStore } from '$lib/stores/language.svelte';
	import { syncStore } from '$lib/stores/sync.svelte';
	import { queueStatsStore } from '$lib/stores/queueStats.svelte';
	import { themeStore } from '$lib/stores/theme.svelte';
	import { prefetchPrefStore } from '$lib/stores/prefetchPref.svelte';
	import { llmHealthStore } from '$lib/stores/llmHealth.svelte';
	import LlmHealthBanner from '$lib/components/LlmHealthBanner.svelte';

	let { children } = $props();

	// Theme, auto-download, and language are set-and-forget prefs — they live on
	// /settings now. The header keeps only critical CTAs (nav + Sync). Their stores
	// still init here so the boot-time preference (theme, prefetch) applies app-wide.

	// The review counts live in the nav (Anki-style) so they're visible from every
	// page. They come from a shared store so a grade on /review updates the nav
	// badge live (not just on the next focus). /queue-stats reads Anki's collection
	// live, so we also refresh on focus and after a sync. Failures degrade silently
	// — the badge just doesn't render.
	onMount(() => {
		themeStore.init();
		prefetchPrefStore.init();
		languageStore.init();
		queueStatsStore.refresh();
		llmHealthStore.refresh();
		const healthTimer = setInterval(() => llmHealthStore.refresh(), 60000);
		return () => clearInterval(healthTimer);
	});

	$effect(() => {
		const onFocus = () => {
			queueStatsStore.refresh();
		};
		window.addEventListener('focus', onFocus);
		return () => window.removeEventListener('focus', onFocus);
	});

	$effect(() => {
		if (syncStore.lastResult) queueStatsStore.refresh();
	});

	const path = $derived($page.url.pathname);
	const onLessons = $derived(path === '/' || path.startsWith('/c/'));
	const onReview = $derived(path === '/review');
	const onCards = $derived(path.startsWith('/cards'));
	const onSettings = $derived(path.startsWith('/settings'));
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<LlmHealthBanner />

<nav class="global-nav">
	<a href="/" class="brand"><img class="brand-mark" src={logo} alt="" />TunaTale</a>
	<div class="nav-links">
		<span class="review-group">
			<a href="/review" class="nav-link" class:active={onReview}>Review</a>
			{#if queueStatsStore.stats}
				<span class="review-badge"><QueueStatsWidget stats={queueStatsStore.stats} /></span>
			{/if}
		</span>
		<a href="/" class="nav-link" class:active={onLessons}>Lessons</a>
		<a href="/cards" class="nav-link" class:active={onCards}>Cards</a>
	</div>
	<div class="nav-actions">
		<a
			href="/settings"
			class="settings-link"
			class:active={onSettings}
			aria-label="Settings"
			title="Settings"
		>⚙️</a>
		<SyncButton />
	</div>
</nav>

{@render children()}

<style>
	/* Mobile-first: base targets small screens; min-width layers on desktop. */
	.global-nav {
		position: sticky;
		top: 0;
		z-index: 50;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		padding: 0.55rem 0.75rem;
		background: color-mix(in srgb, var(--color-surface) 88%, transparent);
		backdrop-filter: saturate(140%) blur(10px);
		border-bottom: 1px solid var(--color-border);
	}
	.brand {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		font-weight: 800;
		font-size: 1.05rem;
		letter-spacing: -0.01em;
		color: var(--color-brand);
		text-decoration: none;
	}
	.brand-mark {
		width: 28px;
		height: 28px;
		display: block;
	}
	.nav-actions {
		margin-left: auto;
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.settings-link {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 34px;
		height: 34px;
		padding: 0;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		cursor: pointer;
		font-size: 0.95rem;
		line-height: 1;
		text-decoration: none;
		transition: border-color 0.15s ease, background 0.15s ease;
	}
	.settings-link:hover {
		border-color: var(--color-primary);
		background: var(--color-surface-2);
	}
	.settings-link.active {
		border-color: var(--color-primary);
		background: color-mix(in srgb, var(--color-primary) 14%, transparent);
	}
	/* On mobile the links drop to their own full-width row so the review badge
	   has room instead of being squeezed beside the brand and Sync. */
	.nav-links {
		order: 3;
		width: 100%;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.25rem;
	}
	.review-group {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}
	.nav-link {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.35rem 0.7rem;
		border-radius: var(--radius-pill);
		color: var(--color-secondary);
		text-decoration: none;
		font-size: 0.88rem;
		font-weight: 600;
		transition: background 0.15s ease, color 0.15s ease;
	}
	.nav-link:hover {
		background: var(--color-surface-2);
		color: var(--color-text);
	}
	.nav-link.active {
		background: color-mix(in srgb, var(--color-primary) 14%, transparent);
		color: var(--color-primary);
	}
	.review-badge {
		font-size: 0.8rem;
		padding: 0.05rem 0.4rem;
		border-radius: var(--radius-pill);
		background: var(--color-surface-2);
	}

	@media (min-width: 641px) {
		.global-nav {
			flex-wrap: nowrap;
			gap: 1rem;
			padding: 0.6rem 1.5rem;
		}
		.brand {
			font-size: 1.15rem;
		}
		.nav-links {
			order: 0;
			width: auto;
			margin-left: auto;
			justify-content: flex-end;
		}
		.nav-actions {
			margin-left: 0;
		}
		.nav-link {
			padding: 0.35rem 0.75rem;
			font-size: 0.9rem;
		}
	}

	/* ─────────────────────────  Design tokens  ───────────────────────── */
	:global(:root) {
		color-scheme: light dark;

		/* Jet Age — mid-century travel poster (light) */
		--color-primary: #1e5e86;
		--color-primary-hover: #184e70;
		--color-on-primary: #ffffff;
		--color-accent: #e0a12c;
		--color-brand: #c8472e;
		--color-success: #2e8775;
		--color-warning: #b5762a;
		--color-danger: #be4730;
		--color-info: #1e5e86;
		--color-secondary: #5c6672;

		/* Surfaces & text */
		--color-bg: #f2ebdb;
		--color-surface: #ffffff;
		--color-surface-2: #eae0cc;
		--color-text: #1b2a3a;
		--color-muted: #6e7682;
		--color-border: #e1d6c0;
		--color-highlight: #fbeac6;

		/* Shape & depth */
		--radius-sm: 8px;
		--radius: 12px;
		--radius-lg: 18px;
		--radius-pill: 999px;
		--shadow-sm: 0 1px 2px rgba(60, 40, 20, 0.06), 0 1px 3px rgba(60, 40, 20, 0.05);
		--shadow: 0 6px 22px rgba(60, 40, 20, 0.1);

		--font-sans: ui-rounded, -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui,
			'Helvetica Neue', sans-serif;
	}

	/* Dark theme is opt-in via [data-theme='dark'] (set by the boot script /
	   theme store), defaulting to the OS preference. */
	:global(:root[data-theme='dark']) {
		/* Detective Noir (dark) */
		--color-primary: #3a82bd;
		--color-primary-hover: #4a90c9;
		--color-on-primary: #ffffff;
		--color-accent: #e0b04a;
		--color-brand: #e0675c;
		--color-success: #2f9d88;
		--color-warning: #c08a3a;
		--color-danger: #cf5a52;
		--color-info: #5c9bd1;
		--color-secondary: #9faaa9;

		--color-bg: #11212e;
		--color-surface: #182f3c;
		--color-surface-2: #21404d;
		--color-text: #efe6d2;
		--color-muted: #9faaa9;
		--color-border: #2b4856;
		--color-highlight: #43391e;

		--shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3), 0 1px 3px rgba(0, 0, 0, 0.25);
		--shadow: 0 6px 22px rgba(0, 0, 0, 0.45);
	}

	/* ─────────────────────────  Global base  ───────────────────────── */
	:global(html) {
		background: var(--color-bg);
	}
	:global(body) {
		margin: 0;
		font-family: var(--font-sans);
		background: var(--color-bg);
		color: var(--color-text);
		-webkit-font-smoothing: antialiased;
	}
	:global(button),
	:global(input),
	:global(select),
	:global(textarea) {
		font-family: inherit;
	}
	:global(:focus-visible) {
		outline: 2px solid var(--color-primary);
		outline-offset: 2px;
	}
	/* Shared surface card — used by the library, review, lessons, etc. */
	:global(.card) {
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-sm);
		padding: 1.25rem;
	}
	:global(.pulse) {
		animation: pulse 1.2s ease-in-out infinite;
	}
	@keyframes -global-pulse {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.4;
		}
	}
</style>
