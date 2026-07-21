export const MAX_CAPTION_CHARS = 64;

const SENTENCE_RE = /[^.!?…]+[.!?…]+/g;
const CLAUSE_RE = /([,;:\u2014\u2013])/;

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

    // Step 2b: split at clause boundaries (comma, semicolon, colon, dash)
    // for sentences longer than MAX. Punctuation kept on the left piece.
    const clauseParts = sentence.split(CLAUSE_RE);
    if (clauseParts.length > 1) {
      // Reassemble: attach each separator to its preceding piece
      const assembled: string[] = [];
      for (let i = 0; i < clauseParts.length; i++) {
        if (clauseParts[i].match(CLAUSE_RE)) {
          // Separator — attach to previous piece
          if (assembled.length > 0) {
            assembled[assembled.length - 1] += clauseParts[i];
          }
        } else {
          const trimmed = clauseParts[i].trimStart();
          if (trimmed.length > 0) {
            assembled.push(trimmed);
          }
        }
      }

      // Check if any assembled piece is still over budget
      const needsWordPack = assembled.some((p) => p.length > MAX_CAPTION_CHARS);
      if (!needsWordPack) {
        chunks.push(...assembled);
        continue;
      }
      // Fall through to word-packing for the whole sentence
    }

    // Step 2c: word-packing (existing greedy logic)
    const words = sentence.split(/\s+/);
    let line = "";
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (candidate.length > MAX_CAPTION_CHARS && line.length > 0) {
        chunks.push(line);
        line = word;
      } else {
        line = candidate;
      }
    }
    if (line) chunks.push(line);
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
