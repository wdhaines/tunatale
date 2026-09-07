<script lang="ts">
	import type { SRSItemDetail, ClozeSentenceVerdict } from '$lib/api';
	import { api } from '$lib/api';

	let { item, onclose, onupdated }: {
		item: SRSItemDetail;
		onclose: () => void;
		onupdated: () => void;
	} = $props();

	// The three states this dialog can be in, and none of them has written
	// anything yet (tunatale-keb0). The write happens on "Use this sentence"
	// alone — the earlier version wrote on the single click that produced the
	// suggestion, which is what made asking for one a gamble.
	let current = $state<ClozeSentenceVerdict | null>(null);
	let candidate = $state<ClozeSentenceVerdict | null>(null);
	let recommended = $state(false);
	let proposing = $state(false);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let noCandidate = $state(false);

	// The edit box starts on the stored sentence, so opening the dialog and
	// fixing one word by hand never requires asking the model at all. Writable
	// $derived, not $state + $effect: typing and accepting a proposal both
	// override it, and the seed re-applies if the item itself changes.
	let draft = $derived(item.source_sentence ?? '');

	const dirty = $derived(draft.trim() !== '' && draft.trim() !== (item.source_sentence ?? '').trim());

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) onclose();
	}

	/** Render `{{c1::han}} kommer` as `___ kommer` — what the learner is shown. */
	function asBlank(sentence: string): string {
		return sentence.replace(/\{\{c\d+::.*?\}\}/g, '___');
	}

	/** Say what fits, in words. The competitor LIST is the useful part, not the
	 * bare status: "underdetermined" is a normal verdict for the closed-class
	 * words this feature exists for, and a reader judges by how many. */
	function verdictLine(v: ClozeSentenceVerdict): string {
		if (v.status === 'determined') return `Only “${item.text}” fits this blank.`;
		if (v.status === 'unknown') return 'The model gave no usable verdict on this blank.';
		if (v.competitors.length === 0) return 'More than one word fits this blank.';
		return `${v.competitors.length} other word${v.competitors.length === 1 ? '' : 's'} also fit: ${v.competitors.join(', ')}`;
	}

	async function propose() {
		proposing = true;
		error = null;
		noCandidate = false;
		try {
			const result = await api.proposeClozeSentence(item.id);
			current = result.current;
			recommended = result.recommended;
			if (result.candidate === null) {
				// Reported, not swallowed. Silently doing nothing here reads as a
				// broken button, and a rate-limited free tier makes this common.
				noCandidate = true;
				return;
			}
			candidate = result.candidate;
			draft = result.candidate.sentence;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			proposing = false;
		}
	}

	async function save() {
		saving = true;
		error = null;
		try {
			await api.setClozeSentence(item.id, draft.trim());
			onupdated();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="backdrop" role="dialog" tabindex="-1" aria-label="Cloze sentence" onclick={handleBackdropClick} onkeydown={handleKeydown}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="modal" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()} tabindex="-1">
		<div class="modal-header">
			<h2>Cloze sentence — {item.text}</h2>
			<button class="close-btn" onclick={onclose} aria-label="Close">&times;</button>
		</div>

		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}

		<section>
			<h3>Current</h3>
			<p class="sentence">{asBlank(item.source_sentence ?? '')}</p>
			{#if item.source_sentence_translation}
				<p class="english">{item.source_sentence_translation}</p>
			{/if}
			{#if current}
				<p class="verdict" class:bad={current.status !== 'determined'}>{verdictLine(current)}</p>
			{/if}
		</section>

		{#if candidate}
			<section class="proposed">
				<h3>Proposed {#if recommended}<span class="pill">recommended</span>{/if}</h3>
				<p class="sentence">{asBlank(candidate.sentence)}</p>
				{#if candidate.translation}
					<p class="english">{candidate.translation}</p>
				{/if}
				<p class="verdict" class:bad={candidate.status !== 'determined'}>{verdictLine(candidate)}</p>
			</section>
		{:else if noCandidate}
			<p class="muted">No new sentence came back — try again, or edit the sentence below by hand.</p>
		{/if}

		<section>
			<h3><label for="cloze-draft">Sentence to store</label></h3>
			<!-- The word may be typed plainly; the server wraps it in {{c1::…}}.
			     This is why a near-miss suggestion is fixable without re-rolling. -->
			<textarea id="cloze-draft" bind:value={draft} rows="3" disabled={saving}></textarea>
			<p class="hint">
				Must contain “{item.text}”. The English and the sentence audio are
				regenerated for whatever you store; Anki is updated on the next sync.
			</p>
		</section>

		<div class="actions">
			<button onclick={propose} disabled={proposing || saving}>
				{proposing ? 'Asking…' : candidate ? 'Suggest another' : 'Suggest a sentence'}
			</button>
			<span class="spacer"></span>
			<button onclick={onclose} disabled={saving}>Cancel</button>
			<button class="primary" onclick={save} disabled={saving || !dirty}>
				{saving ? 'Saving…' : 'Use this sentence'}
			</button>
		</div>
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		z-index: 100;
		display: flex;
		align-items: center;
		justify-content: center;
		background: rgba(0, 0, 0, 0.5);
	}
	.modal {
		background: var(--color-surface);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow);
		padding: 1.5rem;
		max-width: 560px;
		width: 90vw;
		max-height: 80vh;
		overflow-y: auto;
	}
	.modal-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 1rem;
		margin-bottom: 0.5rem;
	}
	h2 {
		margin: 0;
		font-size: 1.05rem;
	}
	h3 {
		margin: 0 0 0.25rem;
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-muted);
	}
	section {
		margin-bottom: 1rem;
	}
	.proposed {
		border-left: 3px solid var(--color-accent, #4a8);
		padding-left: 0.75rem;
	}
	.close-btn {
		background: none;
		border: none;
		font-size: 1.4rem;
		line-height: 1;
		cursor: pointer;
		color: inherit;
	}
	.sentence {
		margin: 0;
		font-size: 1.05rem;
	}
	.english {
		margin: 0.15rem 0 0;
		color: var(--color-muted);
		font-size: 0.9rem;
	}
	.verdict {
		margin: 0.35rem 0 0;
		font-size: 0.82rem;
		color: var(--color-muted);
	}
	.verdict.bad {
		color: var(--color-warning, #b06000);
	}
	.pill {
		font-size: 0.65rem;
		padding: 0.1rem 0.4rem;
		border-radius: 0.6rem;
		background: var(--color-accent, #4a8);
		color: #fff;
		letter-spacing: 0;
	}
	textarea {
		width: 100%;
		font: inherit;
		padding: 0.5rem;
		border-radius: var(--radius-sm, 0.25rem);
		border: 1px solid var(--border, #ccc);
		background: transparent;
		color: inherit;
		resize: vertical;
	}
	.hint,
	.muted {
		color: var(--color-muted);
		font-size: 0.8rem;
	}
	.hint {
		margin: 0.3rem 0 0;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 1rem;
	}
	.spacer {
		flex: 1;
	}
	.error {
		color: var(--color-danger, #c00);
	}
</style>
