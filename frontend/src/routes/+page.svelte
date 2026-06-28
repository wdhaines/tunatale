<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import CurriculumForm from '$lib/components/CurriculumForm.svelte';
	import type { CurriculumSummary } from '$lib/api';
	import { api } from '$lib/api';
	import { listenedStore } from '$lib/stores/listened.svelte';
	import { languageStore } from '$lib/stores/language.svelte';

	// Tagline names the active L2 (falls back to a generic line before the language
	// list has loaded, or in a single-language deployment that hasn't resolved yet).
	const tagline = $derived(
		languageStore.name
			? `AI-powered ${languageStore.name}, tuned to what you know.`
			: 'AI-powered language learning, tuned to what you know.'
	);

	interface CardProgress {
		listenedCount: number;
		totalDays: number;
		percent: number;
		allListened: boolean;
		continueLabel: string;
		continueHref: string;
	}

	let curricula: Array<{ id: string; topic: string; created_at: string }> = $state([]);
	let listLoading = $state(true);
	let listError = $state('');
	let showForm = $state(false);
	let progressById: Record<string, CardProgress> = $state({});

	onMount(async () => {
		try {
			curricula = await api.listCurricula();
		} catch (e) {
			listError = e instanceof Error ? e.message : String(e);
		} finally {
			listLoading = false;
		}

		const entries = await Promise.all(
			curricula.map(async (c) => {
				try {
					const days = await api.getCurriculumProgress(c.id);
					return [c.id, computeProgress(c.id, days)] as const;
				} catch {
					return [c.id, null] as const;
				}
			})
		);
		const next: Record<string, CardProgress> = {};
		for (const [id, progress] of entries) {
			if (progress) next[id] = progress;
		}
		progressById = next;
	});

	function computeProgress(
		curriculumId: string,
		days: Array<{ day: number; lesson_id: string }>
	): CardProgress | null {
		if (days.length === 0) return null;

		const sorted = [...days].sort((a, b) => a.day - b.day);
		const totalDays = sorted.length;
		const listenedCount = sorted.filter((d) => listenedStore.has(d.lesson_id)).length;
		const percent = Math.round((listenedCount / totalDays) * 100);
		const firstUnlistened = sorted.find((d) => !listenedStore.has(d.lesson_id));
		const allListened = !firstUnlistened;
		const target = firstUnlistened ?? sorted[sorted.length - 1];
		const continueLabel = allListened
			? `Revisit Day ${target.day}`
			: `Continue → Day ${target.day}`;
		const continueHref = `/c/${curriculumId}/l/${target.lesson_id}`;

		return { listenedCount, totalDays, percent, allListened, continueLabel, continueHref };
	}

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		});
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
			<p class="tagline">{tagline}</p>
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
					<div class="curric-card card">
						<a class="card-link" href="/c/{c.id}">
							<span class="topic">{c.topic}</span>
							<span class="meta">{formatDate(c.created_at)}</span>
						</a>
						{#if progressById[c.id]}
							{@const p = progressById[c.id]}
							<div class="progress-info">
								<p class="progress-line">{p.listenedCount} of {p.totalDays} days listened</p>
								<div class="progress-bar">
									<div class="progress-fill" style="width: {p.percent}%"></div>
								</div>
								{#if p.allListened}
									<p class="all-done">All {p.totalDays} days listened ✓</p>
								{/if}
								<a class="continue-link" href={p.continueHref}>{p.continueLabel}</a>
							</div>
						{/if}
					</div>
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
		gap: 0.6rem;
		padding: 1rem 1.25rem;
		transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.1s ease;
	}
	.curric-card:hover {
		border-color: var(--color-primary);
		box-shadow: var(--shadow);
		transform: translateY(-1px);
	}
	.card-link {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		text-decoration: none;
		color: var(--color-text);
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
	.progress-info {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.progress-line {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.progress-bar {
		height: 6px;
		border-radius: var(--radius-pill);
		background: var(--color-surface-2);
		overflow: hidden;
	}
	.progress-fill {
		height: 100%;
		border-radius: var(--radius-pill);
		background: var(--color-primary);
	}
	.all-done {
		margin: 0;
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--color-success);
	}
	.continue-link {
		align-self: flex-start;
		padding: 0.4rem 0.9rem;
		border-radius: var(--radius-pill);
		background: var(--color-surface-2);
		color: var(--color-primary);
		font-size: 0.8rem;
		font-weight: 600;
		text-decoration: none;
		transition: background 0.15s ease, color 0.15s ease;
	}
	.continue-link:hover {
		background: var(--color-primary);
		color: var(--color-on-primary);
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
			align-items: center;
			justify-content: space-between;
			gap: 1.5rem;
		}
		.card-link {
			flex: 1 1 auto;
			flex-direction: row;
			align-items: baseline;
			justify-content: space-between;
			gap: 1rem;
		}
		.progress-info {
			flex: 0 0 auto;
			width: 14rem;
		}
	}
</style>
