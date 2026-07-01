// Lesson-page Read/Listen mode preference. The mode determines what the lesson
// page *is*, so it defaults by viewport — Listen is the mobile-primary task,
// Read the desktop-primary one — and remembers an explicit toggle once made.
// An absent stored value means "follow the viewport"; `set` writes the override.
// Mirrors the prefetchPref / theme `$state` + localStorage store pattern.

export type LessonMode = "read" | "listen";

const STORAGE_KEY = "lessonMode";

// 640px is the app's canonical breakpoint (every component uses
// `@media (min-width: 641px)`; mobile is ≤640).
export function viewportDefault(): LessonMode {
  return window.matchMedia("(max-width: 640px)").matches ? "listen" : "read";
}

function createLessonModePref() {
  let mode = $state<LessonMode>("read");

  // Called from the lesson page's onMount (browser-only), the same way the
  // theme/prefetch prefs seed — no SSR guard needed.
  function init(): void {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "read" || stored === "listen") {
      mode = stored;
    } else {
      mode = viewportDefault();
    }
  }

  function set(next: LessonMode): void {
    mode = next;
    localStorage.setItem(STORAGE_KEY, next);
  }

  return {
    get mode(): LessonMode {
      return mode;
    },
    init,
    set,
  };
}

export const lessonModePref = createLessonModePref();
