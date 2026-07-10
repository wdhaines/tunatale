<script lang="ts">
	import WordSpan from '$lib/WordSpan.svelte';
	import Tooltip from './Tooltip.svelte';
	import type { TooltipActions } from './Tooltip.svelte';
	import { api } from '$lib/api';
	import type { LessonDetail, TranscriptData, WordToken } from '$lib/api';
	import { buildScenes, fallbackScenes, cueHighlight } from '$lib/transcriptScenes';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';
	import { masteryBackgroundColor, masteryColor } from '$lib/mastery';

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
		onWordClick?: (word: WordToken, lineIndex: number) => void;
		onCollocationStateChange?: (span_id: number) => void;
		// Undo cycle for phrase grades: when undoableItemId matches a span_id the
		// phrase popover shows "Undo ↩" (calling onCollocationUndo) instead of its
		// grade label. Word-level undo flows through tooltipActions instead.
		undoableItemId?: number | null;
		onCollocationUndo?: (span_id: number) => void | Promise<void>;
		onCreatePhrase?: (args: CreatePhraseArgs) => void | Promise<void>;
		tooltipActions?: TooltipActions;
		controller?: PlaybackController | null;
	}

	let {
		transcript,
		lesson,
		onWordClick,
		onCollocationStateChange,
		undoableItemId = null,
		onCollocationUndo,
		onCreatePhrase,
		tooltipActions,
		controller = null
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
	let showGloss = $state(false);
	// Interlinear: the whole-line L1 translation under each L2 line (BDT-style,
	// cover-one-side reading). Distinct from per-word Gloss.
	let showInterlinear = $state(false);

	// "?" disclosure for the dialogue usage instructions + mastery-color legend.
	// Default closed, no persistence.
	let showHelp = $state(false);

	const SPEAKER_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

	// Map each distinct line.role to a letter (A, B, C, ...) in order of first
	// appearance, so dialogue lines show a compact "speaker" chip instead of a
	// raw voice id like "female-1" / "male-1".
	const speakerLetters = $derived.by(() => {
		const map: Record<string, string> = {};
		let count = 0;
		for (const line of transcript.dialogue_lines) {
			if (!(line.role in map)) {
				map[line.role] = SPEAKER_LETTERS[count % SPEAKER_LETTERS.length];
				count += 1;
			}
		}
		return map;
	});

	// The speaker's position among distinct roles, derived from its letter —
	// used to pick a distinct accent class per speaker. `role` is always a key
	// of speakerLetters (built from the same dialogue_lines), so the lookup
	// never misses.
	function speakerIndex(role: string): number {
		return SPEAKER_LETTERS.indexOf(speakerLetters[role]);
	}

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

		// A single-word "drag" is finger jitter during a tap, not a phrase —
		// without this the confirm bar flashed under the finger on every touch
		// tap (and stuck around when the gesture became a scroll).
		if (start === end) {
			selection = null;
			return;
		}

		if (hasOverlap(words, start, end)) return;

		selection = { lineIndex, startIdx: start, endIdx: end };
	}

	// The browser fires pointercancel (never pointerup) when it claims the
	// touch for scrolling — drop any half-built selection or it sticks open.
	function handlePointerCancel() {
		isDragging = false;
		dragAnchor = null;
		selection = null;
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

	// A collocation's background tint tracks its mastery on the same red→green ramp
	// as single words (its `collocation_progress`), EXCEPT suspended/ignored, which
	// stay off the ramp (gray + strikethrough) — mirroring WordSpan's color logic.
	function collocationOffRamp(state: string | null): boolean {
		return state === 'suspended' || state === 'ignored';
	}

	function handleCollocationClick(segment: { words: WordToken[]; span_id: number }) {
		onCollocationStateChange?.(segment.span_id);
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

	// --- Phrase drill-in: the touch path to a phrase's individual words. The
	// "Words…" popover button sets this; the affected phrase then behaves like
	// Alt-held (group popover suppressed, per-word popovers live) until a tap
	// lands outside that phrase. ---
	let expandedSpanId = $state<number | null>(null);

	$effect(() => {
		if (expandedSpanId === null) return;
		const spanSelector = `[data-span-id="${expandedSpanId}"]`;
		function handleOutside(e: PointerEvent) {
			const el = e.target as HTMLElement;
			if (!el.closest(spanSelector)) expandedSpanId = null;
		}
		document.addEventListener('pointerdown', handleOutside);
		return () => document.removeEventListener('pointerdown', handleOutside);
	});

	const scenes = $derived.by(() => {
		if (lesson) {
			const result = buildScenes(lesson, transcript.dialogue_lines);
			if (result.length > 0) return result;
		}
		return fallbackScenes(transcript.dialogue_lines);
	});

	// --- Synced subtitle state (Phase 3) ---
	// The controller's getters are $state-backed, so plain deriveds track them.
	let currentCue = $derived(controller?.currentCue ?? null);

	let activeRef = $derived(cueHighlight(currentCue));

	// Plain (non-reactive) memo: writing it inside the effect must not re-run it.
	let prevScrollKey = '';
	let suppressNextScroll = false;
	$effect(() => {
		const ref = activeRef;
		const key = ref ? `${ref.kind}:${ref.target_index}` : '';
		if (suppressNextScroll) {
			suppressNextScroll = false;
			prevScrollKey = key;
		} else if (ref && key !== prevScrollKey) {
			prevScrollKey = key;
			// Capture `ref` — activeRef can flip to null before the frame fires.
			requestAnimationFrame(() => {
				const el = document.querySelector(
					ref.kind === 'line'
						? '.dialogue-line.active-line'
						: '.key-phrases-list li.active-kp'
				);
				if (!el) return;
				const rect = el.getBoundingClientRect();
				const vh = window.innerHeight;
				const navEl = document.querySelector('.global-nav');
				const navH = navEl?.getBoundingClientRect().height ?? 0;
				const playerEl = document.querySelector('.player-card');
				const playerH = playerEl?.getBoundingClientRect().height ?? 0;
				const stickyH = navH + playerH;
				// Position the element's top below the sticky headers, then shift
				// up to vertically center it in the visible scroll area.
				const target = rect.top - stickyH - (vh - stickyH - rect.height) / 2;
				window.scrollBy({ top: target, behavior: 'smooth' });
			});
		} else if (!key) {
			prevScrollKey = '';
		}
	});
</script>

<svelte:window onkeydown={handleAltKeyDown} onkeyup={handleAltKeyUp} />

<div class="transcript-wrapper">
	{#if transcript.key_phrases.length > 0}
		<div class="transcript-section">
			<h3>Key Phrases</h3>
			<ul class="key-phrases-list">
				{#each transcript.key_phrases as kp, kpIdx (kp.phrase)}
					{@const seekCue = controller?.findPlayableCue({ kind: 'key_phrase', target_index: kpIdx }) ?? null}
					<li class:active-kp={activeRef?.kind === 'key_phrase' && activeRef.target_index === kpIdx}>
						<div class="kp-text">
							<span class="kp-phrase">{kp.phrase}</span>
							<span class="kp-translation">{kp.translation}</span>
						</div>
						{#if seekCue}
							<button class="seek-btn" onclick={() => { suppressNextScroll = true; controller!.playRef({ kind: 'key_phrase', target_index: kpIdx }); }}>▶</button>
						{/if}
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if transcript.dialogue_lines.length > 0}
		<div class="transcript-section">
			<div class="dialogue-head">
				<h3>
					Dialogue
					<button
						type="button"
						class="help-toggle"
						aria-label="How to use the transcript"
						aria-expanded={showHelp}
						onclick={() => (showHelp = !showHelp)}
					>?</button>
					{#if selectionMode}
						<span class="transcript-hint">Tap first word, then last word to set phrase range.</span>
					{/if}
				</h3>

				<div class="disclosure-toggles" role="group" aria-label="Show variations">
					<button
						type="button"
						class="toggle-pill"
						class:active={showGloss}
						aria-pressed={showGloss}
						onclick={() => (showGloss = !showGloss)}
					>Gloss</button>
					<button
						type="button"
						class="toggle-pill"
						class:active={showInterlinear}
						aria-pressed={showInterlinear}
						onclick={() => (showInterlinear = !showInterlinear)}
					>Interlinear</button>
				</div>
			</div>

			{#if showHelp}
				<div class="help-panel">
					<p class="help-instructions">
						Tap or hover a word/phrase to open its popover — grading and all other actions
						live there. Alt+hover a phrase for its individual words. Drag to create a
						phrase, or tap '+ New phrase' on mobile.
					</p>
					<div class="help-legend">
						<span class="legend-row">
							<span class="legend-swatch" style={`background-color: ${masteryColor(0)};`}></span>
							New
						</span>
						<span class="legend-arrow">→</span>
						<span class="legend-row">
							<span class="legend-swatch" style={`background-color: ${masteryColor(0.5)};`}></span>
							Learning
						</span>
						<span class="legend-arrow">→</span>
						<span class="legend-row">
							<span class="legend-swatch" style={`background-color: ${masteryColor(1)};`}></span>
							Known
						</span>
						<span class="legend-row">
							<span class="legend-swatch word-unknown"></span>
							Unknown
						</span>
					</div>
				</div>
			{/if}

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
					{@const lineSentence = transcript.dialogue_lines[lineIndex]?.sentence ?? ''}
					{@const isActiveLine = activeRef?.kind === 'line' && activeRef.target_index === lineIndex}
					{@const isActiveTranslated = isActiveLine && currentCue?.section_type === 'translated'}
					{@const seekCue = controller?.findPlayableCue({ kind: 'line', target_index: lineIndex }) ?? null}
					<div class="dialogue-line" class:active-line={isActiveLine}>
						<span class="dialogue-role">
							<span
								class="dialogue-role-chip speaker-{speakerIndex(line.role) % 4}"
								title={line.role}
							>{speakerLetters[line.role]}</span>
						</span>
						<div class="dialogue-line-body">
							<!-- svelte-ignore a11y_no_static_element_interactions -->
							<span
								class="dialogue-words"
								onpointerdown={(e) => handlePointerDown(e, lineIndex)}
								onpointermove={(e) => handlePointerMove(e, lineIndex, line.words)}
								onpointerup={(e) => handlePointerUp(e, lineIndex, line.words)}
								onpointercancel={handlePointerCancel}
							>
								{#each segments as segment, segIdx (segIdx)}
									{#if segment.type === 'collocation'}
										{@const collOffRamp = collocationOffRamp(segment.words[0].collocation_srs_state)}
										{@const drilledIn = altHeld || expandedSpanId === segment.span_id}
										{@const collUndoable = undoableItemId === segment.span_id && onCollocationUndo != null}
										<Tooltip
											translation={segment.words[0].collocation_translation}
											suppressed={drilledIn}
											word={segment.words[0]}
											gradeLabel={collUndoable
												? 'Undo ↩'
												: segment.words[0].collocation_is_due
													? 'Got it ✓'
													: null}
											onGrade={collUndoable
												? () => void onCollocationUndo!(segment.span_id)
												: onCollocationStateChange
													? () => handleCollocationClick(segment)
													: null}
											onDrillIn={() => (expandedSpanId = segment.span_id)}
										>
											<span
												class="collocation-span"
												class:coll-bg-ignored={collOffRamp}
												style={collOffRamp
													? ''
													: `background-color: ${masteryBackgroundColor(segment.words[0].collocation_progress ?? 0)};`}
												role="button"
												tabindex="0"
												data-span-id={segment.span_id}
												onkeydown={(e) => handleCollocationKeydown(e, segment)}
											>
												{#each segment.words as cw, innerIdx (innerIdx)}
													{@const wIdx = wordIndexInLine(segments, segIdx, innerIdx)}
													<WordSpan
														word={cw}
														onWordClick={onWordClick}
														requireModifier={true}
														altHover={drilledIn}
														lineIndex={lineIndex}
														wordIndex={wIdx}
														selected={wordIsSelected(lineIndex, wIdx)}
														sentence={lineSentence}
														tooltipActions={tooltipActions}
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
												onWordClick={onWordClick}
												lineIndex={lineIndex}
												wordIndex={wIdx}
												selected={wordIsSelected(lineIndex, wIdx)}
												sentence={lineSentence}
												tooltipActions={tooltipActions}
												{showGloss}
											/>
										</span>
									{/if}
								{/each}
							</span>
							{#if (showInterlinear || isActiveTranslated) && line.translatedText}
								<div class="line-interlinear">{line.translatedText}</div>
							{/if}
						</div>
						{#if seekCue}
							<button class="seek-btn" onclick={() => { suppressNextScroll = true; controller!.playRef({ kind: 'line', target_index: lineIndex }); }}>▶</button>
						{/if}
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

</div>

<style>
	.transcript-wrapper {
		margin-top: 1.25rem;
		position: relative;
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
	.help-toggle {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.1rem;
		height: 1.1rem;
		margin-left: 0.35rem;
		padding: 0;
		font-size: 0.7rem;
		font-style: normal;
		line-height: 1;
		color: var(--color-muted, #6b7280);
		background: transparent;
		border: 1px solid var(--color-border, #e5e7eb);
		border-radius: 50%;
		cursor: pointer;
		vertical-align: middle;
	}
	.help-toggle:hover,
	.help-toggle[aria-expanded='true'] {
		color: var(--color-primary, #2563eb);
		border-color: var(--color-primary, #2563eb);
	}
	.help-panel {
		margin: 0.4rem 0 0.6rem;
		padding: 0.5rem 0.75rem;
		background: var(--color-surface-2);
		border: 1px solid var(--color-border, #e5e7eb);
		border-radius: 4px;
		font-size: 0.8rem;
	}
	.help-instructions {
		margin: 0 0 0.5rem;
		font-style: italic;
		color: var(--color-muted, #6b7280);
	}
	.help-legend {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.legend-row {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.legend-arrow {
		color: var(--color-muted, #6b7280);
	}
	.legend-swatch {
		display: inline-block;
		width: 0.8rem;
		height: 0.8rem;
		border-radius: 2px;
		border: 1px solid var(--color-border, #e5e7eb);
	}
	/* Same indigo as WordSpan's .word-unknown text color, reused here as a swatch fill. */
	.legend-swatch.word-unknown {
		background-color: #818cf8;
	}
	.new-phrase-btn {
		font-size: 0.75rem;
		padding: 0.2rem 0.6rem;
		background: transparent;
		border: 1px solid var(--color-primary, #2563eb);
		color: var(--color-primary, #2563eb);
		border-radius: var(--radius-pill);
		cursor: pointer;
		margin-bottom: 0.5rem;
	}
	.new-phrase-btn:hover {
		background: var(--color-primary-hover);
		color: var(--color-on-primary);
	}
	.key-phrases-list {
		list-style: none;
		padding: 0;
		margin-top: 0.5rem;
	}
	.key-phrases-list li {
		display: flex;
		flex-direction: column;
		padding: 0.25rem 0;
		border-bottom: 1px solid var(--color-border);
	}
	.kp-text {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
	}
	.kp-phrase {
		font-weight: 500;
	}
	.kp-translation {
		color: var(--color-muted);
		font-style: italic;
	}
	.key-phrases-list .seek-btn {
		margin-left: auto;
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
	.dialogue-line.active-line {
		background: rgba(37, 99, 235, 0.06);
		padding-left: 0.5rem;
		border-radius: 0 4px 4px 0;
		position: relative;
	}
	.dialogue-line.active-line::before {
		content: '';
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 3px;
		background: var(--color-primary, #2563eb);
		border-radius: 0 4px 4px 0;
	}
	.key-phrases-list li.active-kp {
		background: rgba(37, 99, 235, 0.06);
		padding-left: 0.5rem;
		position: relative;
	}
	.key-phrases-list li.active-kp::before {
		content: '';
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 3px;
		background: var(--color-primary, #2563eb);
	}
	.seek-btn {
		background: transparent;
		border: 1px solid var(--color-border);
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.75rem;
		padding: 0.1rem 0.35rem;
		line-height: 1;
		color: var(--color-muted);
		transition: color 0.1s, border-color 0.1s;
		min-height: 28px;
	}
	.seek-btn:hover {
		color: var(--color-primary);
		border-color: var(--color-primary);
	}
	.dialogue-line {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
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
		min-width: unset;
		font-weight: 600;
		font-size: 0.85rem;
		padding-top: 0.1rem;
		flex-shrink: 0;
	}
	.dialogue-role-chip {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.4rem;
		height: 1.4rem;
		border-radius: var(--radius-pill);
		font-size: 0.75rem;
		font-weight: 700;
		line-height: 1;
		cursor: default;
	}
	.dialogue-role-chip.speaker-0 {
		background: color-mix(in srgb, var(--color-primary) 18%, transparent);
		color: var(--color-primary);
	}
	.dialogue-role-chip.speaker-1 {
		background: color-mix(in srgb, var(--color-brand) 18%, transparent);
		color: var(--color-brand);
	}
	.dialogue-role-chip.speaker-2 {
		background: color-mix(in srgb, var(--color-accent) 18%, transparent);
		color: var(--color-accent);
	}
	.dialogue-role-chip.speaker-3 {
		background: color-mix(in srgb, var(--color-secondary) 18%, transparent);
		color: var(--color-secondary);
	}
	.dialogue-words {
		display: block;
		line-height: 1.6;
		user-select: text;
	}
	/* Interlinear L1 reads as a deliberate pair under the L2 line: indented and
	   accented so the eye groups it with the sentence above (cover-one-side). */
	.line-interlinear {
		margin-top: 0.2rem;
		padding-left: 0.6rem;
		border-left: 2px solid var(--color-border);
		color: var(--color-secondary, #5c6672);
		font-size: 0.9rem;
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
	.coll-bg-ignored {
		background-color: rgba(156, 163, 175, 0.15);
		text-decoration: line-through;
	}
	.phrase-confirm-bar {
		display: flex;
		/* Wrap on narrow screens — preview + input + 3 buttons overflowed phones. */
		flex-wrap: wrap;
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
		overflow-wrap: anywhere;
	}
	.phrase-translation-input {
		flex: 1 1 8rem;
		min-width: 0;
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
		color: var(--color-on-primary, #fff);
		border: none;
		border-radius: var(--radius-pill);
		cursor: pointer;
		font-size: 0.8rem;
	}
	.confirm-cancel {
		padding: 0.2rem 0.6rem;
		background: transparent;
		border: 1px solid var(--color-muted, #9ca3af);
		border-radius: var(--radius-pill);
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
		border-radius: var(--radius-pill);
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
		color: var(--color-on-primary, #fff);
		border: none;
		border-radius: var(--radius-pill);
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

	@media (min-width: 641px) {
		.dialogue-line {
			flex-direction: row;
			gap: 0.75rem;
		}
		.dialogue-role {
			min-width: 6rem;
			font-weight: 400;
		}
		.key-phrases-list li {
			flex-direction: row;
			justify-content: space-between;
			gap: 0;
		}
	}
</style>
