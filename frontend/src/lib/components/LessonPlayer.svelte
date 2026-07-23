<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api } from '$lib/api';
	import type { LessonAudio } from '$lib/api';
	import { maybePrefetchLesson } from '$lib/sw/prefetch';
	import type { NetworkInformationLike } from '$lib/sw/prefetch';
	import type { CacheStorageLike } from '$lib/sw/audio-cache';
	import { prefetchPrefStore } from '$lib/stores/prefetchPref.svelte';
	import { lessonPlayerPref, pillsForSection } from '$lib/stores/lessonPlayerPref.svelte';
	import type { EnglishMode } from '$lib/stores/lessonPlayerPref.svelte';
	import { createPlaybackController } from '$lib/playback/playbackController.svelte';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';
	import { captionBlurPref } from '$lib/stores/captionBlurPref.svelte';
	import { splitCaption, activeChunkIndex, chunkStartMs } from '$lib/captionChunks';

	interface Props {
		audio: LessonAudio;
		compact?: boolean;
		lessonTitle?: string;
		controller?: PlaybackController | null;
	}

	let { audio, compact = false, lessonTitle = '', controller = $bindable(null) }: Props = $props();

	// audio/lessonTitle are fixed for the life of an instance — the page recreates
	// the player via {#key audio.audio_id} — so snapshot them once at init.
	// untrack marks the initial-value reads as intentional (state_referenced_locally).
	const init = untrack(() => ({ audio, lessonTitle }));

	const ctrl = createPlaybackController({
		lessonId: init.audio.lesson_id,
		lessonTitle: init.lessonTitle || init.audio.lesson_id,
		audioUrl: api.audioUrl(init.audio.audio_id),
		audio: init.audio,
		// Without this, selectTrack falls back to identity and sets audioEl.src to
		// a bare section id — a broken relative URL that never loads.
		sectionUrl: (id) => api.audioUrl(id)
	});

	controller = ctrl;

	const hasCues =
		init.audio.cues !== null && init.audio.cues !== undefined && init.audio.cues.length > 0;

	// Track mode: the phase/enunciation model switches between per-section
	// tracks, which is only meaningful when every section row carries its own
	// cue manifest. Legacy lessons (rendered before per-section cues existed)
	// have cues on the full track only — they must stay on the legacy
	// full-lesson track, where subtitle + sentence nav keep working off the
	// full manifest. Switching them would strand playback on one cue-less
	// section (dead subtitle, dead ▶/nav, other sections unreachable).
	const hasSectionCues =
		init.audio.sections.length > 0 && init.audio.sections.every((s) => (s.cues?.length ?? 0) > 0);
	const trackMode = hasCues && hasSectionCues;

	const sectionTypes = new Set(init.audio.sections.map((s) => s.section_type));
	const hasAllSections =
		sectionTypes.has('key_phrases') &&
		sectionTypes.has('natural_speed') &&
		sectionTypes.has('translated') &&
		sectionTypes.has('slow_speed') &&
		sectionTypes.has('slow_translated');
	// English-first audio only exists on lessons re-rendered after it was added.
	// When absent, the English cycle skips the en_first state (off ↔ l2_first)
	// so the button never selects a missing track.
	const hasEnFirst =
		sectionTypes.has('en_translated') && sectionTypes.has('slow_en_translated');

	// --- Phase / Enunciation / English state ---

	const PHASES = ['key_phrases', 'dialogue'] as const;
	type Phase = (typeof PHASES)[number];

	const ENUNCIATION_OPTIONS = [
		{ level: 'natural', label: 'Natural', rate: 1.0 },
		{ level: 'enunciated', label: 'Enunciated', rate: 1.0 },
		{ level: 'enunciated_0.9', label: 'Enun 0.9×', rate: 0.9 },
		{ level: 'enunciated_0.8', label: 'Enun 0.8×', rate: 0.8 },
	] as const;

	function resolveSectionType(phase: Phase, enunLevel: string, engMode: EnglishMode): string | null {
		if (phase === 'key_phrases') return 'key_phrases';
		const natural = enunLevel === 'natural';
		if (engMode === 'off') return natural ? 'natural_speed' : 'slow_speed';
		if (engMode === 'l2_first') return natural ? 'translated' : 'slow_translated';
		return natural ? 'en_translated' : 'slow_en_translated'; // en_first
	}

	// Value shown in the English setting chip; the chip's "English" field label
	// supplies the noun, so these are just the mode.
	const ENGLISH_LABELS: Record<EnglishMode, string> = {
		off: 'Off',
		l2_first: 'After',
		en_first: 'Before'
	};

	function resolveRate(enunLevel: string): number {
		const opt = ENUNCIATION_OPTIONS.find((o) => o.level === enunLevel);
		return opt?.rate ?? 1.0;
	}

	let phase: Phase = $state('dialogue');
	let enunLevel: string = $state('natural');
	let englishMode: EnglishMode = $state('off');

	// --- Caption blur state ---
	// The reveal is scoped to the whole LINE (cue), not the currently-showing
	// chunk. Clicking unblurs every segment of the speaker's line and it stays
	// unblurred as the audio moves through the line's chunks (and its trailing
	// gap — currentCue holds the last-started cue until the next one begins).
	// It re-blurs only when a genuinely new line starts, so you get a fresh
	// listen-first attempt per line instead of the caption snapping back on
	// every ~2s chunk boundary.
	let revealedCueIndex: number | null = $state(null);

	// --- Chunked caption state ---
	const captionChunks = $derived(ctrl.currentCue ? splitCaption(ctrl.currentCue.text) : []);
	const captionIdx = $derived(
		ctrl.currentCue
			? activeChunkIndex(captionChunks, ctrl.currentCue.start_ms, ctrl.currentCue.end_ms, ctrl.currentTime * 1000)
			: 0
	);
	const captionRevealed = $derived(
		ctrl.currentCue != null && revealedCueIndex === ctrl.currentCue.index
	);

	let selectedSectionType = $derived(resolveSectionType(phase, enunLevel, englishMode));
	let enunIndex = $derived(ENUNCIATION_OPTIONS.findIndex((o) => o.level === enunLevel));

	function cycleEnunciation() {
		const nextIdx = (enunIndex + 1) % ENUNCIATION_OPTIONS.length;
		enunLevel = ENUNCIATION_OPTIONS[nextIdx].level;
	}

	function cycleEnglish() {
		const order: EnglishMode[] = hasEnFirst ? ['off', 'l2_first', 'en_first'] : ['off', 'l2_first'];
		const idx = order.indexOf(englishMode);
		englishMode = order[(idx + 1) % order.length];
	}

	function applyTrack() {
		if (selectedSectionType) {
			ctrl.selectTrack(selectedSectionType);
			ctrl.setEnunciationRate(resolveRate(enunLevel));
		}
	}

	function persistSelection() {
		lessonPlayerPref.set({ phase, enunciation: enunLevel, english: englishMode });
	}

	function onPhaseClick(p: Phase) {
		phase = p;
		applyTrack();
		persistSelection();
	}

	function onEnunClick() {
		if (phase === 'key_phrases') return;
		cycleEnunciation();
		applyTrack();
		persistSelection();
	}

	function onEnglishClick() {
		if (phase === 'key_phrases') return;
		cycleEnglish();
		applyTrack();
		persistSelection();
	}

	function revealCaption() {
		revealedCueIndex = ctrl.currentCue?.index ?? null;
	}

	function onCaptionKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			revealCaption();
		}
	}

	// Mirror the pills onto whatever track is actually playing. A transcript ▶ tap
	// can switch the track from outside the player (e.g. a key-phrase ▶ while
	// Dialogue is selected); this keeps the phase/enunciation/English controls
	// truthful. Idempotent for the player's own clicks (they set the pills first,
	// then select the matching track).
	$effect(() => {
		const p = pillsForSection(ctrl.activeSectionType);
		if (p.phase !== undefined) phase = p.phase;
		if (p.enunciation !== undefined) enunLevel = p.enunciation;
		if (p.english !== undefined) englishMode = p.english;
	});

	// --- Prefetch section URLs ---

	function computePrefetchUrls(
		sections: { audio_id: string; section_type: string }[],
		fullAudioId: string,
		trackMode: boolean,
		phase: Phase,
		enunLevel: string,
		englishMode: EnglishMode,
		currentEnunIndex: number,
	): string[] {
		if (!trackMode) {
			return [api.audioUrl(fullAudioId)];
		}
		const byType = new Map(sections.map((s) => [s.section_type, s.audio_id]));
		const currentType = resolveSectionType(phase, enunLevel, englishMode);
		const currentUrl = currentType ? byType.get(currentType) : undefined;
		// Resolved section missing: applyTrack's selectTrack no-ops there too, so
		// the player stays on the full concatenated track — prefetch that instead.
		if (!currentUrl) return [api.audioUrl(fullAudioId)];

		const nextIdx = (currentEnunIndex + 1) % ENUNCIATION_OPTIONS.length;
		const nextType = resolveSectionType(phase, ENUNCIATION_OPTIONS[nextIdx].level, englishMode);
		const nextUrl = nextType && nextType !== currentType ? byType.get(nextType) : undefined;

		const urls = [api.audioUrl(currentUrl)];
		if (nextUrl) urls.push(api.audioUrl(nextUrl));
		return urls;
	}

	function formatTime(s: number): string {
		const m = Math.floor(s / 60);
		const sec = Math.floor(s % 60);
		return `${m}:${sec.toString().padStart(2, '0')}`;
	}

	onMount(() => {
		captionBlurPref.init();

		// Seed the persisted phase/enunciation/English selection and make it
		// effective. Gated on trackMode: without per-section cues the phase
		// model doesn't apply, so we leave the legacy full-lesson track in
		// place. selectTrack no-ops on a missing section, so a persisted
		// selection that a given lesson can't satisfy safely falls back to the
		// initial track.
		if (trackMode) {
			lessonPlayerPref.init();
			const sel = lessonPlayerPref.selection;
			phase = sel.phase;
			enunLevel = sel.enunciation;
			englishMode = sel.english;
			applyTrack();
		}

		const nav = navigator as Navigator & { connection?: NetworkInformationLike };
		const urls = computePrefetchUrls(
			init.audio.sections,
			init.audio.audio_id,
			trackMode,
			phase,
			enunLevel,
			englishMode,
			enunIndex,
		);
		void maybePrefetchLesson(urls, {
			enabled: prefetchPrefStore.enabled,
			connection: nav.connection,
			caches: (globalThis as { caches?: CacheStorageLike }).caches,
			fetch
		});

		return () => {
			ctrl.destroy();
			controller = null;
		};
	});
</script>

<section class="player" class:compact>

	{#if trackMode}
		<div class="phase-row">
			<button
				class="phase-btn"
				class:active={phase === 'key_phrases'}
				onclick={() => onPhaseClick('key_phrases')}
			>Key Phrases</button>
			<button
				class="phase-btn"
				class:active={phase === 'dialogue'}
				onclick={() => onPhaseClick('dialogue')}
			>Dialogue</button>
		</div>
	{/if}

	<div class="transport-row">
		<button class="ctrl-btn" onclick={() => ctrl.seekBy(-10)} title="Rewind 10s">
			<svg viewBox="0 0 16 16" width="1em" height="1em" style="vertical-align:middle"><polygon points="12,2 4,8 12,14" fill="currentColor"/></svg>10s
		</button>
		<button class="ctrl-btn play-btn" onclick={() => ctrl.togglePlay()} title={ctrl.playing ? 'Pause' : 'Play'}>
			{#if ctrl.playing}
				<svg viewBox="0 0 16 16" width="1.1em" height="1.1em" style="vertical-align:middle"><rect x="3" y="2" width="4" height="12" rx="1" fill="currentColor"/><rect x="9" y="2" width="4" height="12" rx="1" fill="currentColor"/></svg>
			{:else}
				<svg viewBox="0 0 16 16" width="1.1em" height="1.1em" style="vertical-align:middle"><polygon points="4,2 14,8 4,14" fill="currentColor"/></svg>
			{/if}
		</button>
		<button class="ctrl-btn" onclick={() => ctrl.seekBy(10)} title="Forward 10s">
			10s<svg viewBox="0 0 16 16" width="1em" height="1em" style="vertical-align:middle"><polygon points="4,2 12,8 4,14" fill="currentColor"/></svg>
		</button>
	</div>

	{#if hasCues}
		<div class="sentence-row">
			<button class="ctrl-btn small" onclick={() => ctrl.restartSection()} title="Restart section">
				<svg viewBox="0 0 16 16" width="1em" height="1em" style="vertical-align:middle"><rect x="2" y="2" width="2" height="12" rx="1" fill="currentColor"/><polygon points="14,2 6,8 14,14" fill="currentColor"/></svg>
				Section
			</button>
			<button class="ctrl-btn small" onclick={() => ctrl.prevCue()} title="Previous sentence">
				<svg viewBox="0 0 16 16" width="1em" height="1em" style="vertical-align:middle"><polygon points="12,2 4,8 12,14" fill="currentColor"/></svg>
				Sentence
			</button>
			<button class="ctrl-btn small" onclick={() => {
				if (ctrl.currentCue && captionChunks.length > 0) {
					const ms = chunkStartMs(captionChunks, ctrl.currentCue.start_ms, ctrl.currentCue.end_ms, captionIdx);
					ctrl.seekTo(ms / 1000);
				}
			}} title="Repeat current">
				Repeat
				<svg viewBox="0 0 16 16" width="1em" height="1em" style="vertical-align:middle"><path d="M4 8a4 4 0 0 1 7.5-2L10 8h3V4l-1 1a5 5 0 0 0-9 3h1zm8 0a4 4 0 0 1-7.5 2L6 8H3v4l1-1a5 5 0 0 0 9-3h-1z" fill="currentColor"/></svg>
			</button>
			<button class="ctrl-btn small" onclick={() => ctrl.nextCue()} title="Next sentence">
				Sentence
				<svg viewBox="0 0 16 16" width="1em" height="1em" style="vertical-align:middle"><polygon points="4,2 12,8 4,14" fill="currentColor"/></svg>
			</button>
		</div>
	{/if}

	<div class="scrubber-row">
		<input
			type="range"
			min={0}
			max={ctrl.duration || 1}
			step={0.1}
			value={ctrl.currentTime}
			oninput={(e) => ctrl.seekTo(parseFloat((e.target as HTMLInputElement).value))}
			class="scrubber"
		/>
		<div class="time-labels">
			<span>{formatTime(ctrl.currentTime)}</span>
			<span>{formatTime(ctrl.duration)}</span>
		</div>
	</div>

	{#if (trackMode && hasAllSections) || (hasCues && !compact)}
		<!-- Independent setting chips (field:value), NOT a segmented pick-one:
		     the phase row above owns the segmented look. Each chip toggles on its
		     own; the accent (.active) marks a non-default value. -->
		<div class="controls-row">
			{#if trackMode && hasAllSections}
				<button
					class="setting-chip enunciation-btn"
					class:active={enunLevel !== 'natural'}
					onclick={onEnunClick}
					disabled={phase === 'key_phrases'}
				>
					<span class="chip-label">Speed</span>
					<span class="chip-value">{ENUNCIATION_OPTIONS[enunIndex].label}</span>
				</button>
				<button
					class="setting-chip english-btn"
					class:active={englishMode !== 'off'}
					onclick={onEnglishClick}
					disabled={phase === 'key_phrases'}
				>
					<span class="chip-label">English</span>
					<span class="chip-value">{ENGLISH_LABELS[englishMode]}</span>
				</button>
			{/if}
			{#if !compact}
				<button
					class="setting-chip caption-blur-btn"
					class:active={!captionBlurPref.enabled}
					aria-pressed={captionBlurPref.enabled}
					onclick={() => captionBlurPref.set(!captionBlurPref.enabled)}
				>
					<span class="chip-label">Captions</span>
					<span class="chip-value">{captionBlurPref.enabled ? 'Blurred' : 'Visible'}</span>
				</button>
				<!-- Governs what the physical ⏮⏭ (headphone / car) keys skip — a
				     different axis from the on-screen transport pills, hence the
				     headphone marker + dashed treatment to set it apart. -->
				<button
					class="sentence-skip-toggle"
					aria-pressed={ctrl.sentenceSkip}
					title="What the ⏮ ⏭ headphone / car buttons skip"
					onclick={() => ctrl.setSentenceSkip(!ctrl.sentenceSkip)}
				>
					<span class="chip-label">
						<svg viewBox="0 0 16 16" width="0.85em" height="0.85em" style="vertical-align:-1px"><path d="M8 1.5a5.5 5.5 0 0 0-5.5 5.5v3.5a1.5 1.5 0 0 0 1.5 1.5h1v-4h-2V7a5 5 0 0 1 10 0v2h-2v4h1a1.5 1.5 0 0 0 1.5-1.5V7A5.5 5.5 0 0 0 8 1.5z" fill="currentColor"/></svg>
						Keys skip
					</span>
					<span class="chip-value">{ctrl.sentenceSkip ? 'Sentence' : 'Section'}</span>
				</button>
			{/if}
		</div>
	{/if}

	{#if hasCues && !compact}
		<!-- Subtitle sits BELOW the controls: the player is a sticky header, so the
		     line reads nearest the content. Compact (Read mode) omits it — the
		     synced transcript is the subtitle there. -->
		{#if captionBlurPref.enabled}
			<button
				class="current-line blurred"
				class:revealed={captionRevealed}
				title={ctrl.currentCue?.text ?? ''}
				onclick={revealCaption}
				onkeydown={onCaptionKeydown}
			>
				{captionChunks[captionIdx] ?? ''}
			</button>
		{:else}
			<div class="current-line" title={ctrl.currentCue?.text ?? ''}>
				{captionChunks[captionIdx] ?? ''}
			</div>
		{/if}
	{/if}

</section>

<style>
	.player {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.current-line {
		font-size: 1.3rem;
		font-weight: 700;
		line-height: 1.4;
		padding: 0.5rem 0;
		border: none;
		background: none;
		text-align: left;
		width: 100%;
		cursor: default;
		/* Movie-caption line breaks: when a chunk needs two lines, split them
		   evenly instead of filling line 1 and orphaning the last word. */
		text-wrap: balance;
	}
	.current-line.blurred {
		filter: blur(8px);
		cursor: pointer;
		user-select: none;
	}
	.current-line.revealed {
		filter: none;
		cursor: default;
	}
	.transport-row {
		display: flex;
		justify-content: center;
		gap: 0.5rem;
	}
	.ctrl-btn {
		min-width: 48px;
		min-height: 44px;
		white-space: nowrap;
		padding: 0.5rem 0.75rem;
		background: var(--color-surface-2);
		color: var(--color-text);
		border: none;
		border-radius: var(--radius-pill, 999px);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease;
	}
	.ctrl-btn:hover {
		background: var(--color-primary);
		color: var(--color-on-primary);
	}
	/* Play/pause is the primary control: let it grow to fill whatever the two
	   fixed-width 10s buttons leave, so it's the biggest tap target in the row. */
	.play-btn {
		flex: 1;
		min-width: 56px;
		font-size: 1.2rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
	}
	.small {
		min-width: 0;
		min-height: 36px;
		padding: 0.35rem 0.65rem;
		font-size: 0.8rem;
	}
	.sentence-row {
		display: flex;
		justify-content: center;
		align-items: center;
		gap: 0.4rem;
		flex-wrap: wrap;
	}
	/* The hardware-key skip control: a dashed chip so it reads as "settings for
	   the physical ⏮⏭ buttons", distinct from both the filled transport pills
	   and the solid-outline audio setting chips beside it. Sized like the other
	   setting chips it now sits among. */
	.sentence-skip-toggle {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.05rem;
		flex: 1;
		max-width: 9rem;
		min-height: 44px;
		padding: 0.3rem 0.7rem;
		background: transparent;
		color: var(--color-text);
		border: 1px dashed var(--color-border, #ddd);
		border-radius: 10px;
		cursor: pointer;
		transition: border-color 0.15s ease, background 0.15s ease;
	}
	.sentence-skip-toggle .chip-label {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
	}
	.sentence-skip-toggle:hover {
		border-color: var(--color-muted);
	}
	.sentence-skip-toggle[aria-pressed="true"] {
		border-color: var(--color-primary);
		border-style: solid;
		background: color-mix(in srgb, var(--color-primary) 10%, transparent);
	}
	.sentence-skip-toggle[aria-pressed="true"] .chip-value {
		color: var(--color-primary);
	}
	.scrubber-row {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}
	.scrubber {
		width: 100%;
		cursor: pointer;
	}
	.time-labels {
		display: flex;
		justify-content: space-between;
		font-size: 0.75rem;
		color: var(--color-muted);
	}
	.phase-row {
		display: flex;
		justify-content: center;
		background: var(--color-surface-2);
		border-radius: var(--radius-pill, 999px);
		padding: 3px;
		gap: 2px;
	}
	.phase-btn {
		flex: 1;
		padding: 0.4rem 0.8rem;
		border: none;
		border-radius: var(--radius-pill, 999px);
		background: transparent;
		color: var(--color-muted);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease, color 0.15s ease;
	}
	.phase-btn.active {
		background: var(--color-primary);
		color: var(--color-on-primary);
	}
	.phase-btn:hover:not(.active) {
		color: var(--color-text);
	}
	/* Independent setting chips — deliberately NOT the segmented-pill look of
	   .phase-row (that's a pick-one). Each is its own outlined field:value tile
	   so three unrelated settings don't read as one control. */
	.controls-row {
		display: flex;
		justify-content: center;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.setting-chip {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.05rem;
		flex: 1;
		max-width: 9rem;
		min-height: 44px;
		padding: 0.3rem 0.7rem;
		background: transparent;
		color: var(--color-text);
		border: 1px solid var(--color-border, #ddd);
		border-radius: 10px;
		cursor: pointer;
		transition: border-color 0.15s ease, background 0.15s ease;
	}
	.chip-label {
		font-size: 0.6rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--color-muted);
	}
	.chip-value {
		font-size: 0.85rem;
		font-weight: 700;
		line-height: 1.1;
	}
	.setting-chip:hover:not(:disabled) {
		border-color: var(--color-muted);
	}
	.setting-chip.active {
		border-color: var(--color-primary);
		background: color-mix(in srgb, var(--color-primary) 10%, transparent);
	}
	.setting-chip.active .chip-value {
		color: var(--color-primary);
	}
	.setting-chip:disabled {
		opacity: 0.4;
		cursor: default;
	}
	/* Keep the transport pills on one tidy line down to small phones:
	   never let a label wrap inside its pill, and tighten spacing instead. */
	@media (max-width: 430px) {
		.transport-row {
			gap: 0.35rem;
		}
		.ctrl-btn {
			padding: 0.5rem 0.6rem;
			font-size: 0.8rem;
		}
		/* The sentence row's four buttons (Section / Sentence / Repeat /
		   Sentence) must fit one line now that Keys-skip moved to the toggles;
		   tighten padding + gap so the last "Sentence" no longer wraps. */
		.sentence-row {
			gap: 0.3rem;
			flex-wrap: nowrap;
		}
		.sentence-row .small {
			padding: 0.35rem 0.4rem;
			font-size: 0.75rem;
			white-space: nowrap;
		}
	}
</style>
