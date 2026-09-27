/**
 * TT's study-day convention, stated once (tunatale-ss5q.3).
 *
 * The study day is the LOCAL calendar date with a 04:00 LOCAL rollover,
 * mirroring Anki — the frontend half of `ANKI_ROLLOVER_HOUR` in
 * backend/app/config.py and `rollover.py::anki_today`. A compile-time constant
 * on both sides; nothing plumbs it over the wire.
 *
 * Days are returned as UTC-midnight timestamps (`Date.UTC`), so two of them
 * subtract to whole days and format with `timeZone: 'UTC'`.
 *
 * Plain read-only `Date`s, never `SvelteDate`: `SvelteDate` binds the real
 * clock at module-eval time, which `vi.setSystemTime` cannot reach.
 */
export const ROLLOVER_HOUR = 4;

/** The study day an instant falls on, read on the local clock. */
export function studyDayOf(d: Date): number {
  // `Date.UTC` normalises day 0, so the pre-rollover step back crosses month
  // and year ends without a branch.
  return Date.UTC(
    d.getFullYear(),
    d.getMonth(),
    d.getDate() - (d.getHours() < ROLLOVER_HOUR ? 1 : 0),
  );
}

/**
 * The study day a card's `due_at` names.
 *
 * Two different domains, and mixing them was tunatale-l0b6: a DAY-LEVEL due_at
 * (review cards) is written at 04:00 **UTC** of its due date by
 * `due_at_rollover_utc`, so its UTC date is the due day exactly. An INTRADAY
 * due_at (learning / relearning) is a real instant, so it belongs to the study
 * day of its local time: 02:30Z on 09-26 is 22:30 on 09-25 at UTC-4.
 */
export function dueStudyDay(dueAt: Date, intraday: boolean): number {
  if (intraday) return studyDayOf(dueAt);
  return Date.UTC(dueAt.getUTCFullYear(), dueAt.getUTCMonth(), dueAt.getUTCDate());
}
