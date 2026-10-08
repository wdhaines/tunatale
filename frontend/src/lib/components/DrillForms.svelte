<script lang="ts">
	import type { LessonDrill } from '$lib/api';
	import { captionBlurPref } from '$lib/stores/captionBlurPref.svelte';
	import { t } from '$lib/i18n/i18n.svelte';

	/**
	 * What a lesson's affix drill covers: each root and its forms (bd
	 * tunatale-ve4p.5). It stands where the transcript would on a lesson that is
	 * only a drill.
	 *
	 * The forms are the ANSWERS the audio is about to ask for, so they follow the
	 * Captions setting: blurred while captions are blurred, each shown by a tap.
	 * A page that printed them beside the player would turn the drill into
	 * reading aloud.
	 */
	interface Props {
		drill: LessonDrill;
	}

	let { drill }: Props = $props();

	let revealed: Set<string> = $state(new Set());

	function reveal(form: string) {
		revealed = new Set([...revealed, form]);
	}
</script>

<h2 class="drill-title">{t('drillForms.heading')}</h2>
<ul class="drill-roots">
	{#each drill.roots as root (root.root)}
		<li class="drill-root">
			<div class="root-cell">
				<span class="root">{root.root}</span>
				<span class="english">{root.english}</span>
				{#if root.new}
					<span class="new-tag" title={t('drillForms.newTitle')}>{t('drillForms.new')}</span>
				{/if}
			</div>
			{#each root.forms as cell (cell.form)}
				{@const hidden = captionBlurPref.enabled && !revealed.has(cell.form)}
				<div class="form-cell">
					{#if hidden}
						<button
							type="button"
							class="form blurred"
							aria-label={t('drillForms.reveal', { english: cell.english })}
							onclick={() => reveal(cell.form)}
						>{cell.form}</button>
					{:else}
						<span class="form">{cell.form}</span>
					{/if}
					<span class="english">{cell.english}</span>
				</div>
			{/each}
		</li>
	{/each}
</ul>

<style>
	.drill-title {
		margin: 0 0 0.75rem;
		font-size: 1rem;
		font-weight: 700;
	}
	.drill-roots {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
	}
	/* Root, then one column per form. minmax(0, …) on every track so a long
	   form wraps inside its cell instead of widening the page on a phone. */
	.drill-root {
		display: grid;
		grid-template-columns: minmax(0, 0.9fr) minmax(0, 1fr) minmax(0, 1fr);
		gap: 0.25rem 0.75rem;
		align-items: start;
		padding: 0.55rem 0;
		border-top: 1px solid var(--color-border);
	}
	.drill-root:first-child {
		border-top: none;
	}
	.root-cell,
	.form-cell {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		min-width: 0;
	}
	/* A root is one word and cannot wrap at a space; a long one has to break
	   or it runs over the first form (seen at 320px, 2026-10-07). */
	.root {
		font-weight: 700;
		overflow-wrap: anywhere;
	}
	.form {
		font-weight: 600;
		color: var(--color-text);
		overflow-wrap: anywhere;
	}
	button.form {
		padding: 0;
		border: none;
		background: none;
		font: inherit;
		font-weight: 600;
		text-align: left;
		cursor: pointer;
	}
	.form.blurred {
		filter: blur(5px);
	}
	.english {
		font-size: 0.8rem;
		color: var(--color-muted);
	}
	.new-tag {
		margin-top: 0.15rem;
		padding: 0.05rem 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		font-size: 0.65rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--color-muted);
	}
</style>
