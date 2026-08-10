<script module lang="ts">
	import { mount, unmount } from 'svelte';
	import ConfirmDialog from './ConfirmDialog.svelte';

	let dialogIdCounter = 0;

	export function confirmDialog(
		message: string,
		options?: { destructive?: boolean },
	): Promise<boolean> {
		const host = document.createElement('div');
		host.className = 'confirm-dialog-host';
		document.body.appendChild(host);
		return new Promise<boolean>((resolve) => {
			const component = mount(ConfirmDialog, {
				target: host,
				props: {
					message,
					destructive: options?.destructive ?? false,
					onResolve: (value: boolean) => {
						resolve(value);
						unmount(component);
						host.remove();
					},
				},
			});
		});
	}
</script>

<script lang="ts">
	import { onMount } from 'svelte';

	let {
		message,
		destructive = false,
		onResolve,
	}: {
		message: string;
		destructive?: boolean;
		onResolve: (value: boolean) => void;
	} = $props();

	const headingId = `confirm-dialog-heading-${dialogIdCounter}`;
	const bodyId = `confirm-dialog-body-${dialogIdCounter}`;
	dialogIdCounter += 1;

	let dialogEl: HTMLElement | undefined;
	let cancelEl: HTMLButtonElement | undefined;
	let previousFocus: HTMLElement | null = null;

	onMount(() => {
		previousFocus = document.activeElement as HTMLElement | null;
		cancelEl?.focus();
	});

	function dismiss(value: boolean) {
		previousFocus?.focus();
		onResolve(value);
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			dismiss(false);
			return;
		}
		if (e.key !== 'Tab') return;
		const focusable = dialogEl?.querySelectorAll<HTMLElement>(
			'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
		);
		if (!focusable || focusable.length === 0) return;
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		const inDialog =
			!!dialogEl &&
			(dialogEl === document.activeElement || dialogEl.contains(document.activeElement));
		if (e.shiftKey && (!inDialog || document.activeElement === first)) {
			e.preventDefault();
			last.focus();
		} else if (!e.shiftKey && (!inDialog || document.activeElement === last)) {
			e.preventDefault();
			first.focus();
		}
	}
</script>

<div
	class="overlay"
	role="alertdialog"
	aria-modal="true"
	aria-labelledby={headingId}
	aria-describedby={bodyId}
	tabindex="-1"
	bind:this={dialogEl}
	onkeydown={onKeydown}
	onclick={(e) => {
		if (e.target === e.currentTarget) dismiss(false);
	}}
>
	<div class="modal">
		<h2 id={headingId}>Are you sure?</h2>
		<p id={bodyId} class="body">{message}</p>
		<div class="actions">
			<button class="cancel" type="button" bind:this={cancelEl} onclick={() => dismiss(false)}
				>Cancel</button
			>
			<button class:destructive={destructive} type="button" onclick={() => dismiss(true)}
				>Confirm</button
			>
		</div>
	</div>
</div>

<style>
	.overlay {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 1000;
	}
	.modal {
		box-sizing: border-box;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-md);
		padding: 1.5rem;
		width: min(94%, 420px);
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	h2 {
		margin: 0;
		font-size: 1.1rem;
		font-weight: 700;
	}
	.body {
		margin: 0;
		color: var(--color-muted);
		font-size: 0.9rem;
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
		padding-top: 0.5rem;
		border-top: 1px solid var(--color-border);
	}
	.actions button {
		padding: 0.5rem 1rem;
		border-radius: var(--radius-sm);
		background: var(--color-primary);
		color: #fff;
		border: none;
		font-weight: 600;
		cursor: pointer;
	}
	.actions button.cancel {
		background: var(--color-surface-2);
		color: var(--color-text);
		border: 1px solid var(--color-border);
	}
	.actions button.destructive {
		background: var(--color-danger);
	}
</style>
