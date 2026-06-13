import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect } from "@playwright/test";

/**
 * Offline-audio service worker e2e (offline-audio Phases 3/4).
 *
 * Proves the real-browser mechanics the unit tests (faked Cache/Response) can't:
 * the SW registers, caches a real `/api/audio/{id}` response on first fetch, and
 * serves it from cache with the network offline — including a Range request,
 * which must come back as a 206 (the bug that showed 0:00 / stall-at-14s).
 *
 * We seed a real opus file + audio row into the e2e backend's DB (no rendered
 * lesson / EdgeTTS needed). Crucially we do NOT use Playwright route mocking:
 * it intercepts the page request before the service worker, so it can't tell
 * cache-served from network-served. Instead `context.setOffline(true)` makes the
 * discriminator unambiguous — a cached fetch resolves, an uncached one throws.
 *
 * e2e is local-only (not CI); needs ffmpeg + uv (already dev deps).
 */

const BACKEND_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../../backend");
const CACHED_ID = "e2e-fixture-id";
const CACHED_PATH = `/api/audio/${CACHED_ID}`;
const UNCACHED_PATH = "/api/audio/e2e-never-fetched-id";

// Seed a 3s silent opus + its audio_files row into the e2e backend (tunatale-test.db).
const SEED_PY = `
import os, sqlite3, subprocess, pathlib
fixture = pathlib.Path("output/audio/e2e-fixture.opus").resolve()
fixture.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(
    ["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","3",
     "-c:a","libopus","-b:a","28k","-f","ogg",str(fixture)],
    check=True, capture_output=True,
)
con = sqlite3.connect("tunatale-test.db")
con.execute(
    "INSERT OR REPLACE INTO audio_files (id, lesson_id, file_path, section_index, section_type)"
    " VALUES (?,?,?,?,?)",
    ("${CACHED_ID}", "e2e-lesson", str(fixture), None, None),
)
con.commit(); con.close()
print(fixture.stat().st_size)
`;

let fixtureSize = 0;

test.beforeAll(() => {
  const out = execFileSync("uv", ["run", "python", "-c", SEED_PY], {
    cwd: BACKEND_DIR,
    encoding: "utf8",
  });
  fixtureSize = Number(out.trim().split("\n").pop());
  expect(fixtureSize).toBeGreaterThan(0);
});

test("service worker caches audio and serves it offline (incl. range → 206)", async ({
  context,
  page,
}) => {
  await page.goto("/");

  // Wait until the SW is active AND controlling this page (clients.claim()).
  await page.waitForFunction(
    () => "serviceWorker" in navigator && navigator.serviceWorker.controller !== null,
    null,
    { timeout: 15_000 },
  );

  // First play (online): SW miss → real backend → cached.
  const first = await page.evaluate(async (url) => {
    const r = await fetch(url);
    return { status: r.status, len: (await r.arrayBuffer()).byteLength };
  }, CACHED_PATH);
  expect(first.status).toBe(200);
  expect(first.len).toBe(fixtureSize);

  await context.setOffline(true);

  // Sanity: offline really is offline — an uncached audio URL must fail.
  const uncached = await page.evaluate(async (url) => {
    try {
      const r = await fetch(url);
      return { ok: true, status: r.status };
    } catch {
      return { ok: false, status: 0 };
    }
  }, UNCACHED_PATH);
  expect(uncached.ok).toBe(false);

  // Replay from cache while offline: plain fetch served from the SW cache.
  const replay = await page.evaluate(async (url) => {
    const r = await fetch(url);
    return { status: r.status, len: (await r.arrayBuffer()).byteLength };
  }, CACHED_PATH);
  expect(replay.status).toBe(200);
  expect(replay.len).toBe(fixtureSize);

  // Range request offline → synthesized 206 slice from the cached full body.
  const ranged = await page.evaluate(async (url) => {
    const r = await fetch(url, { headers: { Range: "bytes=0-9" } });
    return { status: r.status, contentRange: r.headers.get("content-range"), len: (await r.arrayBuffer()).byteLength };
  }, CACHED_PATH);
  expect(ranged.status).toBe(206);
  expect(ranged.len).toBe(10);
  expect(ranged.contentRange).toBe(`bytes 0-9/${fixtureSize}`);
});
