/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />

/**
 * Service worker — thin event shell. All decision logic lives in the fully
 * tested `$lib/sw/audio-cache` module (the frontend coverage gate forbids
 * untested branches in `src/lib`; this file sits outside the gate's include
 * globs, so it stays a thin wrapper). See `docs/offline-audio-plan.md` Phase 3.
 *
 * - install:  precache the built app shell so the UI loads offline.
 * - activate: drop stale caches from previous versions.
 * - fetch:    cache-first for lesson audio; everything else hits the network.
 */

import { build, files, version } from "$service-worker";
import { AUDIO_CACHE, handleAudioFetch, isCacheableAudioRequest } from "$lib/sw/audio-cache";

// `self` in a service worker is a ServiceWorkerGlobalScope; the webworker lib
// reference above provides the type.
const sw = self as unknown as ServiceWorkerGlobalScope;

const APP_CACHE = `tt-app-${version}`;
const APP_ASSETS = [...build, ...files];

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
