<script lang="ts">
	import { api } from '$lib/api';
	import type { CurriculumSummary, LessonSummary } from '$lib/api';

	let topic = '';
	let cefrLevel = 'A2';
	let numDays = 7;

	let curriculum: CurriculumSummary | null = null;
	let lesson: LessonSummary | null = null;
	let audioUrl: string | null = null;

	let loading = false;
	let error = '';

	async function handleGenerate() {
		if (!topic.trim()) return;
		loading = true;
		error = '';
		curriculum = null;
		lesson = null;
		audioUrl = null;
		try {
			curriculum = await api.generateCurriculum(topic, cefrLevel, numDays);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function handleGenerateLesson(day: number) {
		if (!curriculum) return;
		loading = true;
		error = '';
		try {
			lesson = await api.generateStory(curriculum.id, day);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function handleRenderAudio() {
		if (!lesson) return;
		loading = true;
		error = '';
		try {
			const result = await api.renderAudio(lesson.id);
			audioUrl = api.audioUrl(result.audio_id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}
</script>

<main>
	<h1>TunaTale</h1>
	<p>AI-powered language learning — Slovene</p>

	<section class="input-section">
		<h2>Generate Curriculum</h2>
		<label>
			Topic
			<input bind:value={topic} placeholder="e.g. ordering coffee in Ljubljana" />
		</label>
		<label>
			CEFR Level
			<select bind:value={cefrLevel}>
				<option>A1</option>
				<option>A2</option>
				<option>B1</option>
				<option>B2</option>
			</select>
		</label>
		<label>
			Days
			<input type="number" bind:value={numDays} min="1" max="30" />
		</label>
		<button on:click={handleGenerate} disabled={loading || !topic.trim()}>
			{loading ? 'Generating…' : 'Generate'}
		</button>
		{#if error}
			<p class="error">{error}</p>
		{/if}
	</section>

	{#if curriculum}
		<section class="curriculum-section">
			<h2>Curriculum: {curriculum.topic}</h2>
			<p>{curriculum.days} days · {curriculum.language_code.toUpperCase()} · {cefrLevel}</p>
			<div class="days">
				{#each Array(curriculum.days) as _, i}
					<button class="day-btn" on:click={() => handleGenerateLesson(i + 1)} disabled={loading}>
						Day {i + 1}
					</button>
				{/each}
			</div>
		</section>
	{/if}

	{#if lesson}
		<section class="lesson-section">
			<h2>Lesson: {lesson.title}</h2>
			<ul>
				{#each lesson.sections as section}
					<li>{section.type} — {section.phrase_count} phrase{section.phrase_count === 1 ? '' : 's'}</li>
				{/each}
			</ul>
			<button on:click={handleRenderAudio} disabled={loading}>
				{loading ? 'Rendering…' : 'Render Audio'}
			</button>
		</section>
	{/if}

	{#if audioUrl}
		<section class="audio-section">
			<h2>Audio Player</h2>
			<!-- svelte-ignore a11y-media-has-caption -->
			<audio controls src={audioUrl}>
				Your browser does not support the audio element.
			</audio>
		</section>
	{/if}
</main>

<style>
	main {
		max-width: 700px;
		margin: 2rem auto;
		font-family: system-ui, sans-serif;
		padding: 0 1rem;
	}
	section {
		margin-top: 2rem;
		border: 1px solid #ddd;
		border-radius: 8px;
		padding: 1rem;
	}
	label {
		display: block;
		margin-bottom: 0.5rem;
	}
	input,
	select {
		display: block;
		margin-top: 0.25rem;
		width: 100%;
		padding: 0.4rem;
		border: 1px solid #ccc;
		border-radius: 4px;
	}
	button {
		margin-top: 0.75rem;
		padding: 0.5rem 1.25rem;
		background: #2563eb;
		color: white;
		border: none;
		border-radius: 4px;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.day-btn {
		margin-right: 0.5rem;
	}
	.error {
		color: #dc2626;
		margin-top: 0.5rem;
	}
	audio {
		width: 100%;
	}
</style>
