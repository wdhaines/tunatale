import type { PeerSyncResult } from "$lib/api";

function createSyncStore() {
  let lastResult = $state<PeerSyncResult | null>(null);
  return {
    get lastResult() {
      return lastResult;
    },
    notify(result: PeerSyncResult | null) {
      lastResult = result;
    },
  };
}

export const syncStore = createSyncStore();
