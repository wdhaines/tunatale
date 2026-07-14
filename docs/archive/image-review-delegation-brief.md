# Delegation brief — image-feature review fixes (#2, #4, #5)

Three fixes from the adversarial review of the image-selection feature (see
`docs/image-feature-handoff.md` for feature context). Scoped for a Sonnet-class
delegate. **The orchestrator re-runs `./test.sh` and sabotage-drills every
guardrail test before trusting any of this** — a green you report is not
evidence; a test that can't be shown to fail on the bug it guards is decoration.

## Binding discipline (all three)

- **Strict TDD**: write the failing test FIRST, run it, confirm it's RED for the
  right reason, then fix. Paste the red output in your report.
- **No new `# pragma: no cover`** (backend) and **no `/* c8 ignore */`**
  (frontend). If a line seems uncoverable, write the test or delete the branch.
- **`./test.sh` must pass** (lint + mock-boundary + 100% backend coverage +
  svelte-check + vitest per-file gate + Playwright). Paste the tail.
- **No `patch("app.…")`** internal mocks — the mock-boundary checker will fail
  you. Test through the real code path.
- Each fix is a separate commit; they can share one branch.

---

## #5 — `put_image_from_url` should fail clearly on non-image responses (easiest)

**File**: `backend/app/api/srs_images.py`, `put_image_from_url` (~L143-149).

**Bug**: the fetch never checks HTTP status. A URL that returns a 404/500 error
page, or a 3xx redirect (redirects are disabled for SSRF, so a redirected image
returns the 3xx body), falls through to the magic-byte sniff and surfaces the
misleading `422 "Content is not an image"` instead of "couldn't fetch it."

**Fix**:
1. After `resp = await client.get(body.url)`, add `resp.raise_for_status()`
   wrapped so `httpx.HTTPStatusError` maps to `HTTPException(422, "Could not
   fetch image (HTTP {status})")`.
2. Explicitly handle a redirect: if `resp.is_redirect` (or `300 <= status <
   400`), raise `HTTPException(422, "URL redirected; provide a direct image
   link")`. (raise_for_status does NOT raise on 3xx.)

**Tests** (`tests/test_srs_image_endpoints.py`): use `respx` (already the HTTP
mock boundary for this module — check the existing tests) to stub the outbound
GET returning (a) a 404, (b) a 302 with a `Location` header. Assert 422 with the
new distinct messages. Keep the existing "valid image" and "non-image 200 body"
tests green — the sniff path still owns the 200-but-not-an-image case.

**Guardrail**: don't collapse all failures into one generic message — the point
is that "server returned an error" and "you gave me an image" are now
distinguishable. One test per branch.

---

## #2 — remove dead branches in the modal, handle rate-limit for real

**File**: `frontend/src/lib/components/ImageEditModal.svelte`, `loadCandidates`
(~L31-55).

**Bug**: two branches can never fire in production (classic coverage-padding —
the exact Big-Pickle smell the handoff flagged):
- `resp.status === 'no_key'` (L38): the candidates endpoint **raises HTTP 409**
  for the no-key case (`app/api/srs_images.py:86-87`), so it arrives in the
  `catch` as `msg.includes('409')`, never as a 200 body with `status:'no_key'`.
  The backend's success `status` is one of `ok`/`no_results`/`rate_limited`/
  `api_error` (`app/anki/media/pixabay.py:439-448`) — never `no_key`.
- `msg.includes('429')` (L47): `search_pixabay` swallows a 429 and returns a
  **200** with `status:'rate_limited'` (`pixabay.py:441-443`), so no 429 ever
  reaches the catch. The modal currently renders that as "No results."

**Fix**:
1. Delete the `resp.status === 'no_key'` branch (the 409 catch path already sets
   `noApiKey`).
2. Delete the `msg.includes('429')` catch branch.
3. Add a **real** success-path handler: after the response, if
   `resp.status === 'rate_limited'` → set `candidateError = 'Rate limited — try
   again shortly'` and return; if `resp.status === 'api_error'` → set a
   "Pixabay unavailable" error. Only then assign `candidates = resp.candidates`.

**Tests** (`ImageEditModal.svelte.test.ts`): mock `api.fetchImageCandidates` to
resolve with `{status:'rate_limited', candidates:[]}` and assert the rate-limit
message renders (NOT "No results"); same for `api_error`. Keep the 409-rejection
test (no-key → hidden section). **Delete the tests that asserted the now-removed
branches** — do not keep them limping via a reworded mock.

**Guardrail**: the per-file frontend coverage gate is 100%. You're *removing*
branches, so coverage should get easier, not harder — if you find yourself
wanting a `/* c8 ignore */` or a test that only exists to touch a line, you've
left dead code in. The gate is `frontend/scripts/coverage-gate.ts`; don't touch
it.

> Not in scope but do it if trivial: the mount `$effect` auto-search bug (#1) is
> being handled by the orchestrator — **leave `loadCandidates`'s call sites and
> the `$effect` alone**, only edit the branch logic inside the function body.

---

## #4 — orphaned image files leak on disk (RISKIEST — read the gotcha)

**Files**: `backend/app/anki/media/vocab_media.py` (`replace_item_image`,
L68-77), `backend/app/api/srs_images.py` (`delete_image`, L182-193),
`backend/app/srs/db_media.py`.

**Bug**: replacing or removing an image deletes the **media row** only; the file
in `backend/media/` is never unlinked, so it accumulates forever.

**The gotcha that makes this NOT a one-liner**: image files are **shared across
collocations** — `img_yes.jpg` can back both `ja` and `da` (see the docstring on
`db_media.find_media_by_anki_filename`). You must **not** unlink a file that
another media row (any collocation, any kind) still references, or you blank a
live card's image.

**Fix**:
1. Add a db helper `is_media_filename_referenced(self, filename: str) -> bool`
   to `db_media.py` (`SELECT 1 FROM media WHERE filename = ? LIMIT 1`).
2. **Bump the mixin-composition pin**: `tests/test_database_mixin_composition.py`
   expects a fixed method count — increment it by 1 (the checker error message
   tells you the exact number). Do NOT skip this; a stale pin fails CI.
3. In both the replace path and the delete path: capture the image filenames on
   the collocation **before** deleting the rows, delete the rows, then for each
   captured filename that is (a) not the new file being written and (b) not
   `is_media_filename_referenced`, `unlink()` it from the media dir (guard with
   `.exists()`; catch `OSError` and log — a missing/locked file must not 500 the
   request). Put this in one shared helper, not copy-pasted.

**Tests** (`tests/test_srs_image_endpoints.py` + a `db_media` unit test):
- **The load-bearing one**: two collocations sharing one image filename; remove
  the image from one; assert the file **still exists** on disk and the other
  collocation's `image_url` still resolves. This is the test that proves you
  handled the shared-file case — write it first and watch a naive
  "always unlink" implementation fail it.
- Replace an image → old (unshared) file is gone, new file present.
- Delete an image → unshared file gone.
- Unlink of a missing file is swallowed (no 500).

**Guardrail / honesty note for the orchestrator**: this one grew past
"mechanical" — it needs a new db method, a pin bump, and the shared-file
reference check. If the reference-count logic feels shaky, **stop and hand it
back** rather than shipping an "always unlink" that passes a floor-shadow test
(seeded so the file happens to survive for an unrelated reason). The orchestrator
will sabotage-drill the shared-file test: delete the reference check, expect RED.

---

## Report format

Per fix: red-test output → the diff → `./test.sh` tail. For #4, explicitly show
the shared-file test failing against a naive unlink first. Flag anything you were
unsure about — an honest "I couldn't make the shared-file test fail first" is far
more useful than a green you can't explain.
