<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { GrammarPatternOption } from '$lib/api';
	import { t } from '$lib/i18n/i18n.svelte';

	/**
	 * Adds a grammar lesson (an affix drill) to a curriculum (bd tunatale-ve4p.5).
	 *
	 * Lists the patterns the language has with the roots THIS learner
	 * understands for each, so a pattern that cannot be drilled yet says why
	 * instead of failing after a tap. Draws nothing at all for a language with
	 * no affix patterns, and nothing if the list cannot be fetched: the page it
	 * sits on is about stories, and this is an extra.
	 */
	interface Props {
		curriculumId: string;
		/** The day was appended and its build is queued; the page re-reads the plan. */
		onAdded: () => void | Promise<void>;
	}

	let { curriculumId, onAdded }: Props = $props();

	let patterns: GrammarPatternOption[] = $state([]);
	let open = $state(false);
	let adding: string | null = $state(null);
	let error = $state('');

	async function load() {
		try {
			patterns = await api.listGrammarPatterns(curriculumId);
		} catch {
			patterns = [];
		}
	}

	onMount(load);

	// One line under a pattern's name: which roots carry it, and, when they are
	// too few to drill, that they are.
	function rootsLine(p: GrammarPatternOption): string {
		if (p.roots.length === 0) return t('addGrammarLesson.noRoots');
		const roots = t('addGrammarLesson.roots', { count: p.roots.length, roots: p.roots.join(', ') });
		return p.ready ? roots : `${roots} ${t('addGrammarLesson.needsTwo')}`;
	}

	async function add(pattern: string) {
		adding = pattern;
		error = '';
		try {
			await api.createGrammarLesson(curriculumId, pattern);
			open = false;
			await onAdded();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			adding = null;
		}
	}
</script>

{#if patterns.length > 0}
	<div class="grammar">
		<button type="button" class="grammar-toggle" aria-expanded={open} onclick={() => (open = !open)}>
			{t('addGrammarLesson.toggle')} {open ? '▴' : '▾'}
		</button>
		{#if open}
			<ul class="grammar-patterns">
				{#each patterns as p (p.key)}
					<li class="grammar-pattern">
						<div class="pattern-text">
							<span class="pattern-name">{p.title}</span>
							<span class="pattern-roots">{rootsLine(p)}</span>
						</div>
						<button
							type="button"
							class="pattern-add"
							aria-label={t('addGrammarLesson.addPattern', { title: p.title })}
							disabled={!p.ready || adding !== null}
							onclick={() => add(p.key)}
						>
							{adding === p.key ? t('addGrammarLesson.adding') : t('addGrammarLesson.add')}
						</button>
					</li>
				{/each}
			</ul>
		{/if}
		{#if error}
			<p class="grammar-error">{error}</p>
		{/if}
	</div>
{/if}

<style>
	.grammar {
		margin-top: 0.75rem;
	}
	.grammar-toggle {
		padding: 0;
		border: none;
		background: none;
		color: var(--color-primary);
		font-size: 0.9rem;
		font-weight: 600;
		cursor: pointer;
	}
	.grammar-toggle:hover {
		text-decoration: underline;
	}
	.grammar-patterns {
		list-style: none;
		margin: 0.5rem 0 0;
		padding: 0;
		border: 1px solid var(--color-border);
		border-radius: 12px;
	}
	.grammar-pattern {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 0.75rem;
		align-items: center;
		padding: 0.6rem 0.75rem;
		border-top: 1px solid var(--color-border);
	}
	.grammar-pattern:first-child {
		border-top: none;
	}
	.pattern-text {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.pattern-name {
		font-weight: 700;
		font-size: 0.9rem;
	}
	.pattern-roots {
		color: var(--color-muted);
		font-size: 0.8rem;
		overflow-wrap: anywhere;
	}
	.pattern-add {
		min-height: 36px;
		padding: 0.35rem 0.9rem;
		border: none;
		border-radius: var(--radius-pill);
		background: var(--color-primary);
		color: var(--color-on-primary);
		font-size: 0.8rem;
		font-weight: 600;
		cursor: pointer;
	}
	.pattern-add:disabled {
		background: var(--color-surface-2);
		color: var(--color-muted);
		cursor: not-allowed;
	}
	.grammar-error {
		color: var(--color-danger);
		font-size: 0.85rem;
		margin: 0.5rem 0 0;
	}
</style>
