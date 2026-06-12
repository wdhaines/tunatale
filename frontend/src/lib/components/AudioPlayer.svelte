<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { LessonAudio } from '$lib/api';
	import { maybePrefetchLesson } from '$lib/sw/prefetch';
	import type { NetworkInformationLike, PrefetchCacheStorageLike } from '$lib/sw/prefetch';
	import { prefetchPrefStore } from '$lib/stores/prefetchPref.svelte';

	interface Props {
		audio: LessonAudio;
	}

	let { audio }: Props = $props();

	// On wifi, prefetch this lesson's audio into the service-worker cache so it
	// replays offline later for free. No-op when Cache Storage / wifi-detection
	// is unavailable (all gating lives in maybePrefetchLesson). See
	// docs/offline-audio-plan.md Phase 4.
	onMount(() => {
		const nav = navigator as Navigator & { connection?: NetworkInformationLike };
		const urls = [audio.audio_id, ...audio.sections.map((s) => s.audio_id)].map((id) =>
			api.audioUrl(id)
		);
		void maybePrefetchLesson(urls, {
			enabled: prefetchPrefStore.enabled,
			connection: nav.connection,
			caches: (globalThis as { caches?: PrefetchCacheStorageLike }).caches,
			fetch
		});
	});
</script>

<section class="card">
	<h2>Audio Player</h2>
	<audio controls src={api.audioUrl(audio.audio_id)}>
		Your browser does not support the audio element.
	</audio>

	{#if audio.sections.length > 0}
		<div class="download-sections">
			<a
				class="download-all-btn"
				href={api.audioZipUrl(audio.lesson_id)}
				download
			>Download All Sections</a>

			<details>
				<summary>Individual sections</summary>
				<div class="section-links">
					{#each audio.sections as sec (sec.audio_id)}
						<a
							class="section-dl-btn"
							href={api.audioUrl(sec.audio_id)}
							download
						>{sec.title}</a>
					{/each}
				</div>
			</details>
		</div>
	{/if}
</section>

<style>
	h2 {
		margin-top: 0;
		font-size: 1.1rem;
	}
	audio {
		width: 100%;
	}
	.download-sections {
		margin-top: 1rem;
	}
	.download-all-btn {
		display: block;
		text-align: center;
		min-height: 44px;
		line-height: 44px;
		padding: 0 1.25rem;
		background: var(--color-primary);
		color: var(--color-on-primary);
		border-radius: 4px;
		text-decoration: none;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.download-all-btn:hover {
		filter: brightness(0.9);
	}
	details {
		margin-top: 0.75rem;
	}
	summary {
		cursor: pointer;
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.section-links {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.5rem;
	}
	.section-dl-btn {
		padding: 0.4rem 0.9rem;
		background: var(--color-secondary);
		color: var(--color-on-primary);
		border-radius: 4px;
		text-decoration: none;
		font-size: 0.85rem;
	}
	.section-dl-btn:hover {
		filter: brightness(0.85);
	}

	@media (min-width: 641px) {
		.download-all-btn {
			display: inline-block;
			min-height: 0;
			line-height: normal;
			padding: 0.5rem 1.25rem;
			text-align: left;
		}
	}
</style>
