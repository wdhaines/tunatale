<script lang="ts">
	/**
	 * One lesson line's cards: tap a word to blank it, tap a blank to drop it (tunatale-h0q3.67).
	 *
	 * The tokens are the server's (`sentence_lines`): this never splits words. A
	 * filled word is a card that is on; a dotted underline is one of the lesson's
	 * own turned off; `+` marks a blank the learner added. Grade uses it today and
	 * Read's Cloze… will (slice D). The stage is the parent's, changed in place.
	 */
	import { t } from '#lib/i18n/i18n.svelte.js';
	import {
		addedBlanks,
		confirmOff,
		countOn,
		keep,
		toggle,
		tokenKey,
		tokenSpan,
		WHOLE,
		type LineStage,
		type SentenceLine,
		type SentenceLineToken
	} from '#lib/grading/sentenceStage.js';

	interface Props {
		line: SentenceLine;
		stage: LineStage;
		onchange?: () => void;
	}
	let { line, stage, onchange }: Props = $props();

	type Piece = { kind: 'gap'; text: string } | { kind: 'token'; token: SentenceLineToken };
	const pieces = $derived.by((): Piece[] => {
		const out: Piece[] = [];
		let pos = 0;
		for (const token of line.tokens) {
			if (token.start > pos) out.push({ kind: 'gap', text: line.text.slice(pos, token.start) });
			out.push({ kind: 'token', token });
			pos = token.end;
		}
		if (pos < line.text.length) out.push({ kind: 'gap', text: line.text.slice(pos) });
		return out;
	});

	function tap(key: string) {
		toggle(line, stage, key);
		onchange?.();
	}

	function cardClass(key: string): string {
		const card = stage.cards[key];
		if (!card) return '';
		if (!card.on) return 'declined';
		return card.origin === 'mine' ? 'on mine' : 'on';
	}

	/** What the reviewed card the line asks about is called: its word, or the whole-sentence card. */
	function cardName(key: string): string {
		if (key === WHOLE) return t('sentenceLine.wholeCard');
		const [s, e] = key.split('-').map(Number);
		return line.text.slice(s, e);
	}
	const count = $derived(countOn(stage));
	const added = $derived(addedBlanks(line, stage));
	const wholeOn = $derived(Boolean(stage.cards[WHOLE]?.on));
</script>

<div class="line" data-line-key={line.line_key}>
	<div class="line-top">
		<span class="who">{line.speaker}</span>
		<span class="ncards">{t('sentenceLine.cards', { count })}</span>
	</div>
	<div class="say">
		{#each pieces as piece, i (i)}
			{#if piece.kind === 'gap'}
				<span class="punct">{piece.text}</span>
			{:else}
				{@const token = piece.token}
				{@const key = tokenKey(token)}
				{@const [s, e] = tokenSpan(token)}
				{#if token.part}
					<button class="tok" aria-pressed={Boolean(stage.cards[key]?.on)} onclick={() => tap(key)}
						>{line.text.slice(token.start, s)}<span class="part {cardClass(key)}">{line.text.slice(s, e)}</span
						>{line.text.slice(e, token.end)}</button
					>
				{:else}
					<button
						class="tok {cardClass(key)}"
						aria-pressed={Boolean(stage.cards[key]?.on)}
						onclick={() => tap(key)}>{line.text.slice(s, e)}</button
					>
				{/if}
			{/if}
		{/each}
	</div>
	<div class="en">{line.english}</div>
	{#each added as blank (blank.key)}
		<div class="cap">
			<b>{blank.word}+</b>
			{#if blank.gloss}{blank.gloss}{:else}<i>{t('sentenceLine.glossAtSave')}</i>{/if}
		</div>
	{/each}
	<button class="whole" aria-pressed={wholeOn} onclick={() => tap(WHOLE)}
		>{wholeOn ? t('sentenceLine.wholeOn') : t('sentenceLine.whole')}</button
	>
	{#if stage.refuse}
		<div class="box" role="alert">{t('sentenceLine.keepsOne')}</div>
	{/if}
	{#if stage.ask !== null}
		<div class="box danger" role="alert">
			<span>{t('sentenceLine.reviewed', { card: cardName(stage.ask) })}</span>
			<div class="btns">
				<button
					class="btn"
					onclick={() => {
						keep(stage);
						onchange?.();
					}}>{t('sentenceLine.keep')}</button
				>
				<button
					class="btn danger"
					onclick={() => {
						confirmOff(stage);
						onchange?.();
					}}>{t('sentenceLine.turnOff')}</button
				>
			</div>
		</div>
	{/if}
</div>

<style>
	.line {
		padding: 0.55rem 0 0.6rem;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.line-top {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.5rem;
	}
	.who,
	.ncards {
		font-size: 0.75rem;
		color: var(--color-muted);
	}
	.ncards {
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.say {
		font-size: 1.05rem;
		line-height: 2.2;
	}
	.tok {
		font: inherit;
		color: inherit;
		background: none;
		border: 0;
		padding: 0.3rem 0.14rem;
		margin: 0;
		border-radius: 6px;
		cursor: pointer;
		-webkit-tap-highlight-color: transparent;
	}
	.tok.on,
	.part.on {
		background: color-mix(in srgb, var(--color-primary) 18%, transparent);
		color: var(--color-primary);
		font-weight: 650;
	}
	.tok.on {
		padding-inline: 0.3rem;
	}
	.part {
		border-radius: 5px;
		padding: 0.3rem 0.1rem;
	}
	.tok.mine::after,
	.part.mine::after {
		content: '+';
		font-size: 0.62em;
		vertical-align: super;
		margin-left: 1px;
	}
	.tok.declined,
	.part.declined {
		text-decoration: underline dotted var(--color-muted);
		text-underline-offset: 4px;
	}
	.punct,
	.en {
		color: var(--color-muted);
	}
	.en,
	.cap {
		font-size: 0.8rem;
	}
	.cap b {
		color: var(--color-primary);
	}
	.cap i {
		color: var(--color-muted);
	}
	.whole {
		align-self: flex-start;
		font: inherit;
		font-size: 0.75rem;
		padding: 0.3rem 0.6rem;
		border-radius: 999px;
		cursor: pointer;
		border: 1px solid var(--color-border);
		background: none;
		color: var(--color-muted);
		margin-top: 0.15rem;
	}
	.whole[aria-pressed='true'] {
		border-color: var(--color-primary);
		color: var(--color-primary);
		background: color-mix(in srgb, var(--color-primary) 10%, transparent);
	}
	.box {
		font-size: 0.8rem;
		border: 1px solid var(--color-warning);
		border-radius: 8px;
		padding: 0.45rem 0.55rem;
		margin-top: 0.25rem;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.box.danger {
		border-color: var(--color-danger);
	}
	.btns {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
	}
	.btn {
		font: inherit;
		font-size: 0.8rem;
		padding: 0.4rem 0.7rem;
		border-radius: 8px;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		cursor: pointer;
	}
	.btn.danger {
		background: var(--color-danger);
		border-color: var(--color-danger);
		color: #fff;
	}
</style>
