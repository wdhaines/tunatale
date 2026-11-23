<script lang="ts">
	// The curriculum page's "Next up" card (tunatale-cs3p): the first day in
	// display order that is not yet listened. The backend sends days in display
	// order (the book's own, when the plan carries one), so "first" is list order.
	import type { CurriculumSummary } from '#lib/api.js';
	import { listenedStore } from '#lib/stores/listened.svelte.js';
	import { t } from '#lib/i18n/i18n.svelte.js';

	interface Props {
		curriculum: CurriculumSummary;
		progress: Map<number, string>;
		onSelectDay: (day: number) => void | Promise<void>;
	}

	let { curriculum, progress, onSelectDay }: Props = $props();

	const next = $derived(
		curriculum.days.find(d => {
			const lessonId = progress.get(d.day);
			return !lessonId || !listenedStore.has(lessonId);
		}),
	);
	const lessonName = $derived(next ? next.group_title || next.group : '');
</script>

{#if next}
	<button type="button" class="next-up" onclick={() => onSelectDay(next.day)}>
		<span class="text">
			<small>{lessonName ? t('curriculum.nextUpIn', { lesson: lessonName }) : t('curriculum.nextUp')}</small>
			<b>{next.title}</b>
		</span>
		<span aria-hidden="true">▶</span>
	</button>
{/if}

<style>
	.next-up {
		display: flex;
		width: 100%;
		justify-content: space-between;
		align-items: center;
		gap: 0.75rem;
		margin-top: 0.75rem;
		padding: 0.75rem 1rem;
		border: none;
		border-radius: var(--radius-lg);
		background: var(--color-primary);
		color: var(--color-on-primary);
		text-align: left;
		font: inherit;
		cursor: pointer;
	}
	.text {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	small {
		font-size: 0.75rem;
		opacity: 0.85;
	}
	b {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
</style>
