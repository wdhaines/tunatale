<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { authStore } from '$lib/stores/auth.svelte';
	import logo from '$lib/assets/logo.png';

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let busy = $state(false);

	/**
	 * Where to land after a successful sign-in.
	 *
	 * `next` arrives in the query string, so it is whatever the last person to
	 * send this user a link decided — the classic open-redirect input. Only a
	 * same-origin ABSOLUTE PATH is accepted, and the rejections are all real
	 * payloads: `https://evil.example/x` (absolute), `//evil.example/x`
	 * (protocol-relative — the browser reads it as a host, not a path), and
	 * `/\evil.example` (browsers normalise the backslash to `/`, so it is a
	 * protocol-relative URL wearing a costume). Anything else → the root.
	 */
	function safeNext(raw: string | null): string {
		if (!raw) return '/';
		if (!raw.startsWith('/')) return '/';
		if (raw.startsWith('//') || raw.startsWith('/\\')) return '/';
		return raw;
	}

	// `Error: ` from String(err), then the `METHOD /path: ` prefix api.ts adds so
	// a thrown message names its request. Neither belongs in front of a person.
	function humanise(err: unknown): string {
		let msg = String(err)
			.replace(/^Error:\s*/, '')
			.replace(/^[A-Z]+ \/\S+:\s*/, '');
		const retryAfter = (err as { retryAfter?: number } | undefined)?.retryAfter;
		if (retryAfter) {
			const wait =
				retryAfter > 90
					? `about ${Math.round(retryAfter / 60)} minutes`
					: `${retryAfter} seconds`;
			msg += `. Try again in ${wait}`;
		}
		return msg;
	}

	async function handleSubmit(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		busy = true;
		error = '';
		try {
			await authStore.login(email, password);
			await goto(safeNext($page.url.searchParams.get('next')));
		} catch (err) {
			error = humanise(err);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Sign in · TunaTale</title></svelte:head>

<main>
	<form class="card" onsubmit={handleSubmit}>
		<img class="mark" src={logo} alt="" />
		<h1>TunaTale</h1>
		<p class="lede">Sign in to continue.</p>

		<label for="email">Email</label>
		<!-- svelte-ignore a11y_autofocus -->
		<input
			id="email"
			type="email"
			autocomplete="username"
			autocapitalize="none"
			autocorrect="off"
			autofocus
			required
			bind:value={email}
		/>

		<label for="password">Password</label>
		<input
			id="password"
			type="password"
			autocomplete="current-password"
			required
			bind:value={password}
		/>

		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}

		<button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
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
