// Theme preference: an explicit setting (System / Light / Dark) that defaults to
// the OS preference. The resolved light/dark value is written to
// `<html data-theme>` (CSS keys dark off `:root[data-theme='dark']`) and to
// `color-scheme` so native controls match. A no-flash boot script in app.html
// applies the same thing before first paint; init() re-syncs and starts
// following OS changes while in "system" mode.

export type ThemePref = "system" | "light" | "dark";

const STORAGE_KEY = "theme";
const ORDER: ThemePref[] = ["system", "light", "dark"];

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function resolveTheme(pref: ThemePref): "light" | "dark" {
  if (pref === "system") return systemPrefersDark() ? "dark" : "light";
  return pref;
}

function createThemeStore() {
  let pref = $state<ThemePref>("system");

  function apply(): void {
    const resolved = resolveTheme(pref);
    const root = document.documentElement;
    root.dataset.theme = resolved;
    root.style.colorScheme = resolved;
  }

  function onSystemChange(): void {
    if (pref === "system") apply();
  }

  function init(): void {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && (ORDER as string[]).includes(stored)) {
      pref = stored as ThemePref;
    }
    apply();
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", onSystemChange);
  }

  function set(next: ThemePref): void {
    pref = next;
    localStorage.setItem(STORAGE_KEY, next);
    apply();
  }

  function cycle(): void {
    set(ORDER[(ORDER.indexOf(pref) + 1) % ORDER.length]);
  }

  return {
    get pref(): ThemePref {
      return pref;
    },
    get resolved(): "light" | "dark" {
      return resolveTheme(pref);
    },
    init,
    set,
    cycle,
  };
}

export const themeStore = createThemeStore();
