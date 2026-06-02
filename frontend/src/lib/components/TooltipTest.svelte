<!-- Test harness: renders Tooltip with a simple text child. -->
<script lang="ts">
	import Tooltip from './Tooltip.svelte';
	import type { WordToken } from '$lib/api';
	import type { TooltipActions } from './Tooltip.svelte';
	interface Props {
		translation?: string | null;
		state?: string | null;
		childText?: string;
		word?: WordToken;
		sentence?: string;
		actions?: TooltipActions;
	}
	let { translation, state, childText = 'child', word, sentence, actions }: Props = $props();
</script>

<Tooltip {translation} {state} {word} {sentence} {actions}>
	<span>{childText}</span>
</Tooltip>
