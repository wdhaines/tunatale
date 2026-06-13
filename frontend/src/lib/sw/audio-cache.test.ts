import { describe, it, expect } from "vitest";
import {
  AUDIO_CACHE,
  buildPartialResponse,
  computeByteRange,
  handleAudioFetch,
  isCacheableAudioRequest,
  type CacheLike,
  type CacheStorageLike,
  type RequestLike,
} from "./audio-cache";

function req(url: string, method = "GET"): RequestLike {
  return { method, url };
}

/** A minimal stand-in for the SW's request: just a URL and a Range header. */
function audioRequest(url: string, range: string | null = null) {
  return {
    url,
    headers: { get: (name: string) => (name.toLowerCase() === "range" ? range : null) },
  };
}

function full200(body: string, contentType: string | null = "audio/ogg"): Response {
  const headers: Record<string, string> = {};
  if (contentType !== null) headers["Content-Type"] = contentType;
  return new Response(body, { status: 200, headers });
}

class FakeCache implements CacheLike {
  store = new Map<string, Response>();
  puts: string[] = [];
  // Real Cache.match returns a fresh (re-readable) Response each call.
  match(url: string): Promise<Response | undefined> {
    const r = this.store.get(url);
    return Promise.resolve(r ? r.clone() : undefined);
  }
  put(url: string, response: Response): Promise<void> {
    this.puts.push(url);
    this.store.set(url, response);
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

describe("computeByteRange", () => {
  it("parses a closed range", () => {
    expect(computeByteRange("bytes=2-5", 10)).toEqual({ start: 2, end: 5 });
  });
  it("parses an open range (start-)", () => {
    expect(computeByteRange("bytes=3-", 10)).toEqual({ start: 3, end: 9 });
  });
  it("parses a suffix range (-N = last N bytes)", () => {
    expect(computeByteRange("bytes=-4", 10)).toEqual({ start: 6, end: 9 });
  });
  it("clamps end to the last byte", () => {
    expect(computeByteRange("bytes=0-9999", 10)).toEqual({ start: 0, end: 9 });
  });
  it("clamps a suffix larger than the file to the whole file", () => {
    expect(computeByteRange("bytes=-9999", 10)).toEqual({ start: 0, end: 9 });
  });
  it("returns null for a malformed header", () => {
    expect(computeByteRange("items=0-1", 10)).toBeNull();
  });
  it("returns null when both ends are empty", () => {
    expect(computeByteRange("bytes=-", 10)).toBeNull();
  });
  it("returns null for a zero-length suffix", () => {
    expect(computeByteRange("bytes=-0", 10)).toBeNull();
  });
  it("returns null when start is past the end", () => {
    expect(computeByteRange("bytes=8-3", 10)).toBeNull();
  });
  it("returns null when start is at/after the total", () => {
    expect(computeByteRange("bytes=10-", 10)).toBeNull();
  });
});

describe("buildPartialResponse", () => {
  it("returns a 206 slice with Content-Range for a valid range", async () => {
    const res = await buildPartialResponse(full200("0123456789"), "bytes=2-5");
    expect(res.status).toBe(206);
    expect(res.headers.get("Content-Range")).toBe("bytes 2-5/10");
    expect(res.headers.get("Content-Length")).toBe("4");
    expect(res.headers.get("Content-Type")).toBe("audio/ogg");
    expect(await res.text()).toBe("2345");
  });
  it("returns the full 200 when the range is unsatisfiable", async () => {
    const res = await buildPartialResponse(full200("0123456789"), "bytes=50-60");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("0123456789");
  });
  it("falls back to octet-stream when the source has no content-type", async () => {
    // A binary (BufferSource) body sets no Content-Type, unlike a string body.
    const noType = new Response(new Uint8Array([48, 49, 50, 51]), { status: 200 });
    const res = await buildPartialResponse(noType, "bytes=0-1");
    expect(res.headers.get("Content-Type")).toBe("application/octet-stream");
  });
});

describe("handleAudioFetch", () => {
  it("on a cache miss, fetches the full file, caches it, and returns it (no range)", async () => {
    const caches = new FakeCaches();
    const result = await handleAudioFetch(audioRequest("https://h/api/audio/x"), {
      caches,
      fetch: () => Promise.resolve(full200("0123456789")),
    });
    expect(result.status).toBe(200);
    expect(await result.text()).toBe("0123456789");
    expect(caches.opened).toEqual([AUDIO_CACHE]);
    expect(caches.cache.puts).toEqual(["https://h/api/audio/x"]);
  });

  it("on a cache miss with a Range header, returns a 206 slice", async () => {
    const caches = new FakeCaches();
    const result = await handleAudioFetch(audioRequest("https://h/api/audio/y", "bytes=0-3"), {
      caches,
      fetch: () => Promise.resolve(full200("0123456789")),
    });
    expect(result.status).toBe(206);
    expect(await result.text()).toBe("0123");
  });

  it("serves a cache hit without fetching (range sliced from cache)", async () => {
    const caches = new FakeCaches();
    caches.cache.store.set("https://h/api/audio/z", full200("0123456789"));
    let fetched = false;
    const result = await handleAudioFetch(audioRequest("https://h/api/audio/z", "bytes=4-6"), {
      caches,
      fetch: () => {
        fetched = true;
        return Promise.resolve(full200("xxxxxxxxxx"));
      },
    });
    expect(fetched).toBe(false);
    expect(result.status).toBe(206);
    expect(await result.text()).toBe("456");
  });

  it("passes a non-200 network response through without caching", async () => {
    const caches = new FakeCaches();
    const result = await handleAudioFetch(audioRequest("https://h/api/audio/e", "bytes=0-3"), {
      caches,
      fetch: () => Promise.resolve(new Response("nope", { status: 404 })),
    });
    expect(result.status).toBe(404);
    expect(caches.cache.puts).toHaveLength(0);
  });
});
