<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { LessonAudio, ReadableLesson, TranscriptData } from '$lib/api';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';
	import type { createReadingActions } from '$lib/reading/readingActions.svelte';
	import { lessonModePref } from '$lib/stores/lessonModePref.svelte';
	import LessonPlayer from './LessonPlayer.svelte';
	import ReadListenToggle from './ReadListenToggle.svelte';
	import Transcript from './Transcript.svelte';
	import TranscriptPlaceholder from './TranscriptPlaceholder.svelte';

	/**
	 * The reading surface: sticky player card, Read/Listen toggle, player, and
	 * the transcript below it. Shared by the lesson page and the review-session
	 * reader (bd tunatale-9p9d).
	 *
	 * ⚠️ IT EXISTS TO MAKE DIVERGENCE IMPOSSIBLE, not merely unlikely. The two
	 * surfaces had already drifted twice — a hand-rolled transcript, then a
	 * missing Read/Listen toggle — each time because the shell was written out
	 * per page and nothing forced them to agree. Now the shell is one component;
	 * a change to the sticky card, the mode gate or the transcript block reaches
	 * both by construction.
	 *
	 * What legitimately differs goes in through SNIPPETS, and the list is short
	 * and principled: a lesson has a curriculum breadcrumb, a day pager, a
	 * mastery line, a render button and listen actions; a session has none of
	 * those because it has no curriculum and no day. Anything that is NOT about
	 * that distinction belongs in here, not in a snippet.
	 */
	interface Props {
		title: string;
		/** The lesson or session being read — only what the reading UI needs. */
		content: ReadableLesson;
		audio: LessonAudio | null;
		transcript: TranscriptData | null;
		transcriptLoading: boolean;
		reading: ReturnType<typeof createReadingActions>;
		/** Sticky offset, so the card sits directly below the global nav. */
		navHeight?: number;
		controller?: PlaybackController | null;
		/** Full-width band ABOVE the title row: breadcrumb, day pager. */
		headerAbove?: Snippet;
		/** Column one of the title row; the toggle takes column two. */
		header: Snippet;
		/** Full-width band BELOW the title row: mastery, coverage, status. */
		headerBelow?: Snippet;
		/** Shown in place of the player when there is no audio yet. */
		noAudio?: Snippet;
		/** The action row at the foot of the sticky card. */
		actions?: Snippet;
	}

	let {
		title,
		content,
		audio,
		transcript,
		transcriptLoading,
		reading,
		navHeight = 0,
		controller = $bindable(null),
		headerAbove,
		header,
		headerBelow,
		noAudio,
		actions
	}: Props = $props();

	const mode = $derived(lessonModePref.mode);
</script>

<!-- The sticky card owns everything reached for mid-lesson: the title, the
     Read/Listen toggle, and (once rendered) the player. It sticks below the
     global nav so nothing needed scrolls away. -->
<section class="card player-card" style="top: {navHeight}px">
	<!-- Two-column grid, not a flex row: the title and toggle share row 1, but a
	     stats line below spans BOTH columns. As a flex child of the title column
	     it inherited that column's width and wrapped to two lines on a phone. -->
	<div class="player-header">
		<!-- Three explicit slots rather than letting callers style across the
		     component boundary. The full-width bands are their own grid children
		     here, so a page never needs a :global() rule to span this grid — which
		     Svelte cannot statically verify and therefore reports as dead CSS. -->
		{#if headerAbove}
			<div class="header-band">{@render headerAbove()}</div>
		{/if}
		{@render header()}
		<ReadListenToggle />
		{#if headerBelow}
			<div class="header-band">{@render headerBelow()}</div>
		{/if}
	</div>
	{#if audio}
		{#key audio.audio_id}
			<!-- ONE persistent player across modes: only `compact` flips on
			     Listen↔Read, so the controller (and playback) survives the switch. -->
			<LessonPlayer
				{audio}
				compact={mode !== 'listen'}
				lessonTitle={title}
				bind:controller
			/>
		{/key}
	{:else if noAudio}
		{@render noAudio()}
	{/if}
	{#if actions}
		{@render actions()}
	{/if}
</section>

{#if mode === 'read'}
	<section class="card">
		{#if transcript}
			<Transcript {transcript} lesson={content} {controller} {...reading.transcriptProps} />
		{:else if transcriptLoading}
			<TranscriptPlaceholder lesson={content} />
		{:else}
			<p class="muted">No transcript available.</p>
		{/if}
	</section>
{/if}

<style>
	.player-card {
		position: sticky;
		/* Above transcript content, below word tooltips (z 30) and the global nav
		   (z 50). The tooltips used to sit UNDER this card at z 10 — deliberately,
		   per an earlier version of this comment — and that was wrong: a popover the
		   user long-pressed a specific word to summon is useless occluded, while this
		   card is persistent and always reachable (F-21). */
		z-index: 20;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		padding: 0.9rem 1.1rem;
	}
	.player-header {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: start;
		column-gap: 1rem;
		row-gap: 0.2rem;
	}
	.header-band {
		grid-column: 1 / -1;
		min-width: 0;
	}
	.muted {
		color: var(--color-muted);
	}
</style>
