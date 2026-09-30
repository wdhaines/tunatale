<script lang="ts">
	import Banner from '$lib/components/Banner.svelte';
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
	<Banner tone="danger" actionLabel="Check now" busyLabel="Checking…" busy={probing} onaction={checkNow}>
		<span>{label}</span>
	</Banner>
{/if}
