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
		color: var(--color-info);
	}

	.learning {
		color: var(--color-warning);
	}

	.underlined {
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.review {
		color: var(--color-success);
	}

	.separator {
		color: var(--color-muted);
		margin: 0 2px;
	}
</style>
