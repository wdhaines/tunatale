import type { Cue, CueRef, DialogueLine, LessonDetail, ReadableLesson, WordToken } from "./api";

interface UnifiedLine {
  role: string;
  words: WordToken[];
  naturalText: string;
  translatedText: string;
  transcriptIndex: number;
}

export interface Scene {
  title: string | null;
  lines: UnifiedLine[];
}

// Every section a lesson builder emits opens with its own spoken title, a
// narrator line in the L1 (backend `section_builder.py`). It is skipped BY
// POSITION. An English list mirroring the backend's titles used to decide
// this, and a copy edit or a language plugin's own title would then have
// turned the title into a spurious scene heading (tunatale-ss5q.3). A first
// phrase that is dialogue is kept, since only a narrator line can be a title.
function withoutTitle(
  phrases: LessonDetail["sections"][number]["phrases"],
  languageCode: string,
): LessonDetail["sections"][number]["phrases"] {
  const first = phrases[0];
  const isTitle =
    first !== undefined && first.role === "narrator" && first.language_code !== languageCode;
  return isTitle ? phrases.slice(1) : phrases;
}

function extractTranslations(
  phrases: LessonDetail["sections"][number]["phrases"],
  languageCode: string,
): string[] {
  const out: string[] = [];
  let awaiting = false;
  for (const p of withoutTitle(phrases, languageCode)) {
    if (p.language_code === languageCode) {
      if (awaiting) out.push("");
      awaiting = true;
    } else if (p.role === "narrator" && awaiting) {
      out.push(p.text);
      awaiting = false;
    }
  }
  if (awaiting) out.push("");
  return out;
}

// Narrowed from LessonDetail to what this actually reads, so a REVIEW SESSION
// can use it too: a session has no `day`, and demanding one would have forced
// either a fake day or a second copy of this logic (bd tunatale-dswn). Every
// LessonDetail still satisfies it.
export function buildScenes(lesson: ReadableLesson, dialogueLines: DialogueLine[]): Scene[] {
  const languageCode = lesson.language_code;
  const natural = lesson.sections.find((s) => s.type === "natural_speed");
  if (!natural) return [];

  const translated = lesson.sections.find((s) => s.type === "translated");

  const translatedTexts = translated ? extractTranslations(translated.phrases, languageCode) : [];

  const scenes: Scene[] = [];
  let currentScene: Scene = { title: null, lines: [] };
  let lineIndex = 0;

  for (const p of withoutTitle(natural.phrases, languageCode)) {
    const isNarratorL1 = p.language_code !== languageCode && p.role === "narrator";
    if (isNarratorL1) {
      if (currentScene.lines.length > 0 || currentScene.title !== null) {
        scenes.push(currentScene);
      }
      currentScene = { title: p.text, lines: [] };
    } else if (p.language_code === languageCode) {
      currentScene.lines.push({
        role: p.role,
        words: dialogueLines[lineIndex]?.words ?? [],
        naturalText: p.text,
        translatedText: translatedTexts[lineIndex] ?? "",
        transcriptIndex: lineIndex,
      });
      lineIndex++;
    }
  }
  if (currentScene.lines.length > 0 || currentScene.title !== null) {
    scenes.push(currentScene);
  }
  return scenes;
}

export function fallbackScenes(dialogueLines: DialogueLine[]): Scene[] {
  return [
    {
      title: null,
      lines: dialogueLines.map((dl, idx) => ({
        role: dl.role,
        words: dl.words,
        naturalText: "",
        translatedText: "",
        transcriptIndex: idx,
      })),
    },
  ];
}

export function cueHighlight(cue: Cue | null): CueRef | null {
  if (!cue || !cue.ref) return null;
  if (cue.ref.kind === "narration") return null;
  return cue.ref;
}

export function findSeekCue(
  cues: Cue[],
  lineIndex: number,
  currentSectionIndex: number | null,
): Cue | null {
  let firstMatch: Cue | null = null;
  let sectionMatch: Cue | null = null;
  for (const c of cues) {
    if (c.ref?.kind === "line" && c.ref.target_index === lineIndex) {
      if (!firstMatch) firstMatch = c;
      // First match only: in the translated section a line's group is the L2
      // phrase followed by its narrator translation (both ref the same line);
      // seeking must land on the group start, not the translation.
      if (
        currentSectionIndex !== null &&
        c.section_index === currentSectionIndex &&
        !sectionMatch
      ) {
        sectionMatch = c;
      }
    }
  }
  return sectionMatch ?? firstMatch;
}

export function findKeyPhraseSeekCue(cues: Cue[], kpIndex: number): Cue | null {
  for (const c of cues) {
    if (c.ref?.kind === "key_phrase" && c.ref.target_index === kpIndex) {
      return c;
    }
  }
  return null;
}
