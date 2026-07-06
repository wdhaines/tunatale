import { api } from "$lib/api";
import type { RateLimitStatus } from "$lib/api";

function createRateLimitStore() {
  let status = $state<RateLimitStatus | null>(null);
  let probeError = $state("");

  return {
    get status() {
      return status;
    },
    set(next: RateLimitStatus | null) {
      status = next;
    },
    get probeError() {
      return probeError;
    },
    async refresh() {
      try {
        status = await api.getRateLimit();
        probeError = "";
      } catch {
        // Keep last-known status (or null)
      }
    },
    async probe() {
      try {
        status = await api.probeRateLimit();
        probeError = "";
      } catch (e) {
        probeError = e instanceof Error ? e.message : String(e);
      }
    },
  };
}

export const rateLimitStore = createRateLimitStore();
