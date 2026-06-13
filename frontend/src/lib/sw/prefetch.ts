/**
 * Wifi-only audio prefetch (offline-audio Phase 4). Populates the same
 * `AUDIO_CACHE` the service worker reads, so a lesson can be cached *before*
 * first play — making even the first listen free when off wifi.
 *
 * Pure, fully-tested primitives; the (thin) call site lives in the audio player.
 */

import { AUDIO_CACHE, type ResponseLike } from "./audio-cache";

/**
 * The slice of the Network Information API we use. It's Chrome-on-Android only
 * and non-standard, so callers feature-detect and pass `undefined` when absent.
 */
export interface NetworkInformationLike {
  readonly type?: string;
  readonly saveData?: boolean;
}

export interface PrefetchCacheLike {
  match(url: string): Promise<ResponseLike | undefined>;
  put(url: string, response: ResponseLike): Promise<void>;
}

export interface PrefetchCacheStorageLike {
  open(name: string): Promise<PrefetchCacheLike>;
}

/**
 * Prefetch only on wifi, and never when the user has asked to save data. When
 * the Network Information API is unavailable we return false (conservative) —
 * on-demand caching via the service worker's cache-first still applies, so the
 * user just pays for the first play instead of a background prefetch.
 */
export function shouldPrefetchOnConnection(
  connection: NetworkInformationLike | undefined,
): boolean {
  if (!connection) return false;
  if (connection.saveData === true) return false;
  return connection.type === "wifi";
}

/**
 * Fetch each URL not already cached and store it in `AUDIO_CACHE`. Failed
 * fetches are skipped (not pinned), matching the service worker's cache-first.
 */
export async function prefetchAudioUrls(
  urls: string[],
  deps: { caches: PrefetchCacheStorageLike; fetch: (url: string) => Promise<ResponseLike> },
): Promise<void> {
  const cache = await deps.caches.open(AUDIO_CACHE);
  for (const url of urls) {
    const existing = await cache.match(url);
    if (existing) continue;
    const response = await deps.fetch(url);
    if (response.status === 200) {
      await cache.put(url, response.clone());
    }
  }
}

/**
 * Entry point for the audio player: prefetch *urls* only when Cache Storage
 * exists and the connection allows it. All the gating lives here (not in the
 * Svelte call site) so the component stays branch-free and the decision logic
 * stays unit-tested. A no-op when Cache Storage is unavailable (e.g. older
 * browsers, or a non-secure context).
 */
export function maybePrefetchLesson(
  urls: string[],
  deps: {
    enabled: boolean;
    connection: NetworkInformationLike | undefined;
    caches: PrefetchCacheStorageLike | undefined;
    fetch: (url: string) => Promise<ResponseLike>;
  },
): Promise<void> {
  if (!deps.enabled) return Promise.resolve();
  if (!deps.caches) return Promise.resolve();
  if (!shouldPrefetchOnConnection(deps.connection)) return Promise.resolve();
  return prefetchAudioUrls(urls, { caches: deps.caches, fetch: deps.fetch });
}
