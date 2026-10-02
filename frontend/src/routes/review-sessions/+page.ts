import { api } from "$lib/api";
import { error } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const ssr = false;

/**
 * The review-sessions index: the counterpart of the curriculum page.
 *
 * Unlike home, where the curricula share the page and a failed session list can
 * degrade to "none yet", here the list IS the page — so a backend that cannot
 * be read is an error page rather than a false statement that there is nothing
 * to review.
 */
export const load: PageLoad = async () => {
  const sessions = await api.listReviewSessions().catch(() => null);
  if (!sessions) {
    error(503, "Review sessions could not be loaded");
  }
  return { sessions };
};
