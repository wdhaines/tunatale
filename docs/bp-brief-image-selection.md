# BP brief — automatic image selection: search/choose split, LLM chooser, retry, failure surfacing

Four commits improving how new vocab cards get their Pixabay image. Work TDD
red→green, commits in order (1 → 2 → 3 → 4); each commit has its own red tests
and a green **full-suite** checkpoint (`./test.sh` from repo root) before the
next.

Read first: `backend/app/anki/media/pixabay.py`, `query_llm.py`, `pipeline.py`,
`vocab_media.py`, `backend/app/api/anki.py:13-41`, and the media-consumption
block of `backend/app/anki/sync_engine.py:1356-1400`.

**Interface decisions below are pinned, not suggestions.** If an existing test
or caller makes a pinned decision impossible, STOP and report the conflict —
do not redesign the interface to route around it.

## Scope (hard limits)

Files you may touch:
- `backend/app/anki/media/pixabay.py`, `pipeline.py`, `vocab_media.py`
- NEW `backend/app/anki/media/choose_llm.py`
- `backend/app/api/anki.py` (only `_build_media_fn`)
- `backend/app/anki/sync_engine.py` — **only** the media block inside
  `sync_create_new` (lines ~1370-1400) and only additively (see commit 4)
- `backend/app/anki/sync_common.py` (only the `CreateNewReport` dataclass)
- `backend/app/anki/sync.py` (only the `media_report` dict merge, see commit 4)
- Test files for the above (`backend/tests/`)

Explicitly OUT of scope (a later, separately-supervised change): anything
touching `sync_push`, `sync_pull`, `dirty_fields`, `OfflineWriter`,
`safety.py`, `import_seed.py`, migrations, any API endpoint, any frontend
file. Do not add dependencies. Do not edit `mock_allowlist.txt`,
`mock_grandfather.txt`, `language_literals_*.txt`, `pyproject.toml`, or
`test.sh`.

## Commit 1 — split `pixabay.py` into search / download with status surfacing

**Problem.** `fetch_pixabay_image` (pixabay.py:436-488) does search + rank +
download in one function with a blanket `except Exception: return None`
(line 484) — a rate-limit, an API error, and "no results" are all
indistinguishable from success-with-no-image.

**Change** (in `backend/app/anki/media/pixabay.py`):

1. Add:
   ```python
   @dataclass
   class PixabaySearch:
       hits: list[dict]
       status: str  # exactly one of: "ok" | "no_results" | "rate_limited" | "api_error"

   def search_pixabay(query: str, *, api_key: str, http_client: httpx.Client | None = None,
                      per_page: int = 50) -> PixabaySearch
   def download_hit(hit: dict, *, http_client: httpx.Client | None = None) -> tuple[bytes, str, str] | None
   ```
2. `search_pixabay` is the front half of `fetch_pixabay_image` (same params:
   `image_type=photo, safesearch=true, min_width=300`, timeout 10). Status
   mapping: HTTP 429 → `"rate_limited"`; any other `httpx.HTTPStatusError` or
   network/transport exception → `"api_error"` with a `logger.warning` naming
   the query and the error; JSON with empty `hits` → `"no_results"`; otherwise
   `"ok"`. **Only the two HTTP calls get try/except** — a programming error
   must raise, not be swallowed.
3. `download_hit` is the back half (webformatURL GET, timeout 15, the existing
   ext-sniff comment/logic at lines 479-482). Network failure →
   `logger.warning` + `None`.
4. Rewrite `fetch_pixabay_image` as a thin wrapper:
   `search_pixabay → filter used_urls → best_hit → download_hit`, preserving
   its exact current signature and return type (`(bytes, ext, url) | None`) —
   existing callers and tests must keep passing unmodified except where they
   asserted on the removed blanket-exception behavior.
5. `score_hit` / `best_hit` / `build_query` / `QUERY_MAP` unchanged.

**Tests** (extend `backend/tests/test_anki_media_pixabay.py`, existing style —
fake client objects injected via `http_client`, no patching): 429 →
`rate_limited`; 500 → `api_error` + warning logged (`caplog`); network
exception on search → `api_error`; empty hits → `no_results`; happy path →
`ok` with hits; `download_hit` failure → None + warning; wrapper still returns
the same tuple as before on the happy path.

## Commit 2 — LLM candidate chooser: new `backend/app/anki/media/choose_llm.py`

**Problem.** The final pick is `max(score_hit)` — blind tag-overlap. When tags
don't literally contain a query token, the pick is a near-random popular photo.

**Change.** New module mirroring `query_llm.py`'s structure exactly (module
docstring stating the resilience contract, `_LLM` Protocol, `MODEL_VERSION`-style
constant not needed here — no cache), with:

1. `IMAGE_CHOICE_SYSTEM_PROMPT`: the assistant is shown a flashcard word, its
   English meaning, the search query used, and a numbered list of stock-photo
   candidates described by their tags; it must reply with ONLY the number of
   the candidate whose photo best depicts the meaning, or `0` if none fit.
2. `build_image_choice_prompt(word: str, english: str, query: str,
   hits: list[dict], *, max_candidates: int = 12) -> str` — takes the first
   `max_candidates` hits; one line per hit, 1-indexed:
   `"{i}. tags: {tags} ({imageWidth}x{imageHeight}, {likes} likes)"`, preceded
   by `Word:`/`Meaning:`/`Query:` header lines.
3. `parse_image_choice_response(raw: str) -> int | None` — first integer in
   the reply; `None` when there is no integer. (Range/`0` handling lives in
   `choose_image_hit`, where `len(hits)` is known.)
4. `async def choose_image_hit(word, english, query, hits, *, llm) -> dict | None`
   — returns the chosen hit dict, or `None` meaning "no usable opinion" (caller
   falls back to `best_hit`). `None` cases: `llm is None`, empty hits, LLM
   exception (→ `logger.warning`), unparseable reply, `0`, out-of-range index
   (valid range is 1..min(len(hits), max_candidates)). LLM call: same call
   shape as `query_llm.py`'s (`temperature=0.0`, `max_tokens=256` — keep the
   reasoning-model rationale comment from query_llm.py:119-123).

**Tests** (new `backend/tests/test_anki_media_choose_llm.py`): stub LLM
objects injected as the `llm` param (the pattern `test_anki_media_*` files
already use — no `patch("app.…")`): picks hit 3 when LLM says "3"; "0" → None;
"7" with 5 hits → None; garbage → None; exception → None + warning; empty
hits → None without calling the LLM; prompt builder caps at 12 and formats as
specified (pin one exact prompt string).

Plus ONE cassette-backed test using the `cassette_llm` fixture
(`backend/tests/conftest.py:844`) pinning that a representative real prompt
parses to a valid index. **You cannot record cassettes** — no API key, never
run `--llm-mode=record/live/patch`. Write the test so default mock mode skips
it while the cassette is missing, and report it as SKIPPED (the human records
it after review). Do not fabricate a cassette JSON by hand.

## Commit 3 — pipeline: retry/broaden + chooser wiring in `fetch_card_media`

**Problem + constraint.** The image fetch runs inside `anyio.to_thread`
(pipeline.py:83-98) but the chooser is async — so choosing must happen in the
async pipeline between a threaded search and a threaded download.

**Change** (in `backend/app/anki/media/pipeline.py`):

1. `MediaResult` gains defaulted fields (existing constructors stay valid):
   ```python
   image_status: str | None = None    # "ok"|"no_results"|"rate_limited"|"api_error"|"skipped"
   image_query_used: str | None = None
   image_chooser: str | None = None   # "llm" | "tag_overlap"
   ```
2. `fetch_card_media` new parameters — ALL keyword-with-default so existing
   callers and the allowlisted patch point `app.api.anki.fetch_card_media`
   keep working: `llm=None`, `_search_fn=None`, `_download_fn=None`,
   `_choose_fn=None` (defaults resolve to `search_pixabay` / `download_hit` /
   `choose_image_hit`). Remove the internal use of `fetch_pixabay_image` here;
   if a `_pixabay_fn` seam currently exists, drop it and update its tests to
   the new seams (report the removal).
3. Image flow (replaces the current image block; audio flow untouched):
   a. `image_query == ""` → `image_status="skipped"`, no fetch (current
      behavior, now labeled).
   b. Primary query = `image_query or build_query(english)`. Search in a
      thread. Filter hits whose `webformatURL` is in `used_image_urls`.
   c. **Retry (max one, pinned rules):** retry iff
      (i) status is `"no_results"`, OR (ii) status is `"ok"` but no remaining
      hit has any tag overlap with the primary query (reuse `_tag_overlap`;
      overlap computed on the post-`used_image_urls` hit list). Retry query:
      `build_query(english)` if it differs from the primary query, else the
      first two words of the parenthetical-stripped gloss
      (`re.sub(r"\s*\(.*?\)", "", english)`); if the retry query equals the
      primary query, do not retry. `rate_limited`/`api_error` never retry.
      A retry that itself errors keeps the retry's status.
   d. Chooser: if `llm` is not None and hits remain, `await choose_fn(...)`.
      **Always ask when an LLM is provided — do NOT add an overlap-based
      skip heuristic.** Chosen hit → `image_chooser="llm"`; `None` →
      `best_hit(hits, query)` with `image_chooser="tag_overlap"`.
   e. Download in a thread. Download failure → `image_status="api_error"`
      (+ the bytes fields stay None). Success → `image_status="ok"`,
      `image_url` recorded into `used_image_urls` (preserve current lines
      97-98).
   f. `image_query_used` = whichever query produced the final hit list (the
      retry query when the retry ran, else primary).

**Tests** (`backend/tests/test_anki_media_pipeline.py`, stub `_search_fn` /
`_download_fn` / `_choose_fn`): retry fires on no_results; retry fires on
zero-overlap-ok; no retry when retry query would equal primary; never two
retries; no retry on rate_limited/api_error; statuses propagate into
`MediaResult`; `skipped` on empty image_query; LLM choice wins over
tag-overlap ranking; `_choose_fn` returning None falls back to `best_hit`;
`llm=None` never calls `_choose_fn`; used_image_urls filtered BEFORE both the
overlap check and the chooser; download failure → api_error; audio behavior
unchanged.

## Commit 4 — thread `llm` through + log failures + sync report counters

Four small changes:

1. `vocab_media.py::generate_vocab_media`: pass `llm=llm` into the fetch call.
   After the fetch: if `media` is not None, image bytes are None, and
   `media.image_status not in (None, "skipped")` → `logger.warning` with a
   distinct message per status (include word, status, `image_query_used`).
   When `media.image_status` is set, also put it in the returned dict:
   `stored["image_status"] = media.image_status` (values remain `str`; the
   only caller `_generate_add_time_media` (srs.py:196) ignores the return, so
   this is test-visibility only — do not change that caller).
2. `app/api/anki.py::_build_media_fn`: pass `llm=llm` into `fetch_card_media`.
3. `sync_common.py::CreateNewReport` (line 125): add
   `image_ok: int = 0`, `image_no_results: int = 0`, `image_failed: int = 0`
   (failed = rate_limited + api_error).
4. `sync_engine.py::sync_create_new`, inside the existing media block
   (~1370-1400): after the `_media_fn` await, classify `media.image_status`
   into the three counters and `logger.warning` per non-ok fetch. **Purely
   additive**: no change to when `_media_fn` is called, filenames, tags,
   field building, or any control flow — if you think a flow change is
   needed, STOP and report. Then in `sync.py::run_full_sync`: add
   `"image_fetch_failed": 0` to the default `media_report` dict (line 231)
   and set it from `create_report.image_failed` on the non-dry path.

**Tests:**
- `test_vocab_media.py`: stub `_fetch_fn` returning
  `MediaResult(image_status="rate_limited")` → warning via `caplog`, no
  `"image"` key, `stored["image_status"] == "rate_limited"`; happy path sets
  `image_status="ok"`.
- `test_anki_sync_create_new.py`: use the existing
  `_make_dual_collection_conn` helper; `_media_fn` stubs returning ok /
  no_results / rate_limited `MediaResult`s → report counters assert; a card
  with existing TT media (no `_media_fn` call) leaves counters at 0.
- `test_anki_sync_main.py::TestRunFullSync`: extend the existing contract
  assertions for the `media_report` key — the PHASE LIST must not change; if
  the contract test needs a phase edit, your change is wrong.
- Confirm `TestSociableSync` (`test_anki_sync_orchestrator.py`) passes
  untouched — its media_fn shape is unchanged.

## Guardrails (standing gaps — all mandatory)

1. Run the FULL gate from repo root per commit: `./test.sh` — paste the tail
   (backend pass/skip counts at 100% coverage incl. `--run-oracle`, frontend
   counts, e2e, ruff file count ~278). A bare `uv run pytest` is not the gate.
   Expected backend skips: the usual ~14 **plus exactly one** new skip (the
   commit-2 cassette test) from commit 2 onward — name it in the report.
2. No new `# pragma: no cover`, no `coverage.run.omit` additions, no
   per-file exemptions. Every new status branch gets a real test.
3. No new `patch("app.…")` / `monkeypatch.setattr("app.…", …)` targets.
   Every seam in this brief is an injected parameter (`http_client`, `llm`,
   `_search_fn`, `_download_fn`, `_choose_fn`, `_fetch_fn`, `_media_fn`) —
   use those. The mock-boundary checker will fail the gate otherwise; the
   fix is never a grandfather/allowlist edit.
4. No hardcoded language literals in new code (`scripts/check_language_literals.py`
   runs in the gate).
5. Never run with a real API key: no `--llm-mode=record/live/patch`, no
   outbound HTTP in any test (Pixabay included — fake clients only).
6. If any pinned interface or golden conflicts with reality, report the
   divergence with the actual output/error — do not adjust the pin, do not
   special-case around it.
7. Deliver UNCOMMITTED work per your usual flow unless told otherwise; final
   report includes: `git status --short`, `git diff --stat`, the `./test.sh`
   tail per commit checkpoint, the list of new test names, and the skipped
   cassette test name.
