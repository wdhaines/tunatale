<script lang="ts">
	import { llmHealthStore } from '$lib/stores/llmHealth.svelte';
	import { rateLimitStore } from '$lib/stores/rateLimit.svelte';

	let probing = $state(false);

	const status = $derived(llmHealthStore.status);

	const isMock = $derived(status?.llm_mode === 'mock');
	const show = $derived(!!status && !status.healthy && !isMock);

	const lastError = $derived(status?.last_error ?? null);
	const fallbackSuffix = $derived(
		status?.fallback_allowed ? ' — using local fallback' : '',
	);

	const label = $derived.by(() => {
		if (!lastError) return `LLM provider failing${fallbackSuffix}. Check GROQ_API_KEY in backend/.env and restart the backend.`;
		const ago = lastError.ago_s < 60
			? `${Math.round(lastError.ago_s)}s ago`
			: `${Math.round(lastError.ago_s / 60)}m ago`;
		return `LLM provider failing — ${lastError.message} (${ago})${fallbackSuffix}. Check GROQ_API_KEY in backend/.env and restart the backend.`;
	});

	async function checkNow() {
		probing = true;
		await rateLimitStore.probe();
		await llmHealthStore.refresh();
		probing = false;
	}
</script>

{#if show}
	<div class="health-banner" role="alert">
		<span class="banner-text">{label}</span>
		<button class="check-btn" onclick={checkNow} disabled={probing}>
			{probing ? 'Checking…' : 'Check now'}
		</button>
	</div>
{/if}

<style>
	.health-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.55rem 0.75rem;
		background: color-mix(in srgb, var(--color-danger) 14%, transparent);
		border-bottom: 1px solid var(--color-danger);
		font-size: 0.85rem;
		color: var(--color-danger);
	}
	.banner-text {
		flex: 1;
		line-height: 1.4;
	}
	.check-btn {
		flex-shrink: 0;
		padding: 0.3rem 0.7rem;
		border: 1px solid var(--color-danger);
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--color-danger);
		font-size: 0.82rem;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease, color 0.15s ease;
	}
	.check-btn:hover:not(:disabled) {
		background: var(--color-danger);
		color: var(--color-on-primary);
	}
	.check-btn:disabled {
		opacity: 0.6;
		cursor: default;
	}
</style>
