# Image-selection + in-app image editor — handoff

> **ARCHIVED 2026-07-14 — review complete.** The adversarial review below was
> carried out; all findings are fixed or consciously accepted. Fixes: #1
> per-keystroke Pixabay auto-search (`untrack` the mount effect), #2 dead
> modal branches + real rate-limit/api_error handling, #3 streamed download
> cap (SSRF host-block prototyped then reverted — local-server paste is a
> supported workflow), #4 orphaned-image-file cleanup with a shared-file
> reference guard, #5 HTTP status/redirect checks. #6 was a doc-accuracy nit
> (the idempotency bullet, corrected below). Delegation notes:
> `docs/archive/image-review-delegation-brief.md`.

Context for a fresh session doing **bug fixes** or **adversarial review** of the
image-selection feature. A new chat opened in this repo auto-loads `CLAUDE.md` and
`.claude/rules/*`, so this doc front-loads the feature-specific context those files
don't cover — the commits, the mechanism, the seams, and the risks flagged at review.

> Binding house rules (read before touching code): `CLAUDE.md` and
> `.claude/rules/{testing,tdd,anki-sync,anki-queue-parity}.md` — 100% backend
> coverage, frontend 100%-per-file phantom-filter gate, enforced mock boundaries,
> no language literals in `app/**`, Anki USN rules.

## What shipped

7 commits, `17157bc..59e5f43`, all CI-green:

| Commit | Summary |
|---|---|
| `0f8c4a0` | pixabay search/download split + LLM chooser + retry/broaden + failure surfacing |
| `17157bc` | `sync_push` learns an `"image"` dirty-field → writes the Anki note's Image field |
| `a953a4e` | manual image API: `GET candidates` / `PUT url` / `PUT upload` / `DELETE` |
| `9a8e9f3` | batched `get_image_filenames` + `image_url` in `/items` list |
| `49f7224` | frontend: `ImageEditModal.svelte` + `/cards` thumbnails + "Change image…" menu |
| `9a31a58` | Playwright `card-image.spec.ts` (upload / remove / paste-URL) |
| `59e5f43` | sociable seam test (endpoint → `sync_push` → Anki note) |

## Mechanism (hold this in your head)

A TT-side image edit is **not** written to Anki directly:

1. The endpoint stores the image in TT's media
   (`vocab_media.replace_item_image` → `store_tt_media`) and stamps
   `collocations.dirty_fields += "image"` (`db_sync.add_dirty_field_by_id`).
2. On the next sync, `sync_engine.py:1111` reads that flag, writes the Anki note's
   Image field to `<img src="FNAME">`, and copies the bytes into `collection.media`.
3. That push **must** run before the media-refresh collapse in `run_full_sync`, or
   the refresh reverts the swap (it collapses any TT media row the Anki note doesn't
   reference).

## Key files

**Backend**
- `app/api/srs_images.py` — the 4 endpoints
- `app/anki/media/{pixabay,choose_llm,pipeline,vocab_media}.py`
- `app/anki/sync_engine.py:1111` — the image push branch
- `app/srs/db_sync.py` — `add_dirty_field_by_id`
- `app/srs/db_media.py` — `get_image_filename` / `get_image_filenames` / `delete_all_media_for_kind`
- `app/api/srs.py` — `list_items` `image_url`; the `/api/srs/media/{filename}` route (~line 345)

**Frontend**
- `src/lib/api.ts` — 4 methods + `ImageCandidate`/`ImageCandidatesResponse` types
- `src/lib/components/ImageEditModal.svelte`
- `src/routes/cards/+page.svelte` — thumbnail column + row menu

**Tests**
- `tests/test_srs_image_endpoints.py`
- `tests/test_anki_media_{pixabay,choose_llm,pipeline}.py`
- `tests/test_anki_sync_push.py::TestSyncPushImage`
- `tests/test_e2e_listen_to_sync.py::TestImageEndpointToSyncSeam`
- `frontend/.../ImageEditModal.svelte.test.ts`, `cards/page.test.ts`, `tests/card-image.spec.ts`

## Known soft spots / deliberate trade-offs — start adversarial review here

- **SSRF / memory in `put_image_from_url`** (`srs_images.py`): the review's
  unbounded-buffer half is **fixed** — the body is now streamed with a hard cap
  (`client.stream` + `aiter_bytes`), so an oversized URL can't buffer unbounded before
  the 10 MB check. The internal-IP-reachability half is **consciously accepted, not
  blocked**: a host-block was prototyped and reverted because pasting an image URL from
  your own loopback/LAN server is a supported workflow (`tests/card-image.spec.ts` serves
  the fixture from `127.0.0.1`), and TunaTale is single-user localhost, so the SSRF value
  is marginal while the feature loss is real. `follow_redirects=False` still blocks
  redirect pivots.
- **Type validation is magic-bytes only** (`_sniff_ext`: jpg/png/webp/gif), never
  Content-Type / filename / URL. SVG intentionally unsupported. Probe spoofing and
  the RIFF-but-not-WEBP path.
- **Filename collision surface**: `replace_item_image` uses a `sha256(data)[:8]`
  (32-bit) suffix; `delete_all_media_for_kind` runs first so normally one image row
  exists. `get_image_filenames` resolves "most recent wins" (`ORDER BY id DESC`) for
  the multi-row case.
- **Cloze**: endpoints `409`; `sync_engine` drops a stray `"image"` flag for cloze so
  it can't pin `dirty_fields` forever. Check both halves agree.
- **Unlinked collocations** (no `anki_note_id`) edited via endpoint: `sync_create_new`
  is supposed to attach the current TT image at mint and clear the flag the same sync.
  Verify that path is actually covered.
- **Idempotency**: re-uploading identical bytes → same filename, so the media-copy
  layer reports `unchanged_media` (no byte re-copy into `collection.media`). Note the
  *field* push is not idempotent: `replace_item_image` stamps the `"image"` dirty flag
  unconditionally, so the next `sync_push` re-writes the Anki note's Image field even
  when the bytes are unchanged — redundant, but harmless (same `<img src>`).

## Provenance — be skeptical of the tests, not just the code

The feature was implemented by a free Sonnet-class delegate ("Big Pickle"). Its
documented failure modes: fabricated gate/commit claims, coverage-chasing tests
(assertions that exist only to cover a branch that should be deleted), and tests that
assert "in a floor's shadow" (seeded state where fixed and regressed code both pass).
**Re-run the gates yourself; sabotage-drill any guardrail test before trusting it.**
Full notes: the `feedback-big-pickle-workflow` memory.

## How to verify

- Full gate: `./test.sh` (from repo root).
- Backend only: `cd backend && uv run pytest` (add `--run-oracle` for parity).
- Never trust a pasted "green" — run it.

## Task framing

- **Bug fix** — state the symptom + repro. Keep the "re-run gates / sabotage-drill"
  discipline; it matters for a fix too.
- **Adversarial review** — hunt correctness bugs and unsafe trust boundaries across
  the files above; report findings ranked by severity with a concrete failure
  scenario each; don't fix in the same pass unless asked. Consider `/code-review high`
  (or `/code-review ultra` for the deep multi-agent cloud pass) — this doc gives the
  reviewer the trust-boundary and provenance context those skills otherwise lack.
