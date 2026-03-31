<script lang="ts">
	import { api } from '$lib/api';
	import type { CurriculumSummary, LessonSummary, LessonDetail } from '$lib/api';

	let topic = $state('');
	let cefrLevel = $state('A2');
	let numDays = $state(7);

	let curriculum: CurriculumSummary | null = $state(null);
	let lesson: LessonSummary | null = $state(null);
	let lessonDetail: LessonDetail | null = $state(null);
	let audioUrl: string | null = $state(null);

	let loading = $state(false);
	let error = $state('');

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
		lessonDetail = null;
		try {
			lesson = await api.generateStory(curriculum.id, day);
			lessonDetail = await api.getLesson(lesson.id);
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
	<nav><a href="/practice">Practice (SRS)</a></nav>
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
		<button onclick={handleGenerate} disabled={loading || !topic.trim()}>
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
					<button class="day-btn" onclick={() => handleGenerateLesson(i + 1)} disabled={loading}>
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
			<button onclick={handleRenderAudio} disabled={loading}>
				{loading ? 'Rendering…' : 'Render Audio'}
			</button>
		</section>
	{/if}

	{#if lessonDetail}
		<section class="script-section">
			<h2>Lesson Script</h2>
			{#each lessonDetail.sections as section}
				<div class="script-block">
					<h3>{section.type}</h3>
					{#each section.phrases as phrase}
						<div class="phrase">
							<span class="role">{phrase.role}</span>
							<span class="phrase-text">{phrase.text}</span>
							<span class="lang">{phrase.language_code}</span>
						</div>
					{/each}
				</div>
			{/each}
		</section>
	{/if}

	{#if audioUrl}
		<section class="audio-section">
			<h2>Audio Player</h2>
			<!-- svelte-ignore a11y_media_has_caption -->
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
	.lang {
		color: #999;
		font-size: 0.8rem;
	}
</style>
