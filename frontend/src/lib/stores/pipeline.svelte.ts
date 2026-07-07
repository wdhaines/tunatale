import { api } from "$lib/api";
import type { PipelineStatus } from "$lib/api";
import { rateLimitStore } from "./rateLimit.svelte";
import { llmActivityStore } from "./llmActivity.svelte";

function createPipelineStore() {
  let status = $state<PipelineStatus | null>(null);
  let error = $state("");
  let _timerId: ReturnType<typeof setTimeout> | null = null;
  // Bumped on every start/stop so in-flight polls and armed timers from a
  // previous session can't write state or re-arm after stop()/restart.
  let _gen = 0;

  async function poll(gen: number, id: string) {
    try {
      const s = await api.getPipeline(id);
      if (gen !== _gen) return;
      status = s;
      error = "";
      rateLimitStore.refresh();
      llmActivityStore.refresh();
    } catch (e) {
      if (gen !== _gen) return;
      error = e instanceof Error ? e.message : String(e);
    }
  }

  function scheduleNext(gen: number, id: string) {
    if (gen !== _gen) return;
    const delay = status?.active ? 2000 : 10000;
    _timerId = setTimeout(async () => {
      await poll(gen, id);
      scheduleNext(gen, id);
    }, delay);
  }

  function stop() {
    _gen += 1;
    if (_timerId) clearTimeout(_timerId);
    _timerId = null;
    status = null;
    error = "";
  }

  return {
    get status() {
      return status;
    },
    get error() {
      return error;
    },
    start(id: string) {
      stop();
      const gen = _gen;
      poll(gen, id).then(() => scheduleNext(gen, id));
    },
    stop,
  };
}

export const pipelineStore = createPipelineStore();
