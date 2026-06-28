// Active L2 language (Phase 5 — simultaneous multi-language). The selected code
// is persisted to localStorage under the same key api.ts reads, so every request
// carries the X-TT-Language header and the backend serves the right per-language
// connection. `options` is populated from GET /api/languages so the selector only
// offers configured languages; single-language deployments just show one.

import { api, LANGUAGE_STORAGE_KEY, type LanguageOption } from "$lib/api";

function createLanguageStore() {
  let code = $state<string>("");
  let options = $state<LanguageOption[]>([]);

  function set(next: string): void {
    code = next;
    localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
  }

  async function init(): Promise<void> {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    try {
      const resp = await api.getLanguages();
      options = resp.languages;
      // Honor a stored choice only if it's still a configured language; else the
      // backend's active language (its default) is the source of truth.
      const codes = resp.languages.map((l) => l.code);
      code = stored && codes.includes(stored) ? stored : resp.active;
      localStorage.setItem(LANGUAGE_STORAGE_KEY, code);
    } catch {
      // Backend unreachable — fall back to the stored choice (or empty → default).
      code = stored ?? "";
    }
  }

  return {
    get code(): string {
      return code;
    },
    get options(): LanguageOption[] {
      return options;
    },
    get name(): string {
      return options.find((l) => l.code === code)?.name ?? "";
    },
    init,
    set,
  };
}

export const languageStore = createLanguageStore();
