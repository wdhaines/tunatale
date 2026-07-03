<script lang="ts">
	import { api } from '$lib/api';
	import type { ProposedBatch } from '$lib/api';
	import PlannerChat from '$lib/components/PlannerChat.svelte';
	import ProposedBatchView from '$lib/components/ProposedBatch.svelte';
	import { appendTurn, commitEvent, type ChatMessage } from '$lib/planner';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The loader snapshot deliberately seeds session state once — after mount
	// this page owns the live copy (turns and commits mutate it locally).
	// svelte-ignore state_referenced_locally
	const initial = data.curriculum;

	// Session-local transcript: the server keeps the full chat for prompt
	// context, but GET /{id} doesn't expose it — returning users get the
	// committed-day count as context instead.
	const initialMessages: ChatMessage[] =
		initial.days.length > 0
			? [{ role: 'event', content: `${initial.days.length} days committed so far.` }]
			: [];

	let messages: ChatMessage[] = $state(initialMessages);
	let proposed: ProposedBatch | null = $state(initial.proposed);
	let committedCount = $state(initial.days.length);
	let pending = $state(false);
	let batchSize = $state(5);
	let error = $state('');
	let chat: PlannerChat;

	async function handleSend(message: string): Promise<boolean> {
		pending = true;
		error = '';
		try {
			const turn = await api.planTurn(data.curriculum.id, message, batchSize);
			messages = appendTurn(messages, message, turn.reply);
			proposed = turn.proposed;
			return true;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			return false;
		} finally {
			pending = false;
		}
	}

	async function handleCommit(batch: ProposedBatch) {
		pending = true;
		error = '';
		try {
			const result = await api.commitPlan(data.curriculum.id);
			messages = [...messages, commitEvent(batch)];
			committedCount = result.days;
			proposed = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			pending = false;
		}
	}
</script>

<main>
	<a class="back" href="/c/{data.curriculum.id}">← {data.curriculum.topic}</a>
	<section class="card">
		<header class="plan-head">
			<h2>{data.curriculum.topic}</h2>
			<p class="meta">
				{data.curriculum.cefr_level} · {committedCount}
				{committedCount === 1 ? 'day' : 'days'} committed
			</p>
		</header>

		<PlannerChat bind:this={chat} {messages} {pending} bind:batchSize onSend={handleSend} />

		{#if error}
			<p class="error">{error}</p>
		{/if}
	</section>

	{#if proposed}
		{@const batch = proposed}
		<ProposedBatchView
			proposed={batch}
			{pending}
			onCommit={() => handleCommit(batch)}
			onRevise={() => chat.focusInput()}
		/>
	{/if}
</main>

<style>
	main {
		max-width: 700px;
		margin: 1.5rem auto;
		padding: 0 1rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.back {
		display: inline-block;
		color: var(--color-muted);
		text-decoration: none;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.back:hover {
		color: var(--color-primary);
	}
	.card {
		padding: 1.25rem;
	}
	.plan-head {
		margin-bottom: 1rem;
	}
	h2 {
		margin: 0;
		font-size: 1.4rem;
		font-weight: 800;
		letter-spacing: -0.01em;
	}
	.meta {
		color: var(--color-muted);
		font-size: 0.85rem;
		margin: 0.25rem 0 0;
	}
	.error {
		color: var(--color-danger);
		margin: 0.75rem 0 0;
		font-size: 0.9rem;
	}
</style>
