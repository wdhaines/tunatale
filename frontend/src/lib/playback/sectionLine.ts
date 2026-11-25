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
//
// storyDrills is that boundary rule on its own. Read's drill tabs (slice 3)
// seek to a drill's opener with it, so a tab and this line can never disagree
// about where a drill starts (the owner, 2026-11-23: cue ranges, no re-render).

import type { Cue } from "#lib/api.js";
import { t } from "#lib/i18n/i18n.svelte.js";

/** One step of a story drill: the cue you hear, and the sentence you say. */
export interface StoryDrillStep {
  prompt: Cue;
  /** null when the render stopped before it (never seen live; kept rather than dropped). */
  answer: Cue | null;
}

/** One drill inside a story drill section, as its cues lay it out. */
export interface StoryDrill {
  /** The narrator's instruction the drill opens on: where a tab seeks to. */
  opener: Cue;
  instruction: string;
  /** A substitution drill's model sentence; a transformation or a question has none. */
  model: Cue | null;
  /** What names the drill: its model sentence, else its instruction. */
  label: string;
  steps: StoryDrillStep[];
}

/**
 * The drills in ONE section's cues, in order, or null when the section is not a
 * story drill (another section type, a rule-built affix drill, or no cues).
 */
export function storyDrills(section: Cue[]): StoryDrill[] | null {
  if (section.length === 0 || section[0].section_type !== "affix_drill") return null;
  const narratorLanguage = section[0].language_code;
  const isStory = section.some((c) => c.role === "prompt" && c.language_code !== narratorLanguage);
  if (!isStory) return null;

  const openers = section.flatMap((c, i) => (c.role === "narrator" && i > 0 ? [i] : []));
  return openers.map((at, k) => {
    const end = k + 1 < openers.length ? openers[k + 1] : section.length;
    const body = section.slice(at + 1, end);
    const model = body[0]?.role === "model" ? body[0] : null;
    const steps: StoryDrillStep[] = body.flatMap((c, i) =>
      c.role === "prompt"
        ? [{ prompt: c, answer: body[i + 1]?.role === "answer" ? body[i + 1] : null }]
        : [],
    );
    return {
      opener: section[at],
      instruction: section[at].text,
      model,
      label: model?.text ?? section[at].text,
      steps,
    };
  });
}

export function sectionLine(cues: Cue[] | null, current: Cue | null, sectionTitle: string): string {
  if (current === null) return "";
  const section = (cues ?? []).filter((c) => c.section_index === current.section_index);
  const at = section.findIndex((c) => c.index === current.index);
  if (at < 0) return sectionTitle;
  const drills = storyDrills(section);
  if (drills === null) return sectionTitle;

  const k = drills.filter((d) => section.indexOf(d.opener) <= at).length;
  if (k === 0) return sectionTitle;
  return `${t("lessonPlayer.sectionLine.drill", { n: k, total: drills.length })} · ${drills[k - 1].label}`;
}
