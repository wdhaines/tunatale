/**
 * A silent, looping audio element that keeps the page's media session alive
 * while the lesson audio changes track (tunatale-bibo).
 *
 * The lesson player has ONE audio element and changes track by assigning a new
 * `src`. In Chrome that destroys the element's media player and builds another.
 * While the lesson audio was the page's only player, the tab's media session
 * went inactive for that instant and gave up Android's audio focus. With the
 * screen on nobody notices: Chrome is the top app and gets focus straight back.
 * With the screen LOCKED it is refused (since Android 15 an app must be on top
 * or running a foreground service to request focus, and Chrome's media service
 * ends with the session), so Chrome pauses the player it has just started. The
 * 2026-10-01 on-device trace shows exactly that, three sections running:
 * `el:ended`, `el:play` on the next section, then an `el:pause` about a second
 * later that no call and no media button asked for.
 *
 * A second player that never stops across the swap keeps the session active, so
 * the new track joins a session that already holds focus. That is this element.
 *
 * The silence is in the FILE, at full volume: Chrome leaves a muted or
 * zero-volume player out of the media session, which would defeat the purpose.
 * It is built in memory rather than served, so it needs no request, no
 * service-worker cache entry and no Range handling, and works offline. The CSP
 * allows it (`media-src 'self' blob:`).
 *
 * Every failure is reported through `onEvent` and none is thrown: a browser
 * that cannot build or play this must still play the lesson.
 */

export interface KeepAlive {
  /** Play the silence. Idempotent; builds the element on first use. */
  start(): void;
  /** Pause it. A no-op before the first start. */
  stop(): void;
  /** Stop for good and release the element and its URL. */
  destroy(): void;
}

interface KeepAliveDeps {
  createAudio?: () => HTMLAudioElement;
  createUrl?: (blob: Blob) => string;
  revokeUrl?: (url: string) => void;
  /** `ka:play`, `ka:pause`, `ka:rejected err=…`, `ka:unavailable err=…`. */
  onEvent?: (event: string) => void;
}

// Longer than 5 s on purpose: Chrome treats shorter audio as a transient sound
// (a notification blip), which takes a different kind of audio focus and is not
// part of the controllable media session.
const SECONDS = 10;
// The lowest rate every decoder accepts; silence has no quality to lose.
const SAMPLE_RATE = 8000;
const BYTES_PER_SAMPLE = 2;
const HEADER_BYTES = 44;

/** A canonical 16-bit mono PCM WAV of digital silence (zero samples). */
export function silentWav(): ArrayBuffer {
  const dataBytes = SECONDS * SAMPLE_RATE * BYTES_PER_SAMPLE;
  const buffer = new ArrayBuffer(HEADER_BYTES + dataBytes);
  const view = new DataView(buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, HEADER_BYTES - 8 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * BYTES_PER_SAMPLE, true); // byte rate
  view.setUint16(32, BYTES_PER_SAMPLE, true); // block align
  view.setUint16(34, BYTES_PER_SAMPLE * 8, true); // bits per sample
  ascii(36, "data");
  view.setUint32(40, dataBytes, true);
  return buffer;
}

export function createKeepAlive(deps: KeepAliveDeps = {}): KeepAlive {
  const report = deps.onEvent ?? (() => {});
  const listenerAbort = new AbortController();
  let el: HTMLAudioElement | null = null;
  let url: string | null = null;
  // Set once the element can never be (re)built: the build failed, or destroy
  // ran. Without it every later start would retry and report again.
  let dead = false;

  function build(): HTMLAudioElement | null {
    try {
      const blob = new Blob([silentWav()], { type: "audio/wav" });
      url = deps.createUrl ? deps.createUrl(blob) : URL.createObjectURL(blob);
      const created = deps.createAudio ? deps.createAudio() : new Audio();
      created.loop = true;
      created.src = url;
      const opts = { signal: listenerAbort.signal };
      created.addEventListener("play", () => report("ka:play"), opts);
      created.addEventListener("pause", () => report("ka:pause"), opts);
      return created;
    } catch (err) {
      dead = true;
      report(`ka:unavailable err=${String(err)}`);
      return null;
    }
  }

  return {
    start() {
      if (dead) return;
      el ??= build();
      if (!el || !el.paused) return;
      const started = el.play();
      if (started && typeof started.catch === "function") {
        started.catch((err: unknown) => report(`ka:rejected err=${String(err)}`));
      }
    },
    stop() {
      el?.pause();
    },
    destroy() {
      dead = true;
      if (!el) return;
      // Before the pause below: browsers queue the pause event, and a destroyed
      // keep-alive must not report into a trace whose owner is gone.
      listenerAbort.abort();
      el.pause();
      el.src = "";
      if (deps.revokeUrl) deps.revokeUrl(url!);
      else URL.revokeObjectURL(url!);
      el = null;
    },
  };
}
