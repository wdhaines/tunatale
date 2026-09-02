import { api } from "$lib/api";
import { error } from "@sveltejs/kit";
import type { LessonAudio } from "$lib/api";
import type { PageLoad } from "./$types";

export const ssr = false;

export const load: PageLoad = async ({ params }) => {
  const session = await api.getReviewSession(params.sessionId).catch(() => null);
  if (!session) error(404, "Review session not found");

  // allSettled, not await: a session is readable the moment it is generated,
  // and rendering is a separate, slower step. Blocking the page on audio would
  // make a brand-new session look broken for as long as the render takes.
  const [audio] = await Promise.allSettled([api.getLessonAudio(params.sessionId)]);

  return {
    session,
    audio: audio.status === "fulfilled" ? audio.value : (null as LessonAudio | null),
  };
};
