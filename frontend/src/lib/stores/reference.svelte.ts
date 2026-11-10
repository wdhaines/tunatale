/**
 * Which Reference pages the active language has (tunatale-h0q3.47). The nav shows the
 * Reference tab only when this is non-empty, so a language with none never offers it.
 * Refreshed by the layout whenever the active language changes.
 */
import { api, type ReferencePage } from "#lib/api.js";

function createReferenceStore() {
  let pages = $state<ReferencePage[]>([]);

  return {
    get pages(): ReferencePage[] {
      return pages;
    },
    /** Re-read the active language's pages; on failure the tab hides rather than guessing. */
    async refresh(): Promise<void> {
      try {
        pages = (await api.getReferencePages()).pages;
      } catch {
        pages = [];
      }
    },
  };
}

export const referenceStore = createReferenceStore();
