<script lang="ts">
	import { listenedStore } from './listened.svelte';

	let { lessonId }: { lessonId: string } = $props();
	let isListened = $derived(listenedStore.has(lessonId));
</script>

<span data-testid="result">{isListened}</span>
