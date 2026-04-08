<script lang="ts">
	let { children } = $props();
</script>

<header>
	<a href="/">← TunaTale</a>
</header>

{@render children()}

<style>
	header {
		padding: 0.75rem 1.5rem;
		border-bottom: 1px solid var(--color-border);
		background: #fff;
	}
	header a {
		color: var(--color-primary);
		text-decoration: none;
		font-size: 0.9rem;
	}
	header a:hover {
		text-decoration: underline;
	}
</style>
