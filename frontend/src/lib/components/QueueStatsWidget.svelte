<script lang="ts">
	import type { QueueStats } from '$lib/api';

	let { stats, currentState = null }: { stats: QueueStats; currentState?: string | null } = $props();
</script>

<span class="queue-stats">
	<span class="new" class:underlined={currentState === 'new'}>{stats.new}</span>
	<span class="separator">+</span>
	<span class="learning" class:underlined={currentState === 'learning' || currentState === 'relearning'}>{stats.learning}</span>
	<span class="separator">+</span>
	<span class="review" class:underlined={currentState === 'review'}>{stats.review}</span>
</span>

<style>
	.queue-stats {
		font-weight: 500;
	}

	.new {
		color: #0369a1;
	}

	.learning {
		color: #dc2626;
	}

	.underlined {
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.review {
		color: #059669;
	}

	.separator {
		color: #64748b;
		margin: 0 2px;
	}
</style>
