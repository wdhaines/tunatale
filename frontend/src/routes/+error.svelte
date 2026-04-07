<script lang="ts">
	import { page } from '$app/stores';
</script>

<main>
	<h1>{$page.status}</h1>
	<p>{$page.error?.message ?? 'Something went wrong'}</p>
	<a href="/">← Back to TunaTale</a>
</main>

<style>
	main {
		max-width: 700px;
		margin: 4rem auto;
		font-family: system-ui, sans-serif;
		padding: 0 1rem;
		text-align: center;
	}
	h1 {
		font-size: 4rem;
		color: var(--color-muted);
	}
	a {
		color: var(--color-primary);
	}
</style>
