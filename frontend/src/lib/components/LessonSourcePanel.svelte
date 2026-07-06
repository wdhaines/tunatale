<script lang="ts">
	import { api } from '$lib/api';
	import type { StorySourceResponse } from '$lib/api';
	import { formatSource, buildClaudePrompt } from '$lib/lessonSource';

	interface Props {
		lessonId: string;
		curriculumId: string;
		day: number;
		onImported: (newLessonId: string) => void;
	}

	let { lessonId, curriculumId, day, onImported }: Props = $props();

	let _open = $state(false);
	let source: StorySourceResponse | null = $state(null);
	let sourceLoading = $state(false);
	let sourceError = $state('');
	let _fetched = $state(false);

	$effect(() => {
		if (!_open) return;
		if (_fetched) return;
		_fetched = true;
		sourceLoading = true;
		sourceError = '';
		api.getStorySource(lessonId).then((s) => {
			source = s;
		}).catch((e) => {
			sourceError = e instanceof Error ? e.message : String(e);
		}).finally(() => {
			sourceLoading = false;
		});
	});

	let pasteText = $state('');
	let validationError = $state('');
	let importError = $state('');
	let importWarnings: string[] = $state([]);
	let importLoading = $state(false);
	// Set when an import succeeded WITH warnings: navigation is deferred so the
	// user can actually read them; the continue button hands off to onImported.
	let importedLessonId: string | null = $state(null);

	let copyLabel = $state('');

	async function copyJson() {
		await navigator.clipboard.writeText(formatSource(source!.story));
		copyLabel = 'Copied ✓';
	}

	async function copyPrompt() {
		await navigator.clipboard.writeText(buildClaudePrompt(source!.story));
		copyLabel = 'Copied ✓';
	}

	function handlePasteInput(e: Event) {
		const target = e.target as HTMLTextAreaElement;
		pasteText = target.value;
		validationError = '';
		importError = '';
		importWarnings = [];
		importedLessonId = null;
	}

	async function handleImport() {
		validationError = '';
		importError = '';
		importWarnings = [];
		importedLessonId = null;

		let parsed: Record<string, unknown>;
		try {
			parsed = JSON.parse(pasteText);
		} catch {
			validationError = 'Invalid JSON — check the syntax and try again.';
			return;
		}

		importLoading = true;
		try {
			const result = await api.importStory({
				curriculum_id: curriculumId,
				day,
				story: parsed,
			});
			if (result.warnings.length > 0) {
				importWarnings = result.warnings;
				importedLessonId = result.id;
			} else {
				onImported(result.id);
			}
		} catch (e) {
			importError = e instanceof Error ? e.message : String(e);
		} finally {
			importLoading = false;
		}
	}
</script>

<details class="lesson-source-panel" bind:open={_open}>
	<summary>Lesson source — edit with Claude</summary>

	{#if sourceLoading}
		<p class="muted">Loading source…</p>
	{:else if sourceError}
		<p class="error">{sourceError}</p>
	{:else if source}
		<div class="source-actions">
			<button data-testid="copy-json" onclick={copyJson}>
				Copy JSON
			</button>
			<button data-testid="copy-prompt" onclick={copyPrompt}>
				Copy prompt for Claude
			</button>
			{#if copyLabel}
				<span class="copied-label">{copyLabel}</span>
			{/if}
		</div>

		<pre class="source-view">{formatSource(source.story)}</pre>

		<div class="import-area">
			<textarea
				placeholder="Paste edited JSON here…"
				value={pasteText}
				oninput={handlePasteInput}
				rows={8}
			></textarea>

			{#if validationError}
				<p class="error">{validationError}</p>
			{/if}

			{#if importError}
				<p class="error">{importError}</p>
			{/if}

			{#if importWarnings.length > 0}
				<ul class="warnings">
				{#each importWarnings as w (w)}
					<li>{w}</li>
				{/each}
				</ul>
			{/if}

			{#if importedLessonId}
				<button
					data-testid="continue-btn"
					onclick={() => onImported(importedLessonId!)}
				>
					Continue to imported lesson →
				</button>
			{:else}
				<button
					data-testid="import-btn"
					onclick={handleImport}
					disabled={importLoading || !pasteText.trim()}
				>
					{importLoading ? 'Importing…' : 'Import'}
				</button>
			{/if}
		</div>
	{/if}
</details>

<style>
	.lesson-source-panel {
		margin-top: 1rem;
		font-size: 0.9rem;
	}
	.lesson-source-panel summary {
		cursor: pointer;
		font-weight: 600;
		color: var(--color-muted);
	}
	.lesson-source-panel[open] summary {
		margin-bottom: 0.75rem;
	}
	.source-actions {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		margin-bottom: 0.5rem;
		flex-wrap: wrap;
	}
	.source-actions button {
		padding: 0.35rem 0.85rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		font-size: 0.82rem;
		font-weight: 600;
		cursor: pointer;
	}
	.source-actions button:hover {
		background: var(--color-primary-hover);
	}
	.copied-label {
		font-size: 0.8rem;
		color: var(--color-success);
		font-weight: 600;
	}
	.source-view {
		background: var(--color-surface-2);
		padding: 0.75rem;
		border-radius: 6px;
		font-size: 0.78rem;
		overflow-x: auto;
		max-height: 400px;
		overflow-y: auto;
		white-space: pre;
		margin: 0 0 0.75rem;
	}
	.import-area {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.import-area textarea {
		width: 100%;
		padding: 0.5rem;
		border: 1px solid var(--color-border, #ccc);
		border-radius: 4px;
		font-family: monospace;
		font-size: 0.82rem;
		resize: vertical;
		box-sizing: border-box;
	}
	.import-area button {
		align-self: flex-start;
		padding: 0.4rem 1rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.import-area button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.import-area button:hover:not(:disabled) {
		background: var(--color-primary-hover);
	}
	.error {
		color: var(--color-danger);
		margin: 0;
		font-size: 0.85rem;
	}
	.muted {
		color: var(--color-muted);
		font-style: italic;
		font-size: 0.85rem;
	}
	.warnings {
		margin: 0;
		padding: 0 0 0 1.25rem;
		font-size: 0.82rem;
		color: var(--color-warning, #b8860b);
	}
	.warnings li {
		margin: 0.15rem 0;
	}
</style>
