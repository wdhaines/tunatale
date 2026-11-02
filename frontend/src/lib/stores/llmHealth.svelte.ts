import { api } from "#lib/api.js";
import type { LlmHealthStatus } from "#lib/api.js";

function createLlmHealthStore() {
  let status = $state<LlmHealthStatus | null>(null);

  return {
    get status() {
      return status;
    },
    set(next: LlmHealthStatus | null) {
      status = next;
    },
    async refresh() {
      try {
        status = await api.getLlmHealth();
      } catch {
        // Keep last-known status (or null)
      }
    },
  };
}

export const llmHealthStore = createLlmHealthStore();
