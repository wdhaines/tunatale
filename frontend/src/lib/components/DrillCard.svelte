<script lang="ts">
	import type { SRSItemDetail } from '$lib/api';

	type Rating = 'again' | 'hard' | 'good' | 'easy';

	let {
		item,
		direction,
		onRate
	}: {
		item: SRSItemDetail;
		direction: 'recognition' | 'production';
		onRate: (rating: Rating, timeMs: number) => Promise<void>;
	} = $props();

	// The L2 headword shown to the learner: gender article prefixed (e.g. "en
	// orden"), and the part of speech appended in parens ("fange (noun)") only
	// when the backend flagged the surface as ambiguous. Empty article/pos
	// collapse to the bare word, so non-gendered languages are unaffected.
	const headword = $derived(`${item.article ? item.article + ' ' : ''}${item.text}`);
	const posLabel = $derived(item.pos ? ` (${item.pos})` : '');

	// Rich back-of-card fields, grouped by where they render on the answer:
	// summary inline, details inside one collapsed disclosure, deep (the verbose
	// dictionary entry) behind its own nested disclosure.
	const summaryExtras = $derived((item.extras ?? []).filter((e) => e.tier === 'summary'));
	const detailExtras = $derived((item.extras ?? []).filter((e) => e.tier === 'details'));
	const deepExtras = $derived((item.extras ?? []).filter((e) => e.tier === 'deep'));

	let revealed = $state(false);
	let inFlight = $state(false);
	let audioEl: HTMLAudioElement | undefined = $state();
	let wordAudioEl: HTMLAudioElement | undefined = $state();
	const startedAt = performance.now();

	function show() {
		revealed = true;
	}

	async function rate(r: Rating) {
		if (inFlight) return;
		inFlight = true;
		try {
			const elapsed = Math.min(60000, Math.round(performance.now() - startedAt));
			await onRate(r, elapsed);
		} finally {
			inFlight = false;
		}
	}

	const RATING_KEYS: Record<string, Rating> = {
		'1': 'again',
		'2': 'hard',
		'3': 'good',
		'4': 'easy'
	};

	const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

	function isTypingTarget(target: EventTarget | null): boolean {
		if (!(target instanceof HTMLElement)) return false;
		if (TYPING_TAGS.has(target.tagName)) return true;
		return target.getAttribute('contenteditable') === 'true';
	}

	function handleKeyDown(event: KeyboardEvent) {
		if (event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;
		if (isTypingTarget(event.target)) return;
		if (inFlight) return;

		if (!revealed) {
			if (event.key === ' ' || event.key === 'Enter') {
				event.preventDefault();
				show();
			}
			return;
		}

		if (event.key === ' ' || event.key === 'Enter') {
			event.preventDefault();
			rate('good');
			return;
		}

		const rating = RATING_KEYS[event.key];
		if (rating) {
			event.preventDefault();
			rate(rating);
		}
	}

	function playAudio() {
		audioEl?.play().catch(() => {});
	}

	function playWordAudio() {
		wordAudioEl?.play().catch(() => {});
	}

	function escapeRegex(s: string): string {
		return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	}

	// Cloze markup can repeat: make_morphology_cloze_text wraps *every* occurrence
	// of the surface, so a surface appearing twice yields two {{c1::...}} spans.
	// Replace them all (global regex), and use replacer functions so `$`/`&` in a
	// surface or hint can't be reinterpreted as replacement-string specials.
	function clozePromptHtml(): string {
		const sent = item.source_sentence!;
		if (/\{\{c1::.+?\}\}/.test(sent)) {
			return sent
				.replace(/\{\{c1::(.+?)::(.+?)\}\}/g, () => '[...]')
				.replace(/\{\{c1::(.+?)\}\}/g, () => '[...]');
		}
		const escaped = escapeRegex(item.text);
		const re = new RegExp(`(?<!\\p{L})${escaped}(?!\\p{L})`, 'giu');
		return sent.replace(re, '[...]');
	}

	function clozeAnswerHtml(): string {
		const sent = item.source_sentence!;
		if (/\{\{c1::.+?\}\}/.test(sent)) {
			return sent
				.replace(
					/\{\{c1::(.+?)::(.+?)\}\}/g,
					(_m, surface) => `<mark class="cloze-answer">${surface}</mark>`
				)
				.replace(
					/\{\{c1::(.+?)\}\}/g,
					(_m, surface) => `<mark class="cloze-answer">${surface}</mark>`
				);
		}
		const escaped = escapeRegex(item.text);
		const re = new RegExp(`(?<!\\p{L})${escaped}(?!\\p{L})`, 'giu');
		return sent.replace(re, '<mark class="cloze-answer">$&</mark>');
	}
</script>

<svelte:window onkeydown={handleKeyDown} />

{#snippet extraField(e: { label: string; html: string })}
	<div class="extra-field">
		<span class="extra-eyebrow">{e.label}</span>
		<div class="extra-value">{@html e.html}</div>
	</div>
{/snippet}

{#snippet backExtras()}
	{#if summaryExtras.length > 0 || detailExtras.length > 0 || deepExtras.length > 0}
		<div class="extras">
			{#if summaryExtras.length > 0}
				<div class="extras-summary">
					{#each summaryExtras as e (e.label)}
						{@render extraField(e)}
					{/each}
				</div>
			{/if}
			{#if detailExtras.length > 0 || deepExtras.length > 0}
				<details class="extras-details">
					<summary><span class="disc-label">Details</span></summary>
					<div class="extras-body">
						{#each detailExtras as e (e.label)}
							{@render extraField(e)}
						{/each}
						{#each deepExtras as e (e.label)}
							<details class="extra-deep">
								<summary><span class="disc-label">{e.label}</span></summary>
								<div class="extra-value extra-dict">{@html e.html}</div>
							</details>
						{/each}
					</div>
				</details>
			{/if}
		</div>
	{/if}
{/snippet}

<div class="drill-card">
	<div class="prompt" class:revealed>
		{#if direction === 'recognition'}
			{#if item.audio_url}
				<audio bind:this={audioEl} src={item.audio_url} autoplay preload="auto"></audio>
				<button class="play-btn" onclick={playAudio} aria-label="Play audio">▶</button>
			{/if}
			<p class="main-text slovene">{headword}{posLabel}</p>
		{:else if direction === 'production'}
			{#if item.card_type === 'cloze' && item.source_sentence}
				<p class="main-text">{@html clozePromptHtml()}</p>
			{:else if item.image_url != null}
				<img src={item.image_url} alt={item.translation} class="prompt-image" />
			{:else}
				<p class="main-text">{item.translation}</p>
			{/if}
		{/if}
	</div>

	{#if revealed}
		<hr class="answer-divider" />
		<div class="answer">
			{#if direction === 'recognition'}
				{#if item.image_url != null}
					<img src={item.image_url} alt={item.translation} class="answer-image" />
				{/if}
				<p class="answer-text english">{item.translation}</p>
				{#if item.grammar}
					<div class="gram">{@html item.grammar}</div>
				{/if}
				{#if item.note}
					<div class="note">{@html item.note}</div>
				{/if}
				{@render backExtras()}
			{:else if direction === 'production'}
				{#if item.card_type === 'cloze' && item.source_sentence}
					{#if item.audio_url}
						<audio bind:this={audioEl} src={item.audio_url} autoplay preload="auto"></audio>
						<button class="play-btn" onclick={playAudio} aria-label="Play audio">▶</button>
					{/if}
					<p class="main-text">{@html clozeAnswerHtml()}</p>
					{#if item.word_audio_url}
						<audio bind:this={wordAudioEl} src={item.word_audio_url} preload="auto"></audio>
						<button class="word-audio-btn" onclick={playWordAudio} aria-label="Play word audio">🔊 {item.text}</button>
					{/if}
					{#if item.source_sentence_translation}
						<p class="answer-text english">{item.source_sentence_translation}</p>
					{/if}
				{:else}
					{#if item.audio_url}
						<audio bind:this={audioEl} src={item.audio_url} autoplay preload="auto"></audio>
						<button class="play-btn" onclick={playAudio} aria-label="Play audio">▶</button>
					{/if}
					<p class="answer-text slovene">{headword}{posLabel}</p>
				{/if}
				<p class="answer-text english">{item.translation}</p>
				{#if item.grammar}
					<div class="gram">{@html item.grammar}</div>
				{/if}
				{#if item.note}
					<div class="note">{@html item.note}</div>
				{/if}
				{@render backExtras()}
			{/if}
		</div>
		<div class="ratings">
			<button class="btn-again" onclick={() => rate('again')}>Again</button>
			<button class="btn-hard" onclick={() => rate('hard')}>Hard</button>
			<button class="btn-good" onclick={() => rate('good')}>Good</button>
			<button class="btn-easy" onclick={() => rate('easy')}>Easy</button>
		</div>
	{:else}
		<button onclick={show}>Show</button>
	{/if}
	<p class="key-hint">Space to flip · 1–4 to grade</p>
</div>

<style>
	.drill-card {
		text-align: center;
		padding: 0.75rem;
	}
	.main-text {
		font-size: 1.5rem;
		font-weight: bold;
		margin-bottom: 0.5rem;
	}
	.prompt-image {
		max-width: 100%;
		max-height: 32vh;
		max-height: 32dvh;
		width: auto;
		height: auto;
		object-fit: contain;
		border-radius: 8px;
		margin-bottom: 0.5rem;
		transition: max-height 0.2s ease, opacity 0.2s ease;
	}
	/* Once revealed, the answer text is the focus — shrink and fade the prompt
	   image so it stops dominating the card. */
	.prompt.revealed .prompt-image {
		max-height: 18vh;
		max-height: 18dvh;
		opacity: 0.6;
	}
	.answer-divider {
		border: none;
		border-top: 1px solid var(--color-border, #ccc);
		margin: 0.75rem 0;
	}
	.answer-image {
		max-width: 100%;
		max-height: 32vh;
		max-height: 32dvh;
		width: auto;
		height: auto;
		object-fit: contain;
		border-radius: 8px;
		margin-bottom: 0.5rem;
	}
	.answer-text {
		font-size: 1rem;
		color: var(--color-muted);
		margin-bottom: 0.4rem;
	}
	/* The first answer line is the primary answer (e.g. the target word) — make
	   it the visually dominant element on the revealed card. */
	.answer-text:first-of-type {
		font-size: 1.5rem;
		font-weight: bold;
		color: var(--color-text);
		margin-bottom: 0.5rem;
	}
	.slovene {
		/* Semantic hook for per-language typography — currently inherits .main-text */
		font: inherit;
	}
	.english {
		/* Semantic hook for per-language typography — currently inherits .answer-text */
		font: inherit;
	}
	.gram {
		font-size: 0.9rem;
		color: var(--color-muted);
		font-style: italic;
		margin-bottom: 0.5rem;
	}
	.note {
		font-size: 0.85rem;
		color: var(--color-muted);
		margin-bottom: 0.75rem;
	}
	/* Rich back-of-card fields (IPA, inflections, dictionary entry…) live in a
	   contained, tinted "reference" panel. The fill + border set it apart from
	   the centered answer as a clearly secondary layer, while text inside is
	   left-aligned so definitions, tables and lists stay readable (Anki centers
	   these, which reads poorly for prose). */
	.extras {
		margin: 1rem auto 0;
		padding: 0.7rem 0.85rem;
		background: var(--color-surface-2);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		text-align: left;
	}
	.extra-field {
		margin-bottom: 0.6rem;
	}
	.extra-field:last-child {
		margin-bottom: 0;
	}
	/* Tiny eyebrow label above each field — structure without shouting. */
	.extra-eyebrow {
		display: block;
		font-size: 0.66rem;
		font-weight: 700;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--color-muted);
		margin-bottom: 0.15rem;
	}
	.extra-value {
		font-size: 0.95rem;
		line-height: 1.5;
		color: var(--color-text);
	}
	/* Disclosures: understated toggles with a chevron that rotates when open. */
	.extras-summary + .extras-details {
		margin-top: 0.7rem;
		border-top: 1px solid var(--color-border);
		padding-top: 0.6rem;
	}
	.extras-details > summary,
	.extra-deep > summary {
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 0.45rem;
		list-style: none;
	}
	.extras-details > summary::-webkit-details-marker,
	.extra-deep > summary::-webkit-details-marker {
		display: none;
	}
	.extras-details > summary::before,
	.extra-deep > summary::before {
		content: '▸';
		color: var(--color-primary);
		font-size: 1rem;
		line-height: 1;
		transition: transform 0.15s ease;
	}
	.extras-details[open] > summary::before,
	.extra-deep[open] > summary::before {
		transform: rotate(90deg);
	}
	.disc-label {
		font-size: 0.76rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--color-primary);
	}
	.extras-details > summary:hover .disc-label,
	.extra-deep > summary:hover .disc-label {
		text-decoration: underline;
	}
	.extras-body {
		margin-top: 0.7rem;
	}
	.extra-deep {
		margin-top: 0.6rem;
	}
	/* The verbose dictionary entry expands in full when opened — it's already
	   triple-gated (reveal → Details → Dictionary entry), so an inner scroll
	   would just hide content behind an undiscoverable affordance. */
	.extra-dict {
		margin-top: 0.5rem;
	}
	/* Anki ships inflection/comparison fields as small bordered tables; restyle
	   them (and headings/lists in the dictionary entry) now that the inline
	   <style> Anki carried is stripped. */
	.extra-value :global(table) {
		border-collapse: collapse;
		margin: 0.3rem 0;
	}
	.extra-value :global(td),
	.extra-value :global(th) {
		border: 1px solid var(--color-border);
		padding: 0.2rem 0.5rem;
		text-align: center;
	}
	.extra-value :global(h2) {
		font-size: 1.05rem;
		margin: 0.4rem 0 0.2rem;
	}
	.extra-value :global(h3) {
		font-size: 0.9rem;
		margin: 0.5rem 0 0.2rem;
	}
	.extra-value :global(p) {
		margin: 0.3rem 0;
	}
	.extra-value :global(ul),
	.extra-value :global(ol) {
		padding-left: 1.2rem;
		margin: 0.25rem 0;
	}
	:global(.cloze-answer) {
		background: var(--color-highlight, #fff3cd);
		padding: 0.1em 0.3em;
		border-radius: 3px;
		font-weight: bold;
	}
	.play-btn {
		background: var(--color-text, #333);
		border: 1px solid var(--color-text, #333);
		border-radius: 50%;
		width: 36px;
		height: 36px;
		cursor: pointer;
		font-size: 1rem;
		color: var(--color-bg, #fff);
		margin-bottom: 0.4rem;
		display: inline-flex;
		align-items: center;
		justify-content: center;
	}
	.word-audio-btn {
		background: var(--color-primary);
		border: 1px solid var(--color-primary);
		border-radius: var(--radius-pill);
		cursor: pointer;
		font-size: 0.85rem;
		color: var(--color-on-primary);
		margin-bottom: 0.75rem;
		padding: 0.25rem 0.6rem;
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.ratings {
		display: flex;
		justify-content: center;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin-top: 1rem;
	}
	button {
		margin-top: 0.75rem;
		padding: 0.5rem 1.25rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border: none;
		border-radius: var(--radius-pill);
		font-weight: 600;
		cursor: pointer;
	}
	/* Mobile-first: ratings are a 2-up grid with big touch targets; images are
	   capped by viewport height (not a fixed px box) so they shrink on short
	   screens and never push the buttons off-screen, whatever the aspect ratio. */
	.ratings button {
		margin-top: 0;
		flex: 1 1 calc(50% - 0.5rem);
		min-height: 44px;
		font-size: 1rem;
	}
	.btn-again { background: var(--color-danger); }
	.btn-hard  { background: var(--color-warning); }
	.btn-good  { background: var(--color-success); }
	.btn-easy  { background: var(--color-primary); }

	.key-hint {
		margin: 0.75rem 0 0;
		font-size: 0.75rem;
		color: var(--color-muted);
	}
	/* Touch-primary devices have no keyboard, so the hint is just clutter. */
	@media (hover: none) {
		.key-hint {
			display: none;
		}
	}

	@media (min-width: 641px) {
		.drill-card { padding: 2rem 1rem; }
		.main-text { font-size: 2rem; margin-bottom: 1rem; }
		.prompt-image,
		.answer-image {
			max-width: 240px;
			max-height: 240px;
			margin-bottom: 1rem;
		}
		.answer-divider { margin: 1.5rem 0; }
		.answer-text { font-size: 1.1rem; margin-bottom: 0.75rem; }
		.answer-text:first-of-type { font-size: 1.75rem; margin-bottom: 0.75rem; }
		.prompt.revealed .prompt-image { max-height: 120px; }
		.play-btn { margin-bottom: 0.75rem; }
		.ratings { gap: 0.75rem; }
		.ratings button { flex: 0 1 auto; }
	}
</style>
