<script lang="ts">
	import { goto } from '$app/navigation';
	import CurriculumForm from '$lib/components/CurriculumForm.svelte';
	import type { CurriculumSummary } from '$lib/api';

	async function handleGenerate(curriculum: CurriculumSummary) {
		await goto(`/c/${curriculum.id}`);
	}
</script>

<main>
	<h1>TunaTale</h1>
	<nav>
		<a href="/practice">Practice (SRS)</a>
		<a href="/admin/srs">SRS Admin</a>
	</nav>
	<p class="tagline">AI-powered language learning — Slovene</p>

	<CurriculumForm onGenerate={handleGenerate} />
</main>

<style>
	main {
		max-width: 700px;
		margin: 2rem auto;
		font-family: system-ui, sans-serif;
		padding: 0 1rem;
	}
	.tagline {
		color: var(--color-muted);
		margin-top: 0.25rem;
	}
	nav {
		margin-top: 0.5rem;
	}
</style>
