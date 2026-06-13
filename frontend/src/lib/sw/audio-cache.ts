/**
 * Service-worker audio caching logic — kept as plain, fully-tested functions so
 * the service worker shell (`src/service-worker.ts`) stays a thin, untested
 * wrapper. See `docs/offline-audio-plan.md` Phase 3.
 *
 * Strategy: **cache-first** for lesson-audio byte requests. They're immutable
 * (keyed by a server-minted UUID), so the first play — ideally on wifi —
 * populates the cache and every replay afterward is served from disk with zero
 * network, working fully offline.
 */

/** Cache bucket holding lesson-audio responses. Bump the suffix to invalidate. */
export const AUDIO_CACHE = "tt-audio-v1";

// Minimal structural shapes so this module needs no DOM lib and tests can pass
// deterministic fakes. Real browser `Request`/`Response`/`Cache`/`CacheStorage`
// satisfy these structurally, so the service-worker shell passes them directly.
export interface RequestLike {
  readonly method: string;
  readonly url: string;
}

export interface ResponseLike {
  readonly status: number;
  clone(): ResponseLike;
}

export interface CacheLike {
  match(request: RequestLike): Promise<ResponseLike | undefined>;
  put(request: RequestLike, response: ResponseLike): Promise<void>;
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
 * Serve *request* from the audio cache if present; otherwise fetch the full
 * resource, cache it, and return it.
 *
 * Range handling is the crux: `<audio>` requests with a `Range` header and the
 * server answers ranges with `206 Partial Content`, which `Cache.put()` rejects
 * (TypeError) — that crash rejected `respondWith` and showed a 0:00 player. So
 * `deps.fetch` MUST fetch the whole resource *without* a Range header (the SW
 * shell fetches by URL), yielding a cacheable `200`. We cache **only** `200`;
 * anything else (an error, or an unexpected partial) is passed through uncached.
 * A cached full `200` served back to a later Range request is handled by the
 * browser, so offline replay and seeking both work.
 */
export async function cacheFirstAudio(
  request: RequestLike,
  deps: { caches: CacheStorageLike; fetch: (request: RequestLike) => Promise<ResponseLike> },
): Promise<ResponseLike> {
  const cache = await deps.caches.open(AUDIO_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await deps.fetch(request);
  if (response.status === 200) {
    await cache.put(request, response.clone());
  }
  return response;
}
