/**
 * Pure helpers for the curriculum-planner chat page.
 *
 * The chat transcript shown in the UI is session-local: the server keeps its
 * own copy inside Curriculum.metadata for prompt context, but GET /{id}
 * doesn't expose it, so the page accumulates messages from turn responses.
 */
import type { ProposedBatch } from "./api";

export interface ChatMessage {
  role: "user" | "planner" | "event";
  content: string;
}

/** Immutable reducer: one completed turn appends the user message and the reply. */
export function appendTurn(
  messages: ChatMessage[],
  userMessage: string,
  reply: string,
): ChatMessage[] {
  return [...messages, { role: "user", content: userMessage }, { role: "planner", content: reply }];
}

export function batchRange(proposed: ProposedBatch): { start: number; end: number } {
  return {
    start: proposed.days[0].day,
    end: proposed.days[proposed.days.length - 1].day,
  };
}

/** Session-local mirror of the event entry the server writes on commit. */
export function commitEvent(proposed: ProposedBatch): ChatMessage {
  const { start, end } = batchRange(proposed);
  const label = start === end ? `day ${start}` : `days ${start}-${end}`;
  return { role: "event", content: `Committed ${label}.` };
}

/** Keep the batch-size input sane: integer, 1..14, NaN → the default of 5. */
export function clampBatchSize(value: number): number {
  if (Number.isNaN(value)) return 5;
  return Math.min(14, Math.max(1, Math.floor(value)));
}
