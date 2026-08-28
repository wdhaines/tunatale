import type { Cue, CueRef, DialogueLine, LessonDetail, WordToken } from "./api";

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

// ⚠️ Duplicate of backend `section_builder.py::SECTION_TITLES`. Keep the
// two lists in lockstep (tunatale-v3ri).
//
// Measured 2026-08-28, because the obvious reading of this set is wrong. Only
// ONE of the two consumers is live against today's phrase order:
//   - buildScenes (natural_speed): LIVE. The section title is the first
//     narrator L1 phrase, and without it here it becomes a spurious scene
//     heading. Dropping "Natural Speed" reds 4 tests.
//   - extractTranslations (translated): NOT currently reachable. The builders
//     emit [title, scene_label, l2, gloss, ...], so every title and scene label
//     is already skipped by the `awaiting` guard before this set is consulted.
//     The entries are defence-in-depth against a phrase order where a title
//     follows an L2 line; see the test named for that shape.
const SECTION_TITLES = new Set([
  "Key Phrases",
  "Natural Speed",
  "Enunciated",
  "English After",
  "English Before",
  "Enunciated, English After",
  "Enunciated, English Before",
]);

function extractTranslations(
  phrases: LessonDetail["sections"][number]["phrases"],
  languageCode: string,
): string[] {
  const out: string[] = [];
  let awaiting = false;
  for (const p of phrases) {
    if (p.language_code === languageCode) {
      if (awaiting) out.push("");
      awaiting = true;
    } else if (p.role === "narrator" && awaiting && !SECTION_TITLES.has(p.text)) {
      out.push(p.text);
      awaiting = false;
    }
  }
  if (awaiting) out.push("");
  return out;
}

export function buildScenes(lesson: LessonDetail, dialogueLines: DialogueLine[]): Scene[] {
  const languageCode = lesson.language_code;
  const natural = lesson.sections.find((s) => s.type === "natural_speed");
  if (!natural) return [];

  const translated = lesson.sections.find((s) => s.type === "translated");

  const translatedTexts = translated ? extractTranslations(translated.phrases, languageCode) : [];

  const scenes: Scene[] = [];
  let currentScene: Scene = { title: null, lines: [] };
  let lineIndex = 0;

  for (const p of natural.phrases) {
    const isNarratorL1 = p.language_code !== languageCode && p.role === "narrator";
    if (isNarratorL1) {
      if (SECTION_TITLES.has(p.text)) continue;
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
