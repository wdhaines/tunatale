/**
 * Which assets the service worker precaches into the app shell on install.
 *
 * Everything SvelteKit reports in `files` (i.e. everything in `static/`) is
 * precached by default, and that default is a trap for debug pages: the
 * `tunatale-qi5.1` voice probe was DELETED at the end of that spike purely
 * because carrying it risked shipping it into every user's app-shell cache
 * (see `.beads-tasks/briefs/findings-voice-car-2026-09-02.md` § Housekeeping).
 *
 * The wake-word spike (`tunatale-mkyb`) makes that trap much worse — its probe
 * needs ~19 MB of ONNX models and the onnxruntime-web WASM runtime — so the
 * rule is expressed here instead of relying on someone remembering to delete
 * the page. An excluded probe is safe to keep in the tree, which is the point:
 * the last one had to be recovered from a commit hash.
 */

/** Static-asset paths that never belong in the PWA app shell. */
const EXCLUDED = ["/voice-probe.html", "/voice-probe/"];

/**
 * True if `path` should be precached at install time.
 *
 * Matching is by exact path or by directory prefix — never a bare
 * `startsWith`, so `/voice-probes-report.html` is not caught by the
 * `/voice-probe/` rule.
 */
export function isPrecachableAsset(path: string): boolean {
  return !EXCLUDED.some((ex) => (ex.endsWith("/") ? path.startsWith(ex) : path === ex));
}

/** The install-time precache list: the built app shell plus permitted statics. */
export function precacheAssets(build: readonly string[], files: readonly string[]): string[] {
  return [...build, ...files].filter(isPrecachableAsset);
}
