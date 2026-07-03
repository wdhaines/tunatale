<script lang="ts">
	import type { ChatMessage } from '$lib/planner';
	import { clampBatchSize } from '$lib/planner';

	interface Props {
		messages: ChatMessage[];
		pending: boolean;
		batchSize: number;
		onSend: (message: string) => boolean | Promise<boolean>;
	}

	let { messages, pending, batchSize = $bindable(), onSend }: Props = $props();

	let draft = $state('');
	let textareaEl: HTMLTextAreaElement | undefined = $state();

	const canSend = $derived(!pending && draft.trim().length > 0);

	export function focusInput() {
		textareaEl?.focus();
	}

	async function send() {
		if (!canSend) return;
		const message = draft.trim();
		draft = '';
		const ok = await onSend(message);
		// A failed turn persists nothing server-side; restore the typed message
		// so the user doesn't have to retype it — but only if they haven't
		// already started a new draft while the turn was in flight.
		if (ok === false && !draft) draft = message;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}

	function handleBatchSizeChange(e: Event) {
		batchSize = clampBatchSize(Number((e.target as HTMLInputElement).value));
	}
</script>

<div class="planner-chat">
	<div class="messages">
		{#if messages.length === 0}
			<p class="empty-hint">
				Describe what you want to learn — a trip, a theme, a situation — and the planner will
				propose your next days.
			</p>
		{/if}
		{#each messages as msg, i (i)}
			<div class="msg msg-{msg.role}">{msg.content}</div>
		{/each}
		{#if pending}
			<div class="msg msg-planner thinking">…</div>
		{/if}
	</div>

	<div class="composer">
		<textarea
			bind:this={textareaEl}
			bind:value={draft}
			placeholder="Message the planner…"
			rows="2"
			onkeydown={handleKeydown}
		></textarea>
		<div class="controls">
			<label class="batch-size">
				Days per batch
				<input
					type="number"
					min="1"
					max="14"
					value={batchSize}
					onchange={handleBatchSizeChange}
				/>
			</label>
			<button class="quick" onclick={() => onSend(`Plan the next ${batchSize} days.`)} disabled={pending}>
				Plan the next {batchSize} days
			</button>
			<button class="send" onclick={send} disabled={!canSend}>
				{pending ? 'Thinking…' : 'Send'}
			</button>
		</div>
	</div>
</div>

<style>
	.planner-chat {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.messages {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		min-height: 8rem;
	}
	.empty-hint {
		color: var(--color-muted);
		font-size: 0.9rem;
		margin: 0;
	}
	.msg {
		padding: 0.6rem 0.9rem;
		border-radius: var(--radius);
		font-size: 0.92rem;
		white-space: pre-wrap;
		max-width: 85%;
	}
	.msg-user {
		align-self: flex-end;
		background: var(--color-primary);
		color: var(--color-on-primary);
	}
	.msg-planner {
		align-self: flex-start;
		background: var(--color-surface-2);
	}
	.msg-event {
		align-self: center;
		background: none;
		color: var(--color-muted);
		font-size: 0.8rem;
		font-style: italic;
		padding: 0.1rem 0.5rem;
	}
	.thinking {
		opacity: 0.7;
	}
	.composer {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	textarea {
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
	.quick {
		margin-left: auto;
		padding: 0.45rem 0.9rem;
		border: 1px solid var(--color-primary);
		border-radius: var(--radius-pill);
		background: var(--color-surface);
		color: var(--color-primary);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.send {
		padding: 0.45rem 1.1rem;
		border: none;
		border-radius: var(--radius-pill);
		background: var(--color-primary);
		color: var(--color-on-primary);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.quick:disabled,
	.send:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
