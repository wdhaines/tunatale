/** Map a mastery fraction (0 = new, 1 = mastered) to a red→green hue.
 *  0 → red (hue 0), 0.5 → yellow (hue 60), 1 → green (hue 120). */
export function masteryColor(progress: number): string {
  const p = Math.max(0, Math.min(1, progress));
  const hue = p * 120;
  const lightness = 50 - p * 8;
  return `hsl(${hue}, 70%, ${lightness}%)`;
}
