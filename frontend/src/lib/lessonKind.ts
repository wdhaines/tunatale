import type { ReadableLesson } from "$lib/api";

/**
 * Whether a lesson is ONLY an affix drill: a grammar lesson (bd tunatale-ve4p.5).
 *
 * Such a lesson has no story, so it has nothing to read and no words to count:
 * the reader shows the table of what it drills instead of a transcript, and
 * the page leaves out what is measured from a transcript. A story lesson that
 * merely ENDS with a drill is not one, and is read like any other.
 */
export function isDrillOnly(lesson: ReadableLesson): boolean {
  return lesson.drill != null && lesson.sections.every((s) => s.type === "affix_drill");
}
