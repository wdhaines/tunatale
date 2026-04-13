<script lang="ts">
	import { api } from '$lib/api';
	import type { LessonAudio } from '$lib/api';

	interface Props {
		audio: LessonAudio;
	}

	let { audio }: Props = $props();
</script>

<section class="audio-section">
	<h2>Audio Player</h2>
	<!-- svelte-ignore a11y_media_has_caption -->
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
					{#each audio.sections as sec}
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
	.audio-section {
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	audio {
		width: 100%;
	}
	.download-sections {
		margin-top: 1rem;
	}
	.download-all-btn {
		display: inline-block;
		padding: 0.5rem 1.25rem;
		background: var(--color-primary);
		color: white;
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
		color: white;
		border-radius: 4px;
		text-decoration: none;
		font-size: 0.85rem;
	}
	.section-dl-btn:hover {
		filter: brightness(0.85);
	}

	@media (max-width: 640px) {
		.download-all-btn {
			display: block;
			text-align: center;
			min-height: 44px;
			line-height: 44px;
			padding: 0 1.25rem;
		}
	}
</style>
