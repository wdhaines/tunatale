<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api } from '$lib/api';
	import type { LessonAudio } from '$lib/api';
	import { maybePrefetchLesson } from '$lib/sw/prefetch';
	import type { NetworkInformationLike } from '$lib/sw/prefetch';
	import type { CacheStorageLike } from '$lib/sw/audio-cache';
	import { prefetchPrefStore } from '$lib/stores/prefetchPref.svelte';
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
		audio: init.audio
	});

	controller = ctrl;

	const hasCues =
		init.audio.cues !== null && init.audio.cues !== undefined && init.audio.cues.length > 0;

	const SPEED_OPTIONS = [0.7, 0.8, 0.85, 0.9, 0.95, 1.0];

	function cycleSpeed() {
		const current = ctrl.playbackRate;
		const idx = SPEED_OPTIONS.indexOf(current);
		const next = SPEED_OPTIONS[(idx + 1) % SPEED_OPTIONS.length];
		ctrl.setRate(next);
	}

	function formatTime(s: number): string {
		const m = Math.floor(s / 60);
		const sec = Math.floor(s % 60);
		return `${m}:${sec.toString().padStart(2, '0')}`;
	}

	onMount(() => {
		const nav = navigator as Navigator & { connection?: NetworkInformationLike };
		const urls = [api.audioUrl(audio.audio_id)];
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

	<div class="transport-row">
		{#if hasCues}
			<button class="ctrl-btn" onclick={() => ctrl.prevSection()} title="Previous section">⏮ Sec</button>
		{/if}
		<button class="ctrl-btn" onclick={() => ctrl.seekBy(-10)} title="Rewind 10s">◀10s</button>
		<button class="ctrl-btn play-btn" onclick={() => ctrl.togglePlay()} title={ctrl.playing ? 'Pause' : 'Play'}>
			{ctrl.playing ? '⏸' : '▶'}
		</button>
		<button class="ctrl-btn" onclick={() => ctrl.seekBy(10)} title="Forward 10s">10s▶</button>
		{#if hasCues}
			<button class="ctrl-btn" onclick={() => ctrl.nextSection()} title="Next section">Sec ⏭</button>
		{/if}
	</div>

	{#if hasCues}
		<div class="sentence-row">
			<button class="ctrl-btn small" onclick={() => ctrl.prevCue()} title="Previous sentence">◀ Sentence</button>
			<button class="ctrl-btn small" onclick={() => ctrl.repeatCue()} title="Repeat current">Repeat ↻</button>
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

	<div class="speed-row">
		<button class="speed-btn" onclick={cycleSpeed}>{ctrl.playbackRate}×</button>
	</div>

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
	.speed-row {
		display: flex;
		justify-content: center;
	}
	.speed-btn {
		min-width: 56px;
		min-height: 40px;
		padding: 0.35rem 0.8rem;
		background: var(--color-surface-2);
		color: var(--color-text);
		border: 1px solid var(--color-border, #ddd);
		border-radius: var(--radius-pill, 999px);
		font-size: 0.9rem;
		font-weight: 600;
		cursor: pointer;
	}
	.speed-btn:hover {
		background: var(--color-primary);
		color: var(--color-on-primary);
		border-color: var(--color-primary);
	}
	/* Keep the five transport pills on one tidy line down to small phones:
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
