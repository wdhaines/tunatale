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
		onChildClick?: () => void;
	}
	let {
		translation,
		childText = 'child',
		word,
		sentence,
		actions,
		suppressed = false,
		onChildClick
	}: Props = $props();
</script>

<Tooltip {translation} {word} {sentence} {actions} {suppressed}>
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<span onclick={onChildClick}>{childText}</span>
</Tooltip>
