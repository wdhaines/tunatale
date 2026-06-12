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
		gradeLabel?: string | null;
		onGrade?: (() => void) | null;
		onDrillIn?: (() => void) | null;
		onChildClick?: () => void;
	}
	let {
		translation,
		childText = 'child',
		word,
		sentence,
		actions,
		suppressed = false,
		gradeLabel = null,
		onGrade = null,
		onDrillIn = null,
		onChildClick
	}: Props = $props();
</script>

<Tooltip {translation} {word} {sentence} {actions} {suppressed} {gradeLabel} {onGrade} {onDrillIn}>
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<span onclick={onChildClick}>{childText}</span>
</Tooltip>
