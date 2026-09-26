import { createLocalPref } from "./localPref.svelte";

// Lesson-page Read/Listen mode preference. The mode determines what the lesson
// page *is*, so it defaults by viewport — Listen is the mobile-primary task,
// Read the desktop-primary one — and remembers an explicit toggle once made.
// An absent stored value means "follow the viewport"; `set` writes the override.
// Mirrors the prefetchPref / theme `$state` + localStorage store pattern.

export type LessonMode = "read" | "listen";

// 640px is the app's canonical breakpoint (every component uses
// `@media (min-width: 641px)`; mobile is ≤640).
export function viewportDefault(): LessonMode {
  // No window means no viewport to ask — the reader gets the desktop default,
  // which is also what the store holds before it ever seeds.
  if (typeof window === "undefined") return "read";
  return window.matchMedia("(max-width: 640px)").matches ? "listen" : "read";
}

const pref = createLocalPref<LessonMode>("lessonMode", {
  parse: (raw) => (raw === "read" || raw === "listen" ? raw : viewportDefault()),
  serialize: (next) => next,
});

export const lessonModePref = {
  get mode(): LessonMode {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
};
