import { api } from "$lib/api";
import type { QueueStats } from "$lib/api";

/**
 * Shared review-count stats so the nav badge and the /review page can't drift.
 *
 * Before this store the layout kept its own `stats` and only refreshed on
 * focus / sync / mount — so grading on the /review page updated the page's
 * badge but left the nav badge stale until the next focus event. Both now read
 * (and write) the same singleton: any grade refetch updates the nav live.
 */
function createQueueStatsStore() {
  let stats = $state<QueueStats | null>(null);
  return {
    get stats() {
      return stats;
    },
    set(next: QueueStats | null) {
      stats = next;
    },
    async refresh() {
      try {
        stats = await api.fetchQueueStats();
      } catch {
        // Keep last-known (or none) — the badge is non-critical chrome.
      }
    },
  };
}

export const queueStatsStore = createQueueStatsStore();
