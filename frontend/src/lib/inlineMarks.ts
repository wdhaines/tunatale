/**
 * The reference pages' light inline markup, parsed into segments a template can loop
 * over (no `{@html}`): `*text*` is italic (Cebuano, or a title), and a backquoted span
 * is an affix. Anything else, unbalanced marks included, is plain text.
 */
export interface Mark {
  kind: "text" | "em" | "affix";
  text: string;
}

const PATTERN = /\*([^*]+)\*|`([^`]+)`/g;

export function inlineMarks(source: string): Mark[] {
  const out: Mark[] = [];
  let last = 0;
  for (const m of source.matchAll(PATTERN)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: "text", text: source.slice(last, at) });
    out.push(m[1] !== undefined ? { kind: "em", text: m[1] } : { kind: "affix", text: m[2] });
    last = at + m[0].length;
  }
  if (last < source.length) out.push({ kind: "text", text: source.slice(last) });
  return out;
}
