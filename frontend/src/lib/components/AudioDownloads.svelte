<script lang="ts">
	import { api } from '$lib/api';
	import type { LessonAudio } from '$lib/api';

	/**
	 * The offline-download row, shared by the lesson reader and the review-session
	 * reader.
	 *
	 * ⚠️ Extracted because it is IDENTICAL and LEAF-LIKE, not because the two
	 * tools panels are the same thing — they are not, and a shared `<details>`
	 * shell was tried and rejected. The shell's CSS (`.tools-card hr`,
	 * `.tools-card .muted`) styles content each PAGE passes in, and Svelte scopes
	 * a slot's styles to the parent, so that shell could only work through
	 * `:global()` rules the compiler cannot verify — the exact "page styles across
	 * the component boundary" pattern LessonReader was built to avoid. This
	 * component owns its own markup and its own CSS and nothing crosses.
	 *
	 * `audio.lesson_id` is a content id: the audio routes never learned about
	 * review sessions because `audio_files` joins on an id, and a session id is
	 * an id.
	 */
	let { audio }: { audio: LessonAudio | null } = $props();
</script>

{#if audio}
	<div class="download-links">
		<a class="download-all-btn" href={api.audioZipUrl(audio.lesson_id)} download>
			Download All Sections
		</a>
		{#each audio.sections as sec (sec.audio_id)}
			<a class="section-dl-btn" href={api.audioUrl(sec.audio_id)} download>{sec.title}</a>
		{/each}
	</div>
{/if}

<style>
	.download-links {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.download-all-btn {
		display: inline-block;
		padding: 0.5rem 1.25rem;
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
</style>
