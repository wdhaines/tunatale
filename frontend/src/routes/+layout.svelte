<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

{@render children()}

<style>
	:global(:root) {
		--color-primary: #2563eb;
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
