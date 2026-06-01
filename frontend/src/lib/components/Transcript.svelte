<script lang="ts">
	import WordSpan from '$lib/WordSpan.svelte';
	import Tooltip from './Tooltip.svelte';
	import { api } from '$lib/api';
	import type { LessonDetail, TranscriptData, WordToken } from '$lib/api';
	import { buildScenes, fallbackScenes } from '$lib/transcriptScenes';

	interface CreatePhraseArgs {
		text: string;
		word_count: number;
		translation: string;
		lineIndex: number;
		startIdx: number;
		endIdx: number;
		source_sentence?: string;
		source_lesson_id?: string;
		source_line_index?: number;
	}

	interface Props {
		transcript: TranscriptData;
		lesson?: LessonDetail;
		isListened: boolean;
		listenLoading: boolean;
		listenResult: { registered: number } | null;
		error: string;
		onStateChange?: (lemma: string, srs_item_id: number | null) => void;
		onCollocationStateChange?: (lemma: string, span_id: number, current_state: string) => void;
		onMarkListened: () => void;
		onCreatePhrase?: (args: CreatePhraseArgs) => void | Promise<void>;
	}

	let {
		transcript,
		lesson,
		isListened,
		listenLoading,
		listenResult,
		error,
		onStateChange,
		onCollocationStateChange,
		onMarkListened,
		onCreatePhrase
	}: Props = $props();

	type WordSegment = { type: 'word'; word: WordToken } | { type: 'collocation'; words: WordToken[]; span_id: number };

	// --- Selection state ---
	let selectionMode = $state(false);
	let isDragging = $state(false);
	let selection = $state<{ lineIndex: number; startIdx: number; endIdx: number } | null>(null);
	let dragAnchor = $state<{ lineIndex: number; wordIdx: number } | null>(null);
	let pendingTranslation = $state('');

	let translateLoading = $state(false);
	let translateError = $state('');

	// Add-phrase section state
	let showAddPhrase = $state(false);
	let addPhraseText = $state('');
	let addPhraseTranslation = $state('');
	let addPhraseLoading = $state(false);
	let addPhraseError = $state('');

	// Progressive-disclosure toggles for variations
	let showSlow = $state(false);
	let showTranslation = $state(false);

	function resetSelection() {
		selection = null;
		dragAnchor = null;
		isDragging = false;
		pendingTranslation = '';
	}

	function toggleSelectionMode() {
		selectionMode = !selectionMode;
		resetSelection();
	}

	function wordIsSelected(lineIndex: number, wordIdx: number): boolean {
		if (!selection) return false;
		if (selection.lineIndex !== lineIndex) return false;
		return wordIdx >= selection.startIdx && wordIdx <= selection.endIdx;
	}

	function hasOverlap(words: WordToken[], start: number, end: number): boolean {
		for (let i = start; i <= end; i++) {
			if (words[i]?.collocation_span_id !== null) return true;
		}
		return false;
	}

	function resolveWordTarget(e: PointerEvent | MouseEvent): { lineIndex: number; wordIdx: number } | null {
		const target = e.target as HTMLElement;
		const wordEl = target.closest('[data-word-index]') as HTMLElement | null;
		if (!wordEl) return null;
		const wordIdx = parseInt(wordEl.getAttribute('data-word-index') ?? '', 10);
		const lineIdx = parseInt(wordEl.getAttribute('data-line-index') ?? '', 10);
		if (isNaN(wordIdx) || isNaN(lineIdx)) return null;
		return { lineIndex: lineIdx, wordIdx };
	}

	function handlePointerDown(e: PointerEvent, lineIndex: number) {
		const resolved = resolveWordTarget(e);
		if (!resolved || resolved.lineIndex !== lineIndex) return;
		isDragging = true;
		dragAnchor = resolved;
		selection = null;
	}

	function handlePointerMove(e: PointerEvent, lineIndex: number, words: WordToken[]) {
		if (!isDragging || !dragAnchor) return;
		const resolved = resolveWordTarget(e);
		if (!resolved || resolved.lineIndex !== lineIndex || resolved.lineIndex !== dragAnchor.lineIndex) return;

		const start = Math.min(dragAnchor.wordIdx, resolved.wordIdx);
		const end = Math.max(dragAnchor.wordIdx, resolved.wordIdx);

		if (hasOverlap(words, start, end)) return;

		selection = { lineIndex, startIdx: start, endIdx: end };
	}

	function handlePointerUp(e: PointerEvent, lineIndex: number, words: WordToken[]) {
		if (!isDragging || !dragAnchor) {
			isDragging = false;
			return;
		}

		const resolved = resolveWordTarget(e);
		isDragging = false;

		if (!resolved || resolved.lineIndex !== dragAnchor.lineIndex || resolved.lineIndex !== lineIndex) {
			dragAnchor = null;
			selection = null;
			return;
		}

		const start = Math.min(dragAnchor.wordIdx, resolved.wordIdx);
		const end = Math.max(dragAnchor.wordIdx, resolved.wordIdx);
		dragAnchor = null;

		if (start === end || hasOverlap(words, start, end)) {
			selection = null;
			return;
		}

		selection = { lineIndex, startIdx: start, endIdx: end };
	}

	function handleWordTapInSelectionMode(lineIndex: number, wordIdx: number, words: WordToken[]) {
		if (!dragAnchor) {
			dragAnchor = { lineIndex, wordIdx };
			selection = null;
		} else {
			if (dragAnchor.lineIndex !== lineIndex) {
				dragAnchor = { lineIndex, wordIdx };
				selection = null;
				return;
			}

			const start = Math.min(dragAnchor.wordIdx, wordIdx);
			const end = Math.max(dragAnchor.wordIdx, wordIdx);
			dragAnchor = null;

			if (start === end || hasOverlap(words, start, end)) {
				selection = null;
				return;
			}

			selection = { lineIndex, startIdx: start, endIdx: end };
		}
	}

	function confirmPhrase(lineIndex: number, words: WordToken[]) {
		const { startIdx, endIdx } = selection!;
		const text = words.slice(startIdx, endIdx + 1).map((w) => w.surface).join(' ');
		const sourceSentence = transcript?.dialogue_lines?.[lineIndex]?.words?.map((w: WordToken) => w.surface).join(' ');
		onCreatePhrase?.({
			text,
			word_count: endIdx - startIdx + 1,
			translation: pendingTranslation,
			lineIndex,
			startIdx,
			endIdx,
			source_sentence: sourceSentence,
			source_lesson_id: lesson?.id,
			source_line_index: lineIndex
		});
		selectionMode = false;
		resetSelection();
	}

	function cancelPhrase() {
		selectionMode = false;
		resetSelection();
	}

	async function fetchTranslation(lineIndex: number) {
		if (!selection || !lesson) return;
		const text = transcript.dialogue_lines[lineIndex].words
			.slice(selection.startIdx, selection.endIdx + 1)
			.map((w: WordToken) => w.surface)
			.join(' ');
		translateLoading = true;
		translateError = '';
		try {
			const { translation } = await api.translateTerm(text, lesson.language_code);
			pendingTranslation = translation;
		} catch {
			translateError = 'Translation failed. Check connection and try again.';
		} finally {
			translateLoading = false;
		}
	}

	async function fetchAddPhraseTranslation() {
		if (!addPhraseText.trim() || !lesson) return;
		addPhraseLoading = true;
		addPhraseError = '';
		try {
			const { translation } = await api.translateTerm(addPhraseText.trim(), lesson.language_code);
			addPhraseTranslation = translation;
		} catch {
			addPhraseError = 'Translation failed. Check connection and try again.';
		} finally {
			addPhraseLoading = false;
		}
	}

	function submitAddPhrase() {
		if (!addPhraseText.trim()) return;
		const text = addPhraseText.trim();
		const word_count = text.split(/\s+/).length;
		onCreatePhrase?.({
			text,
			word_count,
			translation: addPhraseTranslation,
			lineIndex: -1, // sentinel: not from transcript; handler tolerates
			startIdx: -1,  // sentinel: not from transcript; handler tolerates
			endIdx: -1,    // sentinel: not from transcript; handler tolerates
		});
		addPhraseText = '';
		addPhraseTranslation = '';
		showAddPhrase = false;
	}

	function collocationClassFor(state: string): string {
		if (state === 'learning' || state === 'relearning') return 'coll-bg-learning';
		if (state === 'review') return 'coll-bg-review';
		if (state === 'known') return 'coll-bg-known';
		if (state === 'suspended' || state === 'ignored') return 'coll-bg-ignored';
		return 'coll-bg-new';
	}

	function handleCollocationClick(segment: { words: WordToken[]; span_id: number }) {
		const first = segment.words[0];
		onCollocationStateChange?.(
			first.collocation_lemma!,
			segment.span_id,
			first.collocation_srs_state!
		);
	}

	function handleCollocationKeydown(
		e: KeyboardEvent,
		segment: { words: WordToken[]; span_id: number }
	) {
		if (e.key !== 'Enter' && e.key !== ' ') return;
		e.preventDefault();
		handleCollocationClick(segment);
	}

	function groupIntoSegments(words: WordToken[]): WordSegment[] {
		const segments: WordSegment[] = [];
		let i = 0;
		while (i < words.length) {
			const w = words[i];
			if (w.collocation_span_id !== null) {
				const id = w.collocation_span_id;
				const group: WordToken[] = [];
				while (i < words.length && words[i].collocation_span_id === id) {
					group.push(words[i]);
					i++;
				}
				segments.push({ type: 'collocation', words: group, span_id: id });
			} else {
				segments.push({ type: 'word', word: w });
				i++;
			}
		}
		return segments;
	}

	function wordIndexInLine(segments: WordSegment[], segIdx: number, innerIdx: number): number {
		let idx = 0;
		for (let s = 0; s < segIdx; s++) {
			const seg = segments[s];
			idx += seg.type === 'collocation' ? seg.words.length : 1;
		}
		return idx + innerIdx;
	}

	// --- Alt-key state: hold Alt while hovering a collocation to see per-word tooltips ---
	let altHeld = $state(false);

	function handleAltKeyDown(e: KeyboardEvent) {
		if (e.key === 'Alt') altHeld = true;
	}
	function handleAltKeyUp(e: KeyboardEvent) {
		altHeld = false;
	}


	const scenes = $derived.by(() => {
		if (lesson) {
			const result = buildScenes(lesson, transcript.dialogue_lines);
			if (result.length > 0) return result;
		}
		return fallbackScenes(transcript.dialogue_lines);
	});
</script>

<svelte:window onkeydown={handleAltKeyDown} onkeyup={handleAltKeyUp} />

<div class="transcript-wrapper">
	{#if transcript.key_phrases.length > 0}
		<div class="transcript-section">
			<h3>Key Phrases</h3>
			<ul class="key-phrases-list">
				{#each transcript.key_phrases as kp (kp.phrase)}
					<li>
						<span class="kp-phrase">{kp.phrase}</span>
						<span class="kp-translation">{kp.translation}</span>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if transcript.dialogue_lines.length > 0}
		<div class="transcript-section">
			<div class="dialogue-head">
				<h3>Dialogue <span class="transcript-hint">{selectionMode ? 'Tap first word, then last word to set phrase range.' : 'Drag to create a phrase, or tap \'+ New phrase\' on mobile. Click phrases/words to change SRS state; Alt+click a word inside a phrase for word-only.'}</span></h3>

				<div class="disclosure-toggles" role="group" aria-label="Show variations">
					<button
						type="button"
						class="toggle-pill"
						class:active={showSlow}
						aria-pressed={showSlow}
						onclick={() => (showSlow = !showSlow)}
					>Slow</button>
					<button
						type="button"
						class="toggle-pill"
						class:active={showTranslation}
						aria-pressed={showTranslation}
						onclick={() => (showTranslation = !showTranslation)}
					>Translation</button>
				</div>
			</div>

			<button class="new-phrase-btn" onclick={toggleSelectionMode}>
				{selectionMode ? 'Cancel' : '+ New phrase'}
			</button>

			{#each scenes as scene, sceneIdx (sceneIdx)}
				{#if scene.title}
					<h4 class="scene-header">{scene.title}</h4>
				{/if}
				{#each scene.lines as line (line.transcriptIndex)}
					{@const lineIndex = line.transcriptIndex}
					{@const segments = groupIntoSegments(line.words)}
					<div class="dialogue-line">
						<span class="dialogue-role">{line.role}</span>
						<div class="dialogue-line-body">
							<!-- svelte-ignore a11y_no_static_element_interactions -->
							<span
								class="dialogue-words"
								onpointerdown={(e) => handlePointerDown(e, lineIndex)}
								onpointermove={(e) => handlePointerMove(e, lineIndex, line.words)}
								onpointerup={(e) => handlePointerUp(e, lineIndex, line.words)}
							>
								{#each segments as segment, segIdx (segIdx)}
									{#if segment.type === 'collocation'}
										<Tooltip
											translation={altHeld ? null : segment.words[0].collocation_translation}
											state={altHeld ? null : segment.words[0].collocation_srs_state}
										>
											<span
												class="collocation-span {collocationClassFor(segment.words[0].collocation_srs_state!)}"
												role="button"
												tabindex="0"
												onclick={() => handleCollocationClick(segment)}
												onkeydown={(e) => handleCollocationKeydown(e, segment)}
											>
												{#each segment.words as cw, innerIdx (innerIdx)}
													{@const wIdx = wordIndexInLine(segments, segIdx, innerIdx)}
													<WordSpan
														word={cw}
														{onStateChange}
														requireModifier={true}
														altHover={altHeld}
														lineIndex={lineIndex}
														wordIndex={wIdx}
														selected={wordIsSelected(lineIndex, wIdx)}
													/>
												{/each}
											</span>
										</Tooltip>
									{:else}
										{@const wIdx = wordIndexInLine(segments, segIdx, 0)}
										<!-- svelte-ignore a11y_click_events_have_key_events -->
										<!-- svelte-ignore a11y_no_static_element_interactions -->
										<span
											onclick={selectionMode ? () => handleWordTapInSelectionMode(lineIndex, wIdx, line.words) : undefined}
										>
											<WordSpan
												word={segment.word}
												{onStateChange}
												lineIndex={lineIndex}
												wordIndex={wIdx}
												selected={wordIsSelected(lineIndex, wIdx)}
											/>
										</span>
									{/if}
								{/each}
							</span>
							{#if showSlow && line.slowText}
								<div class="line-slow">{line.slowText}</div>
							{/if}
							{#if showTranslation && line.translatedText}
								<div class="line-translation">{line.translatedText}</div>
							{/if}
						</div>
					</div>

					{#if selection && selection.lineIndex === lineIndex}
						<div class="phrase-confirm-bar">
							<span class="phrase-preview">
								{line.words.slice(selection.startIdx, selection.endIdx + 1).map((w) => w.surface).join(' ')}
							</span>
							<input
								class="phrase-translation-input"
								type="text"
								placeholder="translation (optional)"
								bind:value={pendingTranslation}
							/>
							<button
								class="phrase-translate-btn"
								onclick={() => fetchTranslation(lineIndex)}
								disabled={translateLoading}
								title="Translate with AI"
							>{translateLoading ? '…' : '✨'}</button>
							<button class="confirm-create" onclick={() => confirmPhrase(lineIndex, line.words)}>Create</button>
							<button class="confirm-cancel" onclick={cancelPhrase}>Cancel</button>
							{#if translateError}
								<span class="phrase-error">{translateError}</span>
							{/if}
						</div>
					{/if}
				{/each}
			{/each}
		</div>
	{/if}

	<div class="add-phrase-section">
		<button class="add-phrase-toggle" onclick={() => (showAddPhrase = !showAddPhrase)}>
			Add phrase… {showAddPhrase ? '▴' : '▾'}
		</button>
		{#if showAddPhrase}
			<div class="add-phrase-form">
				<input class="add-phrase-text" type="text" placeholder="phrase text" bind:value={addPhraseText} />
				<input class="add-phrase-translation" type="text" placeholder="translation" bind:value={addPhraseTranslation} />
				<button
					class="add-phrase-translate-btn"
					onclick={fetchAddPhraseTranslation}
					disabled={addPhraseLoading || !addPhraseText.trim()}
					title="Translate with AI"
				>{addPhraseLoading ? '…' : '✨'}</button>
				<button class="add-phrase-create" onclick={submitAddPhrase} disabled={!addPhraseText.trim()}>Create</button>
				{#if addPhraseError}
					<span class="phrase-error">{addPhraseError}</span>
				{/if}
			</div>
		{/if}
	</div>

	<div class="listen-footer">
		<button
			class="listen-btn"
			class:listened={isListened}
			onclick={onMarkListened}
			disabled={listenLoading}
		>
			{#if listenLoading}
				Registering…
			{:else if isListened}
				✓ Listened
			{:else}
				Mark as Listened
			{/if}
		</button>

		{#if listenResult && !error}
			<p class="listen-confirmation">
				{listenResult.registered}
				{listenResult.registered === 1 ? 'word' : 'words'} tracked in SRS
			</p>
		{/if}
	</div>
</div>

<style>
	.transcript-wrapper {
		margin-top: 1.25rem;
		position: relative;
	}
	.listen-footer {
		position: sticky;
		bottom: 0;
		margin-top: 1.25rem;
		padding: 0.75rem 1rem;
		background: var(--color-bg, #fff);
		border-top: 1px solid var(--color-border);
		z-index: 5;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
		box-shadow: 0 -2px 6px rgba(0, 0, 0, 0.03);
	}
	.listen-btn {
		padding: 0.5rem 1.25rem;
		background: var(--color-primary);
		color: white;
		border: none;
		border-radius: 4px;
		cursor: pointer;
	}
	.listen-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.listen-btn.listened {
		background: var(--color-success);
	}
	.listen-confirmation {
		color: var(--color-success);
		font-size: 0.85rem;
		margin: 0;
	}
	.transcript-section {
		margin-bottom: 1.25rem;
	}
	.transcript-section h3 {
		font-size: 0.8rem;
		text-transform: uppercase;
		color: var(--color-muted);
		margin-bottom: 0.5rem;
	}
	.dialogue-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.disclosure-toggles {
		display: flex;
		gap: 0.35rem;
	}
	.toggle-pill {
		font-size: 0.75rem;
		padding: 0.2rem 0.7rem;
		background: transparent;
		color: var(--color-muted, #6b7280);
		border: 1px solid var(--color-border, #e5e7eb);
		border-radius: 999px;
		cursor: pointer;
		transition: background-color 0.1s, color 0.1s, border-color 0.1s;
	}
	.toggle-pill:hover {
		border-color: var(--color-primary, #2563eb);
	}
	.toggle-pill.active {
		background: var(--color-primary, #2563eb);
		color: white;
		border-color: var(--color-primary, #2563eb);
	}
	.transcript-hint {
		font-style: italic;
		text-transform: none;
		font-size: 0.75rem;
	}
	.new-phrase-btn {
		font-size: 0.75rem;
		padding: 0.2rem 0.6rem;
		background: transparent;
		border: 1px solid var(--color-primary, #2563eb);
		color: var(--color-primary, #2563eb);
		border-radius: 3px;
		cursor: pointer;
		margin-bottom: 0.5rem;
	}
	.new-phrase-btn:hover {
		background: rgba(37, 99, 235, 0.08);
	}
	.key-phrases-list {
		list-style: none;
		padding: 0;
		margin-top: 0.5rem;
	}
	.key-phrases-list li {
		display: flex;
		justify-content: space-between;
		padding: 0.25rem 0;
		border-bottom: 1px solid var(--color-border);
	}
	.kp-phrase {
		font-weight: 500;
	}
	.kp-translation {
		color: var(--color-muted);
		font-style: italic;
	}
	.scene-header {
		margin: 1.25rem 0 0.5rem;
		padding: 0.45rem 0.75rem;
		font-size: 0.82rem;
		font-weight: 600;
		letter-spacing: 0.02em;
		color: var(--color-primary, #2563eb);
		background: rgba(37, 99, 235, 0.08);
		border-left: 3px solid var(--color-primary, #2563eb);
		border-radius: 0 4px 4px 0;
		text-transform: uppercase;
	}
	.scene-header:first-of-type {
		margin-top: 0.5rem;
	}
	.dialogue-line {
		display: flex;
		gap: 0.75rem;
		padding: 0.3rem 0;
		border-bottom: 1px solid var(--color-border);
		font-size: 0.95rem;
		line-height: 1.5;
	}
	.dialogue-line-body {
		flex: 1;
		min-width: 0;
	}
	.dialogue-role {
		color: var(--color-primary);
		min-width: 6rem;
		font-size: 0.85rem;
		padding-top: 0.1rem;
		flex-shrink: 0;
	}
	.dialogue-words {
		display: block;
		line-height: 1.6;
		user-select: text;
	}
	.line-slow {
		margin-top: 0.15rem;
		color: var(--color-muted, #6b7280);
		font-size: 0.85rem;
		font-style: italic;
	}
	.line-translation {
		margin-top: 0.15rem;
		color: var(--color-muted, #6b7280);
		font-size: 0.85rem;
	}
	.collocation-span {
		display: inline;
		border-bottom: 2px solid var(--color-primary, #2563eb);
		padding-bottom: 1px;
		cursor: pointer;
		border-radius: 2px;
		transition: background-color 0.1s;
	}
	.collocation-span:hover {
		filter: brightness(0.95);
	}
	.collocation-span:focus-visible {
		outline: 2px solid var(--color-primary, #2563eb);
		outline-offset: 2px;
	}
	.coll-bg-new {
		background-color: rgba(37, 99, 235, 0.1);
	}
	.coll-bg-learning {
		background-color: rgba(202, 138, 4, 0.15);
	}
	.coll-bg-review {
		background-color: rgba(22, 163, 74, 0.12);
	}
	.coll-bg-known {
		background-color: rgba(156, 163, 175, 0.15);
	}
	.coll-bg-ignored {
		background-color: rgba(156, 163, 175, 0.15);
		text-decoration: line-through;
	}
	.phrase-confirm-bar {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.4rem 0.75rem;
		margin: 0.25rem 0 0.5rem;
		background: rgba(99, 102, 241, 0.08);
		border: 1px solid rgba(99, 102, 241, 0.3);
		border-radius: 4px;
		font-size: 0.875rem;
	}
	.phrase-preview {
		font-weight: 500;
		color: var(--color-primary, #4f46e5);
	}
	.phrase-translation-input {
		flex: 1;
		border: 1px solid var(--color-border, #e5e7eb);
		border-radius: 3px;
		padding: 0.15rem 0.4rem;
		font-size: 0.85rem;
	}
	.phrase-translate-btn {
		padding: 0.2rem 0.4rem;
		background: transparent;
		border: 1px solid var(--color-border, #e5e7eb);
		border-radius: 3px;
		cursor: pointer;
		font-size: 0.85rem;
		line-height: 1;
	}
	.phrase-translate-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.confirm-create {
		padding: 0.2rem 0.6rem;
		background: var(--color-primary, #2563eb);
		color: white;
		border: none;
		border-radius: 3px;
		cursor: pointer;
		font-size: 0.8rem;
	}
	.confirm-cancel {
		padding: 0.2rem 0.6rem;
		background: transparent;
		border: 1px solid var(--color-muted, #9ca3af);
		border-radius: 3px;
		cursor: pointer;
		font-size: 0.8rem;
	}

	.phrase-error {
		color: var(--color-danger, #dc2626);
		font-size: 0.75rem;
		flex-basis: 100%;
	}
	.add-phrase-section {
		margin-top: 1rem;
		padding: 0.5rem 0;
		border-top: 1px solid var(--color-border, #e5e7eb);
	}
	.add-phrase-toggle {
		font-size: 0.8rem;
		padding: 0.3rem 0.75rem;
		background: transparent;
		border: 1px solid var(--color-border, #e5e7eb);
		border-radius: 4px;
		cursor: pointer;
		color: var(--color-muted, #6b7280);
	}
	.add-phrase-toggle:hover {
		border-color: var(--color-primary, #2563eb);
		color: var(--color-primary, #2563eb);
	}
	.add-phrase-form {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 0.75rem;
		margin-top: 0.4rem;
		background: rgba(99, 102, 241, 0.06);
		border: 1px solid rgba(99, 102, 241, 0.2);
		border-radius: 4px;
		font-size: 0.875rem;
	}
	.add-phrase-form input {
		flex: 1;
		border: 1px solid var(--color-border, #e5e7eb);
		border-radius: 3px;
		padding: 0.25rem 0.4rem;
		font-size: 0.85rem;
	}
	.add-phrase-form button {
		margin-top: 0;
	}
	.add-phrase-create {
		padding: 0.2rem 0.6rem;
		background: var(--color-primary, #2563eb);
		color: white;
		border: none;
		border-radius: 3px;
		cursor: pointer;
		font-size: 0.8rem;
	}
	.add-phrase-create:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.add-phrase-translate-btn {
		padding: 0.2rem 0.4rem;
		background: transparent;
		border: 1px solid var(--color-border, #e5e7eb);
		border-radius: 3px;
		cursor: pointer;
		font-size: 0.85rem;
		line-height: 1;
	}
	.add-phrase-translate-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	@media (max-width: 640px) {
		.dialogue-line {
			flex-direction: column;
			gap: 0.15rem;
		}
		.dialogue-role {
			min-width: unset;
			font-weight: 600;
		}
		.key-phrases-list li {
			flex-direction: column;
			gap: 0.1rem;
		}
	}
</style>
