<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import logo from '$lib/assets/logo.png';
	import { t } from '$lib/i18n/i18n.svelte';

	let token = $state('');
	let email = $state('');
	let password = $state('');
	let error = $state('');
	let busy = $state(false);

	// The invite link is `/invite#<token>`: the token is in the FRAGMENT, so it
	// is never part of a requested URL and stays out of the proxy's access log;
	// the server sees it once, in the redeem request's body. Read once on mount
	// — a person without the link can type the token into the field instead.
	onMount(() => {
		token = window.location.hash.replace(/^#/, '');
	});

	// `Error: ` from String(err), then the `METHOD /path: ` prefix api.ts adds so
	// a thrown message names its request. Neither belongs in front of a person.
	function humanise(err: unknown): string {
		return String(err)
			.replace(/^Error:\s*/, '')
			.replace(/^[A-Z]+ \/\S+:\s*/, '');
	}

	async function handleSubmit(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		busy = true;
		error = '';
		try {
			// Redeeming creates the account but no session, so this lands on the
			// login page rather than signing the person in here.
			await api.redeemInvite(token.trim(), email.trim(), password);
			await goto('/login');
		} catch (err) {
			error = humanise(err);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{t('invite.pageTitle')} · TunaTale</title></svelte:head>

<main>
	<form class="card" onsubmit={handleSubmit}>
		<img class="mark" src={logo} alt="" />
		<h1>TunaTale</h1>
		<p class="lede">{t('invite.lede')}</p>

		<label for="token">{t('invite.tokenLabel')}</label>
		<input
			id="token"
			type="text"
			autocomplete="off"
			autocapitalize="none"
			autocorrect="off"
			required
			bind:value={token}
		/>

		<label for="email">{t('invite.emailLabel')}</label>
		<input
			id="email"
			type="email"
			autocomplete="username"
			autocapitalize="none"
			autocorrect="off"
			required
			bind:value={email}
		/>

		<label for="password">{t('invite.passwordLabel')}</label>
		<input
			id="password"
			type="password"
			autocomplete="new-password"
			minlength="8"
			required
			bind:value={password}
		/>

		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}

		<button type="submit" disabled={busy}>{busy ? t('invite.redeeming') : t('invite.redeem')}</button>
	</form>
</main>

<style>
	main {
		min-height: 100dvh;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 1.5rem;
	}
	form {
		width: 100%;
		max-width: 22rem;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.mark {
		width: 44px;
		height: 44px;
		align-self: center;
	}
	h1 {
		margin: 0.35rem 0 0;
		text-align: center;
		font-size: 1.3rem;
		letter-spacing: -0.01em;
		color: var(--color-brand);
	}
	.lede {
		margin: 0 0 0.9rem;
		text-align: center;
		color: var(--color-muted);
		font-size: 0.9rem;
	}
	label {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--color-secondary);
	}
	input {
		padding: 0.55rem 0.7rem;
		margin-bottom: 0.45rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		background: var(--color-bg);
		color: var(--color-text);
		font-size: 1rem;
	}
	input:focus-visible {
		border-color: var(--color-primary);
	}
	.error {
		margin: 0 0 0.5rem;
		padding: 0.5rem 0.65rem;
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--color-danger) 12%, transparent);
		color: var(--color-danger);
		font-size: 0.85rem;
	}
	button {
		margin-top: 0.35rem;
		padding: 0.6rem 1rem;
		border: none;
		border-radius: var(--radius-pill);
		background: var(--color-primary);
		color: var(--color-on-primary);
		font-size: 0.95rem;
		font-weight: 700;
		cursor: pointer;
	}
	button:hover:not(:disabled) {
		background: var(--color-primary-hover);
	}
	button:disabled {
		opacity: 0.6;
		cursor: progress;
	}
</style>
