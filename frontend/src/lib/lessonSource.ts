export function formatSource(source: unknown): string {
  return JSON.stringify(source, null, 2);
}

export function buildClaudePrompt(source: unknown): string {
  const json = formatSource(source);
  return [
    "You are editing a TunaTale lesson story. Please review and edit this story JSON.",
    "",
    "SCHEMA REMINDER:",
    "- The JSON has a `title`, `key_phrases` (array of {phrase, translation}),",
    "  `scenes` (array of {label, lines} where each line has speaker, text, translation),",
    "  `dialogue_glosses` (array of {word, translation}), and `morphology_focus` (array of strings).",
    "- Preserve the overall structure — only change the dialogue content.",
    "- Each line must have a `speaker`, `text`, and `translation`.",
    "",
    "```json",
    json,
    "```",
    "",
    "Paste the edited JSON back and I will Import it.",
  ].join("\n");
}
