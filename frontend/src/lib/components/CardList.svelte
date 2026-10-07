<script module lang="ts">
	export interface CardRow {
		id: string;
		href: string;
		title: string;
		meta: string;
		/** Third line (phone) / right-hand text (desktop). Absent: no element. */
		note?: string | null;
		/** The one row to do next: primary border, and the note becomes a pill. */
		emphasis?: boolean;
	}
</script>

<script lang="ts">
	let { rows, testid }: { rows: CardRow[]; testid: string } = $props();
</script>

<!--
	The ONE card shape (bd tunatale-e6fq), now a component: home's recent
	lessons and its review sessions both render through it, so one page can't
	look like two. A session is a different KIND of thing, not a different
	kind of list item — bespoke markup per list is what made one page look
	like two.
-->
<ul class="library">
	{#each rows as r (r.id)}
		<li data-testid={testid}>
			<div class="curric-card card" class:emphasis={r.emphasis}>
				<a class="card-link" href={r.href}>
					<span class="topic">{r.title}</span>
					<span class="meta">{r.meta}</span>
				</a>
				{#if r.note}
					<p class="progress-line" class:pill={r.emphasis}>{r.note}</p>
				{/if}
			</div>
		</li>
	{/each}
</ul>

<style>
	/*
		The single home of the card styles: they used to be copied between home
		and the session list, and now both render through this component.
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
		flex-shrink: 0;
	}
	.curric-card.emphasis {
		border-color: var(--color-primary);
	}
	.progress-line.pill {
		align-self: flex-start;
		padding: 0.2rem 0.7rem;
		border-radius: var(--radius-pill);
		background: var(--color-primary);
		color: var(--color-on-primary);
		font-size: 0.78rem;
		font-weight: 600;
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
		.progress-line.pill {
			align-self: center;
		}
	}
</style>
