/**
 * The ONE answer to "which lessons does home show, and for which curriculum".
 *
 * Home shows a short run of lessons, each one click away, ending at the first
 * lesson nobody has listened to yet — so the run resumes where the learner
 * stopped and shows the lessons before it, newest first. With several
 * curricula ONE of them leads: the one listened to most recently, and the
 * rest drop to a one-line list below it.
 *
 * The user agreed both rules from screenshots on 2026-10-07 (bd
 * tunatale-e6fq). Pure and separate from the page so both are testable
 * without a DOM: these values are app-computed, which puts them below the
 * browser tier.
 */
export interface LessonDay {
  day: number;
  position: number;
  lesson_id: string;
}

export interface RecentLesson extends LessonDay {
  next: boolean;
  listened: boolean;
}

/**
 * The `limit` lessons ending at the next one to listen to, highest position
 * first, as a NEW array — the caller's list is never sorted in place.
 *
 * The next lesson is the FIRST unlistened one in position order, not the
 * first hole in the day keys: a lesson listened out of order does not promote
 * the lesson after it, and the lessons after the next one are never included,
 * so the run never pads with lessons the learner has not reached. When
 * everything is listened the run ends at the last lesson and nothing is
 * marked next.
 */
export function recentLessons(
  days: readonly LessonDay[],
  isListened: (lessonId: string) => boolean,
  limit = 3,
): RecentLesson[] {
  const ordered = [...days].sort((a, b) => a.position - b.position);
  const nextIndex = ordered.findIndex((d) => !isListened(d.lesson_id));
  const end = nextIndex === -1 ? ordered.length - 1 : nextIndex;
  const start = Math.max(0, end - limit + 1);

  const rows: RecentLesson[] = [];
  for (let i = end; i >= start; i--) {
    const d = ordered[i];
    rows.push({
      day: d.day,
      position: d.position,
      lesson_id: d.lesson_id,
      next: i === nextIndex,
      listened: isListened(d.lesson_id),
    });
  }
  return rows;
}

/**
 * The curriculum owning the lesson listened to most recently — the one home
 * shows its recent lessons for. Null only when there is no curriculum at all;
 * no listens anywhere keeps the first curriculum in the list.
 *
 * Instants are compared with `Date.parse`, never as strings: the server
 * writes `2026-10-06T22:50:07.654582+00:00` and the browser writes
 * `2026-10-07T12:00:00.000Z`, and an offset can put the later INSTANT under
 * the earlier calendar date. `null` and an unparseable timestamp both mean
 * "never listened", and a strictly later instant is needed to take the lead —
 * so a tie keeps the curriculum that already had it. A curriculum with no
 * progress fetch has no lessons and so can never take the lead on listens.
 */
export function leadCurriculum<T extends { id: string }>(
  curricula: readonly T[],
  daysById: Readonly<Record<string, readonly LessonDay[] | undefined>>,
  lastListenedAt: (lessonId: string) => string | null,
): T | null {
  let lead: T | null = null;
  let leadAt = Number.NEGATIVE_INFINITY;

  for (const curriculum of curricula) {
    let latest = Number.NEGATIVE_INFINITY;
    for (const d of daysById[curriculum.id] ?? []) {
      const at = Date.parse(lastListenedAt(d.lesson_id) ?? "");
      if (!Number.isNaN(at) && at > latest) latest = at;
    }
    if (lead === null || latest > leadAt) {
      lead = curriculum;
      leadAt = latest;
    }
  }
  return lead;
}
