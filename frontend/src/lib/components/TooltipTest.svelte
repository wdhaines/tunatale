<!-- Test harness: renders Tooltip with a simple text child. -->
<script lang="ts">
	import Tooltip from './Tooltip.svelte';
	import type { WordToken } from '$lib/api';
	import type { TooltipActions } from './Tooltip.svelte';
	interface Props {
		translation?: string | null;
		childText?: string;
		word?: WordToken;
		sentence?: string;
		actions?: TooltipActions;
		suppressed?: boolean;
	}
	let { translation, childText = 'child', word, sentence, actions, suppressed = false }: Props = $props();
</script>

<Tooltip {translation} {word} {sentence} {actions} {suppressed}>
	<span>{childText}</span>
</Tooltip>
