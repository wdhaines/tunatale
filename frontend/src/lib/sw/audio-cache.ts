/**
 * Service-worker audio caching logic — kept as plain, fully-tested functions so
 * the service worker shell (`src/service-worker.ts`) stays a thin wrapper.
 * See `docs/offline-audio-plan.md` Phase 3.
 *
 * Strategy: **cache-first, full-file, with real Range support.** Lesson audio is
 * immutable (keyed by a server-minted UUID), so the first play fetches the whole
 * file once and every later play is served from the cache — online or offline.
 *
 * Why Range matters: `<audio>` requests with a `Range` header and Chromium media
 * playback *requires* a `206 Partial Content` response over a service worker — a
 * `200` makes it stall after the initial buffer (~seconds) and stop. And the
 * server's own `206` can't be cached (`Cache.put` rejects partials). So we cache
 * the full `200` and synthesize `206` slices from it ourselves.
 */

/** Cache bucket holding lesson-audio responses. Bump the suffix to invalidate. */
export const AUDIO_CACHE = "tt-audio-v1";

export interface RequestLike {
  readonly method: string;
  readonly url: string;
}

// Real browser `Cache`/`CacheStorage` satisfy these structurally (match/put
// accept a URL string); tests pass deterministic fakes.
export interface CacheLike {
  match(url: string): Promise<Response | undefined>;
  put(url: string, response: Response): Promise<void>;
}

export interface CacheStorageLike {
  open(name: string): Promise<CacheLike>;
}

/**
 * True for the lesson-audio byte endpoint (`GET /api/audio/{id}`) — the large
 * payload worth caching. The JSON list (`/api/audio/lesson/{id}`, two path
 * segments) and the zip download are deliberately excluded.
 */
export function isCacheableAudioRequest(request: RequestLike): boolean {
  if (request.method !== "GET") return false;
  const { pathname } = new URL(request.url);
  return /^\/api\/audio\/[^/]+$/.test(pathname);
}

/**
 * Resolve an HTTP `Range` header against a known total length to inclusive
 * `{start, end}` byte offsets. Supports `bytes=start-`, `bytes=start-end`, and
 * suffix `bytes=-N`. Returns null for an unparseable or unsatisfiable range.
 */
export function computeByteRange(
  rangeHeader: string,
  total: number,
): { start: number; end: number } | null {
  const match = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader.trim());
  if (!match) return null;
  const [, startStr, endStr] = match;
  if (startStr === "" && endStr === "") return null;

  let start: number;
  let end: number;
  if (startStr === "") {
    // Suffix range: the last N bytes.
    const suffix = Number(endStr);
    if (suffix === 0) return null;
    start = Math.max(0, total - suffix);
    end = total - 1;
  } else {
    start = Number(startStr);
    end = endStr === "" ? total - 1 : Math.min(Number(endStr), total - 1);
  }
  // `end` is already clamped to total-1, so start>end also covers start>=total.
  if (start > end) return null;
  return { start, end };
}

/**
 * Build a `206 Partial Content` response (or a full `200` if the range is
 * unsatisfiable) by slicing the cached full-body response.
 */
export async function buildPartialResponse(full: Response, rangeHeader: string): Promise<Response> {
  const body = await full.arrayBuffer();
  const contentType = full.headers.get("content-type") ?? "application/octet-stream";
  const range = computeByteRange(rangeHeader, body.byteLength);
  if (range === null) {
    return new Response(body, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Length": String(body.byteLength),
        "Accept-Ranges": "bytes",
      },
    });
  }
  const slice = body.slice(range.start, range.end + 1);
  return new Response(slice, {
    status: 206,
    headers: {
      "Content-Type": contentType,
      "Content-Range": `bytes ${range.start}-${range.end}/${body.byteLength}`,
      "Content-Length": String(slice.byteLength),
      "Accept-Ranges": "bytes",
    },
  });
}

/**
 * Serve an audio request cache-first. On a miss, fetch the *full* file (by URL,
 * so no `Range` header → a cacheable `200`), store it, then satisfy the original
 * request: a `Range` request gets a synthesized `206` slice; a plain request
 * gets the full response. Non-200 network responses pass through uncached.
 */
export async function handleAudioFetch(
  request: { url: string; headers: { get(name: string): string | null } },
  deps: { caches: CacheStorageLike; fetch: (url: string) => Promise<Response> },
): Promise<Response> {
  const cache = await deps.caches.open(AUDIO_CACHE);
  let full = await cache.match(request.url);
  if (full === undefined) {
    const networkResponse = await deps.fetch(request.url);
    if (networkResponse.status !== 200) return networkResponse;
    await cache.put(request.url, networkResponse.clone());
    full = networkResponse;
  }

  const rangeHeader = request.headers.get("range");
  if (rangeHeader === null) return full;
  return buildPartialResponse(full, rangeHeader);
}
