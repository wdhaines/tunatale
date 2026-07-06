<script lang="ts">
	import { rateLimitStore } from '$lib/stores/rateLimit.svelte';
	import { formatCompactNumber } from '$lib/formatCompactNumber';

	let probing = $state(false);
	let now = $state(Date.now());
	let lastFetchAt = $state(0);

	$effect(() => {
		const id = setInterval(() => {
			now = Date.now();
		}, 1000);
		return () => clearInterval(id);
	});

	$effect(() => {
		if (rateLimitStore.status) {
			lastFetchAt = Date.now();
		}
	});

	const status = $derived(rateLimitStore.status);
	const probeError = $derived(rateLimitStore.probeError);

	const isMock = $derived(status?.llm_mode === 'mock');
	const snapshot = $derived(status?.snapshot ?? null);
	const last429 = $derived(status?.last_429 ?? null);

	const elapsed = $derived(
		lastFetchAt > 0 ? (now - lastFetchAt) / 1000 : 0,
	);

	const retryIn = $derived(
		last429?.retry_in_s != null
			? Math.max(0, Math.round(last429.retry_in_s - elapsed))
			: null,
	);

	const is429Active = $derived(retryIn != null && retryIn > 0);

	const tokensResetIn = $derived(
		snapshot?.tokens_reset_in_s != null
			? Math.max(0, Math.round(snapshot.tokens_reset_in_s - elapsed))
			: null,
	);

	const requestsResetIn = $derived(
		snapshot?.requests_reset_in_s != null
			? Math.max(0, Math.round(snapshot.requests_reset_in_s - elapsed))
			: null,
	);

	const chipLabel = $derived.by(() => {
		if (is429Active) return `Rate limited · ${retryIn}s`;
		if (snapshot) {
			const tokensRem = formatCompactNumber(snapshot.tokens_remaining ?? 0);
			const tokensLim = formatCompactNumber(snapshot.tokens_limit ?? 0);
			const reset = tokensResetIn != null ? String(tokensResetIn) : '?';
			return `LLM ${tokensRem}/${tokensLim} · ↻${reset}s`;
		}
		return 'LLM —/—';
	});

	const tokensPct = $derived(
		snapshot?.tokens_remaining != null && snapshot?.tokens_limit != null
			? snapshot.tokens_remaining / snapshot.tokens_limit
			: null,
	);

	const dailyPct = $derived(
		status?.tokens_used_24h != null && status?.tokens_per_day_limit != null
			? status.tokens_used_24h / status.tokens_per_day_limit
			: null,
	);

	const isWarning = $derived(
		!is429Active &&
			((tokensPct != null && tokensPct < 0.2) || (dailyPct != null && dailyPct > 0.8)),
	);

	const ageElapsed = $derived(
		snapshot?.age_s != null ? Math.round(snapshot.age_s + elapsed) : null,
	);

	const detailTitle = $derived.by(() => {
		const s = status!;
		const parts: string[] = [];
		if (snapshot) {
			parts.push(
				`Tokens/min: ${snapshot.tokens_remaining ?? '?'} of ${snapshot.tokens_limit ?? '?'}` +
					(tokensResetIn != null ? ` (resets in ${tokensResetIn}s)` : ''),
			);
			parts.push(
				`Requests/day: ${snapshot.requests_remaining ?? '?'} of ${snapshot.requests_limit ?? '?'}` +
					(requestsResetIn != null ? ` (resets in ${Math.round(requestsResetIn / 60)}m)` : ''),
			);
		}
		if (s.tokens_used_24h != null && s.tokens_per_day_limit != null) {
			parts.push(
				`~${formatCompactNumber(s.tokens_used_24h)} of ${formatCompactNumber(s.tokens_per_day_limit)} tokens today`,
			);
		}
		if (s.model) parts.push(`Model: ${s.model}`);
		if (ageElapsed != null) parts.push(`As of ${ageElapsed}s ago`);
		return parts.join(' · ');
	});

	async function handleProbe() {
		probing = true;
		await rateLimitStore.probe();
		probing = false;
	}
</script>

{#if !status}
	<span
		class="llm-chip"
		class:busy={probing}
		role="button"
		tabindex="0"
		title={probeError ? probeError : 'No LLM call yet this session — click to check'}
		onclick={handleProbe}
		onkeydown={(e) => e.key === 'Enter' && handleProbe()}
	>
		{probing ? 'LLM …' : probeError ? 'LLM !' : 'LLM —'}
	</span>
{:else if isMock}
	<span class="llm-chip muted" title="Mock mode — quota display unavailable">LLM mock</span>
{:else}
	<span
		class="llm-chip"
		class:danger={is429Active}
		class:warning={isWarning}
		title={detailTitle}
	>
		{chipLabel}
		<button class="probe-btn" title="Refresh quota" onclick={handleProbe}>↻</button>
	</span>
{/if}

<style>
	.llm-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		padding: 0.2rem 0.5rem;
		border-radius: var(--radius-pill);
		font-size: 0.78rem;
		font-weight: 600;
		background: var(--color-surface-2);
		color: var(--color-muted);
		cursor: default;
		white-space: nowrap;
	}
	.llm-chip.busy {
		opacity: 0.7;
	}
	.llm-chip.muted {
		opacity: 0.6;
	}
	.llm-chip.warning {
		color: var(--color-warning);
		background: color-mix(in srgb, var(--color-warning) 14%, transparent);
	}
	.llm-chip.danger {
		color: var(--color-danger);
		background: color-mix(in srgb, var(--color-danger) 14%, transparent);
	}
	.probe-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0;
		margin: 0;
		border: none;
		background: none;
		color: inherit;
		font-size: inherit;
		font-weight: inherit;
		cursor: pointer;
		line-height: 1;
		opacity: 0.7;
		transition: opacity 0.15s ease;
	}
	.probe-btn:hover {
		opacity: 1;
	}
</style>
