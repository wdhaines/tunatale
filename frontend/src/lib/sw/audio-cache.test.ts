import { describe, it, expect } from "vitest";
import {
  AUDIO_CACHE,
  cacheFirstAudio,
  isCacheableAudioRequest,
  type CacheLike,
  type CacheStorageLike,
  type RequestLike,
  type ResponseLike,
} from "./audio-cache";

function req(url: string, method = "GET"): RequestLike {
  return { method, url };
}

function fakeResponse(ok: boolean, id = "r"): ResponseLike {
  const self: ResponseLike = {
    ok,
    clone: () => ({ ...self, _cloned: true }) as ResponseLike & { _cloned: boolean },
  };
  (self as ResponseLike & { id: string }).id = id;
  return self;
}

class FakeCache implements CacheLike {
  store = new Map<string, ResponseLike>();
  puts: Array<[string, ResponseLike]> = [];

  match(request: RequestLike): Promise<ResponseLike | undefined> {
    return Promise.resolve(this.store.get(request.url));
  }
  put(request: RequestLike, response: ResponseLike): Promise<void> {
    this.puts.push([request.url, response]);
    this.store.set(request.url, response);
    return Promise.resolve();
  }
}

class FakeCaches implements CacheStorageLike {
  cache = new FakeCache();
  opened: string[] = [];
  open(name: string): Promise<CacheLike> {
    this.opened.push(name);
    return Promise.resolve(this.cache);
  }
}

describe("isCacheableAudioRequest", () => {
  it("caches GET /api/audio/{id}", () => {
    expect(isCacheableAudioRequest(req("https://host:5173/api/audio/abc-123"))).toBe(true);
  });

  it("does not cache non-GET methods", () => {
    expect(isCacheableAudioRequest(req("https://host/api/audio/abc-123", "POST"))).toBe(false);
  });

  it("does not cache the JSON lesson list (two path segments)", () => {
    expect(isCacheableAudioRequest(req("https://host/api/audio/lesson/abc"))).toBe(false);
  });

  it("does not cache unrelated paths", () => {
    expect(isCacheableAudioRequest(req("https://host/api/srs/review-queue"))).toBe(false);
  });
});

describe("cacheFirstAudio", () => {
  it("returns the cached response without fetching on a hit", async () => {
    const caches = new FakeCaches();
    const cached = fakeResponse(true, "cached");
    caches.cache.store.set("https://host/api/audio/x", cached);
    let fetched = false;

    const result = await cacheFirstAudio(req("https://host/api/audio/x"), {
      caches,
      fetch: () => {
        fetched = true;
        return Promise.resolve(fakeResponse(true, "network"));
      },
    });

    expect(result).toBe(cached);
    expect(fetched).toBe(false);
    expect(caches.opened).toEqual([AUDIO_CACHE]);
  });

  it("fetches and caches a successful miss", async () => {
    const caches = new FakeCaches();
    const network = fakeResponse(true, "network");

    const result = await cacheFirstAudio(req("https://host/api/audio/y"), {
      caches,
      fetch: () => Promise.resolve(network),
    });

    expect(result).toBe(network);
    expect(caches.cache.puts).toHaveLength(1);
    expect(caches.cache.puts[0][0]).toBe("https://host/api/audio/y");
  });

  it("returns but does not cache a failed fetch", async () => {
    const caches = new FakeCaches();
    const network = fakeResponse(false, "error");

    const result = await cacheFirstAudio(req("https://host/api/audio/z"), {
      caches,
      fetch: () => Promise.resolve(network),
    });

    expect(result).toBe(network);
    expect(caches.cache.puts).toHaveLength(0);
  });
});
