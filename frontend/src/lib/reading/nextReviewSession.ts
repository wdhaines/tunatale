/**
 * Which review session follows the one being read.
 *
 * A review session has no `day` and no curriculum, so it has none of the lesson
 * page's ordering machinery — but a hands-free run still has to know where to
 * go when its passes are done. Date is the only ordering a session carries.
 *
 * Pure and separate from the page so the ordering is testable without a DOM:
 * the value here is app-computed, which puts it below the browser tier.
 */
export interface SessionOrderable {
  id: string;
  session_date: string;
}

/**
 * The session immediately after *currentId* in date order, or null when there
 * is none — the newest session, an unknown id, an empty list.
 *
 * Ties on date are broken by id. The list endpoint sorts by date descending and
 * two sessions can legitimately share a day; without a deterministic tiebreak
 * "next" would follow array order, and a hands-free run could bounce between
 * the same two sessions forever.
 */
export function nextSessionAfter(
  sessions: readonly SessionOrderable[],
  currentId: string,
): SessionOrderable | null {
  const ordered = [...sessions].sort(
    (a, b) => a.session_date.localeCompare(b.session_date) || a.id.localeCompare(b.id),
  );
  const i = ordered.findIndex((x) => x.id === currentId);
  if (i < 0 || i >= ordered.length - 1) return null;
  return ordered[i + 1];
}
