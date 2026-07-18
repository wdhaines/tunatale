<script lang="ts">
	import { api } from '$lib/api';

	interface Props {
		curriculumId: string;
		day: number;
		onImported: (lessonId: string) => void;
	}

	let { curriculumId, day, onImported }: Props = $props();

	let copyError = $state('');
	let copyLabel = $state('');
	let pasteText = $state('');
	let importError = $state('');
	let importWarnings: string[] = $state([]);
	let importLoading = $state(false);
	let importedLessonId: string | null = $state(null);

	async function handleCopy() {
		copyError = '';
		try {
			const result = await api.getStoryPrompt(curriculumId, day);
			await navigator.clipboard.writeText(result.system_prompt + '\n\n' + result.user_prompt);
			copyLabel = 'Copied ✓';
		} catch (e) {
			copyError = e instanceof Error ? e.message : String(e);
		}
	}

	function handlePasteInput(e: Event) {
		const target = e.target as HTMLTextAreaElement;
		pasteText = target.value;
		importError = '';
		importWarnings = [];
		importedLessonId = null;
	}

	async function handleImport() {
		importError = '';
		importWarnings = [];
		importedLessonId = null;

		importLoading = true;
		try {
			const result = await api.importStory({
				curriculum_id: curriculumId,
				day,
				raw: pasteText,
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

<div class="manual-story-panel">
	<button data-testid="copy-btn" onclick={handleCopy}>
		Copy story prompt
	</button>
	{#if copyLabel}
		<span class="copied-label">{copyLabel}</span>
	{/if}
	{#if copyError}
		<p class="error">{copyError}</p>
	{/if}

	<div class="import-area">
		<textarea
			placeholder="Paste story JSON here…"
			value={pasteText}
			oninput={handlePasteInput}
			rows={8}
		></textarea>

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
</div>

<style>
	.manual-story-panel {
		margin-top: 1rem;
		padding: 1rem;
		border: 1px solid var(--color-border, #ccc);
		border-radius: 6px;
		font-size: 0.9rem;
	}
	.manual-story-panel > button {
		padding: 0.35rem 0.85rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		font-size: 0.82rem;
		font-weight: 600;
		cursor: pointer;
	}
	.manual-story-panel > button:hover {
		background: var(--color-primary-hover);
	}
	.copied-label {
		margin-left: 0.5rem;
		font-size: 0.8rem;
		color: var(--color-success);
		font-weight: 600;
	}
	.import-area {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		margin-top: 0.75rem;
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
		margin: 0.25rem 0 0;
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
