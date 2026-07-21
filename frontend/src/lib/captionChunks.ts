// Tuned so a chunk fits one line of the 1.3rem caption on a narrow phone;
// longer text is split (at clause boundaries first) into several timed chunks
// rather than wrapping to a second, orphaned line.
export const MAX_CAPTION_CHARS = 32;

const SENTENCE_RE = /[^.!?…]+[.!?…]+/g;
const CLAUSE_RE = /([,;:—–])/;

// Greedily pack words onto lines of at most `max` chars, never splitting a
// single word (an over-long word is emitted alone, exceeding `max`).
function wordPack(text: string, max: number): string[] {
  const out: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const candidate = line ? `${line} ${word}` : word;
    if (candidate.length > max && line.length > 0) {
      out.push(line);
      line = word;
    } else {
      line = candidate;
    }
  }
  if (line) out.push(line);
  return out;
}

// Break one sentence into clause pieces at comma/semicolon/colon/dash, keeping
// each separator on its left piece. Returns [sentence] when there is no clause
// boundary.
function clauseSplit(sentence: string): string[] {
  const parts = sentence.split(CLAUSE_RE);
  if (parts.length <= 1) return [sentence];
  const pieces: string[] = [];
  for (const part of parts) {
    if (CLAUSE_RE.test(part)) {
      if (pieces.length > 0) pieces[pieces.length - 1] += part;
    } else {
      const t = part.trimStart();
      if (t.length > 0) pieces.push(t);
    }
  }
  return pieces;
}

export function splitCaption(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [""];

  const sentences: string[] = [];
  let match: RegExpExecArray | null;
  let lastEnd = 0;
  SENTENCE_RE.lastIndex = 0;
  while ((match = SENTENCE_RE.exec(trimmed)) !== null) {
    const m = match[0].trim();
    if (m) sentences.push(m);
    lastEnd = match.index + match[0].length;
  }
  const remainder = trimmed.slice(lastEnd).trim();
  if (remainder) sentences.push(remainder);

  const chunks: string[] = [];
  for (const sentence of sentences) {
    if (sentence.length <= MAX_CAPTION_CHARS) {
      chunks.push(sentence);
      continue;
    }
    // Prefer clause boundaries, then word-pack any clause that's still too
    // long — so the comma split survives even when MAX is small.
    for (const piece of clauseSplit(sentence)) {
      if (piece.length <= MAX_CAPTION_CHARS) {
        chunks.push(piece);
      } else {
        chunks.push(...wordPack(piece, MAX_CAPTION_CHARS));
      }
    }
  }

  return chunks.filter((c) => c.length > 0);
}

export function chunkStartMs(
  chunks: string[],
  startMs: number,
  endMs: number,
  idx: number,
): number {
  if (chunks.length <= 1) return startMs;
  if (endMs <= startMs) return startMs;

  const totalChars = chunks.reduce((sum, c) => sum + c.length, 0);
  if (totalChars === 0) return startMs;
  if (idx <= 0) return startMs;
  if (idx >= chunks.length) idx = chunks.length - 1;

  const totalDuration = endMs - startMs;
  let cumMs = 0;
  for (let i = 0; i < idx; i++) {
    cumMs += (chunks[i].length / totalChars) * totalDuration;
  }
  return startMs + cumMs;
}

export function activeChunkIndex(
  chunks: string[],
  startMs: number,
  endMs: number,
  currentMs: number,
): number {
  if (chunks.length <= 1) return 0;
  if (endMs <= startMs) return 0;

  const totalChars = chunks.reduce((sum, c) => sum + c.length, 0);
  if (totalChars === 0) return 0;

  let elapsed = currentMs - startMs;
  if (elapsed <= 0) return 0;

  const totalDuration = endMs - startMs;
  if (currentMs >= endMs) return chunks.length - 1;

  const charBudget = totalChars;
  let cumMs = 0;
  for (let i = 0; i < chunks.length - 1; i++) {
    const chunkDuration = (chunks[i].length / charBudget) * totalDuration;
    cumMs += chunkDuration;
    if (elapsed < cumMs) return i;
  }
  return chunks.length - 1;
}
