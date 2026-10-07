<script lang="ts">
	import CardList from '$lib/components/CardList.svelte';
	import type { CardRow } from '$lib/components/CardList.svelte';
	import { formatSessionDate } from '$lib/reading/sessionDate';
	import { t } from '$lib/i18n/i18n.svelte';

	// Only the fields a row reads: home's session shape (bd tunatale-e6fq) carries
	// these five and not the API's `language_code`, and both lists render here.
	type Session = Pick<
		Awaited<ReturnType<typeof import('$lib/api').api.listReviewSessions>>[number],
		'id' | 'session_date' | 'title' | 'review_requested' | 'review_used'
	>;

	let { sessions }: { sessions: Session[] } = $props();

	function coverageLine(s: Session): string | null {
		// null is "never measured" and gets NO line. [] is a measured zero and
		// gets one — "reused 0 of 5" is a real observation.
		if (s.review_requested === null || s.review_used === null) return null;
		return t('home.reusedOf', { used: s.review_used.length, total: s.review_requested.length });
	}

	let rows = $derived(
		sessions.map((s): CardRow => {
			const line = coverageLine(s);
			return {
				id: s.id,
				href: `/review-sessions/${s.id}`,
				title: s.title,
				meta: formatSessionDate(s.session_date),
				note: line === null ? null : `${line} ${t('home.wordsForgetting')}`,
			};
		}),
	);
</script>

<!--
	Rows render in the order GIVEN: the index orders the list and this renders
	it, so there is one ordering (lib/reading/nextReviewSession.ts).
-->
<CardList {rows} testid="review-session-row" />
