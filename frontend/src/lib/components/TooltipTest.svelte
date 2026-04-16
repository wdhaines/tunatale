<!-- Test harness: renders Tooltip with a simple text child. -->
<script lang="ts">
	import Tooltip from './Tooltip.svelte';
	interface Props {
		translation?: string | null;
		state?: string | null;
		childText?: string;
	}
	let { translation, state, childText = 'child' }: Props = $props();
</script>

<Tooltip {translation} {state}>
	<span>{childText}</span>
</Tooltip>
