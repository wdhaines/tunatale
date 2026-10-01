import { describe, it, expect, vi, afterEach } from "vitest";
import { createKeepAlive, silentWav } from "../keepAlive";

// tunatale-bibo — hands-free with the screen LOCKED played a tiny clip of the
// next section and stopped. See keepAlive.ts for the mechanism; these tests pin
// the two halves a browser cannot be asked about from jsdom: that the file is
// one Chrome will treat as real, long-form audio, and that the element's
// lifecycle is exactly start / stop / destroy with every failure reported.

function makeFakeAudio(overrides: Partial<HTMLAudioElement> = {}): HTMLAudioElement {
  const listeners = new Map<string, Set<EventListener>>();
  const el = {
    paused: true,
    loop: false,
    src: "",
    volume: 1,
    muted: false,
    // The signal is honoured, not ignored: destroy() drops the listeners with
    // one abort(), and a double that discarded the options would pass that test
    // whatever the code did.
    addEventListener: vi.fn(
      (type: string, handler: EventListener, opts?: AddEventListenerOptions) => {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type)!.add(handler);
        opts?.signal?.addEventListener("abort", () => listeners.get(type)?.delete(handler));
      },
    ),
    dispatchEvent: vi.fn((event: Event) => {
      for (const h of listeners.get(event.type) ?? []) h(event);
      return true;
    }),
    play: vi.fn(() => {
      el.paused = false;
      return Promise.resolve();
    }),
    pause: vi.fn(() => {
      el.paused = true;
    }),
    ...overrides,
  };
  return el as unknown as HTMLAudioElement;
}

function ascii(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.slice(offset, offset + length));
}

describe("silentWav", () => {
  const buffer = silentWav();
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);

  it("is a canonical 16-bit mono PCM WAV", () => {
    expect(ascii(bytes, 0, 4)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(bytes.length - 8);
    expect(ascii(bytes, 8, 4)).toBe("WAVE");
    expect(ascii(bytes, 12, 4)).toBe("fmt ");
    expect(view.getUint32(16, true)).toBe(16);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(1); // mono
    const sampleRate = view.getUint32(24, true);
    expect(view.getUint32(28, true)).toBe(sampleRate * 2); // byte rate
    expect(view.getUint16(32, true)).toBe(2); // block align
    expect(view.getUint16(34, true)).toBe(16); // bits per sample
    expect(ascii(bytes, 36, 4)).toBe("data");
    expect(view.getUint32(40, true)).toBe(bytes.length - 44);
  });

  it("runs longer than 5 s, the length under which Chrome treats audio as a transient sound", () => {
    const byteRate = view.getUint32(28, true);
    const seconds = view.getUint32(40, true) / byteRate;
    expect(seconds).toBeGreaterThan(5);
  });

  it("is digital silence: every sample is zero", () => {
    expect(bytes.subarray(44).every((b) => b === 0)).toBe(true);
  });
});

describe("createKeepAlive", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function make(overrides: Partial<HTMLAudioElement> = {}) {
    const el = makeFakeAudio(overrides);
    const events: string[] = [];
    const createAudio = vi.fn(() => el);
    const createUrl = vi.fn((_blob: Blob) => "blob:silence");
    const revokeUrl = vi.fn();
    const keepAlive = createKeepAlive({
      createAudio,
      createUrl,
      revokeUrl,
      onEvent: (e) => events.push(e),
    });
    return { el, events, createAudio, createUrl, revokeUrl, keepAlive };
  }

  it("builds nothing until the first start", () => {
    const { createAudio, createUrl } = make();
    expect(createAudio).not.toHaveBeenCalled();
    expect(createUrl).not.toHaveBeenCalled();
  });

  it("start plays a looping, audible element whose source is the silent WAV", () => {
    const { el, createUrl, keepAlive } = make();
    keepAlive.start();
    expect(el.loop).toBe(true);
    expect(el.src).toBe("blob:silence");
    // A muted or zero-volume player is not part of Chrome's media session, so
    // the silence has to be in the FILE, not on the element.
    expect(el.muted).toBe(false);
    expect(el.volume).toBe(1);
    expect(el.play).toHaveBeenCalledTimes(1);
    const blob = createUrl.mock.calls[0][0];
    expect(blob.type).toBe("audio/wav");
    expect(blob.size).toBe(silentWav().byteLength);
  });

  it("start while already playing does not call play again, and reuses the element", () => {
    const { el, createAudio, keepAlive } = make();
    keepAlive.start();
    keepAlive.start();
    expect(el.play).toHaveBeenCalledTimes(1);
    expect(createAudio).toHaveBeenCalledTimes(1);
  });

  it("stop pauses, and a later start plays the same element again", () => {
    const { el, createAudio, keepAlive } = make();
    keepAlive.start();
    keepAlive.stop();
    expect(el.pause).toHaveBeenCalledTimes(1);
    keepAlive.start();
    expect(el.play).toHaveBeenCalledTimes(2);
    expect(createAudio).toHaveBeenCalledTimes(1);
  });

  it("stop before any start is a no-op", () => {
    const { el, createAudio, keepAlive } = make();
    keepAlive.stop();
    expect(createAudio).not.toHaveBeenCalled();
    expect(el.pause).not.toHaveBeenCalled();
  });

  it("reports the element's own play and pause events", () => {
    const { el, events, keepAlive } = make();
    keepAlive.start();
    el.dispatchEvent(new Event("play"));
    el.dispatchEvent(new Event("pause"));
    expect(events).toEqual(["ka:play", "ka:pause"]);
  });

  it("reports a refused play instead of leaving it unhandled", async () => {
    const err = new DOMException("blocked", "NotAllowedError");
    const { events, keepAlive } = make({
      play: vi.fn(() => Promise.reject(err)),
    } as Partial<HTMLAudioElement>);
    keepAlive.start();
    await Promise.resolve();
    await Promise.resolve();
    expect(events).toEqual(["ka:rejected err=NotAllowedError: blocked"]);
  });

  it("tolerates a play() that returns no promise", () => {
    const { keepAlive } = make({
      play: vi.fn(() => undefined),
    } as unknown as Partial<HTMLAudioElement>);
    expect(() => keepAlive.start()).not.toThrow();
  });

  it("a browser that cannot build the element reports it once and never throws", () => {
    const events: string[] = [];
    const createUrl = vi.fn(() => {
      throw new Error("no blob urls");
    });
    const createAudio = vi.fn(() => makeFakeAudio());
    const keepAlive = createKeepAlive({ createAudio, createUrl, onEvent: (e) => events.push(e) });
    expect(() => keepAlive.start()).not.toThrow();
    keepAlive.start();
    keepAlive.stop();
    keepAlive.destroy();
    expect(events).toEqual(["ka:unavailable err=Error: no blob urls"]);
    expect(createUrl).toHaveBeenCalledTimes(1);
    expect(createAudio).not.toHaveBeenCalled();
  });

  it("destroy pauses, empties the source, revokes the URL and drops the listeners", () => {
    const { el, events, revokeUrl, keepAlive } = make();
    keepAlive.start();
    keepAlive.destroy();
    expect(el.pause).toHaveBeenCalledTimes(1);
    expect(el.src).toBe("");
    expect(revokeUrl).toHaveBeenCalledWith("blob:silence");
    // Browsers queue the pause event, so it arrives after destroy returned.
    el.dispatchEvent(new Event("pause"));
    expect(events).toEqual([]);
  });

  it("start after destroy does nothing", () => {
    const { el, createAudio, keepAlive } = make();
    keepAlive.start();
    keepAlive.destroy();
    keepAlive.start();
    expect(el.play).toHaveBeenCalledTimes(1);
    expect(createAudio).toHaveBeenCalledTimes(1);
  });

  it("destroy before any start builds and revokes nothing", () => {
    const { createAudio, revokeUrl, keepAlive } = make();
    keepAlive.destroy();
    keepAlive.start();
    expect(createAudio).not.toHaveBeenCalled();
    expect(revokeUrl).not.toHaveBeenCalled();
  });

  it("with no dependencies supplied it uses the browser's Audio and object URLs, silently", () => {
    const el = makeFakeAudio();
    // A constructor, because the code calls `new Audio()`.
    vi.stubGlobal(
      "Audio",
      vi.fn(function () {
        return el;
      }),
    );
    const createObjectURL = vi.fn(() => "blob:default");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });

    const keepAlive = createKeepAlive();
    keepAlive.start();
    el.dispatchEvent(new Event("play"));
    expect(el.src).toBe("blob:default");
    expect(el.play).toHaveBeenCalledTimes(1);
    keepAlive.destroy();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:default");
  });
});
