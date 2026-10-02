import { t } from "$lib/i18n/i18n.svelte";
import type { MessageKey } from "$lib/i18n/i18n.svelte";

/**
 * Month names for a session date, as catalog KEYS.
 *
 * The names are looked up when `formatSessionDate` is CALLED rather than in a
 * module-level array of translated strings, so a locale change is picked up by
 * the templates that render the result.
 */
const MONTH_KEYS: MessageKey[] = [
  "reviewSessions.january",
  "reviewSessions.february",
  "reviewSessions.march",
  "reviewSessions.april",
  "reviewSessions.may",
  "reviewSessions.june",
  "reviewSessions.july",
  "reviewSessions.august",
  "reviewSessions.september",
  "reviewSessions.october",
  "reviewSessions.november",
  "reviewSessions.december",
];

/**
 * "2 September" from "2026-09-02", without going through Date.
 *
 * ⚠️ `new Date('2026-09-02')` is parsed as UTC MIDNIGHT, which renders as
 * 1 September in every negative-offset timezone — a wrong date for half the
 * world, and a bug that passes every test run in London. The value is a
 * calendar date, not an instant, so it is formatted as one.
 *
 * One copy for every page that shows a session's date (home, the index, the
 * reader): two formatters would be two answers to "when was this".
 */
export function formatSessionDate(iso: string): string {
  const [, month, day] = iso.split("-").map(Number);
  return `${day} ${t(MONTH_KEYS[month - 1])}`;
}
