/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />

/**
 * Service worker — thin event shell. All decision logic lives in the fully
 * tested `#lib/sw/audio-cache.js` module (the frontend coverage gate forbids
 * untested branches in `src/lib`; this file sits outside the gate's include
 * globs, so it stays a thin wrapper). See `docs/offline-audio-plan.md` Phase 3.
 *
 * - install:  precache the built app shell so the UI loads offline.
 * - activate: drop stale caches from previous versions.
 * - fetch:    cache-first for lesson audio; everything else hits the network.
 */

import { version } from "$app/env";
import { assets, immutable } from "$app/manifest";
import { resolve } from "$app/paths";
import { AUDIO_CACHE, handleAudioFetch, isCacheableAudioRequest } from "#lib/sw/audio-cache.js";
import { precacheAssets } from "#lib/sw/precache.js";

// `self` in a service worker is a ServiceWorkerGlobalScope; the webworker lib
// reference above provides the type.
const sw = self as unknown as ServiceWorkerGlobalScope;

const APP_CACHE = `tt-app-${version}`;
// SvelteKit 3's `$app/manifest` replaced `$service-worker`'s `build` / `files`.
// Its paths are relative to the base path with no leading `/`, so `resolve()`
// turns each back into the absolute pathname the old arrays carried — the form
// `cache.addAll` fetches and `precacheAssets`' exclusion list matches against.
// Not a bare [...immutable, ...assets]: debug pages under static/ are filtered
// out, so carrying one costs users nothing. See #lib/sw/precache.
const APP_ASSETS = precacheAssets(
  immutable.map(({ path }) => resolve(path)),
  assets.map(({ path }) => resolve(path)),
);

sw.addEventListener("install", (event) => {
  event.waitUntil(
    sw.caches
      .open(APP_CACHE)
      .then((cache) => cache.addAll(APP_ASSETS))
      .then(() => sw.skipWaiting()),
  );
});

sw.addEventListener("activate", (event) => {
  event.waitUntil(
    sw.caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== APP_CACHE && key !== AUDIO_CACHE)
            .map((key) => sw.caches.delete(key)),
        ),
      )
      .then(() => sw.clients.claim()),
  );
});

sw.addEventListener("fetch", (event) => {
  if (isCacheableAudioRequest(event.request)) {
    // Fetch by URL (not the Request) so the SW's own fetch carries no `Range`
    // header → a cacheable full 200. handleAudioFetch then synthesizes a 206
    // slice for the media element's Range request (Chromium needs a 206, not a
    // 200, or playback stalls after the first buffer).
    event.respondWith(
      handleAudioFetch(event.request, {
        caches: sw.caches,
        fetch: (url) => fetch(url),
      }),
    );
  }
});
