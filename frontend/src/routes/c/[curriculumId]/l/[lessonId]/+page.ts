import { api } from "$lib/api";
import { error } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const ssr = false;

export const load: PageLoad = async ({ params }) => {
  const [curriculum, lesson] = await Promise.all([
    api.getCurriculum(params.curriculumId).catch(() => null),
    api.getLesson(params.lessonId).catch(() => null),
  ]);

  if (!curriculum) error(404, "Curriculum not found");
  if (!lesson) error(404, "Lesson not found");

  const [audioResult, transcriptResult] = await Promise.allSettled([
    api.getLessonAudio(params.lessonId),
    api.getLessonTranscript(params.lessonId),
  ]);

  return {
    curriculum,
    lesson,
    audio: audioResult.status === "fulfilled" ? audioResult.value : null,
    transcript: transcriptResult.status === "fulfilled" ? transcriptResult.value : null,
  };
};
