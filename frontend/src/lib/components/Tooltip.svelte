<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { WordToken } from '$lib/api';

	export interface TooltipActions {
		onCreateInflection?: (word: WordToken, sentence: string) => Promise<void>;
		onSetState?: (id: number, state: string) => Promise<void>;
		onUntrack?: (id: number) => Promise<void>;
	}

	interface Props {
		translation?: string | null;
		state?: string | null;
		children: Snippet;
		word?: WordToken;
		sentence?: string;
		actions?: TooltipActions;
	}

	let { translation, state, children, word, sentence, actions }: Props = $props();

	const STATE_LABELS: Record<string, string> = {
		unknown: 'Unknown',
		new: 'New',
		learning: 'Learning',
		relearning: 'Relearning',
		review: 'Review',
		known: 'Known',
		suspended: 'Suspended'
	};

	const stateLabel = $derived(state ? (STATE_LABELS[state] ?? state) : null);

	const showCreateInflection = $derived(Boolean(word?.inflectable && actions?.onCreateInflection));

	const hasSrsItem = $derived(word != null && word.srs_item_id != null);

	const showIgnore = $derived(
		Boolean(
			hasSrsItem &&
				word!.active_state !== 'unknown' &&
				word!.active_state !== 'suspended'
		)
	);

	const showUnignore = $derived(
		Boolean(hasSrsItem && word!.active_state === 'suspended')
	);

	const showMarkKnown = $derived(
		Boolean(
			hasSrsItem &&
				word!.active_state !== 'unknown' &&
				word!.active_state !== 'known' &&
				word!.active_state !== 'suspended'
		)
	);

	const showResetNew = $derived(
		Boolean(
			hasSrsItem &&
				word!.active_state !== 'unknown' &&
				word!.active_state !== 'new' &&
				word!.active_state !== 'suspended'
		)
	);

	const hasActions = $derived(
		showCreateInflection || showIgnore || showUnignore || showMarkKnown || showResetNew
	);

	const hasContent = $derived(Boolean(translation || stateLabel || hasActions));
</script>

<span class="tt-wrap">
	{@render children()}
	{#if hasContent}
		<span class="tt" role="tooltip" aria-hidden="false">
			{#if translation}<span class="tt-translation">{translation}</span>{/if}
			{#if stateLabel}<span class="tt-state tt-state-{state}">{stateLabel}</span>{/if}
			{#if hasActions}
				<span class="tt-actions">
					{#if showCreateInflection}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onCreateInflection!(word!, sentence ?? '')}
						>Create inflection card</button>
					{/if}
					{#if showUnignore}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onSetState!(word!.srs_item_id!, 'new')}
						>Un-ignore</button>
					{/if}
					{#if showIgnore}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onUntrack!(word!.srs_item_id!)}
						>Ignore</button>
					{/if}
					{#if showMarkKnown}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onSetState!(word!.srs_item_id!, 'known')}
						>Known</button>
					{/if}
					{#if showResetNew}
						<button
							type="button"
							class="tt-btn"
							onclick={() => actions!.onSetState!(word!.srs_item_id!, 'new')}
						>Reset</button>
					{/if}
				</span>
			{/if}
		</span>
	{/if}
</span>

<style>
	.tt-wrap {
		position: relative;
		display: inline;
	}
	.tt {
		position: absolute;
		bottom: 100%;
		left: 50%;
		transform: translateX(-50%);
		background: #111827;
		color: #f9fafb;
		padding: 4px 8px;
		border-radius: 4px;
		font-size: 12px;
		white-space: nowrap;
		z-index: 10;
		opacity: 0;
		pointer-events: none;
		transition: opacity 0.1s;
	}
	.tt-wrap:hover > .tt,
	.tt-wrap:focus-within > .tt {
		opacity: 1;
		pointer-events: auto;
	}
	.tt::before {
		content: '';
		position: absolute;
		top: 100%;
		left: 0;
		right: 0;
		height: 8px;
	}
	.tt-translation {
		font-weight: 500;
	}
	.tt-state {
		margin-left: 6px;
		opacity: 0.75;
		font-size: 11px;
	}
	.tt-actions {
		display: flex;
		gap: 4px;
		margin-top: 4px;
	}
	.tt-btn {
		font-size: 11px;
		padding: 2px 6px;
		background: rgba(255, 255, 255, 0.15);
		color: #f9fafb;
		border: 1px solid rgba(255, 255, 255, 0.3);
		border-radius: 3px;
		cursor: pointer;
		white-space: nowrap;
	}
	.tt-btn:hover {
		background: rgba(255, 255, 255, 0.25);
	}
</style>
