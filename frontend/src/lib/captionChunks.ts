export const MAX_CAPTION_CHARS = 90;

const SENTENCE_RE = /[^.!?…]+[.!?…]+/g;

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
    } else {
      // Greedily pack into lines ≤ MAX_CAPTION_CHARS at word boundaries
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
  }

  return chunks.filter((c) => c.length > 0);
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
