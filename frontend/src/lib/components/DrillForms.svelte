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

	/** A bar's fill, as a whole percent of the way to what the gate needs. */
	function filled(share: number): string {
		return `${Math.round(Math.min(1, share) * 100)}%`;
	}

	/**
	 * The parts of the pace gate beside what each needs (bd tunatale-ve4p.30).
	 * A review-only pattern needs its drill days alone. A part is drawn short
	 * (amber) only once the pattern is stuck: before then it is just not there yet.
	 */
	const meters = $derived.by(() => {
		const p = drill.progress;
		if (!p) return [];
		const rate = p.reviews ? p.passes / p.reviews : 0;
		const parts = [
			{
				label: t('drillForms.drillDays'),
				value: `${p.drill_days} / ${p.drill_days_needed}`,
				share: p.drill_days / p.drill_days_needed,
				met: p.drill_days >= p.drill_days_needed
			}
		];
		if (!p.review_only) {
			parts.push(
				{
					label: t('drillForms.firstPasses'),
					value: `${p.first_passes} / ${p.first_passes_needed}`,
					share: p.first_passes / p.first_passes_needed,
					met: p.first_passes >= p.first_passes_needed
				},
				{
					label: t('drillForms.passRate'),
					value: p.reviews
						? `${Math.round(rate * 100)}% / ${Math.round(p.pass_rate_needed * 100)}%`
						: t('drillForms.noReviews'),
					share: rate / p.pass_rate_needed,
					met: p.reviews > 0 && rate >= p.pass_rate_needed
				}
			);
		}
		return parts.map((part) => ({ ...part, width: filled(part.share), short: p.stuck && !part.met }));
	});

	// One column per form: two for a word pattern, one for a half of a split
	// sentence pattern (on-gi), whose sentence then takes the row.
	const columns = $derived(Math.max(1, ...drill.roots.map((root) => root.forms.length)));
</script>

<h2 class="drill-title">{t('drillForms.heading')}</h2>
{#if drill.labels.length}
	<!-- What the two forms of a root are: named by the language's own plugin,
	     by whether the thing has begun and never by a tense (bd tunatale-ve4p.25). -->
	<div class="drill-labels" style:--forms={columns}>
		<span></span>
		{#each drill.labels as label (label)}
			<span class="label">{label}</span>
		{/each}
	</div>
{/if}
<ul class="drill-roots" style:--forms={columns}>
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
{#if meters.length}
	<div class="meters">
		<div class="meters-head">
			<span class="meters-title">{t('drillForms.progressHeading')}</span>
			{#if drill.progress?.stuck}
				<span class="chip stuck">{t('drillForms.stuck')}</span>
			{:else if drill.progress?.met}
				<span class="chip passed">{t('drillForms.passed')}</span>
			{/if}
		</div>
		{#each meters as meter (meter.label)}
			<div class="meter">
				<span class="meter-label">{meter.label}</span>
				<span class="meter-value">{meter.value}</span>
				<span class="bar"><i class:short={meter.short} style="width: {meter.width}"></i></span>
			</div>
		{/each}
	</div>
{/if}

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
	.drill-root,
	.drill-labels {
		display: grid;
		grid-template-columns: minmax(0, 0.9fr) repeat(var(--forms, 2), minmax(0, 1fr));
		gap: 0.25rem 0.75rem;
		align-items: start;
	}
	.drill-root {
		padding: 0.55rem 0;
		border-top: 1px solid var(--color-border);
	}
	/* The same tracks as a row, so a label sits over its column. */
	.drill-labels {
		padding-bottom: 0.3rem;
		border-bottom: 1px solid var(--color-border);
	}
	.label {
		min-width: 0;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--color-muted);
		overflow-wrap: anywhere;
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
	/* The pace gate's three parts under the table (bd tunatale-ve4p.30). */
	.meters {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		margin-top: 0.6rem;
		padding-top: 0.6rem;
		border-top: 1px solid var(--color-border);
	}
	.meters-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 0.5rem;
	}
	.meters-title {
		font-size: 0.8rem;
		font-weight: 700;
	}
	.chip {
		padding: 0.05rem 0.5rem;
		border: 1px solid currentColor;
		border-radius: var(--radius-pill);
		font-size: 0.65rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}
	.chip.stuck {
		color: var(--color-warning);
	}
	.chip.passed {
		color: var(--color-success);
	}
	.meter {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 0.15rem 0.75rem;
		font-size: 0.8rem;
		font-variant-numeric: tabular-nums;
	}
	.meter-value {
		color: var(--color-muted);
	}
	.bar {
		grid-column: 1 / -1;
		height: 6px;
		border-radius: var(--radius-pill);
		background: var(--color-border);
		overflow: hidden;
	}
	.bar i {
		display: block;
		height: 100%;
		border-radius: inherit;
		background: var(--color-success);
	}
	.bar i.short {
		background: var(--color-warning);
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
