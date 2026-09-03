/**
 * Marking content listened, and what follows from it — shared by the lesson
 * page and the review-session reader (bd tunatale-9p9d).
 *
 * ⚠️ ALL OF THIS WAS ALREADY CONTENT-SCOPED, which is why it can move at all.
 * Every call keys on one id: the listen POST, the review queue, the transcript
 * re-read. The only thing that ever made it look lesson-only was
 * `store.get_lesson(id)` in five handlers — now
 * `ContentStore.get_readable_content`, behind /api/srs/content/{id}/…
 *
 * `contentId` is a GETTER so it is read at call time: SvelteKit reuses the
 * component across navigations, and a captured id would leave the queue count
 * describing the page you just left.
 */
import { api } from "$lib/api";
import type { ListenResponse, TranscriptData } from "$lib/api";
import { listenedStore } from "$lib/stores/listened.svelte";
import { queueStatsStore } from "$lib/stores/queueStats.svelte";

export interface ListenActionsOptions {
  contentId: string;
  languageCode: string;
  setTranscript: (t: TranscriptData) => void;
  setError: (message: string) => void;
}

export function createListenActions(opts: ListenActionsOptions) {
  let listenResult = $state<ListenResponse | null>(null);
  let queueCount = $state(0);
  let hasUnreviewedListen = $state(false);
  let showPreview = $state(false);

  async function fetchQueue() {
    const id = opts.contentId;
    try {
      const { queue, has_unreviewed_listen } = await api.fetchLessonReviewQueue(id);
      // Ignore a response for content we have since navigated away from.
      if (opts.contentId === id) {
        queueCount = queue.length;
        hasUnreviewedListen = has_unreviewed_listen;
      }
    } catch {
      // 404 or network error — leave queueCount at its last value.
    }
  }

  async function onPreviewDone(result: ListenResponse | { status: "cancelled" }) {
    const id = opts.contentId;
    showPreview = false;
    // `in` narrows the union properly; a plain `result.status === 'cancelled'`
    // check does not, because ListenResponse.status is `string` (not a literal),
    // so TS cannot exclude that arm from the negative branch.
    if (!("created" in result)) return;
    listenResult = result;
    try {
      await listenedStore.refresh();
      const t = await api.getTranscript(id);
      if (opts.contentId === id) opts.setTranscript(t);
      await fetchQueue();
      queueStatsStore.refresh();
    } catch (e) {
      if (opts.contentId === id) opts.setError(e instanceof Error ? e.message : String(e));
    }
  }

  function reset() {
    listenResult = null;
    queueCount = 0;
    hasUnreviewedListen = false;
  }

  return {
    get listenResult() {
      return listenResult;
    },
    get queueCount() {
      return queueCount;
    },
    get showPreview() {
      return showPreview;
    },
    get isListened() {
      return listenedStore.has(opts.contentId);
    },
    /**
     * The "Check your work" link is offered only once there is something to
     * check: listened, a non-empty queue, AND an unreviewed listen. `queueCount`
     * alone used to gate it and meant something different then.
     */
    get showCheckWorkLink() {
      return this.isListened ? queueCount > 0 && hasUnreviewedListen : false;
    },
    /**
     * Everything <ListenPreviewModal> needs, as one object to spread.
     *
     * Same reasoning as Transcript's props bundle: Svelte compiles each prop
     * expression into a lazy getter, so listing them per call site leaves
     * uncovered statements until the modal happens to read each one — and two
     * pages listing the same three props is duplication besides.
     */
    get previewProps() {
      return {
        lessonId: opts.contentId,
        languageCode: opts.languageCode,
        onDone: onPreviewDone,
      };
    },
    fetchQueue,
    onPreviewDone,
    reset,
    open: () => {
      showPreview = true;
    },
  };
}
