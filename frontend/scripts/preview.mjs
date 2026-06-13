// Robust `vite preview` for the on-device testing server (start-dev.sh --prod).
//
// vite preview's static middleware (sirv) emits an *unhandled* ReadStream
// 'error' (ENOENT) when a client requests a content-hashed asset that the
// current build no longer contains — typically a stale service worker whose
// precache list points at a previous build's hashes. That unhandled error
// crashes the whole preview process (see start.<hash>.js ENOENT). On a real
// host the old hashed files would still be served and the SW would update
// cleanly; in this local rebuild-often loop they vanish each build.
//
// We run preview programmatically so we can install a narrowly-scoped guard:
// swallow ENOENT (the missing asset just fails for that one stale request) and
// re-throw everything else. Keeping the server alive lets the stale client
// reload and pick up the new service worker instead of taking the server down.
//
// This launcher loads vite.config.ts (host/HTTPS/proxy/allowedHosts via the
// shared `serverOptions`), so it behaves like `vite preview` otherwise.

import { preview } from "vite";

process.on("uncaughtException", (err) => {
  if (err && (err.code === "ENOENT" || /ENOENT/.test(String(err.message)))) {
    console.warn(`[preview] ignored missing file (stale client?): ${err.path ?? err.message}`);
    return;
  }
  throw err;
});

const portFlag = process.argv.indexOf("--port");
const port = portFlag !== -1 ? Number(process.argv[portFlag + 1]) : 5173;

const server = await preview({ preview: { port } });
server.printUrls();
