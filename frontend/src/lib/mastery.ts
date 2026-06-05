/** Map a mastery fraction (0 = new, 1 = mastered) to a red→green hue.
 *  0 → red (hue 0), 0.5 → yellow (hue 60), 1 → green (hue 120). */
export function masteryColor(progress: number): string {
  const p = Math.max(0, Math.min(1, progress));
  const hue = p * 120;
  const lightness = 50 - p * 8;
  return `hsl(${hue}, 70%, ${lightness}%)`;
}

/** Same red→green hue ramp as {@link masteryColor}, but a low-alpha tint for use
 *  as a background behind text (e.g. a collocation span). 0 → faint red,
 *  1 → faint green. */
export function masteryBackgroundColor(progress: number): string {
  const p = Math.max(0, Math.min(1, progress));
  const hue = p * 120;
  return `hsla(${hue}, 70%, 45%, 0.15)`;
}
