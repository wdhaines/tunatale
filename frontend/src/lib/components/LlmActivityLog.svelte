<script lang="ts">
	import type { ActivityEvent, RateLimitStatus } from '$lib/api';

	interface Props {
		events: ActivityEvent[];
		currentLine: string;
		rateLimitStatus: RateLimitStatus | null;
	}

	let { events, currentLine, rateLimitStatus }: Props = $props();

	const isMock = $derived(rateLimitStatus?.llm_mode === 'mock');

	function eventLine(e: ActivityEvent): string {
		if (e.kind === 'pipeline') {
			return `[pipeline] day ${e.day}: ${e.state} — ${e.message}`;
		}
		return `[llm] ${e.provider}/${e.model} ${e.status} ${e.latency_ms}ms`;
	}
</script>

<div class="activity-card card">
	<h3 class="activity-heading">LLM Activity</h3>
	{#if events.length === 0}
		<p class="empty-state">
			{isMock ? 'Mock mode — LLM activity unavailable' : 'No LLM activity yet'}
		</p>
	{:else}
		<p class="current-line">{currentLine}</p>
		<details class="event-log">
			<summary class="event-summary">{events.length} event{events.length === 1 ? '' : 's'}</summary>
			<ul class="event-list">
				{#each [...events].reverse() as e (e.seq)}
					<li class="event-item event-{e.kind}">{eventLine(e)}</li>
				{/each}
			</ul>
		</details>
	{/if}
</div>

<style>
	.activity-card {
		margin-top: 0.5rem;
		padding: 0.75rem;
	}
	.activity-heading {
		margin: 0 0 0.25rem;
		font-size: 0.9rem;
		font-weight: 700;
	}
	.empty-state {
		margin: 0;
		font-size: 0.82rem;
		color: var(--color-muted);
		font-style: italic;
	}
	.current-line {
		margin: 0 0 0.25rem;
		font-size: 0.82rem;
		color: var(--color-text);
		font-family: monospace;
	}
	.event-summary {
		font-size: 0.8rem;
		color: var(--color-muted);
		cursor: pointer;
	}
	.event-list {
		margin: 0.3rem 0 0;
		padding: 0 0 0 1rem;
		list-style: none;
		font-size: 0.78rem;
		font-family: monospace;
		max-height: 200px;
		overflow-y: auto;
	}
	.event-item {
		padding: 0.1rem 0;
		color: var(--color-text);
	}
	.event-pipeline {
		color: var(--color-info);
	}
	.event-llm_call {
		color: var(--color-text);
	}
</style>
