<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { CurriculumSummary, LessonSummary, LessonDetail, TranscriptData, WordRating } from '$lib/api';
	import { saveHomeState, loadHomeState, clearHomeState } from '$lib/storage';
	import WordSpan from '$lib/WordSpan.svelte';


	let topic = $state('');
	let cefrLevel = $state('A2');
	let numDays = $state(7);

	let curriculum: CurriculumSummary | null = $state(null);
	let lesson: LessonSummary | null = $state(null);
	let lessonDetail: LessonDetail | null = $state(null);
	let audioUrl: string | null = $state(null);

	let loading = $state(false);
	let error = $state('');
	let restored = $state(false);

	let listenedLessonIds: string[] = $state([]);
	let listenLoading = $state(false);
	let listenResult: { registered: number } | null = $state(null);

	let transcript: TranscriptData | null = $state(null);
	let pendingRatings: Record<string, WordRating | null> = $state({});

	let isListened = $derived(lessonDetail ? listenedLessonIds.includes(lessonDetail.id) : false);

	$effect(() => {
		if (!restored) return;
		saveHomeState({
			topic,
			cefrLevel,
			numDays,
			...(curriculum?.id ? { curriculumId: curriculum.id } : {}),
			...(lesson?.id ? { lessonId: lesson.id } : {}),
			...(audioUrl ? { audioUrl } : {}),
			...(listenedLessonIds.length > 0 ? { listenedLessonIds } : {})
		});
	});

	onMount(async () => {
		const saved = loadHomeState();
		if (!saved) {
			restored = true;
			return;
		}

		topic = saved.topic;
		cefrLevel = saved.cefrLevel;
		numDays = saved.numDays;

		if (saved.curriculumId) {
			try {
				curriculum = await api.getCurriculum(saved.curriculumId);
			} catch {
				clearHomeState();
				restored = true;
				return;
			}
		}

		if (saved.lessonId) {
			try {
				lessonDetail = await api.getLesson(saved.lessonId);
				lesson = {
					id: lessonDetail.id,
					title: lessonDetail.title,
					sections: lessonDetail.sections.map((s) => ({ type: s.type, phrase_count: s.phrases.length }))
				};
			} catch {
				clearHomeState();
				restored = true;
				return;
			}
		}

		if (saved.audioUrl) {
			audioUrl = saved.audioUrl;
			if (saved.lessonId) {
				await loadTranscript(saved.lessonId);
			}
		}

		if (saved.listenedLessonIds) {
			listenedLessonIds = saved.listenedLessonIds;
		}

		restored = true;
	});

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

	async function handleMarkAsListened() {
		if (!lessonDetail) return;
		listenLoading = true;
		error = '';
		try {
			const activeRatings = Object.fromEntries(
				Object.entries(pendingRatings).filter(([, v]) => v !== null)
			) as Record<string, WordRating>;
			const result = await api.markAsListened(lessonDetail.id, activeRatings);
			listenResult = result;
			if (!listenedLessonIds.includes(lessonDetail.id)) {
				listenedLessonIds = [...listenedLessonIds, lessonDetail.id];
			}
			// Refresh transcript to show updated SRS states
			transcript = await api.getLessonTranscript(lessonDetail.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			listenLoading = false;
		}
	}

	async function handleGenerateLesson(day: number) {
		if (!curriculum) return;
		loading = true;
		error = '';
		lessonDetail = null;
		listenResult = null;
		transcript = null;
		pendingRatings = {};
		try {
			lesson = await api.generateStory(curriculum.id, day);
			lessonDetail = await api.getLesson(lesson.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function loadTranscript(lessonId: string) {
		try {
			transcript = await api.getLessonTranscript(lessonId);
			pendingRatings = {};
		} catch {
			transcript = null;
		}
	}

	function handleWordRatingChange(lemma: string, rating: WordRating | null) {
		pendingRatings = { ...pendingRatings, [lemma]: rating };
	}

	async function handleRenderAudio() {
		if (!lesson) return;
		loading = true;
		error = '';
		try {
			const result = await api.renderAudio(lesson.id);
			audioUrl = api.audioUrl(result.audio_id);
			await loadTranscript(lesson.id);
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

			<div class="listen-action">
				<button
					class="listen-btn"
					class:listened={isListened}
					onclick={handleMarkAsListened}
					disabled={listenLoading}
				>
					{#if listenLoading}
						Registering…
					{:else if isListened}
						✓ Listened
					{:else}
						Mark as Listened
					{/if}
				</button>

				{#if listenResult && !error}
					<p class="listen-confirmation">
						{listenResult.registered}
						{listenResult.registered === 1 ? 'word' : 'words'} tracked in SRS
					</p>
				{/if}
			</div>

			{#if transcript}
				<div class="transcript">
					{#if transcript.key_phrases.length > 0}
						<div class="transcript-section">
							<h3>Key Phrases</h3>
							<ul class="key-phrases-list">
								{#each transcript.key_phrases as kp}
									<li>
										<span class="kp-phrase">{kp.phrase}</span>
										<span class="kp-translation">{kp.translation}</span>
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					{#if transcript.dialogue_lines.length > 0}
						<div class="transcript-section">
							<h3>Dialogue <span class="transcript-hint">(click a word: orange=hard, purple=easy)</span></h3>
							{#each transcript.dialogue_lines as line}
								<div class="dialogue-line">
									<span class="dialogue-role">{line.role}</span>
									<span class="dialogue-words">
										{#each line.words as word}
											<WordSpan
												{word}
												rating={pendingRatings[word.lemma] ?? null}
												onRatingChange={handleWordRatingChange}
											/>{' '}
										{/each}
									</span>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}
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
	.listen-action {
		margin-top: 1rem;
		padding-top: 1rem;
		border-top: 1px solid var(--color-border);
	}
	.listen-btn {
		background: var(--color-primary);
	}
	.listen-btn.listened {
		background: var(--color-success);
	}
	.listen-confirmation {
		color: var(--color-success);
		font-size: 0.85rem;
		margin-top: 0.5rem;
	}
	.key-phrases-list {
		list-style: none;
		padding: 0;
		margin-top: 0.5rem;
	}
	.key-phrases-list li {
		display: flex;
		justify-content: space-between;
		padding: 0.25rem 0;
		border-bottom: 1px solid var(--color-border);
	}
	.kp-phrase {
		font-weight: 500;
	}
	.kp-translation {
		color: var(--color-muted);
		font-style: italic;
	}
	.transcript {
		margin-top: 1.25rem;
	}
	.transcript-section {
		margin-bottom: 1.25rem;
	}
	.transcript-section h3 {
		font-size: 0.8rem;
		text-transform: uppercase;
		color: var(--color-muted);
		margin-bottom: 0.5rem;
	}
	.transcript-hint {
		font-style: italic;
		text-transform: none;
		font-size: 0.75rem;
	}
	.dialogue-line {
		display: flex;
		gap: 0.75rem;
		padding: 0.3rem 0;
		border-bottom: 1px solid var(--color-border);
		font-size: 0.95rem;
		line-height: 1.5;
	}
	.dialogue-role {
		color: var(--color-primary);
		min-width: 6rem;
		font-size: 0.85rem;
		padding-top: 0.1rem;
		flex-shrink: 0;
	}
	.dialogue-words {
		flex: 1;
		line-height: 1.6;
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
