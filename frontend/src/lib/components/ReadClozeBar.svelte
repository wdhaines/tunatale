<script lang="ts">
	/**
	 * Read's Cloze… (tunatale-h0q3.69): the line editor under a transcript line, where the phrase
	 * confirm bar goes. It opens with the tapped word on; Save posts that one line's edit through
	 * Grade's sentence sequence (`POST /sentence-lines`); Cancel discards.
	 *
	 * A refusal (a reviewed card, the last card of a linked line, a line changed under the screen,
	 * half a word) is named here and nothing is saved, as on Grade. A blank with no meaning is named
	 * by the parent after the save, with everything else saved.
	 */
	import { untrack } from 'svelte';
	import { api, type SentenceLinesSaveResponse } from '#lib/api.js';
	import { t } from '#lib/i18n/i18n.svelte.js';
	import SentenceLineEditor from './SentenceLineEditor.svelte';
	import {
		initStage,
		lineEdit,
		sentenceRefusal,
		toggle,
		tokenKey,
		type SentenceLine
	} from '#lib/grading/sentenceStage.js';

	interface Props {
		contentId: string;
		line: SentenceLine;
		/** The token of the word the learner tapped, or null when the transcript's words do not map. */
		tapped: number | null;
		onsaved: (result: SentenceLinesSaveResponse) => void | Promise<void>;
		oncancel: () => void;
	}
	let { contentId, line, tapped, onsaved, oncancel }: Props = $props();

	// The stage is this bar's own: it starts from the line as the server read it, with the tapped word on.
	const stage = $state(
		untrack(() => {
			const s = initStage(line);
			const token = tapped === null ? undefined : line.tokens[tapped];
			if (token && !s.cards[tokenKey(token)]?.on) toggle(line, s, tokenKey(token));
			return s;
		})
	);
	let saving = $state(false);
	let error = $state('');

	async function save() {
		const edit = lineEdit(line, stage);
		if (edit === null) {
			oncancel();
			return;
		}
		saving = true;
		error = '';
		try {
			await onsaved(await api.saveSentenceLines(contentId, [edit]));
		} catch (e) {
			const reason = sentenceRefusal(e, [404, 409, 422]);
			error = reason === null ? (e instanceof Error ? e.message : String(e)) : t('readCloze.refused', { reason });
		} finally {
			saving = false;
		}
	}
</script>

<div class="read-cloze">
	<SentenceLineEditor {line} {stage} onchange={() => (error = '')} />
	<div class="bar-actions">
		<button class="confirm-create" onclick={save} disabled={saving}
			>{saving ? t('readCloze.saving') : t('readCloze.save')}</button
		>
		<button class="confirm-cancel" onclick={oncancel} disabled={saving}>{t('readCloze.cancel')}</button>
	</div>
	{#if error}
		<p class="cloze-error" role="alert">{error}</p>
	{/if}
</div>

<style>
	/* The phrase confirm bar's frame and buttons (Transcript.svelte), since it stands in that place. */
	.read-cloze {
		margin: 0.25rem 0 0.5rem;
		padding: 0.4rem 0.75rem;
		background: rgba(99, 102, 241, 0.08);
		border: 1px solid rgba(99, 102, 241, 0.3);
		border-radius: 4px;
	}
	.bar-actions {
		display: flex;
		gap: 0.5rem;
		justify-content: flex-end;
		margin-top: 0.4rem;
	}
	.confirm-create {
		padding: 0.2rem 0.6rem;
		background: var(--color-primary, #2563eb);
		color: var(--color-on-primary, #fff);
		border: none;
		border-radius: var(--radius-pill);
		cursor: pointer;
		font-size: 0.8rem;
	}
	.confirm-cancel {
		padding: 0.2rem 0.6rem;
		background: transparent;
		color: inherit;
		border: 1px solid var(--color-muted, #9ca3af);
		border-radius: var(--radius-pill);
		cursor: pointer;
		font-size: 0.8rem;
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.cloze-error {
		margin: 0.4rem 0 0;
		color: var(--color-danger, #dc2626);
		font-size: 0.85rem;
	}
</style>
