import { describe, it, expect } from "vitest";
import {
  maybePrefetchLesson,
  prefetchAudioUrls,
  shouldPrefetchOnConnection,
  type PrefetchCacheLike,
  type PrefetchCacheStorageLike,
} from "./prefetch";
import type { ResponseLike } from "./audio-cache";

describe("shouldPrefetchOnConnection", () => {
  it("is false when the Network Information API is absent", () => {
    expect(shouldPrefetchOnConnection(undefined)).toBe(false);
  });

  it("is false when the user opted into data saving", () => {
    expect(shouldPrefetchOnConnection({ type: "wifi", saveData: true })).toBe(false);
  });

  it("is true on wifi without data saving", () => {
    expect(shouldPrefetchOnConnection({ type: "wifi", saveData: false })).toBe(true);
  });

  it("is false on a cellular connection", () => {
    expect(shouldPrefetchOnConnection({ type: "cellular" })).toBe(false);
  });
});

function ok(): ResponseLike {
  const self: ResponseLike = { status: 200, clone: () => self };
  return self;
}

class FakeCache implements PrefetchCacheLike {
  store = new Map<string, ResponseLike>();
  match(url: string): Promise<ResponseLike | undefined> {
    return Promise.resolve(this.store.get(url));
  }
  put(url: string, response: ResponseLike): Promise<void> {
    this.store.set(url, response);
    return Promise.resolve();
  }
}

class FakeCaches implements PrefetchCacheStorageLike {
  cache = new FakeCache();
  open(): Promise<PrefetchCacheLike> {
    return Promise.resolve(this.cache);
  }
}

describe("prefetchAudioUrls", () => {
  it("fetches uncached urls, skips cached ones, and skips failed fetches", async () => {
    const caches = new FakeCaches();
    caches.cache.store.set("/api/audio/already", ok());
    const fetched: string[] = [];

    await prefetchAudioUrls(["/api/audio/already", "/api/audio/new", "/api/audio/bad"], {
      caches,
      fetch: (url) => {
        fetched.push(url);
        const failed: ResponseLike = { status: 500, clone: () => failed };
        return Promise.resolve(url.endsWith("bad") ? failed : ok());
      },
    });

    expect(fetched).toEqual(["/api/audio/new", "/api/audio/bad"]);
    expect(caches.cache.store.has("/api/audio/new")).toBe(true);
    expect(caches.cache.store.has("/api/audio/bad")).toBe(false);
  });

  it("does nothing for an empty url list", async () => {
    const caches = new FakeCaches();
    await prefetchAudioUrls([], {
      caches,
      fetch: () => Promise.reject(new Error("should not fetch")),
    });
    expect(caches.cache.store.size).toBe(0);
  });
});

describe("maybePrefetchLesson", () => {
  it("is a no-op when the preference is disabled", async () => {
    const caches = new FakeCaches();
    await maybePrefetchLesson(["/api/audio/x"], {
      enabled: false,
      connection: { type: "wifi" },
      caches,
      fetch: () => Promise.resolve(ok()),
    });
    expect(caches.cache.store.size).toBe(0);
  });

  it("is a no-op when Cache Storage is unavailable", async () => {
    let fetched = false;
    await maybePrefetchLesson(["/api/audio/x"], {
      enabled: true,
      connection: { type: "wifi" },
      caches: undefined,
      fetch: () => {
        fetched = true;
        return Promise.resolve(ok());
      },
    });
    expect(fetched).toBe(false);
  });

  it("is a no-op when the connection disallows prefetch", async () => {
    const caches = new FakeCaches();
    await maybePrefetchLesson(["/api/audio/x"], {
      enabled: true,
      connection: { type: "cellular" },
      caches,
      fetch: () => Promise.resolve(ok()),
    });
    expect(caches.cache.store.size).toBe(0);
  });

  it("prefetches when enabled, Cache Storage exists, and the connection is wifi", async () => {
    const caches = new FakeCaches();
    await maybePrefetchLesson(["/api/audio/x"], {
      enabled: true,
      connection: { type: "wifi" },
      caches,
      fetch: () => Promise.resolve(ok()),
    });
    expect(caches.cache.store.has("/api/audio/x")).toBe(true);
  });
});
