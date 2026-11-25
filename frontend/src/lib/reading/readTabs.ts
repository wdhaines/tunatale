// The Read side's tab bar (tunatale-cs3p slice 3; the owner's Option A of
// 2026-11-24, sketches https://claude.ai/artifact/8H8MFzgKq4Cok8R4fJRJkV):
// Outline, one tab per part of the lesson, then Grade.
//
// A Wolff practice lesson keeps all its drills in ONE recording, so a drill tab
// is a cue range inside it, found by the same rule Listen's section line uses
// (storyDrills). The owner chose cue ranges over a re-render on 2026-11-23.
// Listen is untouched: none of this runs there.

import type { LessonAudio, ReadableLesson } from "#lib/api.js";
import { t } from "#lib/i18n/i18n.svelte.js";
import { isDrillOnly } from "#lib/lessonKind.js";
import type { PlaybackController } from "#lib/playback/playbackController.svelte.js";
import { storyDrills, type StoryDrill } from "#lib/playback/sectionLine.js";

export type ReadTabKind =
  | "outline"
  | "key_phrases"
  | "dialogue"
  | "drill"
  | "story_drill"
  | "grade";

export interface ReadTab {
  /** Stable across visits, so the lesson can remember it: "drill-2", not an index. */
  id: string;
  kind: ReadTabKind;
  label: string;
  /** A story drill's cue range (kind "story_drill"). */
  drill?: StoryDrill;
  /** Grade is a link to the grade page, never a panel. */
  href?: string;
}

// The dialogue's recordings: whichever of these is playing already IS the dialogue.
const DIALOGUE_TRACKS = new Set([
  "natural_speed",
  "slow_speed",
  "translated",
  "slow_translated",
  "en_translated",
  "slow_en_translated",
]);

/** LessonPlayer's own test: a full-track manifest and one per section (tunatale-guzo.3). */
function trackMode(audio: LessonAudio): boolean {
  return (
    (audio.cues?.length ?? 0) > 0 &&
    audio.sections.length > 0 &&
    audio.sections.every((s) => (s.cues?.length ?? 0) > 0)
  );
}

/** The story drills in the lesson's drill recording, or none. */
export function lessonDrills(audio: LessonAudio | null): StoryDrill[] {
  const section = audio?.sections.find((s) => s.section_type === "affix_drill");
  return (section?.cues && storyDrills(section.cues)) || [];
}

export function readTabs(
  lesson: ReadableLesson,
  drills: StoryDrill[],
  gradeHref: string | null,
): ReadTab[] {
  const tabs: ReadTab[] = [{ id: "outline", kind: "outline", label: t("readTabs.outline") }];
  if (!isDrillOnly(lesson)) {
    const keyPhrases =
      lesson.key_phrases.length > 0 || lesson.sections.some((s) => s.type === "key_phrases");
    if (keyPhrases)
      tabs.push({ id: "key_phrases", kind: "key_phrases", label: t("readTabs.keyPhrases") });
    tabs.push({ id: "dialogue", kind: "dialogue", label: t("readTabs.dialogue") });
  }
  if (drills.length > 0) {
    drills.forEach((drill, i) =>
      tabs.push({
        id: `drill-${i + 1}`,
        kind: "story_drill",
        label: t("readTabs.drillN", { n: i + 1 }),
        drill,
      }),
    );
  } else if (lesson.drill) {
    tabs.push({ id: "drill", kind: "drill", label: t("readTabs.drill") });
  }
  if (gradeHref)
    tabs.push({ id: "grade", kind: "grade", label: t("readTabs.grade"), href: gradeHref });
  return tabs;
}

/** The remembered tab if the lesson still has it as a panel, else Outline. */
export function tabOrOutline(tabs: ReadTab[], id: string): ReadTab {
  return tabs.find((tab) => tab.id === id && tab.kind !== "grade") ?? tabs[0];
}

/**
 * Point the one player at the part a tab shows (R2): its recording for Key
 * phrases, Dialogue and a rule-built drill, and the drill's first cue for a
 * story drill. The play state is the player's own: a seek never starts
 * playback. A lesson rendered before per-section cues has one track and no
 * sections to choose between, so nothing moves there.
 */
export function seekForTab(
  tab: ReadTab,
  ctrl: PlaybackController | null,
  audio: LessonAudio | null,
): void {
  if (!ctrl || !audio || !trackMode(audio)) return;
  const active = ctrl.activeSectionType;
  switch (tab.kind) {
    case "key_phrases":
      if (active !== "key_phrases") ctrl.selectTrack("key_phrases");
      break;
    case "dialogue":
      if (!DIALOGUE_TRACKS.has(active ?? "")) ctrl.selectTrack("natural_speed");
      break;
    case "drill":
      if (active !== "affix_drill") ctrl.selectTrack("affix_drill");
      break;
    case "story_drill":
      if (active === "affix_drill") ctrl.seekToCue(tab.drill!.opener);
      else ctrl.selectTrack("affix_drill", tab.drill!.opener.ref);
      break;
  }
}
