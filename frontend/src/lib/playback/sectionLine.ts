// The line Listen shows under the player naming what is playing
// (tunatale-cs3p slice 4): "Drill 1 of 2 · Nia siya dinhi." inside a lesson's
// own drills, the section's title everywhere else.
//
// A Wolff practice lesson keeps ALL its drills in ONE affix_drill section, so
// the drill number is not a section index. It is read from the cues already
// rendered, with no new field and no re-render, because the story-drill
// builder (section_builder.py::build_story_drill_section) emits a fixed shape:
// the narrator's title, then for each drill exactly one narrator instruction,
// an optional model, and cue/answer pairs. Its prompts are in the lesson's
// language. A rule-built affix drill (build_affix_drill_section) speaks its
// prompts in the narrator's English and has many narrator lines, so it is
// told apart by its prompts and keeps the plain title. Measured on all 24
// live drill sections 2026-11-23: 3 rule-built, 21 story (1-3 drills each).

import type { Cue } from "#lib/api.js";
import { t } from "#lib/i18n/i18n.svelte.js";

export function sectionLine(cues: Cue[] | null, current: Cue | null, sectionTitle: string): string {
  if (current === null) return "";
  const section = (cues ?? []).filter((c) => c.section_index === current.section_index);
  const at = section.findIndex((c) => c.index === current.index);
  if (at < 0 || section[0].section_type !== "affix_drill") return sectionTitle;

  const narratorLanguage = section[0].language_code;
  const isStory = section.some((c) => c.role === "prompt" && c.language_code !== narratorLanguage);
  if (!isStory) return sectionTitle;

  const openers = section.flatMap((c, i) => (c.role === "narrator" && i > 0 ? [i] : []));
  const k = openers.filter((i) => i <= at).length;
  if (k === 0) return sectionTitle;

  const opener = openers[k - 1];
  const next = section[opener + 1];
  const label = next?.role === "model" ? next.text : section[opener].text;
  return `${t("lessonPlayer.sectionLine.drill", { n: k, total: openers.length })} · ${label}`;
}
