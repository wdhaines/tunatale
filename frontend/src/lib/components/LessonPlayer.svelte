<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api } from '$lib/api';
	import type { LessonAudio } from '$lib/api';
	import { maybePrefetchLesson } from '$lib/sw/prefetch';
	import type { NetworkInformationLike } from '$lib/sw/prefetch';
	import type { CacheStorageLike } from '$lib/sw/audio-cache';
	import { prefetchPrefStore } from '$lib/stores/prefetchPref.svelte';
	import { lessonPlayerPref } from '$lib/stores/lessonPlayerPref.svelte';
	import { createPlaybackController } from '$lib/playback/playbackController.svelte';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';

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

	const sectionTypes = new Set(init.audio.sections.map((s) => s.section_type));
	const hasAllSections =
		sectionTypes.has('key_phrases') &&
		sectionTypes.has('natural_speed') &&
		sectionTypes.has('translated') &&
		sectionTypes.has('slow_speed') &&
		sectionTypes.has('slow_translated');

	// --- Phase / Enunciation / English state ---

	const PHASES = ['key_phrases', 'dialogue'] as const;
	type Phase = (typeof PHASES)[number];

	const ENUNCIATION_OPTIONS = [
		{ level: 'natural', label: 'Natural', rate: 1.0 },
		{ level: 'enunciated', label: 'Enunciated', rate: 1.0 },
		{ level: 'enunciated_0.9', label: 'Enun 0.9×', rate: 0.9 },
		{ level: 'enunciated_0.8', label: 'Enun 0.8×', rate: 0.8 },
	] as const;

	function resolveSectionType(phase: Phase, enunLevel: string, engOn: boolean): string | null {
		if (phase === 'key_phrases') return 'key_phrases';
		if (enunLevel === 'natural') return engOn ? 'translated' : 'natural_speed';
		return engOn ? 'slow_translated' : 'slow_speed';
	}

	function resolveRate(enunLevel: string): number {
		const opt = ENUNCIATION_OPTIONS.find((o) => o.level === enunLevel);
		return opt?.rate ?? 1.0;
	}

	let phase: Phase = $state('dialogue');
	let enunLevel: string = $state('natural');
	let englishOn: boolean = $state(false);

	let selectedSectionType = $derived(resolveSectionType(phase, enunLevel, englishOn));
	let enunIndex = $derived(ENUNCIATION_OPTIONS.findIndex((o) => o.level === enunLevel));

	function cycleEnunciation() {
		const nextIdx = (enunIndex + 1) % ENUNCIATION_OPTIONS.length;
		enunLevel = ENUNCIATION_OPTIONS[nextIdx].level;
	}

	function applyTrack() {
		if (selectedSectionType) {
			ctrl.selectTrack(selectedSectionType);
			ctrl.setRate(resolveRate(enunLevel));
		}
	}

	function persistSelection() {
		lessonPlayerPref.set({ phase, enunciation: enunLevel, english: englishOn });
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

	function onEnglishToggle() {
		englishOn = !englishOn;
		applyTrack();
		persistSelection();
	}

	// --- Prefetch section URLs ---

	function formatTime(s: number): string {
		const m = Math.floor(s / 60);
		const sec = Math.floor(s % 60);
		return `${m}:${sec.toString().padStart(2, '0')}`;
	}

	onMount(() => {
		// Seed the persisted phase/enunciation/English selection and make it
		// effective. Gated on hasCues: without cues the phase model doesn't
		// apply, so we leave the legacy full-lesson track in place. selectTrack
		// no-ops on a missing section, so a persisted selection that a given
		// lesson can't satisfy safely falls back to the initial track.
		if (hasCues) {
			lessonPlayerPref.init();
			const sel = lessonPlayerPref.selection;
			phase = sel.phase;
			enunLevel = sel.enunciation;
			englishOn = sel.english;
			applyTrack();
		}

		const nav = navigator as Navigator & { connection?: NetworkInformationLike };
		const sectionUrls = init.audio.sections.map((s) => api.audioUrl(s.audio_id));
		const urls = [api.audioUrl(audio.audio_id), ...sectionUrls];
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

	{#if hasCues}
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

	{#if hasCues && hasAllSections}
		<div class="controls-row">
			<button class="enunciation-btn" onclick={onEnunClick}>
				{ENUNCIATION_OPTIONS[enunIndex].label}
			</button>
			<button class="english-btn" onclick={onEnglishToggle}>
				English {englishOn ? 'On' : 'Off'}
			</button>
		</div>
	{/if}

	{#if hasCues && !compact}
		<!-- Subtitle sits BELOW the controls: the player is a sticky header, so the
		     line reads nearest the content. Compact (Read mode) omits it — the
		     synced transcript is the subtitle there. -->
		<div class="current-line" title={ctrl.currentCue?.text ?? ''}>
			{ctrl.currentCue?.text ?? ''}
		</div>
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
		overflow: hidden;
		text-overflow: ellipsis;
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
