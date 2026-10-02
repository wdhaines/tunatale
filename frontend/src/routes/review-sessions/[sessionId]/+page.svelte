<script lang="ts">
	import ReviewSessionReader from './ReviewSessionReader.svelte';
	import type { PageData } from './$types';

	// SvelteKit REUSES a page component when only the route param changes, which
	// is what the hands-free hand-off to the next session does. The reader keeps
	// a dozen pieces of per-session state (audio, transcript, the render poll,
	// errors, the listen result), and it used to keep all of them across that
	// navigation: the title changed and everything under it stayed on the
	// session it came from. The player is keyed on the audio id, so the next
	// session's player was never built and the run stopped at every boundary.
	//
	// Keyed on the id rather than taught to follow `data` field by field (the
	// lesson page's approach): a new instance cannot inherit state, including
	// state added later. A reload of the SAME session (invalidateAll after a
	// rewrite, re-gloss or paste) keeps the instance.
	let { data }: { data: PageData } = $props();
</script>

{#key data.session.id}
	<ReviewSessionReader {data} />
{/key}
