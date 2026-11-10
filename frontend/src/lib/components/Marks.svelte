<script lang="ts">
	/** Text with the reference pages' inline marks: `*x*` italic, a backquoted span an affix. */
	import { inlineMarks } from '#lib/inlineMarks.js';

	let { text }: { text: string } = $props();
	const marks = $derived(inlineMarks(text));
</script>

{#each marks as mark, i (i)}{#if mark.kind === 'em'}<em>{mark.text}</em>{:else if mark.kind === 'affix'}<code class="affix">{mark.text}</code>{:else}{mark.text}{/if}{/each}

<style>
	.affix {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.92em;
		font-weight: 600;
	}
</style>
