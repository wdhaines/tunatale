<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import { page } from '$app/stores';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<nav class="global-nav">
	<a href="/" class="brand">TunaTale</a>
	<div class="nav-links">
		<a href="/practice" class:active={$page.url.pathname === '/practice'}>Practice</a>
		<a href="/admin/srs" class:active={$page.url.pathname.startsWith('/admin')}>Admin</a>
	</div>
</nav>

{@render children()}

<style>
	.global-nav {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.6rem 1.5rem;
		background: #fff;
		border-bottom: 1px solid var(--color-border);
	}
	.brand {
		font-weight: 700;
		font-size: 1.1rem;
		color: var(--color-primary);
		text-decoration: none;
	}
	.nav-links {
		display: flex;
		gap: 1rem;
	}
	.nav-links a {
		color: var(--color-secondary);
		text-decoration: none;
		font-size: 0.9rem;
	}
	.nav-links a:hover,
	.nav-links a.active {
		color: var(--color-primary);
		font-weight: 600;
	}

	@media (max-width: 640px) {
		.global-nav {
			padding: 0.6rem 0.75rem;
		}
	}

	:global(:root) {
		--color-primary: #2563eb;
		--color-secondary: #4b5563;
		--color-danger: #dc2626;
		--color-warning: #ea580c;
		--color-success: #16a34a;
		--color-muted: #666;
		--color-border: #ddd;
		--radius: 8px;
	}
	:global(body) {
		margin: 0;
		font-family: system-ui, sans-serif;
		background: #fafafa;
		color: #111;
	}
	:global(.pulse) {
		animation: pulse 1.2s ease-in-out infinite;
	}
	@keyframes -global-pulse {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.4; }
	}
</style>
