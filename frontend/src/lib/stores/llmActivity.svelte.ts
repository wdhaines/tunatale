import { api } from "$lib/api";
import type { ActivityEvent } from "$lib/api";

function createLlmActivityStore() {
  let events = $state<ActivityEvent[]>([]);
  let latestSeq = $state(0);
  const MAX_EVENTS = 100;

  const currentLine = $derived.by(() => {
    if (events.length === 0) return "";
    const last = events[events.length - 1];
    if (last.kind === "pipeline") {
      return `[pipeline] day ${last.day}: ${last.state} — ${last.message}`;
    }
    return `[llm] ${last.provider}/${last.model} ${last.status} ${last.latency_ms}ms`;
  });

  return {
    get events() {
      return events;
    },
    get latestSeq() {
      return latestSeq;
    },
    get currentLine() {
      return currentLine;
    },
    async refresh() {
      try {
        const resp = await api.getLlmActivity(latestSeq || undefined);
        if (resp.latest < latestSeq) {
          // Server seq space went backwards (backend restart) — start over,
          // or stale seqs would be filtered out forever.
          events = [];
          latestSeq = 0;
        }
        // Only append genuinely new seqs: a re-sent event would duplicate a
        // key in the keyed {#each} and crash the log component.
        const fresh = resp.events.filter((e) => e.seq > latestSeq);
        if (fresh.length > 0) {
          events = [...events, ...fresh].slice(-MAX_EVENTS);
          latestSeq = resp.latest;
        }
      } catch {
        // silently degrade
      }
    },
    reset() {
      events = [];
      latestSeq = 0;
    },
  };
}

export const llmActivityStore = createLlmActivityStore();
