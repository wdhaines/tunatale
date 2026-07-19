<script lang="ts">
	import { api } from '$lib/api';
	import type { ProposedBatch } from '$lib/api';
	import { onDestroy, onMount } from 'svelte';
	import PlannerChat from '$lib/components/PlannerChat.svelte';
	import ProposedBatchView from '$lib/components/ProposedBatch.svelte';
	import RateLimitWidget from '$lib/components/RateLimitWidget.svelte';
	import PipelineCard from '$lib/components/PipelineCard.svelte';
	import LlmActivityLog from '$lib/components/LlmActivityLog.svelte';
	import { appendTurn, commitEvent, type ChatMessage } from '$lib/planner';
	import { pipelineStore } from '$lib/stores/pipeline.svelte';
	import { llmActivityStore } from '$lib/stores/llmActivity.svelte';
	import { rateLimitStore } from '$lib/stores/rateLimit.svelte';
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
	let confirmingReset = $state(false);
	let chat: PlannerChat;

	// Manual mode state
	let generationMode: 'auto' | 'manual' = $state(initial.generation_mode ?? 'auto');
	let modeLoading = $state(false);
	let manualMessage = $state('');
	let copiedPrompt = $state(false);
	let pastedReply = $state('');
	let manualTextarea: HTMLTextAreaElement | undefined = $state();

	const isManual = $derived(generationMode === 'manual');

	const pipelineStatus = $derived(pipelineStore.status);
	const showPipeline = $derived(
		pipelineStatus != null && pipelineStatus.days.some(d => d.state !== 'ready'),
	);

	// The rate-limit widget lives on this page now (not the global nav), so the
	// page owns keeping its store fresh: on mount and after every planner turn
	// (each turn consumes quota; a failed turn may BE the 429 worth showing).
	onMount(() => {
		rateLimitStore.ensureFresh();
		pipelineStore.start(data.curriculum.id);
	});

	async function handleReset() {
		confirmingReset = false;
		pending = true;
		error = '';
		try {
			await api.resetPlanChat(data.curriculum.id);
			messages = [];
			proposed = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			pending = false;
		}
	}

	function handleResetClick() {
		if (confirmingReset) {
			handleReset();
		} else {
			confirmingReset = true;
		}
	}

	function handleResetBlur() {
		confirmingReset = false;
	}

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
			rateLimitStore.refresh();
			llmActivityStore.refresh();
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
			pipelineStore.start(data.curriculum.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			pending = false;
		}
	}

	async function handleToggleMode() {
		modeLoading = true;
		error = '';
		try {
			const newMode = isManual ? 'auto' : 'manual';
			const result = await api.setGenerationMode(data.curriculum.id, newMode);
			generationMode = result.mode as 'auto' | 'manual';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			modeLoading = false;
		}
	}

	function handleEditMessage() {
		// Re-open the message input after a Copy so the user can revise before
		// submitting — the input is frozen between copy and submit so the pasted
		// reply always corresponds to the prompt that was actually copied.
		copiedPrompt = false;
		pastedReply = '';
	}

	async function handleCopyPrompt() {
		error = '';
		try {
			const result = await api.getPlanTurnPrompt(data.curriculum.id, manualMessage, batchSize);
			await navigator.clipboard.writeText(result.system_prompt + '\n\n' + result.user_prompt);
			copiedPrompt = true;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	async function handlePasteSubmit() {
		pending = true;
		error = '';
		try {
			const turn = await api.planTurn(data.curriculum.id, manualMessage, batchSize, pastedReply);
			messages = appendTurn(messages, manualMessage, turn.reply);
			proposed = turn.proposed;
			// Reset for next turn
			manualMessage = '';
			pastedReply = '';
			copiedPrompt = false;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			pending = false;
			rateLimitStore.refresh();
			llmActivityStore.refresh();
		}
	}

	onDestroy(() => {
		pipelineStore.stop();
	});
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
			<div class="head-controls">
				<RateLimitWidget />
				<button
					class="mode-toggle"
					disabled={modeLoading}
					onclick={handleToggleMode}
				>
					{isManual ? 'Auto' : 'Manual'}
				</button>
			</div>
		</header>

		{#if isManual}
			<PlannerChat {messages} {pending} bind:batchSize onSend={handleSend} readonly />
			<div class="manual-flow">
				<div class="manual-input">
				<textarea
					bind:this={manualTextarea}
					placeholder="Message the planner…"
					rows="2"
					bind:value={manualMessage}
					disabled={pending || copiedPrompt}
				></textarea>
					<div class="controls">
						<label class="batch-size">
							Days per batch
							<input
								type="number"
								min="1"
								max="14"
								bind:value={batchSize}
								disabled={pending || copiedPrompt}
							/>
						</label>
						<button
							class="copy-prompt"
							onclick={handleCopyPrompt}
							disabled={pending || copiedPrompt || !manualMessage.trim()}
						>
							Copy prompt
						</button>
					</div>
				</div>
				{#if copiedPrompt}
					<div class="paste-area">
						<textarea
							class="paste-reply"
							placeholder="Paste Claude's reply…"
							rows="4"
							bind:value={pastedReply}
							disabled={pending}
						></textarea>
						<div class="paste-controls">
							<button class="edit-message" onclick={handleEditMessage} disabled={pending}>
								Edit message
							</button>
							<button
								class="send"
								onclick={handlePasteSubmit}
								disabled={pending || !pastedReply.trim()}
							>
								{pending ? 'Submitting…' : 'Submit reply'}
							</button>
						</div>
					</div>
				{/if}
			</div>
		{:else}
			<PlannerChat bind:this={chat} {messages} {pending} bind:batchSize onSend={handleSend} />
		{/if}

		{#if error}
			<p class="error">{error}</p>
		{/if}
		<div class="reset-area">
			<button
				class="reset"
				class:confirming={confirmingReset}
				onclick={handleResetClick}
				onblur={handleResetBlur}
				disabled={pending}
			>
				{confirmingReset ? 'Confirm reset' : 'Reset chat'}
			</button>
		</div>
	</section>

	{#if proposed}
		{@const batch = proposed}
		<ProposedBatchView
			proposed={batch}
			{pending}
			onCommit={() => handleCommit(batch)}
			onRevise={() => isManual ? manualTextarea?.focus() : chat.focusInput()}
		/>
	{/if}

	{#if showPipeline}
		<PipelineCard
			status={pipelineStatus!}
			curriculumId={data.curriculum.id}
			onRefresh={() => pipelineStore.start(data.curriculum.id)}
		/>
	{/if}
	<LlmActivityLog
		events={llmActivityStore.events}
		currentLine={llmActivityStore.currentLine}
		rateLimitStatus={rateLimitStore.status}
	/>
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
	.head-controls {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 0.5rem;
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
	.reset-area {
		display: flex;
		justify-content: flex-end;
		margin-top: 0.75rem;
	}
	.reset {
		padding: 0.5rem 1.1rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.reset:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.reset.confirming {
		border-color: var(--color-danger);
		color: var(--color-danger);
	}
	.mode-toggle {
		padding: 0.35rem 0.8rem;
		border: 1px solid var(--color-primary);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-primary);
		font-size: 0.8rem;
		font-weight: 600;
		cursor: pointer;
	}
	.mode-toggle:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.manual-flow {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.manual-input textarea,
	.paste-reply {
		width: 100%;
		resize: vertical;
		padding: 0.6rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		font: inherit;
		font-size: 0.92rem;
		background: var(--color-surface);
		color: var(--color-text);
		box-sizing: border-box;
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		margin-top: 0.5rem;
	}
	.batch-size {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.8rem;
		color: var(--color-muted);
	}
	.batch-size input {
		width: 3.5rem;
		padding: 0.35rem 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		font: inherit;
		font-size: 0.85rem;
		background: var(--color-surface);
		color: var(--color-text);
	}
	.copy-prompt {
		padding: 0.45rem 0.9rem;
		border: 1px solid var(--color-primary);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-primary);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.copy-prompt:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.paste-area {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.paste-controls {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 0.5rem;
	}
	.edit-message {
		padding: 0.45rem 0.9rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-muted);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.edit-message:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.send {
		align-self: flex-end;
		padding: 0.45rem 1.1rem;
		border: none;
		border-radius: var(--radius-pill);
		background: var(--color-primary);
		color: var(--color-on-primary);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.send:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
