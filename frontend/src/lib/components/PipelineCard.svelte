<script lang="ts">
	import { api } from '$lib/api';
	import type { PipelineStatus } from '$lib/api';

	interface Props {
		status: PipelineStatus;
		curriculumId: string;
		onRefresh?: () => void;
	}

	let { status, curriculumId, onRefresh = () => {} }: Props = $props();

	let retrying = $state<number | null>(null);

	async function handleRetry(day: number) {
		retrying = day;
		try {
			await api.retryPipelineDay(curriculumId, day);
			onRefresh();
		} catch {
			// Error is surfaced through the store's error state
		} finally {
			retrying = null;
		}
	}
</script>

{#if status.days.length > 0}
	<div class="pipeline-card card">
		<h3 class="pipeline-heading">Pipeline</h3>
		{#each status.days as d (d.day)}
			<div class="pipeline-row">
				<span class="day-label">Day {d.day}</span>
				<span class="state-badge state-{d.state}">{d.state}</span>
				<span class="detail-line">{d.detail ?? ''}</span>
				<span class="actions">
					{#if d.state === 'ready' && d.lesson_id}
						<a href="/c/{curriculumId}/l/{d.lesson_id}" class="listen-link">Listen →</a>
					{/if}
					{#if d.state === 'failed' && d.retryable}
						<button
							class="retry-btn"
							disabled={retrying === d.day}
							onclick={() => handleRetry(d.day)}
						>
							{retrying === d.day ? '…' : 'Retry'}
						</button>
					{/if}
				</span>
			</div>
		{/each}
	</div>
{/if}

<style>
	.pipeline-card {
		margin-top: 0.5rem;
		padding: 0.75rem;
	}
	.pipeline-heading {
		margin: 0 0 0.5rem;
		font-size: 0.95rem;
		font-weight: 700;
	}
	.pipeline-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.3rem 0;
		font-size: 0.85rem;
	}
	.day-label {
		font-weight: 600;
		min-width: 3.5rem;
		color: var(--color-text);
	}
	.state-badge {
		padding: 0.1rem 0.45rem;
		border-radius: var(--radius-pill);
		font-size: 0.78rem;
		font-weight: 600;
		min-width: 5rem;
		text-align: center;
	}
	.state-queued {
		background: var(--color-surface-2);
		color: var(--color-muted);
	}
	.state-generating {
		background: color-mix(in srgb, var(--color-info) 14%, transparent);
		color: var(--color-info);
	}
	.state-rendering {
		background: color-mix(in srgb, var(--color-accent) 14%, transparent);
		color: var(--color-accent);
	}
	.state-ready {
		background: color-mix(in srgb, var(--color-success) 14%, transparent);
		color: var(--color-success);
	}
	.state-failed {
		background: color-mix(in srgb, var(--color-danger) 14%, transparent);
		color: var(--color-danger);
	}
	.detail-line {
		flex: 1;
		color: var(--color-muted);
		font-size: 0.8rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.actions {
		display: flex;
		gap: 0.4rem;
		align-items: center;
		min-width: 5rem;
		justify-content: flex-end;
	}
	.listen-link {
		color: var(--color-primary);
		font-weight: 600;
		text-decoration: none;
		font-size: 0.82rem;
	}
	.listen-link:hover {
		text-decoration: underline;
	}
	.retry-btn {
		padding: 0.15rem 0.6rem;
		border: 1px solid var(--color-danger);
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--color-danger);
		font-size: 0.78rem;
		font-weight: 600;
		cursor: pointer;
	}
	.retry-btn:hover:not(:disabled) {
		background: color-mix(in srgb, var(--color-danger) 10%, transparent);
	}
	.retry-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
