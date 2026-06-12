# Offline / low-mobile-data audio plan

**Goal:** when listening on an Android phone connected to TunaTale, don't burn
mobile data on audio. Two independent levers:

1. **Shrink each play** (compress audio) — biggest single win, self-contained.
2. **Cache plays** (download on wifi, replay offline) — service-worker / PWA.

The phases below are ordered by value-per-effort and are **independently
shippable**. An agent can take any one phase without the others. Phase 1 alone
delivers ~95% of the data savings; Phases 2–4 add true offline playback.

> **STATUS 2026-06-12: ALL PHASES IMPLEMENTED.** Phase 1 on `main` (commit
> c7c0aa3). Phases 2–4 on branch `feat/offline-audio-cache`. To use offline mode:
> run `./start-dev.sh --prod` (builds + serves so the service worker activates),
> open the app on the phone, play a lesson on wifi → it caches and replays
> offline. Manual phone verification of install/offline replay is the one thing
> not automatable here — see "Remaining manual verification" at the bottom.

---

## Current architecture (grounded, 2026-06-12)

- **Transport:** phone reaches the Mac's Vite dev server over **Tailscale**
  (`frontend/vite.config.ts`: `server.host = true`, `allowedHosts: ['.ts.net']`;
  `start-dev.sh` detects the MagicDNS name and mints HTTPS certs covering it).
  Tailscale tunnels over cellular too, which is *why* mobile data gets spent off
  wifi — the phone streams straight from the Mac through the VPN.
- **Playback:** `frontend/src/lib/components/AudioPlayer.svelte:14` is a plain
  `<audio controls src={api.audioUrl(audio.audio_id)}>`. URL helpers:
  `frontend/src/lib/api.ts:306` (`audioUrl`) and `:310` (`audioZipUrl`).
- **Serving:** `backend/app/api/audio.py:177` `GET /api/audio/{audio_id}` →
  `FileResponse(..., media_type="audio/wav")`. **Uncompressed WAV.**
- **Rendering:** `backend/app/audio/renderer.py` assembles with
  soundfile+numpy and writes WAV via `_write_wav` at two sites:
  per-section (`:191`) and full lesson (`:205`).
- **Storage:** `backend/app/storage/store.py` `audio_files` table
  (`id, lesson_id, file_path, section_index, section_type`).
  `save_audio_file` (`:224`), `get_audio_file_row` (`:242`),
  `list_audio_files_for_lesson` (`:253`). Real rendered WAVs live under
  `backend/output/audio/` (dev) / `request.app.state.audio_dir`.
- **No PWA / service worker exists.** Frontend ships via
  `@sveltejs/adapter-auto`, run in **dev** mode on the phone-facing port (5173).
- **ffmpeg** is already a backend/CI system dependency (root `CLAUDE.md`:
  "CI requires ffmpeg as system dependency (backend job only)").
- **No Anki/sync interaction.** Audio is fully separate from the
  `collection.anki2` parity machinery — none of these phases touch sync, FSRS,
  or queue code. No parity risk.

### Spike numbers (real 19 MB lesson WAV, `ffmpeg 8.0`)

| Format        | Size  | Reduction |
|---------------|-------|-----------|
| WAV (current) | 19 MB | —         |
| **Opus 28k**  | 957 KB| **95%**   |
| AAC 48k       | 1.6 MB| 92%       |
| MP3 64k       | 3.1 MB| 84%       |

Opus is the clear winner for speech and is natively supported by Android
Chrome's `<audio>`. (If iOS support ever matters, AAC/m4a is the safe fallback —
see Phase 1 "Format decision".)

---

## Cross-cutting constraints (read before any phase)

- **TDD red-green** (`.claude/rules/tdd.md`): write the failing test first.
- **Backend coverage is 100%** (`fail_under = 100`). Every new branch needs a test.
- **Frontend coverage is 100% per-file** via the phantom-filter gate
  (`.claude/rules/testing.md` "Frontend Coverage Gate"). Service-worker code is
  notoriously hard to cover — factor logic into plain testable TS modules and
  keep the SW shell as a thin, exercised wrapper. Budget time for this.
- **Mock boundaries** (`.claude/rules/testing.md`): **do not mock ffmpeg.** It's
  a real CI dependency — transcode a tiny real WAV fixture in tests instead.
  Mocking the subprocess would require a `mock_allowlist.txt` addition (needs
  user approval) *and* would test against a fake. Run it for real.
- **`./test.sh` must pass before every commit.** No exceptions.
- Each phase's "Done" includes the `./test.sh` tail + (after push) the green CI
  run URL, per root `CLAUDE.md` "Delivering".

---

## Phase 1 — Serve compressed audio (Opus) instead of WAV ✅ DONE (2026-06-12)

**Status: SHIPPED.** Default delivery codec is now `opus` @ 28 kbps. Implemented
exactly as "option (a)" below: render straight to compressed, no WAV master.
Files: `backend/app/audio/transcode.py` (new — `encode_audio` + codec maps),
`renderer.py` (`delivery_codec`/`delivery_bitrate` ctor args, `_write_audio`),
`config.py` (`audio_delivery_codec`/`audio_delivery_bitrate`), `main.py` (wires
settings → renderer), `api/audio.py` (render uses `CODEC_EXT`; serving + zip
derive media-type/filename from the actual file suffix so old `.wav` files still
serve). Tests: `test_audio_transcode.py`, renderer Opus tests, API media-type
tests. `./test.sh` green; backend 100% coverage. ffmpeg run for real in tests
(no mock). Old WAV files on disk keep working (served `audio/wav` by suffix).

**Value:** ~95% mobile-data reduction on every play, *with or without caching.*
Self-contained; no frontend transport changes. **Do this first regardless.**

### Format decision
Default to **Opus in Ogg** (`-c:a libopus -b:a 28k`, `media_type="audio/ogg"`).
Make the codec/bitrate a `Settings` field (`backend/app/config.py`) so it's not
hardcoded — e.g. `audio_delivery_codec: Literal["opus","aac","mp3","wav"] =
"opus"`, `audio_delivery_bitrate: str = "28k"`. `wav` preserves today's behavior
for an escape hatch. (No module-level side effects — config via Pydantic
Settings, per root `CLAUDE.md`.)

### Design
Add a transcode step after WAV assembly. Two viable shapes — **prefer (a):**

- **(a) Render straight to compressed, drop the WAV.** In
  `renderer.py`, after building `combined`/section audio, pipe the buffer through
  ffmpeg (stdin WAV → stdout Opus) and write `{audio_id}.opus`. Store the
  compressed path in `audio_files.file_path`. The zip endpoint
  (`audio.py` lesson zip) bundles the compressed files too. Smallest storage,
  simplest serving (the served file just *is* the stored file).
- **(b) Keep WAV as master, transcode a delivery copy.** Adds a `delivery_path`
  column to `audio_files` and a regen path. More flexible (re-encode without
  re-TTS) but more moving parts + a schema migration. Only choose if you
  foresee multiple delivery formats.

Recommend **(a)** unless there's a reason to keep WAV masters. WAV is
reproducible from TTS anyway.

### Implementation sketch (option a)
1. New helper `backend/app/audio/transcode.py`:
   `transcode_wav_bytes(wav_bytes: bytes, codec: str, bitrate: str) -> bytes`
   — `subprocess.run(["ffmpeg","-i","pipe:0","-c:a",...,"-b:a",...,"-f","ogg","pipe:1"], ...)`.
   Pure process boundary; tested with a real tiny WAV.
2. `renderer.py`: replace the two `_write_wav` calls with a write-compressed path
   (gate on `settings.audio_delivery_codec == "wav"` to keep the WAV branch).
   Filenames become `{id}.{ext}` where ext maps from codec.
3. `audio.py` `get_audio`: serve with the correct `media_type`
   (`audio/ogg` / `audio/mp4` / `audio/mpeg` / `audio/wav`). The download
   filename builder (`_build_section_filename`, `:203`) needs the new extension.
4. `audio.py` render endpoint (`:41`): the section/full paths it constructs
   (`{id}.wav` at `:56`,`:59`) must use the new extension.
5. Storage: no schema change for option (a) — `file_path` just points at the
   compressed file.

### Tests (TDD)
- `backend/tests/test_audio_transcode.py` — transcode a checked-in tiny WAV
  fixture, assert output is non-empty and ffprobe/soundfile reads it back as the
  expected codec; assert it's materially smaller than the input.
- Extend renderer tests: assert the written file has the configured extension and
  decodes; assert `codec="wav"` still produces WAV (escape hatch).
- `tests/test_api.py` audio-serving test: assert `Content-Type` matches codec and
  the body decodes.
- Frontend: `AudioPlayer` is codec-agnostic (`<audio>` sniffs) — likely no change,
  but add/adjust a test asserting the `src` still resolves.

### Acceptance
- `GET /api/audio/{id}` returns Opus; a real lesson drops from ~19 MB to ~1 MB.
- `./test.sh` green (incl. 100% backend coverage — note the new `wav` escape-hatch
  branch needs a test).
- Manual: play a lesson on the phone, confirm it still plays in Android Chrome.

**Effort:** ~0.5–1 day.

---

## Phase 2 — Serve a production frontend build to the phone (prereq for SW) ✅ DONE

Implemented: `vite.config.ts` extracts shared host/HTTPS/proxy/allowedHosts into
`serverOptions`, applied to both `server` and `preview` (preview had no wiring
before, so a Tailscale phone couldn't reach it). `start-dev.sh --prod` runs
`vite build` then `vite preview --port 5173`. Verified preview serves `/`,
`/service-worker.js`, `/manifest.webmanifest` (all 200). Kept `adapter-auto`;
preview's SvelteKit SSR works without a forced adapter swap.

**Why:** service workers + Vite **dev** (HMR) fight each other; SW caching is
only reliable against `vite build` / `vite preview` (or adapter output). Phase 3
needs this. Phase 1 does **not** — skip Phase 2 if you only want Phase 1.

### Design
Add a "served build" mode to `start-dev.sh` (or a sibling `start-phone.sh`):
- `cd frontend && bun run build` then `bun run preview --host --port 5173`
  (preview honors `host`/HTTPS the same way dev does — verify the SSL wiring in
  `vite.config.ts` applies to `preview`; if not, add a `preview` block mirroring
  the `server` one).
- Keep the existing dev path for laptop development; the phone path serves the
  build. Decide whether this is a flag (`--build`) or a separate script.
- `@sveltejs/adapter-auto` → likely pin `@sveltejs/adapter-node` or
  `adapter-static` for a predictable preview/serve target. Static is simplest if
  the app has no server routes that must run on the device side; confirm SSR
  needs first (`api.ts:11` has an SSR branch — check what actually SSRs).

### Tests / acceptance
- This is mostly ops/scripting. Validate by: build succeeds, `preview` serves over
  HTTPS on the Tailscale host, phone loads the app and plays audio.
- No new unit tests unless you add TS logic; keep `./test.sh` green.

**Effort:** ~0.5 day (more if adapter swap surfaces SSR assumptions).

---

## Phase 3 — PWA + service worker: cache played audio for offline replay ✅ DONE

Implemented: `src/lib/sw/audio-cache.ts` (pure cache-first logic, 100% tested),
thin `src/service-worker.ts` shell (auto-registered by SvelteKit; precaches app
shell, cleans stale caches, delegates audio fetches), `static/manifest.webmanifest`
+ `icon.svg` + `app.html` link for installability. Coverage strategy from the plan
held exactly: logic in `src/lib/**` is gated to 100%; the SW shell sits outside the
gate's include globs. `vite build` emits `service-worker.js` precaching the shell.

**Value:** "played it once on wifi → free forever after." No per-lesson button,
no fragile wifi detection. Depends on Phase 2.

### Design
- Add a service worker (SvelteKit `src/service-worker.ts`, or
  `@vite-pwa/sveltekit` for manifest + registration ergonomics + Workbox).
- **Cache strategy for `/api/audio/*`: cache-first** (it's immutable content keyed
  by UUID). On fetch: serve from Cache Storage if present, else network then
  populate cache. This means the *first* play (ideally on wifi) costs data; every
  replay is free and works offline.
- **App shell:** precache the built JS/CSS/HTML so the UI loads offline too.
- **Manifest** (`manifest.webmanifest`) + icons so Android offers "Add to Home
  Screen" (installable PWA, standalone display).
- **Cache eviction:** Opus lessons are ~1 MB each (post-Phase-1), so quota is a
  non-issue for a long time — but add a simple LRU/size cap helper anyway
  (testable pure function) and a "clear cached audio" action in the UI.

### Coverage strategy (important — read `.claude/rules/testing.md`)
The 100% per-file frontend gate is the hard part of this phase. Pattern:
- Put all decision logic (cache key derivation, should-cache predicate, LRU
  eviction math) in **plain `.ts` modules** with full Vitest coverage.
- Keep `service-worker.ts` a thin shell that wires events to those modules; cover
  the shell with targeted SW-event tests (mock `caches`/`fetch` via the standard
  testing-library/jsdom + a `Cache` polyfill or fake).
- Do **not** reach for `/* c8 ignore */` (gate ignores it). If a branch feels
  uncoverable, refactor it into a tested module.

### Tests (TDD)
- Unit: cache-key derivation, cache-first predicate, LRU eviction — pure TS.
- SW behavior: first fetch populates cache; second fetch served from cache
  without network; non-audio requests pass through.
- E2E (Playwright, local-only via `./test.sh`): load a lesson online, go offline
  (Playwright `context.setOffline(true)`), assert audio still plays / is served
  from cache.

### Acceptance
- Install PWA on phone. Play a lesson on wifi. Switch to mobile data (or airplane
  mode). Replay the same lesson — **zero** data, plays from cache.
- `./test.sh` green incl. frontend 100% gate + Playwright offline test.

**Effort:** ~2–3 days (the coverage gate is the long pole).

---

## Phase 4 — (Optional) Automatic wifi-only prefetch ✅ DONE

Implemented: `src/lib/sw/prefetch.ts` (`shouldPrefetchOnConnection`,
`prefetchAudioUrls`, `maybePrefetchLesson` — all gating in the lib, 100% tested);
`AudioPlayer.svelte` `onMount` fires `maybePrefetchLesson` with real
`navigator.connection` / `globalThis.caches` / `fetch`, prefetching the lesson's
full + section audio on wifi. No-op where the (Chrome-Android-only) Network
Information API or Cache Storage is absent — on-demand cache-first still applies.
**Not yet done:** a user-facing "auto-download on wifi" toggle (the plan suggested
one; currently always-on when wifi is detected). Add a setting if you want opt-out.

**Value:** lessons are cached *before* first play, so even the first listen off
wifi is free. Only build this if Phase 3 leaves you wanting pre-caching.

### Design
- On app load / lesson-list view, if on wifi, prefetch upcoming lessons' audio
  into the same cache the SW uses.
- **Wifi detection caveat:** the Network Information API
  (`navigator.connection.type === 'wifi'`, `navigator.connection.saveData`) is
  **Chrome-on-Android only** and non-standard. It works for the target device but
  don't rely on it cross-platform. Gate prefetch behind a feature-detect; degrade
  to "no auto-prefetch" (Phase 3's on-demand caching still applies) when the API
  is absent. Respect `saveData === true` (user asked to conserve) by never
  prefetching.
- Add a user setting: "Auto-download lessons on wifi" (default on), so it's never
  a surprise data event.

### Tests
- Pure: the prefetch-eligibility predicate (`onWifi && !saveData && setting on`)
  with the API present/absent/`saveData` permutations.
- SW/cache: prefetch populates the same cache Phase 3 reads.

**Effort:** ~1 day on top of Phase 3.

---

## Recommended path

- **Minimum useful:** Phase 1 only. ~95% data cut, half a day, no transport
  changes. If mobile data is the whole concern, this may be enough.
- **Full offline:** Phase 1 → 2 → 3. Phase 4 only if pre-play caching is wanted.

## Handoff notes for agents

- Start every phase by re-reading the "Current architecture" anchors above —
  line numbers may drift; re-grep `audioUrl`, `media_type=`, `_write_wav`,
  `save_audio_file` to re-locate.
- Phase 1 is the only one that touches backend; Phases 2–4 are frontend/ops.
  They can proceed in parallel *after* Phase 1 fixes the served format (Phase 3's
  cache should store the compressed bytes, not WAV).
- None of this touches Anki sync / FSRS / queue parity. Do not let it.
- Per root `CLAUDE.md` "Delivering": paste the `./test.sh` tail and the green CI
  URL into each phase's completion report.

## Remaining manual verification (not automatable here)

The automated gates (unit + coverage + build + preview-serves-200) all pass, but
driving a real Android phone is out of scope for the test suite. Confirm on the
phone once:

1. `./start-dev.sh --prod`, open `https://<your-host>.ts.net:5173` on the phone.
2. DevTools / chrome://inspect → Application → Service Workers shows it active,
   and Manifest shows TunaTale installable ("Add to Home Screen").
3. Play a lesson on wifi. Then enable airplane mode (or switch off wifi with the
   Mac unreachable) and replay — it should play from cache with no network.
4. Optional: confirm the wifi prefetch populated `Cache Storage → tt-audio-v1`
   for sections you hadn't played yet.

If install/offline don't behave, the usual culprits: the mkcert CA isn't trusted
on the phone (service workers require a valid secure context — see start-dev.sh's
Android CA-trust hint), or you're on `vite dev` not `--prod` (SW only registers
against the build).
