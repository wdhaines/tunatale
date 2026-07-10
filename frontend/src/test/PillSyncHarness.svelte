<script lang="ts">
	// Test harness: renders LessonPlayer and hands its bound controller back so a
	// test can drive an EXTERNAL track change (as a transcript ▶ tap does) and
	// assert the player pills follow. Excluded from coverage via `src/test/**`.
	import LessonPlayer from '$lib/components/LessonPlayer.svelte';
	import type { LessonAudio } from '$lib/api';
	import type { PlaybackController } from '$lib/playback/playbackController.svelte';

	let { audio, onController }: { audio: LessonAudio; onController: (c: PlaybackController) => void } =
		$props();
	let controller = $state<PlaybackController | null>(null);

	$effect(() => {
		if (controller) onController(controller);
	});
</script>

<LessonPlayer {audio} bind:controller />
