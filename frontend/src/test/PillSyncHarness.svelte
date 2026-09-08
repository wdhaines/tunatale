<script lang="ts">
	// Test harness: renders LessonPlayer and hands its bound controller back so a
	// test can drive an EXTERNAL track change (as a transcript ▶ tap does) and
	// assert the player pills follow. `onSequenceEnd` is forwarded so a test can
	// also observe the hands-free hand-off the page normally acts on.
	// Excluded from coverage via `src/test/**`.
	import LessonPlayer from '$lib/components/LessonPlayer.svelte';
	import type { LessonAudio } from '$lib/api';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';

	let {
		audio,
		onController,
		onSequenceEnd,
		compact = false
	}: {
		audio: LessonAudio;
		onController: (c: PlaybackController) => void;
		onSequenceEnd?: () => void;
		compact?: boolean;
	} = $props();
	let controller = $state<PlaybackController | null>(null);

	$effect(() => {
		if (controller) onController(controller);
	});
</script>

<LessonPlayer {audio} bind:controller {onSequenceEnd} {compact} />
