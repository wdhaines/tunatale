<script lang="ts">
	import type { HTMLButtonAttributes } from 'svelte/elements';

	interface Props extends HTMLButtonAttributes {
		label: string;
		value: string;
		active?: boolean;
		solo?: boolean;
	}

	let { label, value, active, solo, class: className, ...rest }: Props = $props();
</script>

<button {...rest} class={['setting-chip', className]} class:active class:solo>
	<span class="chip-label">{label}</span>
	<span class="chip-value">{value}</span>
</button>

<style>
	/* The row that holds these chips sets the layout, via four custom properties:
	   --setting-chip-flex, --setting-chip-justify, --setting-chip-pad-inline and
	   --setting-chip-label-spacing. Everything else is the shared chip look. */
	.setting-chip {
		margin: 0;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		justify-content: var(--setting-chip-justify, normal);
		gap: 0.05rem;
		flex: var(--setting-chip-flex, 1 1 auto);
		min-width: 0;
		max-width: 9rem;
		min-height: 44px;
		padding: 0.3rem var(--setting-chip-pad-inline, 0.5rem);
		background: transparent;
		color: var(--color-text);
		border: 1px solid var(--color-border, #ddd);
		border-radius: 10px;
		cursor: pointer;
		transition: border-color 0.15s ease, background 0.15s ease;
	}
	.setting-chip:hover:not(:disabled) {
		border-color: var(--color-muted);
	}
	.chip-label {
		font-size: 0.6rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: var(--setting-chip-label-spacing, 0.05em);
		color: var(--color-muted);
		max-width: 100%;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.chip-value {
		font-size: 0.85rem;
		font-weight: 700;
		line-height: 1.1;
		max-width: 100%;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.setting-chip.active {
		border-color: var(--color-primary);
		background: color-mix(in srgb, var(--color-primary) 10%, transparent);
	}
	.setting-chip.active .chip-value {
		color: var(--color-primary);
	}
	.setting-chip:disabled {
		opacity: 0.4;
		cursor: default;
	}
	/* Collapsed player: one line, and exactly as tall as the button beside it
	   (the action row stretches both). */
	.setting-chip.solo {
		flex: 0 0 auto;
		flex-direction: row;
		align-items: center;
		align-self: stretch;
		gap: 0.4rem;
		min-height: 0;
		padding: 0 0.8rem;
		border-radius: var(--radius-pill);
	}
	/* Too narrow for one line beside the button: back to two lines, and the
	   button stretches to the chip instead. */
	@media (max-width: 359px) {
		.setting-chip.solo {
			flex-direction: column;
			align-items: flex-start;
			justify-content: center;
			gap: 0.05rem;
			padding: 0.3rem 0.6rem;
			border-radius: 10px;
		}
	}
</style>
