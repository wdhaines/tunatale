<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import CurriculumForm from '$lib/components/CurriculumForm.svelte';
	import type { CurriculumSummary } from '$lib/api';
	import { api } from '$lib/api';

	let curricula: Array<{ id: string; topic: string; created_at: string }> = $state([]);
	let listLoading = $state(true);
	let listError = $state('');

	onMount(async () => {
		try {
			curricula = await api.listCurricula();
		} catch (e) {
			listError = e instanceof Error ? e.message : String(e);
		} finally {
			listLoading = false;
		}
	});

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleDateString();
	}

	async function handleGenerate(curriculum: CurriculumSummary) {
		curricula = [{ id: curriculum.id, topic: curriculum.topic, created_at: new Date().toISOString() }, ...curricula];
		await goto(`/c/${curriculum.id}`);
	}
</script>

<main>
	<p class="tagline">AI-powered language learning — Slovene</p>

	<CurriculumForm onGenerate={handleGenerate} />

	<section class="review-section">
		<h2>Review</h2>
		<div class="review-links">
			<a href="/review/recognition" class="review-btn">Review (recognition)</a>
			<a href="/review/production" class="review-btn">Review (production)</a>
		</div>
	</section>

	<section class="recent-section">
		<h2>Recent Curricula</h2>
		{#if listLoading}
			<p class="muted">Loading…</p>
		{:else if listError}
			<p class="error">{listError}</p>
		{:else if curricula.length === 0}
			<p class="muted">No curricula yet — generate one above.</p>
		{:else}
			<ul>
				{#each curricula as c (c.id)}
					<li>
					<a href="/c/{c.id}">{c.topic}</a>
					<span class="meta">{formatDate(c.created_at)}</span>
				</li>
				{/each}
			</ul>
		{/if}
	</section>
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
	.review-section {
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	.review-section h2 {
		margin: 0 0 0.5rem;
		font-size: 1rem;
	}
	.review-links {
		display: flex;
		gap: 0.75rem;
		flex-wrap: wrap;
	}
	.review-btn {
		display: inline-block;
		padding: 0.5rem 1rem;
		background: var(--color-primary);
		color: white;
		text-decoration: none;
		border-radius: 4px;
		font-size: 0.9rem;
	}
	.review-btn:hover {
		opacity: 0.85;
	}
	.recent-section {
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	.recent-section h2 {
		margin: 0 0 0.5rem;
		font-size: 1rem;
	}
	.recent-section ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.recent-section li {
		padding: 0.3rem 0;
		border-bottom: 1px solid var(--color-border);
	}
	.recent-section li:last-child {
		border-bottom: none;
	}
	.recent-section a {
		color: var(--color-primary);
		text-decoration: none;
	}
	.recent-section a:hover {
		text-decoration: underline;
	}
	.meta {
		color: var(--color-muted);
		font-size: 0.8rem;
		margin-left: 0.5rem;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.9rem;
		margin: 0;
	}
	.error {
		color: var(--color-danger);
		margin: 0;
	}

	@media (max-width: 640px) {
		.recent-section li {
			display: flex;
			flex-direction: column;
			gap: 0.15rem;
		}
		.meta {
			margin-left: 0;
		}
	}
</style>
