/**
 * Everything the reading UI DOES, shared by the lesson page and the review
 * session reader (bd tunatale-9p9d).
 *
 * ⚠️ EXTRACTED RATHER THAN COPIED, and the distinction is the whole point. The
 * session reader first shipped with a hand-rolled transcript because the lesson
 * transcript endpoint missed on a session id; that fork was visibly worse within
 * a day. The user's instruction was "as much like the lesson page as possible,
 * just with a different source" — so the SOURCE is the only parameter, and the
 * behaviour has exactly one implementation.
 *
 * Only two things vary between the two callers:
 *   contentId    — a lesson id or a session id; both are just ids
 *   languageCode — the content's language
 *
 * There is no transcript-fetcher parameter: /api/srs/content/{id}/transcript
 * resolves either, so even the SOURCE is now shared.
 *
 * Everything else (createBaseCard, submitDrill, undoGrade, the tooltip actions)
 * is content-agnostic and always was.
 *
 * The caller keeps ownership of `transcript` and `error` through accessors
 * rather than having them moved in here: the lesson page reads both in several
 * places this module knows nothing about, and taking them over would have
 * turned a behaviour extraction into a rewrite of that page.
 */
import { api } from "$lib/api";
import type { TranscriptData, WordToken } from "$lib/api";
import { confirmDialog } from "$lib/components/ConfirmDialog.svelte";
import { queueStatsStore } from "$lib/stores/queueStats.svelte";

export interface ReadingActionsOptions {
  contentId: string;
  languageCode: string;
  getTranscript: () => TranscriptData | null;
  setTranscript: (t: TranscriptData) => void;
  setError: (message: string) => void;
}

export interface CreatePhraseArgs {
  text: string;
  word_count: number;
  translation: string;
  lineIndex: number;
  startIdx: number;
  endIdx: number;
  source_sentence?: string;
  source_lesson_id?: string;
  source_line_index?: number;
}

export function createReadingActions(opts: ReadingActionsOptions) {
  // A just-graded item stays reversible from its popover ("Undo ↩") until
  // something else is graded, the page reloads, or a sync hands the review to
  // Anki (the backend 409s then).
  let undoable = $state<{ itemId: number; direction: "recognition" | "production" } | null>(null);
  let wordActionInFlight = false;

  // No fetcher parameter any more: /api/srs/content/{id}/transcript resolves a
  // lesson OR a review session, so both callers re-read through the same route.
  const refetch = async () => opts.setTranscript(await api.getTranscript(opts.contentId));

  const guarded = async (fn: () => Promise<void>) => {
    opts.setError("");
    try {
      await fn();
    } catch (e) {
      opts.setError(e instanceof Error ? e.message : String(e));
    }
  };

  async function onWordClick(word: WordToken, lineIndex: number) {
    if (wordActionInFlight) return;
    wordActionInFlight = true;
    await guarded(async () => {
      if (word.active_state === "unknown") {
        // Reading an untracked word introduces AND reviews it in one tap: create
        // the base card, then record a first recognition review so it enters
        // learning right away rather than parking at NEW.
        const sentence = opts.getTranscript()?.dialogue_lines[lineIndex]?.sentence ?? "";
        const created = await api.createBaseCard({
          surface: word.surface,
          lemma: word.lemma,
          sentence,
          language_code: opts.languageCode,
          translation: word.translation ?? "",
        });
        await api.submitDrill(created.id, "recognition", "good");
        undoable = { itemId: created.id, direction: "recognition" };
      } else if (word.is_due && word.active_direction && word.srs_item_id != null) {
        const direction = word.active_direction as "recognition" | "production";
        await api.submitDrill(word.srs_item_id, direction, "good");
        undoable = { itemId: word.srs_item_id, direction };
      } else if (word.recognition_reviewable && word.srs_item_id != null) {
        // Read-ahead: reading a not-due word is a valid RECOGNITION review.
        // Always grade the literal recognition direction — never
        // active_direction, which flips to production once recognition
        // graduates (that would silently grade the wrong card).
        await api.submitDrill(word.srs_item_id, "recognition", "good");
        undoable = { itemId: word.srs_item_id, direction: "recognition" };
      } else {
        return;
      }
      await refetch();
      // A grade changes the review counts; keep the shared nav badge truthful.
      queueStatsStore.refresh();
    });
    wordActionInFlight = false;
  }

  async function onCollocationStateChange(span_id: number) {
    if (wordActionInFlight) return;
    wordActionInFlight = true;
    await guarded(async () => {
      await api.submitDrill(span_id, "recognition", "good");
      undoable = { itemId: span_id, direction: "recognition" };
      await refetch();
    });
    wordActionInFlight = false;
  }

  async function onUndoGrade(itemId: number, direction: "recognition" | "production") {
    // Either way the snapshot is spent: success restores it, failure means a
    // newer grade or a sync invalidated it — drop the Undo button regardless.
    undoable = null;
    await guarded(async () => {
      await api.undoGrade(itemId, direction);
      await refetch();
    });
  }

  async function onCreatePhrase(args: CreatePhraseArgs) {
    await guarded(async () => {
      await api.createSRSItem({
        text: args.text,
        language_code: opts.languageCode,
        word_count: args.word_count,
        translation: args.translation,
        source_sentence: args.source_sentence,
        source_lesson_id: args.source_lesson_id,
        source_line_index: args.source_line_index,
      });
      await refetch();
    });
  }

  const tooltipActions = {
    onCreateInflection: async (word: WordToken, sentence: string) =>
      guarded(async () => {
        await api.createInflectionCloze({
          surface: word.surface,
          lemma: word.lemma,
          feature: word.inflection_feature!,
          sentence,
          language_code: opts.languageCode,
          lesson_id: opts.contentId,
          translation: word.translation ?? "",
        });
        await refetch();
      }),
    onSetState: async (id: number, state: string) => {
      // Reset-to-new forgets the card in Anki too (re-learn from scratch), so
      // confirm before discarding the schedule. Other states are label-only.
      if (
        state === "new" &&
        !(await confirmDialog(
          "Reset this word? It will be forgotten in Anki too and re-learned from scratch.",
          { destructive: true },
        ))
      ) {
        return;
      }
      await guarded(async () => {
        await api.setSRSItemState(id, state);
        await refetch();
      });
    },
    onUntrack: async (id: number) =>
      guarded(async () => {
        await api.untrackSRSItem(id);
        await refetch();
      }),
    onUnignore: async (id: number) =>
      guarded(async () => {
        await api.suspendSRSItem(id, false);
        await refetch();
      }),
    onIgnoreLemma: async (lemma: string) =>
      guarded(async () => {
        await api.ignoreLemma(lemma, opts.languageCode);
        await refetch();
      }),
    onUnignoreLemma: async (lemma: string) =>
      guarded(async () => {
        await api.unignoreLemma(lemma, opts.languageCode);
        await refetch();
      }),
    onRestoreKnown: async (id: number) =>
      guarded(async () => {
        await api.restoreKnown(id);
        await refetch();
      }),
    // Match on item id only — grading recognition can graduate it, flipping the
    // refetched word's active_direction to production; the undo must still hit
    // the direction that was actually graded (stored in `undoable`).
    isGradeUndoable: (word: WordToken) => undoable != null && word.srs_item_id === undoable.itemId,
    onUndoGrade: async (_word: WordToken) => {
      if (undoable != null) await onUndoGrade(undoable.itemId, undoable.direction);
    },
  };

  // A collocation undo is always a RECOGNITION undo — collocations have no
  // production direction. The rule lived as an inline arrow in two templates;
  // it belongs here, once, with the reason attached.
  const onCollocationUndo = (spanId: number) => onUndoGrade(spanId, "recognition");

  return {
    onWordClick,
    onCollocationStateChange,
    onUndoGrade,
    onCollocationUndo,
    onCreatePhrase,
    tooltipActions,

    /**
     * Everything <Transcript> needs, as one object to spread.
     *
     * Handed over whole rather than listed prop-by-prop at each call site: six
     * identical lines in two templates is exactly the duplication this module
     * exists to remove, and the set can now grow without editing both pages.
     * Reading `undoable` here keeps the spread reactive, so the popover's
     * "Got it ✓" → "Undo ↩" cycle still works through it.
     */
    get transcriptProps() {
      return {
        onWordClick,
        onCollocationStateChange,
        undoableItemId: undoable?.itemId ?? null,
        onCollocationUndo,
        onCreatePhrase,
        tooltipActions,
      };
    },
  };
}
