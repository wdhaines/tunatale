import type { ReadableLesson } from "#lib/api.js";

/**
 * Whether a lesson is ONLY a drill: a grammar lesson (bd tunatale-ve4p.5) or a
 * Practice lesson of authored drills (bd tunatale-h0q3.58).
 *
 * Such a lesson has no story, so it has nothing to read and no words to count:
 * the reader shows the table of what an affix drill drills, when there is one,
 * instead of a transcript, and the page leaves out what is measured from a
 * transcript. A story lesson that merely ENDS with a drill is not one, and is
 * read like any other.
 */
export function isDrillOnly(lesson: ReadableLesson): boolean {
  return lesson.sections.length > 0 && lesson.sections.every((s) => s.type === "affix_drill");
}
