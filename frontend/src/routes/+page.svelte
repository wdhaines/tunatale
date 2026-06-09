<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import CurriculumForm from '$lib/components/CurriculumForm.svelte';
	import type { CurriculumSummary } from '$lib/api';
	import { api } from '$lib/api';

	let curricula: Array<{ id: string; topic: string; created_at: string }> = $state([]);
	let listLoading = $state(true);
	let listError = $state('');
	let showForm = $state(false);

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
		curricula = [
			{ id: curriculum.id, topic: curriculum.topic, created_at: new Date().toISOString() },
			...curricula
		];
		await goto(`/c/${curriculum.id}`);
	}
</script>

<main>
	<header class="page-head">
		<div>
			<h1>Lessons</h1>
			<p class="tagline">AI-powered Slovene, tuned to what you know.</p>
		</div>
		<button class="new-btn" onclick={() => (showForm = !showForm)} aria-expanded={showForm}>
			{showForm ? 'Cancel' : '+ New curriculum'}
		</button>
	</header>

	{#if showForm}
		<CurriculumForm onGenerate={handleGenerate} />
	{/if}

	{#if listLoading}
		<p class="muted">Loading…</p>
	{:else if listError}
		<p class="error">{listError}</p>
	{:else if curricula.length === 0}
		<div class="empty card">
			<p class="muted">No curricula yet.</p>
			<p class="muted small">Use “+ New curriculum” above to generate your first one.</p>
		</div>
	{:else}
		<ul class="library">
			{#each curricula as c (c.id)}
				<li>
					<a class="curric-card card" href="/c/{c.id}">
						<span class="topic">{c.topic}</span>
						<span class="meta">{formatDate(c.created_at)}</span>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</main>

<style>
	main {
		max-width: 760px;
		margin: 1rem auto;
		padding: 0 1rem;
	}
	.page-head {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.75rem;
		margin-bottom: 1.25rem;
	}
	h1 {
		margin: 0;
		font-size: 1.9rem;
		font-weight: 800;
		letter-spacing: -0.02em;
	}
	.tagline {
		color: var(--color-muted);
		margin: 0.25rem 0 0;
		font-size: 0.95rem;
	}
	.new-btn {
		flex-shrink: 0;
		align-self: flex-start;
		padding: 0.55rem 1rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		font-size: 0.9rem;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease, transform 0.1s ease;
	}
	.new-btn:hover {
		background: var(--color-primary-hover);
	}
	.new-btn:active {
		transform: translateY(1px);
	}
	.library {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.75rem;
	}
	.curric-card {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		padding: 1rem 1.25rem;
		text-decoration: none;
		color: var(--color-text);
		transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.1s ease;
	}
	.curric-card:hover {
		border-color: var(--color-primary);
		box-shadow: var(--shadow);
		transform: translateY(-1px);
	}
	.topic {
		font-size: 1.05rem;
		font-weight: 600;
	}
	.meta {
		color: var(--color-muted);
		font-size: 0.8rem;
		flex-shrink: 0;
	}
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.75rem;
		text-align: center;
		padding: 2.5rem 1.25rem;
	}
	.empty .muted {
		margin: 0;
	}
	.muted {
		color: var(--color-muted);
		font-size: 0.95rem;
	}
	.muted.small {
		font-size: 0.85rem;
	}
	.error {
		color: var(--color-danger);
		margin: 0;
	}

	@media (min-width: 641px) {
		main {
			margin: 2rem auto;
		}
		.page-head {
			flex-direction: row;
			justify-content: space-between;
			gap: 1rem;
		}
		.curric-card {
			flex-direction: row;
			align-items: baseline;
			justify-content: space-between;
			gap: 1rem;
		}
	}
</style>
