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
	import { splitCaption, activeChunkIndex } from '$lib/captionChunks';

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

	const totalSections = init.audio.sections.length;

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

	const ENGLISH_LABELS: Record<EnglishMode, string> = {
		off: 'English Off',
		l2_first: 'English After',
		en_first: 'English Before'
	};

	function resolveRate(enunLevel: string): number {
		const opt = ENUNCIATION_OPTIONS.find((o) => o.level === enunLevel);
		return opt?.rate ?? 1.0;
	}

	let phase: Phase = $state('dialogue');
	let enunLevel: string = $state('natural');
	let englishMode: EnglishMode = $state('off');

	// --- Caption blur state ---
	let revealedKey: string | null = $state(null);

	// --- Chunked caption state ---
	const captionChunks = $derived(ctrl.currentCue ? splitCaption(ctrl.currentCue.text) : []);
	const captionIdx = $derived(
		ctrl.currentCue
			? activeChunkIndex(captionChunks, ctrl.currentCue.start_ms, ctrl.currentCue.end_ms, ctrl.currentTime * 1000)
			: 0
	);
	const activeChunkKey = $derived(
		ctrl.currentCue ? `${ctrl.currentCue.index}:${captionIdx}` : ''
	);

	// Re-blur when a new chunk or cue appears
	$effect(() => {
		const _key = activeChunkKey;
		revealedKey = null;
	});

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
		cycleEnunciation();
		applyTrack();
		persistSelection();
	}

	function onEnglishClick() {
		cycleEnglish();
		applyTrack();
		persistSelection();
	}

	function revealCaption() {
		revealedKey = activeChunkKey;
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
	{#if hasCues}
		<div class="section-info">
			<span class="section-title">{ctrl.currentSectionTitle || 'Audio'}</span>
			<span class="section-count">
				{ctrl.currentSectionIndex != null ? ctrl.currentSectionIndex + 1 : '-'}/{totalSections}
			</span>
		</div>
	{/if}

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
			<button class="ctrl-btn small" onclick={() => ctrl.repeatCue()} title="Repeat current">
				Repeat
				<svg viewBox="0 0 16 16" width="1em" height="1em" style="vertical-align:middle"><path d="M4 8a4 4 0 0 1 7.5-2L10 8h3V4l-1 1a5 5 0 0 0-9 3h1zm8 0a4 4 0 0 1-7.5 2L6 8H3v4l1-1a5 5 0 0 0 9-3h-1z" fill="currentColor"/></svg>
			</button>
			<button class="ctrl-btn small" onclick={() => ctrl.nextCue()} title="Next sentence">
				Sentence
				<svg viewBox="0 0 16 16" width="1em" height="1em" style="vertical-align:middle"><polygon points="4,2 12,8 4,14" fill="currentColor"/></svg>
			</button>
			<label class="sentence-skip-toggle">
				<input type="checkbox" checked={ctrl.sentenceSkip} onchange={(e) => ctrl.setSentenceSkip((e.target as HTMLInputElement).checked)} />
				Sentence skip
			</label>
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

	{#if trackMode && hasAllSections}
		<div class="controls-row">
			<button class="enunciation-btn" onclick={onEnunClick}>
				{ENUNCIATION_OPTIONS[enunIndex].label}
			</button>
			<button class="english-btn" onclick={onEnglishClick}>
				{ENGLISH_LABELS[englishMode]}
			</button>
		</div>
	{/if}

	{#if hasCues && !compact}
		<!-- Subtitle sits BELOW the controls: the player is a sticky header, so the
		     line reads nearest the content. Compact (Read mode) omits it — the
		     synced transcript is the subtitle there. -->
		{#if captionBlurPref.enabled}
			<button
				class="current-line blurred"
				class:revealed={revealedKey === activeChunkKey}
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
		<button
			class="caption-blur-btn"
			aria-pressed={captionBlurPref.enabled}
			onclick={() => captionBlurPref.set(!captionBlurPref.enabled)}
		>
			{captionBlurPref.enabled ? 'Blur On' : 'Blur Off'}
		</button>
	{/if}

</section>

<style>
	.player {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.player.compact {
		gap: 0.5rem;
	}
	.section-info {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.section-title {
		font-weight: 600;
	}
	.section-count {
		font-size: 0.8rem;
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
	.caption-blur-btn {
		min-height: 32px;
		padding: 0.25rem 0.6rem;
		background: var(--color-surface-2);
		color: var(--color-text);
		border: 1px solid var(--color-border, #ddd);
		border-radius: var(--radius-pill, 999px);
		font-size: 0.75rem;
		font-weight: 600;
		cursor: pointer;
		align-self: flex-start;
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
	.play-btn {
		min-width: 56px;
		font-size: 1.1rem;
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
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.sentence-skip-toggle {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.8rem;
		color: var(--color-muted);
		cursor: pointer;
	}
	.sentence-skip-toggle input {
		cursor: pointer;
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
	.controls-row {
		display: flex;
		justify-content: center;
		gap: 0.5rem;
	}
	.enunciation-btn,
	.english-btn {
		min-width: 80px;
		min-height: 40px;
		padding: 0.35rem 0.8rem;
		background: var(--color-surface-2);
		color: var(--color-text);
		border: 1px solid var(--color-border, #ddd);
		border-radius: var(--radius-pill, 999px);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease;
	}
	.enunciation-btn:hover,
	.english-btn:hover {
		background: var(--color-primary);
		color: var(--color-on-primary);
		border-color: var(--color-primary);
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
	}
</style>
