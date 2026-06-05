import { api } from "$lib/api";
import { error } from "@sveltejs/kit";
import type { TranscriptData } from "$lib/api";
import type { PageLoad } from "./$types";

export const ssr = false;

export const load: PageLoad = async ({ params }) => {
  const [curriculum, lesson] = await Promise.all([
    api.getCurriculum(params.curriculumId).catch(() => null),
    api.getLesson(params.lessonId).catch(() => null),
  ]);

  if (!curriculum) error(404, "Curriculum not found");
  if (!lesson) error(404, "Lesson not found");

  const [audioResult] = await Promise.allSettled([api.getLessonAudio(params.lessonId)]);

  // The transcript runs the (classla) lemmatizer and can take many seconds on a
  // cold backend. Don't block the page load on it — the component fetches it
  // client-side with a loading indicator, so the lesson shell renders at once.
  return {
    curriculum,
    lesson,
    audio: audioResult.status === "fulfilled" ? audioResult.value : null,
    transcript: null as TranscriptData | null,
  };
};
