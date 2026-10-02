<script lang="ts">
	import { formatSessionDate } from '$lib/reading/sessionDate';
	import { t } from '$lib/i18n/i18n.svelte';

	type Session = Awaited<ReturnType<typeof import('$lib/api').api.listReviewSessions>>[number];

	let { sessions }: { sessions: Session[] } = $props();

	function coverageLine(s: Session): string | null {
		// null is "never measured" and gets NO line. [] is a measured zero and
		// gets one — "reused 0 of 5" is a real observation.
		if (s.review_requested === null || s.review_used === null) return null;
		return t('home.reusedOf', { used: s.review_used.length, total: s.review_requested.length });
	}
</script>

<!--
	The SAME card shape the curricula use on home — .library / .curric-card /
	.card-link / .topic / .meta. A session is a different KIND of thing, not a
	different kind of list item, and giving it bespoke markup made one page look
	like two.

	Rows render in the order GIVEN: the index orders the list and this renders
	it, so there is one ordering (lib/reading/nextReviewSession.ts).
-->
<ul class="library">
	{#each sessions as s (s.id)}
		{@const line = coverageLine(s)}
		<li data-testid="review-session-row">
			<div class="curric-card card">
				<a class="card-link" href="/review-sessions/{s.id}">
					<span class="topic">{s.title}</span>
					<span class="meta">{formatSessionDate(s.session_date)}</span>
				</a>
				{#if line}
					<p class="progress-line">{line} {t('home.wordsForgetting')}</p>
				{/if}
			</div>
		</li>
	{/each}
</ul>

<style>
	/*
		Copied verbatim from home's list, which still carries its own copy of these
		rules for the curriculum cards above its session list. A later home stage
		collapses the two; until then they are two copies of one design, not two
		designs.
	*/
	.library {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.75rem;
	}
	.curric-card {
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		padding: 1rem 1.25rem;
		transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.1s ease;
	}
	.curric-card:hover {
		border-color: var(--color-primary);
		box-shadow: var(--shadow);
		transform: translateY(-1px);
	}
	.card-link {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		text-decoration: none;
		color: var(--color-text);
	}
	.topic {
		font-size: 1.05rem;
		font-weight: 600;
	}
	.meta {
		color: var(--color-muted);
		font-size: 0.8rem;
		flex-shrink: 0;
	}
	.progress-line {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-muted);
	}

	@media (min-width: 641px) {
		.curric-card {
			flex-direction: row;
			align-items: center;
			justify-content: space-between;
			gap: 1.5rem;
		}
		.card-link {
			flex: 1 1 auto;
			flex-direction: row;
			align-items: baseline;
			justify-content: space-between;
			gap: 1rem;
		}
	}
</style>