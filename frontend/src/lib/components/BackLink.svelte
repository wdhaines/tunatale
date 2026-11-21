<script lang="ts">
	// The way back from a page, in one place and one look on every page that has
	// one (owner, 2026-11-21: navigation is a shared component). Spacing below it
	// belongs to the page's layout, not to the link.
	interface Props {
		href: string;
		label: string;
	}
	let { href, label }: Props = $props();
</script>

<a class="back-link" {href}>← {label}</a>

<style>
	.back-link {
		display: inline-block;
		color: var(--color-muted);
		text-decoration: none;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.back-link:hover {
		color: var(--color-primary);
	}
</style>
