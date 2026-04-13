<script lang="ts">
	import type { LessonDetail } from '$lib/api';

	interface Props {
		lesson: LessonDetail;
	}

	let { lesson }: Props = $props();

	const SECTION_TITLES: Record<string, string> = {
		key_phrases: 'Key Phrases',
		natural_speed: 'Natural Speed',
		slow_speed: 'Slow Speed',
		translated: 'Translated'
	};
</script>

<section class="script-section">
	<h2>Lesson Script</h2>
	{#each lesson.sections as section}
		<div class="script-block">
			<h3>{SECTION_TITLES[section.type] ?? section.type}</h3>
			{#each section.phrases as phrase}
				<div class="phrase">
					<span class="role">{phrase.role}</span>
					<span class="phrase-text">{phrase.text}</span>
				</div>
			{/each}
		</div>
	{/each}
</section>

<style>
	.script-section {
		margin-top: 2rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}
	.script-block {
		margin-bottom: 1rem;
	}
	.script-block h3 {
		font-size: 0.85rem;
		text-transform: uppercase;
		color: var(--color-muted);
		margin-bottom: 0.5rem;
	}
	.phrase {
		display: flex;
		gap: 0.75rem;
		padding: 0.25rem 0;
		border-bottom: 1px solid var(--color-border);
		font-size: 0.9rem;
	}
	.role {
		color: var(--color-primary);
		min-width: 6rem;
	}
	.phrase-text {
		flex: 1;
	}

	@media (max-width: 640px) {
		.phrase {
			flex-direction: column;
			gap: 0.15rem;
		}
		.role {
			min-width: unset;
			font-size: 0.8rem;
			font-weight: 600;
		}
	}
</style>
