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
			<h3>Download Sections</h3>
			<div class="section-links">
				{#each audio.sections as sec}
					<a
						class="section-dl-btn"
						href={api.audioUrl(sec.audio_id)}
						download
					>{sec.title}</a>
				{/each}
			</div>
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
	.download-sections h3 {
		font-size: 0.9rem;
		color: var(--color-muted);
		margin-bottom: 0.5rem;
	}
	.section-links {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
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
		background: var(--color-secondary);
		filter: brightness(0.85);
	}
</style>
