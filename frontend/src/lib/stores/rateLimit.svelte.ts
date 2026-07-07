import { api } from "$lib/api";
import type { RateLimitStatus } from "$lib/api";

let autoProbed = false;

function createRateLimitStore() {
  let status = $state<RateLimitStatus | null>(null);
  let probeError = $state("");

  async function doRefresh() {
    try {
      status = await api.getRateLimit();
      probeError = "";
    } catch {
      // Keep last-known status (or null)
    }
  }

  async function doProbe() {
    try {
      status = await api.probeRateLimit();
      probeError = "";
    } catch (e) {
      probeError = e instanceof Error ? e.message : String(e);
    }
  }

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
      await doRefresh();
    },
    async probe() {
      await doProbe();
    },
    async ensureFresh() {
      await doRefresh();
      if (
        !autoProbed &&
        (status === null || (status.llm_mode !== "mock" && status.snapshot == null))
      ) {
        autoProbed = true;
        await doProbe();
      }
    },
  };
}

export const rateLimitStore = createRateLimitStore();
