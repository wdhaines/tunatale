<script lang="ts">
	import { page } from '$app/state';
	import GradePage from '#lib/components/GradePage.svelte';
	import { safeBack } from '#lib/grading/grade.js';
	import { t } from '#lib/i18n/i18n.svelte.js';

	// One route for a lesson or a review session: the plan keys on a content id
	// (tunatale-5i1p.3). `back` and `title` come from the page that linked here.
	const contentId = $derived(page.params.contentId as string);
	const backHref = $derived(safeBack(page.url.searchParams.get('back')));
	const title = $derived(page.url.searchParams.get('title') ?? '');
</script>

<svelte:head><title>{t('gradePage.title')} · TunaTale</title></svelte:head>

{#key contentId}
	<GradePage {contentId} {title} {backHref} />
{/key}
