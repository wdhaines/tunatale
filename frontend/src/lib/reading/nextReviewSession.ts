/**
 * The ONE order review sessions are walked in.
 *
 * A review session has no `day` and no curriculum, so it has none of the lesson
 * page's ordering machinery — date is the only ordering a session carries. The
 * hands-free hand-off, the session pager and the sessions index all read it from
 * here: three readers, one answer, or a pager whose "next" is not the session a
 * hands-free run moves to.
 *
 * Pure and separate from the page so the ordering is testable without a DOM:
 * the value here is app-computed, which puts it below the browser tier.
 */
export interface SessionOrderable {
  id: string;
  session_date: string;
}

/**
 * Sessions oldest first, as a NEW array — the caller's list is never sorted in
 * place, and every field it carries is preserved.
 *
 * Ties on date are broken by id. The list endpoint sorts by date descending and
 * two sessions can legitimately share a day; without a deterministic tiebreak
 * "next" would follow array order, and a hands-free run could bounce between
 * the same two sessions forever.
 */
export function orderSessions<T extends SessionOrderable>(sessions: readonly T[]): T[] {
  return [...sessions].sort(
    (a, b) => a.session_date.localeCompare(b.session_date) || a.id.localeCompare(b.id),
  );
}

/**
 * The sessions either side of *currentId*, or null on that side — the oldest has
 * no previous, the newest has no next, and an unknown id has neither.
 */
export function sessionNeighbours<T extends SessionOrderable>(
  sessions: readonly T[],
  currentId: string,
): { prev: T | null; next: T | null } {
  const ordered = orderSessions(sessions);
  const i = ordered.findIndex((x) => x.id === currentId);
  if (i < 0) return { prev: null, next: null };
  return {
    prev: i > 0 ? ordered[i - 1] : null,
    next: i < ordered.length - 1 ? ordered[i + 1] : null,
  };
}

/**
 * The session immediately after *currentId* in date order, or null when there
 * is none — the newest session, an unknown id, an empty list.
 *
 * The hands-free hand-off's view of `sessionNeighbours`, so the run and the
 * pager can never disagree about what comes next.
 */
export function nextSessionAfter(
  sessions: readonly SessionOrderable[],
  currentId: string,
): SessionOrderable | null {
  return sessionNeighbours(sessions, currentId).next;
}
