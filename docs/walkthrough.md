# TunaTale Codebase Walkthrough

*2026-10-01T17:07:06Z by Showboat 0.6.1*
<!-- showboat-id: 375ac829-829c-4df4-b714-7f5d71ce89df -->

## About This Walkthrough

TunaTale generates Pimsleur-style audio lessons whose stories are written around the learner's own vocabulary. It schedules that vocabulary with FSRS in lock-step with the learner's Anki deck. This document is a guided tour of the codebase as it stands. It is written for two readers: a person who wants to understand how the system works, and an agent about to change it.

The tour is ordered by subsystem, not by history. §1 follows one lesson end to end, from a planned day to a graded card in Anki, and names the chapter that owns each step. §2–§16 then take the subsystems one at a time. Appendix A gives the short history and maps the old `PART N` numbering (still cited by older docs) to the current chapters. The previous, chronological edition is archived at [`docs/archive/walkthrough-2026-03-to-07.md`](archive/walkthrough-2026-03-to-07.md).

**This is a [Showboat](https://github.com/simonw/showboat) document.** Every `bash` block was executed from the repo root, and the `output` block under it is what it printed. `uvx showboat verify docs/walkthrough.md` re-runs them all and diffs the results, so drift between the prose and the code shows up as a failing block rather than going unnoticed. The blocks anchor on symbol names, never line numbers, so ordinary edits elsewhere in a file don't break them. They also run against a lean install (`uv sync --no-default-groups --group dev`), so none of them needs torch, a network connection, or an API key. Elsewhere, code is cited as `path/module.py::symbol`.

### How to read it

| If you want to… | Read |
|---|---|
| understand the product loop | §1, then §8 |
| add or change a language | §3, then `docs/adding-a-language.md` |
| touch anything that reads or writes Anki or schedules cards | §9 and §10 first, then `.claude/rules/anki-safety-core.md`, `.claude/rules/anki-sync.md`, `.claude/rules/anki-queue-parity.md` |
| change generation prompts or lesson structure | §4, §6 |
| change what a lesson sounds like or what a render costs | §7, then `.claude/rules/paid-vendors.md` |
| add an endpoint or a page | §12, §13 |
| get a change through the gate | §14, §16 |
| run or recover production | §15, then `docs/deployment.md` |

### Architecture at a glance

TunaTale is two applications and a set of language plugins:

- **`backend/`**: a FastAPI app (Python 3.14, `uv`). Core packages know no concrete language. Everything language-specific is resolved through the registry in `app/languages.py` from plugins under `app/plugins/languages/`. The Anki integration is an optional plugin under `app/plugins/anki_sync/`. It is the only code that opens the user's `collection.anki2`, and it does so only at sync time.
- **`frontend/`**: a SvelteKit app built with `adapter-static`. In production it is static files served by Caddy beside the API. Its TypeScript types are generated from the backend's committed OpenAPI schema.
- **Per-language SQLite databases**: one SRS-and-content database per language (and per learner account). There is also a standalone `auth.db`. The Anki collection is never touched on a request path.

```bash
find backend/app -mindepth 1 -maxdepth 3 -type d -not -name __pycache__ -not -name data | sort
```

```output
backend/app/api
backend/app/audio
backend/app/audio/preprocessing
backend/app/auth
backend/app/cards
backend/app/cards/media
backend/app/common
backend/app/generation
backend/app/llm
backend/app/media
backend/app/models
backend/app/plugins
backend/app/plugins/anki_sync
backend/app/plugins/languages
backend/app/plugins/languages/ceb
backend/app/plugins/languages/no
backend/app/plugins/languages/sl
backend/app/plugins/languages/tl
backend/app/srs
backend/app/srs/anki_mirror
backend/app/storage
```

| Package | Role | Chapter |
|---|---|---|
| `config.py`, `main.py`, `auth/`, `common/`, `logging_sink.py` | settings, app assembly, accounts, cross-cutting helpers | §2 |
| `languages.py`, `plugins/languages/{sl,no,tl,ceb}` | the language registry and its plugins | §3 |
| `models/` | pure domain models (no I/O) | §4 |
| `llm/` | Groq client, cassettes, usage ledger, priority gate | §5 |
| `generation/`, `storage/` | curriculum planning, story generation, glossing, section building; `ContentStore` | §6 |
| `audio/` | TTS services, rendering, cues, cost accounting | §7 |
| `srs/` (lemmatizer, transcript, mastery, listen) | words, transcripts and the learning loop | §8 |
| `srs/` (FSRS, queue, `anki_mirror/`, migrations) | the scheduler and its Anki-parity machinery | §9 |
| `plugins/anki_sync/` | the safety envelope and the one sync sequence | §10 |
| `cards/`, `media/` | notetypes, card media, drawn pictures, cloze | §11 |
| `api/` | FastAPI routers | §12 |

The frontend's pages, one directory per route:

```bash
find frontend/src/routes -name '+page.svelte' | sed 's|frontend/src/routes||; s|/+page.svelte||; s|^$|/|' | sort
```

```output
/
/c/[curriculumId]
/c/[curriculumId]/l/[lessonId]
/c/[curriculumId]/plan
/cards
/login
/review
/review-sessions
/review-sessions/[sessionId]
/settings
```

Five invariants recur in every chapter:

1. **No hardcoded language logic in core.** Two gates enforce it (§3.9).
2. **Exactly one Anki sync sequence**, `run_full_sync`. Every Anki write goes through `safe_open`, with `usn = -1` and `mod = now` on touched rows (§10).
3. **TT's queue and FSRS mirror Anki's bit for bit.** A divergence is a numbered parity layer, pinned by a differential oracle against the real Anki binary (§9).
4. **Every LLM call in tests replays a cassette.** Coverage is 100% and enforced (§5, §14).
5. **Paid vendors are priced before they run** (§7).

## 1. A Lesson's Journey

This chapter follows one lesson from the learner's goal to a graded card in their Anki deck, at the level of "what happens, and which chapter owns it". Every step names its module so you can jump straight to the code, and every later chapter is the detailed version of one stretch of this path.

### 1.1 Plan

A learner states a goal ("ordering at a café, then directions") in the planner chat on `/c/<id>/plan`. The chat planner turns the conversation into a `Curriculum` of `CurriculumDay`s, each with a title, a focus, target collocations and a learning objective (§4.4, §6). Days are keys, not ordinals: deleting day 5 leaves a gap, and the UI shows positions. A curriculum also carries plan-level settings such as review pressure, the dial for how hard each story should push the learner's due words.

### 1.2 Generate

Generating a day is a background job. `app/generation/pipeline.py::LessonPipeline` keys each job by `(user, language, curriculum, day)` and runs it on a worker loop, so the browser polls a pipeline card instead of holding a request open through a minute of LLM calls (§6).

1. **Story.** `StoryGenerator` builds the prompt from the day, the language's style notes (§3), the strategy (WIDER, DEEPER or REVIEW, §4.6) and the review words. The review words come from `app/srs/review_selector.py`: the learner's due cards in order of increasing retrievability, so the words closest to being forgotten are offered first (§8.12). One call to Groq (`openai/gpt-oss-120b`) returns the story as JSON. In tests, and in dev by default, the call replays a recorded cassette (§5).
2. **Glosses.** Word-by-word glosses are a separate LLM call (`app/generation/glossing.py`), because inside the story call they took most of the token budget and truncated the story. They are spliced back in before the lesson is built.
3. **Sections.** `build_lesson_from_story` and `section_builder` turn the story into a `Lesson` of Pimsleur sections: key phrases with their syllable buildups, the dialogue at natural speed, an enunciated pass, and translated passes in both language orders (§4.3, §6). Every phrase is pinned to a resolved voice from the language plugin's voice map (§3.3).

### 1.3 Publish

Lessons and review sessions are both written through one seam, `app/generation/publishing.py::publish_lesson`. Seven code paths once did these steps by hand, and each dropped different ones. The order is load-bearing, and the module states it:

```bash
grep -E '^[0-9]\. ' backend/app/generation/publishing.py | cut -c1-110
```

```output
0. Lemma resolution, AWAITED and BEFORE the UPOS step, so the tags it reads
1. UPOS annotation, AWAITED and BEFORE the write. A detached task races the
2. ``target.write(lesson)`` — persist the lesson and return its content id.
3. ``if replace: target.invalidate_audio(content_id)`` — drop the audio of the
4. Pre-warm the analysis cache as an ANCHORED background task. The event loop
5. ``await target.schedule_render(content_id, lesson)`` — ensure audio will be
```

Lemma resolution and part-of-speech tagging run *before* the write. Tags added afterwards land on an in-memory object that nobody persists, and the stored lesson then plays every ambiguous word with plain synthesis for the rest of its life (§6, §8.3).

### 1.4 Render

`app/audio/render_service.py::render_lesson_audio` renders the lesson section by section through `LessonRenderer` (§7):

- Each phrase is synthesised by Azure Speech, or by Gemini for Cebuano's voices, with a sha256-keyed file cache in front. Only sub-word breakdown chunks get IPA.
- Clips are gained per voice and laid end to end with pauses sized for repeating aloud.
- Each section is streamed to one Opus file, and a cue manifest records which caption plays at which millisecond.

Renders are serialised to one per process, because the production box has under 1 GB of RAM. Every miss on the cache draws on a free monthly character allowance, which is why renders are priced before they run (`report_render_cost.py`, §7.4).

### 1.5 Listen and read

On the lesson page (§13) the learner plays the lesson section by section, or hands-free from key phrases through the translated pass and on to the next day. They can also read the transcript instead. Each word in the transcript is a `WordToken` (§8.6) that carries the word's lemma, its card and both directions' strength. The reader colours it by mastery, bolds it when its recognition card is due, and offers one tap to start learning an unknown word.

Marking the lesson listened opens the listen preview (§8.9). The learner sees which words the listen will grade, which new cards it will create, and where today's new-card budget runs out. Confirmed grades are applied immediately. The rest are staged per lesson for "Check your work", a read-only drill over just that lesson's cards (§8.10). Hearing a word can only ever grade its recognition direction.

### 1.6 Review

`/review` serves the unified queue, assembled by `app/srs/anki_mirror/queue_engine.py`: learning cards, then due reviews ranked by retrievability, interleaved with a daily-capped slice of new cards. A word's production card is introduced only after its recognition card has graduated. Each grade goes through `fsrs.py::schedule` in f32 and appends a `tt_revlog` event row (§9). The queue order, the badge counts and every scheduling number are built to match what the learner's Anki desktop app would show for the same deck. Each divergence ever found is a numbered parity layer pinned against the real Anki binary (§9.10).

### 1.7 Sync

The Sync button (`POST /api/anki/peer-sync`) brackets one reconcile between two AnkiWeb syncs of TT's own mirror collection (§10.4):

- **Pull leg.** Brings down what the learner did on their phone and desktop.
- **Reconcile.** `run_full_sync` runs the one sequence of phases, in order:
  - mint TT-created cards into the language's deck;
  - push TT's grades as revlog rows;
  - pull Anki's scheduling verbatim;
  - promote graduated words to production cards, ten per sync, each with a picture or a cloze (§11.6);
  - refresh the deck settings TT mirrors.
- **Push leg.** Sends the result up.

Afterwards, background tasks pre-stage the images and cloze sentences that the next sync's promotion will need (§11.5).

Every write to an Anki collection goes through `safe_open`, which runs a lock probe, a SHA256 backup and an integrity check. Every write also follows the USN rules in §10.3. The learner's real collection is production data, and the rules exist because getting them wrong forces a full re-upload on every device.

### 1.8 Around the loop

- **Configuration and accounts** (§2): settings, per-language databases selected by the `X-TT-Language` header, and an owner plus learner accounts in a separate `auth.db`.
- **Languages** (§3): Slovene, Norwegian, Tagalog and Cebuano, all plugins behind a registry that core may not bypass.
- **The API** (§12): typed end to end into the frontend through a committed OpenAPI schema.
- **Gates** (§14): the commit gate, CI and the checker scripts that keep these invariants true.
- **Production** (§15): two containers behind Caddy on a small cloud VM, with tested rollbacks, off-box backups and a restore drill.

## 2. Configuration, Entry Point & Accounts

This chapter covers everything that happens before a request reaches a feature: how settings are read, how `main.py` assembles the process (per-language databases, the LLM client, the pipeline), how each request is bound to a user and a language, and how accounts and sessions work. It is the layer every other chapter stands on: the API (§12) reads what the middleware binds, the content store (§6) and SRS database (§9) are the connections it opens, and the deployment guards that stop a misconfigured box from booting (§2.8) are the reason §15 can treat "the app started" as a meaningful signal.

### 2.1 Settings: one object, grouped by concern

All configuration is a single Pydantic `Settings` object, `backend/app/config.py::Settings`, read from the process environment and then `backend/.env`. There are no module-level side effects beyond constructing that object, and no hardcoded secrets. The process environment outranks the file, which matters in two places later in this chapter: the prod checker (§2.8) has to blank the ambient environment, and `TT_HOME` (below) is read from the environment alone.

At HEAD there are 77 fields. Read them by concern, not alphabetically:

| Concern | Fields (prefix) | Notes |
|---|---|---|
| LLM | `llm_*`, `groq_*` | `llm_mode` is `mock` by default (cassette replay, §5); `live` for prod. The two `groq_*_per_day_limit` values feed the usage ledger and the rate-limit UI. |
| Per-language databases | `database_url`, `database_urls`, `target_language` | `database_urls` (a JSON object, language code to SQLite URL) switches on multi-language mode (§2.3). |
| Anki and sync | `anki_*`, `sync_*`, `tt_collection_path` | Collection and media paths, the pinned `anki_pkg_version`, the AnkiWeb credential sources (§10). `sync_enabled` is True by default. |
| Speech and audio | `tts_*`, `azure_*`, `gemini_*`, `audio_*`, `max_concurrent_renders`, `ffmpeg_nice` | Throttles, the Azure key/region and monthly quota, delivery codec (§7). |
| Cards and media | `media_dir`, `pixabay_api_key`, `forvo_enabled`, `prestage_*` | Forvo is off in prod (§2.8). |
| Durable files | `*_backup_dir`, `*_log`, `*_ledger_path`, `*_cache_dir` | Almost all default to a path under `tt_home()`. |
| Deployment profile | `tt_env`, `tz`, `cors_*`, `parked_at` | Armed by `TT_ENV=prod`. |
| Accounts | `auth_*`, `session_*`, `owner_email`, `user_data_dir`, `trusted_proxy_header` | §2.5 to §2.7. |

The grouping is a convention, not a structure: every field is flat on one class so that `Settings(_env_file=None)` is a complete, testable description of a deployment. A probe makes the grouping checkable:

```bash
cd backend && uv run python -c "
import collections
from app.config import Settings
g = collections.defaultdict(list)
for n in Settings.model_fields:
    g[n.split('_')[0]].append(n)
for k in ('llm', 'groq', 'azure', 'tts', 'auth', 'cors', 'sync'):
    print(f'{k:6}', ' '.join(g[k]))
"
```

```output
llm    llm_mode llm_model llm_allow_fallback llm_usage_ledger_path llm_cassette_miss_log
groq   groq_api_key groq_tokens_per_day_limit groq_requests_per_day_limit
azure  azure_speech_key azure_speech_region azure_tts_chars_per_month_limit azure_tts_usage_ledger_path azure_tts_quota_reset_tz
tts    tts_max_concurrent_requests tts_min_request_delay_s tts_azure_min_request_delay_s tts_retry_base_delay_s tts_cache_dir tts_render_max_attempts tts_render_retry_cooldown_s
auth   auth_enabled auth_database_url
cors   cors_origins cors_allow_origin_regex
sync   sync_log sync_enabled sync_endpoint sync_username sync_password sync_keychain_service sync_password_file
```

Three defaults are easy to get wrong and each has a story:

- **Mutable paths follow `tt_home()`, not the working directory.** `backend/app/config.py::tt_home` returns `$TT_HOME` or `~/.tunatale`. It exists so the laptop's live instance (§15) can keep its data apart from the dev server's. Relocating `HOME` instead would also relocate the macOS Keychain lookup for the AnkiWeb password, which then fails as "not found" (exit 44, measured). `media_dir` and `audio_dir` anchor on `_BACKEND_DIR`, not the CWD, because a container or systemd unit starts somewhere other than `backend/` and a CWD-relative default silently splits the writer from the reader.
- **Environment lists are JSON, not CSV.** `CORS_ORIGINS=["https://x"]` and `DATABASE_URLS={"sl": "..."}` are parsed by pydantic as JSON. A comma-separated value fails at startup.
- **`.env` is no longer pre-loaded.** An older `main.py` called `load_dotenv()` before importing `config`; a lowercase key in `.env` could then beat the real process environment. Settings now owns the file read, with the environment on top.

One module constant lives beside the object: `ANKI_ROLLOVER_HOUR = 4`. It is deliberately not a setting. The study day rolls over at 4 AM *local* time and the arithmetic is single-sourced in `app/srs/anki_mirror/rollover.py` (§9).

### 2.2 The entry point: `main.py` in one pass

`backend/app/main.py` is the only place the process is assembled. Everything else receives its collaborators from `app.state`. The file has four parts, in this order: the `lifespan` coroutine, the exception handlers and CORS, two middlewares, and the router mounts plus the inline `/api/health` and `/api/languages` routes.

The lifespan runs these steps; a probe on the `app.state` assignments shows the result:

```bash
cd backend && grep -o "app\.state\.[a-z_]* = " app/main.py | sed 's/app.state.//; s/ = //' | tr '\n' ' ' | fold -s -w 100; echo
```

```output
auth_db user_dbs srs_dbs content_stores languages srs_db content_store language activity_log llm 
curriculum_planner story_generator renderer tts audio_dir pipeline 
```

In order of operation:

1. **Guards first.** `_assert_prod_profile()` (§2.8), then `languages.discover()` so the plugin registry (§3) is populated before anything asks for a language.
2. **Durable warning sink.** `app/logging_sink.py::install_warning_file_handler` (§2.9).
3. **LLM client.** `LLMClient` with the usage ledger and the failure mirror, wrapped in `CassetteLLMClient` unless `llm_mode == "live"`. This keeps a dev server or CI from reaching the real Groq API by accident. The pipeline additionally receives the *raw* client (`real_client`), because it reads the live client's last-429 and rate-limit state to report back-pressure on the pipeline card (§6); a cassette wrapper has no such state.
4. **Rolling DB snapshots, then one `SRSDatabase` and one `ContentStore` per configured language** (§2.3). `rotate_db_backups` runs before the first open and never raises, so a backup hiccup cannot block boot. `SRSDatabase` receives `pre_migration_backup_dir`, which makes the first pending migration snapshot the file before touching it (§9).
5. **Identity.** `AuthDatabase` is opened eagerly. A missing `app.state.auth_db` makes `require_user` fail *closed*, so every request would 401, including a correct login. Opening at startup turns that into a startup failure instead of a first-request surprise. `UserDatabases` (§2.5) is created but opens nothing.
6. **Renderer and pipeline.** `get_tts_service` and `build_lesson_renderer` (§7), then `LessonPipeline` (§6), started unless `pipeline_autostart` is false.
7. **Lemmatizer warm-up as a background task.** `_warm_lemmatizer` must not be awaited before `yield`: loading a heavy model (~15 s) in the startup event stops uvicorn binding the port, so every frontend `/api/*` call is refused until the model finishes. It swallows per-language errors so one missing model does not stop the others from warming.

Shutdown awaits the warm-up, drains the pipeline, and closes each database.

The slicer is deliberately *not* wired into the renderer at startup. `app.audio.slicer` and `plugins/languages/no/alignment.py` stay in the tree and tested, but breakdown chunks are now rendered from pronunciation-lexicon `<phoneme>` input (§7), and `test_main_lifespan` asserts the slicer stays unwired so it cannot creep back.

### 2.3 Multi-language databases and the per-request binding

TunaTale isolates languages by *file*, not by a `WHERE language = ?` predicate: one `tunatale_<code>.db` per language, each holding that language's SRS rows and content. The queue and badge queries are byte-for-byte the ones tested for Anki parity (§9), so isolation is a property of *which connection serves the request*, not of any query.

`main.py::_language_db_map` returns `settings.database_urls` when set (multi-language) and otherwise `{target_language: database_url}`. The lifespan opens one `SRSDatabase`/`ContentStore`/`Language` triple per entry into `app.state.srs_dbs`, `content_stores` and `languages`, plus singular `srs_db`/`content_store`/`language` aliases for the default language. The singular names exist for non-request consumers and for tests that seed `app.state` by hand.

Per request, the HTTP middleware `main.py::_resolve_language_state` binds `request.state.{srs_db, content_store, language, language_code, user_id, is_owner}`. Routes read only `request.state`. The rules, in order:

- **Auth on, no valid session:** bind nothing at all. Every data route then 401s in `require_user`; binding the owner's databases to an anonymous request would turn a forgotten dependency into a data leak.
- **Auth on, session belongs to a non-owner:** serve from that user's own files (§2.5).
- **Otherwise (auth off, or the owner):** the flat per-language databases, keyed by the `X-TT-Language` header, defaulting to `target_language`.
- **An unconfigured language code answers 400**, not the default language. The old fallback silently read and wrote the default language's database for a client that had selected a language this deployment had no database for. `/api/languages` alone is exempt and answers with the default, because it is how a client holding a stale stored code learns the configured set and heals itself.

A second middleware, `_refuse_while_parked`, wraps the first. When `parked_at` is non-empty (set by `switch.sh` while learning lives on the laptop, §15) every `/api/*` path except `/api/health` answers 503 naming the other address. A parked copy is stale by definition, so nothing graded on it is allowed.

### 2.4 Routers and access tiers

`main.py` mounts one router per module under `app/api/` and attaches the access dependency at the mount, not inside each router. The `auth` router and `/api/health` are open, `anki` and `admin` are owner-only, and everything else needs a logged-in user. The Anki router is mounted only when `sync_enabled` is true and `app.plugins.anki_sync` is importable. The tiers, the generated route inventory, health, the `app_state.py` accessors and the app-wide error mapping are all in §12.

### 2.5 Accounts and the owner

Authentication is behind `auth_enabled` (default False, so dev and tests are unchanged; the Playwright suite runs with it on). The identity store is `backend/app/auth/`, with its own SQLite file, `auth.db`, configured by `auth_database_url`.

Why a separate file: the content databases are per-language and are copied, migrated and restored per language. A `users` table inside one of them would exist once per language and disagree with itself.

- **Passwords** are argon2id (`auth/passwords.py`, library defaults); a malformed stored hash verifies as False rather than raising. `verify_credentials` also checks a dummy hash for unknown emails so the two failure paths cost the same time.
- **Session tokens** are 256 random bits (`auth/tokens.py::mint_token`), stored as a sha256 digest. The asymmetry with argon2 is deliberate: a 256-bit token has no guessable space for a slow KDF to defend, and argon2 would add ~50 ms to every authenticated request. The property the store needs is preimage resistance, so a read of `auth.db` yields no usable cookie.
- **Sessions** last `session_ttl_days` (30). `create_session` deletes expired rows in the same transaction as the insert, so the table is bounded by the login rate with no scheduler. `get_session_user` returns None when the database is missing, the token is unknown or expired, or the user is deactivated.
- **The cookie** is `tt_session`: HttpOnly, Secure, SameSite=Lax.

There is **no self-serve signup**. Accounts come from the command line (§2.7).

**Owner versus learners.** `app/storage/user_dbs.py` implements the multi-user model. One account is the *owner*: `owner_email` if set, otherwise the first account ever created (lowest id). The owner keeps resolving to the flat per-language databases, so nothing of theirs moves. Every other account resolves to `<root>/<user id>/tunatale_<code>.db`, where the root is `user_data_dir` or a `users/` directory beside the owner's databases (so it lands on the data volume wherever that is). The default is fail-safe: a second account created later is never the owner by accident and never reads the owner's reviews.

Key properties of `UserDatabases`:

- A user *has* a language exactly when the file exists. `get()` returns None rather than creating one, because `SRSDatabase` and `ContentStore` both create a missing file on open, and an unguarded open would silently enrol anyone in any language with an empty deck. Seeding a learner's deck is a deliberate act (`seed_user_deck.py`, §11.9).
- `path_for` refuses a bool or non-positive id and any code that is not a configured language, so neither can smuggle a path separator.
- Opened pairs are cached with no eviction. File-backed stores open a fresh sqlite connection per operation and hold no descriptor between them, so a cached pair costs a few objects, not a file handle. Evicting would also be unsafe: `/listen` runs a Starlette background task on the request's `srs_db` after the response.

What each tier may do:

| Action | Owner | Learner |
|---|---|---|
| Lesson, curriculum, generation, audio, review sessions, LLM routes | yes | yes, against their own files |
| Anki sync, `/api/admin/*` | yes | 403 (`require_owner`) |
| Choose a language | any configured | only those they have a deck for |
| `GET /api/languages` `sync_available` | per config | always false |

Lesson writes opened to every account because pipeline jobs now carry the account id: `app/storage/user_dbs.py::pipeline_user_id` returns None for the owner and the user's id otherwise, and *raises* for a non-owner request that carries no account, so a missing id can never generate into the owner's store. Learners share the one Groq budget. Idempotency keys (§12) include the user.

Accounts and learner decks move and back up as one unit, because a learner's `users/<id>/` directory is named by an id that only means something relative to its `auth.db` (§15).

### 2.6 Login, throttling and the route guard

`require_user` (`auth/dependencies.py`) reads `settings.auth_enabled` **per request** (tests monkeypatch it after import), returns None when auth is off, and otherwise looks up the cookie and raises 401. `require_owner` raises 403 when `request.state.is_owner` is false, and is listed after `require_user` so an anonymous caller is told 401, not 403. A missing `is_owner` fails closed.

The auth endpoints, in `app/api/auth.py`:

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/auth/status` | none | Returns `{"auth_enabled": bool}` and nothing else. The SPA reads it first, because `/me` answers 401 to an anonymous caller whether the gate is on or off, so a 401 alone does not mean "go to /login". Kept to one boolean on purpose: user counts or a bootstrap flag would turn a probe into reconnaissance. |
| `POST /api/auth/login` | none | Verifies credentials, sets the cookie. Identical 401 body for unknown email and wrong password. |
| `POST /api/auth/logout` | none | Deletes the session row and clears the cookie; succeeds even when already logged out. |
| `GET /api/auth/me` | `require_user` | The current email. |

The exempt list in `tests/test_auth_route_coverage.py` is exact paths, not a prefix: a prefix would blanket-exempt `/api/auth/me`, the endpoint that most needs the gate. That test enumerates routes and asserts every non-exempt one answers 401 anonymously. It iterates the *OpenAPI* surface rather than walking `app.routes`, because a router included into another router and nested yields almost no flat routes (2 of 72 when this was measured on FastAPI 0.140), and a guard that walks nothing reports total success.

**Throttling** (`auth/throttle.py`) protects login with two independent scopes, per account (5 failures) and per client IP (20), over a one-hour window. Past the threshold the lock doubles from 60 s and caps at 30 minutes; the longer of the two locks wins, and the user is told how long to wait through `Retry-After`.

```bash
cd backend && uv run python -c "
from app.auth import throttle as t
for n in (4, 5, 6, 7, 8, 12):
    print(n, 'failures ->', t._lockout_for(n, t.ACCOUNT_THRESHOLD))
"
```

```output
4 failures -> 0:00:00
5 failures -> 0:01:00
6 failures -> 0:02:00
7 failures -> 0:04:00
8 failures -> 0:08:00
12 failures -> 0:30:00
```

The non-obvious decisions:

- **Only failed attempts are recorded.** A successful login clears the account counter but *not* the IP counter, otherwise an attacker holding one valid account could reset the per-IP budget while guessing every other password from the same address. A consequence: the e2e suite signs in many times per run from one address and never trips the throttle, without any limit having been loosened to get it green.
- **The 429 is not an enumeration oracle.** Failures are recorded against the submitted address whether or not it names a real account, so a nonexistent email locks out on the same schedule as a real one.
- **Client IP behind the proxy.** With `trusted_proxy_header` set (`X-Forwarded-For` in prod), `throttle.client_ip` reads the *rightmost* entry. Caddy appends the peer it actually saw, so the leftmost entry is whatever the client claimed; reading it would let any caller mint a fresh bucket per request. Unset behind a proxy, every user shares one bucket, which is why the prod guard requires it (§2.8).
- **Accepted cost:** anyone who can reach the login endpoint can lock a *known* account out for up to 30 minutes by failing on purpose. That is the standard cost of account lockout, it is time-bounded, and it was preferred to leaving distributed guessing unthrottled.

On the frontend the login page, a 401 interceptor and a route guard consume `/api/auth/status` and `/api/auth/me` (§13). A logged-out deep link redirects to login and then returns to that lesson.

### 2.7 Creating accounts: the CLI

`python -m app.auth.cli` is how every account comes into existence, including the first on a fresh deployment:

```bash
cd backend && uv run python -m app.auth.cli --help | sed -n '1,3p'; uv run python -c "
from app.auth.cli import build_parser
print('commands:', sorted(build_parser()._subparsers._group_actions[0].choices))
"
```

```output
usage: python -m app.auth.cli [-h]
                              {create-user,set-password,list-users,deactivate-user} ...

commands: ['create-user', 'deactivate-user', 'list-users', 'set-password']
```

There is **no `--password` flag**, and a test asserts the parser rejects one: a password in argv shows up in shell history, in `ps` output, and in process-accounting logs. The password comes from stdin, from `$TT_AUTH_PASSWORD`, or from a `getpass` prompt on an interactive terminal. In a container the exact invocation (`docker compose exec -T api /app/.venv/bin/python -m app.auth.cli ...`, with `-T` and the venv's interpreter rather than `uv run`) is in `docs/deployment.md` § Accounts; the reasons for both are there too.

### 2.8 Prod boot guards

`TT_ENV=prod` arms a startup guard. Dev, Tailscale and test boots never reach it. `main.py::_assert_prod_profile` raises `RuntimeError` listing *every* problem at once, rather than warning, because the failures it catches all produce a server that looks healthy:

- a `mock` LLM serves recorded replies and returns 200s;
- an unauthenticated API answers everyone;
- a missing Azure key lets the box pass `/api/health` and then fail every render;
- a placeholder Groq key passed every other check and failed the first sync with "invalid API key" (2026-09-18).

A deploy that fails one restart per mistake is a deploy nobody finishes, so the check reports all problems in one go. The rules live in `config.py::prod_profile_problems`, which is pure and does not consult `tt_env`, so both callers decide when it applies. Run against bare defaults it lists what a prod profile must override:

```bash
cd backend && env -i PATH="$PATH" HOME="$HOME" UV_NO_SYNC=1 uv run python -c "
from app.config import Settings, prod_profile_problems
for p in prod_profile_problems(Settings(_env_file=None)):
    print('-', p.split(' —')[0].split(', a path')[0])
"
```

```output
- llm_mode is 'mock', not 'live'
- auth_enabled is False
- session_secret is unset
- azure_speech_key is unset
- azure_speech_region is unset
- groq_api_key is unset or not a Groq key (they start with 'gsk_')
- lemmatizer_type is 'lowercase', not 'table'
- auth_database_url is 'sqlite:///./auth.db'
- database_url is 'sqlite:///./tunatale_sl.db'
- tz is unset
```

The rules, grouped:

| Group | Refused when |
|---|---|
| Honest LLM | `llm_mode != "live"` |
| Auth | `auth_enabled` false; `session_secret` empty; `trusted_proxy_header` empty with auth on (all callers would share one throttle bucket) |
| CORS | `*` in `cors_origins`; a catch-all `cors_allow_origin_regex`; no origins and no regex at all |
| Speech and LLM keys | `azure_speech_key` or `azure_speech_region` empty; `groq_api_key` not starting `gsk_` (shape only, no network call) |
| Lemmatizer | `lemmatizer_type != "table"`: the lowercase engine treats every inflected form as its own word, so `deg` would not match `du` and a known word would show as NEW; the PyTorch engines do not fit the host (218 s model load on the e2-micro) |
| Relative SQLite paths | any opened database URL like `sqlite:///x`. In a container the CWD is the image filesystem, replaced on every redeploy; `./auth.db` was deleted by exactly that on 2026-09-18 |
| Clock | `tz` unset or unresolvable (`_zone_problems`) |

`config.py::clock_runtime_problems` is separate because it is not a property of the settings: it compares the zone `tz` *names* with the zone the live process actually *keeps*. libc handed a `TZ` it has no zone data for does not raise, it falls back to UTC, so `zoneinfo` happily resolves the name from its bundled `tzdata` wheel while every study-day boundary sits in the wrong place. Only the running box can answer, so it runs in `_assert_prod_profile` and not in the offline checker.

`backend/scripts/check_prod_env.py` is the offline form: the same rules applied to a file on disk, run by `./test.sh` against the committed `backend/.env.prod.example` so the template a deployment copies stays a profile that boots. It evaluates the file with `os.environ` swapped for the file's own keys, because pydantic ranks the environment above the file and an exported `LLM_MODE=mock` (or `live`) would otherwise decide the verdict in either direction, silently.

Two more settings belong to the profile story:

- **`forvo_enabled`** is True on a laptop and false in prod. Forvo blocks datacenter IPs (the same word, found from a residential IP, was refused with HTTP 403 plus an anti-bot challenge from the GCP box, nonsense-word control included), and leaving it on would spend a round-trip and log a warning on every card add (§11).
- **`parked_at`** (§2.3) is written and cleared only by `switch.sh`.

### 2.9 CORS, background work and logging

**CORS** is read from settings in `main.py::cors_kwargs`, replacing an `allow_origins=["*"]` plus credentials combination that shipped while the app had no authentication, under which any page the browser loaded could read and write TunaTale data from localhost or the tailnet. The normal flows need no cross-origin entry at all: the browser talks to Vite on :5173, which proxies `/api`, and a production build is served same-origin behind Caddy. `cors_origins` (default localhost:5173) exists for direct-to-:8000 use. `cors_allow_origin_regex` is for origins that cannot be enumerated (per-tailnet MagicDNS names) and is passed to Starlette *only when non-empty*, because `re.compile("")` matches every origin and would silently restore the wildcard.

Methods and headers are enumerated too, and the three custom headers are load-bearing: `X-TT-Language` selects the language, `Range` is how the audio player seeks, and `Idempotency-Key` is what keeps one paste from becoming two review sessions. None is CORS-safelisted. The last one fails *quietly* if dropped: deduplication simply turns off.

```bash
grep '"allow_headers"\|"allow_methods"\|"expose_headers"' backend/app/main.py
```

```output
        "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-TT-Language", "Range", "Idempotency-Key"],
        "expose_headers": ["Content-Range", "Accept-Ranges", "Content-Length"],
```

**Background work.** Peer-sync schedules image and cloze prestage as Starlette background tasks, and `/listen` defers its media fetches the same way, so the request returns promptly but the end of the work was unobservable: on 2026-09-19 a sync POST returned 200 while the box sat at load 5.58 for minutes, and an agent inferred idleness from log volume and was wrong twice. `app/common/background_work.py::BackgroundWork.track(kind, fn)` wraps each such job, counts it while it runs, logs one `BACKGROUND_DONE kind=... ok=... elapsed_s=... inflight=...` line when it ends, and marks the task as background through a `ContextVar` (`in_background()`), which is how the LLM `PriorityGate` (§5) lets a foreground call go first. It never throttles the work or makes prestage synchronous. `GET /api/admin/background-work` returns `{idle, inflight, completed, failed, last_finished}`; wait on `idle` before measuring anything on the box.

**Logging.** `main.py` configures INFO globally with the renderer at DEBUG. Three durable channels exist because uvicorn runs at `--log-level warning` under `start-dev.sh` and its output is redirected nowhere, and an in-memory ring (`/api/llm/activity`, 300 events) empties on a `--reload`:

- `app/logging_sink.py::install_warning_file_handler` attaches a rotating WARNING-and-above file (`warning_log`, 5 MB x 3). It is idempotent across `--reload`, **fails open** (a logging problem must never stop boot), and timestamps in UTC with a literal `Z`: `sync.log` is local time and is the odd one out, and reading one as the other cost a four-hour error on 2026-09-08.
- `llm_failure_mirror` wraps the LLM activity callback so a failed call also reaches that sink. `LLMClient` warns on some failure paths and raises silently on others (a hard failure with `allow_fallback=False`), but every outcome passes through the callback, so wrapping it covers all of them without auditing raise sites.
- `app/api/client_log.py` (`POST /api/client-log`) appends browser-supplied lines to `client_log`. It is off by default (`client_log_enabled`; 404 when off, so a disabled channel does not advertise itself), requires login, collapses whitespace so one submitted line is exactly one log line (no forged entries), and caps lines per batch and characters per line. It exists because a device console dies with the tab and the machine that could read it is not the one running the app.

The other durable files (`sync.log`, `llm_usage.log`, `azure_tts_usage.log`) are append-only ledgers and are deliberately not rotated (§15).

## 3. Languages as Plugins

Everything that differs between languages in TunaTale lives in a plugin package under `backend/app/plugins/languages/<code>/`, and the rest of the backend reaches it only through accessors in `app/languages.py`. Four plugins are wired: Slovene (`sl`), Norwegian (`no`), Tagalog (`tl`) and Cebuano (`ceb`), plus English (`en`), which core registers itself as the gloss language. This chapter owns *what each plugin provides*: voices, syllabifiers, preprocessors, lemmatizer wiring, card-headword rules and data files. The audio mechanics that consume those facets (IPA via `<phoneme>`, breakdown chunk provenance, rendering) are in §7, the lemmatizer engines are in §8, and the card pipeline is in §11. Two documents go deeper on the process than this chapter does: [docs/adding-a-language.md](adding-a-language.md) (the measured path Tagalog and Cebuano took) and [docs/language-plugin-hardening.md](language-plugin-hardening.md) (why the registry and its gates exist).

### 3.1 The registry: `LanguageConfig`, `register`, `discover`

`app/languages.py::LanguageConfig` is a dataclass of per-language wiring wrapped around a `app/models/language.py::Language` (code, names, script, voice maps). A plugin's `__init__.py` builds one inline and calls `register("<code>", config)` at import time. There is no central list of languages: `discover()` registers `en`, then imports every subpackage of `app.plugins.languages` with `pkgutil.iter_modules`, so deleting a plugin folder removes the language and nothing else breaks. It raises `RuntimeError` if no non-English plugin is present, and `app.main.lifespan` calls it eagerly so a zero-plugin install fails at boot rather than on the first request.

```bash
cd backend && uv run python -c "
import dataclasses, textwrap
from app.languages import LanguageConfig
names = [f.name for f in dataclasses.fields(LanguageConfig)]
print(len(names), 'LanguageConfig fields:')
print(textwrap.fill(', '.join(names), 100))
"
```

```output
39 LanguageConfig fields:
language, preprocessor_factory, deck_name, mint_deck_name, notetype_profiles, vocab_notetype,
l2_scorer, lemmatizer_type, syllabifier_fn, morphology_profile, slow_word_fn, story_text_normalizer,
definite_form_fn, lemma_plausible_fn, multiword_traps_fn, variant_separator, infinitive_marker,
verb_headword_fn, gender_articles, noun_gender_fn, style_notes, function_words_path, numbers_path,
spatial_path, pronouns_path, calendar_path, breakdown_spans_fn, alignment, planner_example,
wordfreq_lang, frequency_table_path, a1_morphology, lexicon_factory, phoneme_planner_factory,
ipa_read_in_voice_locale, ipa_for_drill_phrases, phrase_match_exact_form, lemma_table_path,
built_data
```

Every field is optional except `language`; a language opts into a capability by supplying the field, and every consumer treats `None` as "this language has no such thing" rather than guessing. The accessors follow two shapes. Plain facets share one helper, `_facet(code, attr, default)`, which calls `discover()`, returns the field or a default for an unknown code, and is used by the flag getters (`get_ipa_read_in_voice_locale`, `get_phrase_match_exact_form`, `get_lemmatizer_type`, ...). Getters that *call* what they fetch (`get_lexicon`, `get_phoneme_planner`) keep their own bodies because they must not open a store on the import path: the field holds a zero-argument factory, never an instance.

Three behaviours are worth knowing before reading any call site:

- **Unknown codes.** `get_language`, `get_preprocessor`, `get_deck_name` and `get_tts_locale` raise `KeyError`/`ValueError`; most facet getters return a neutral default instead. `known_language_codes()` is the single source for request validation (`app/api/srs.py::_VALID_LANGUAGE_CODES`), so adding a plugin widens it automatically.
- **Selector purity.** `get_planner_example` and `language_name_for_tts_locale` delegate to pure functions (`_select_planner_example`, `_select_name_for_tts_locale`) that take the config mapping as an argument. That is deliberate: tests exercise the one-language and ambiguous-locale cases by passing a mapping, not by mutating the module-level registry and leaking into later tests.
- **`LanguageContext`.** `resolve_language_context(code, settings)` bundles the runtime facets (database URL, deck, target language, which differ between single-language and `database_urls` multi-language mode, §2) with the static ones (`language`, `preprocessor_factory`, `lemmatizer_type`, `vocab_notetype`). `resolve_db_path(code, settings)` is the only sanctioned way to ask "which database file does this language use?"; the hand-rolled `settings.database_url.removeprefix(...)` answers for the *singular* setting and silently queries the wrong language, which is how a graving CLI once reported "nothing to grave" for a month. `backend/scripts/check_singular_database_url.py` fails the gate on that shape.

### 3.2 The registered languages, live

The table below is generated from the registry at HEAD: code, English name, TTS locale, lemmatizer engine, and every facet the plugin actually supplies (a facet absent from the list is `None`/`False`/empty).

```bash
cd backend && uv run python -c "
import dataclasses, textwrap
from app import languages as L
L.discover()
skip = {'language', 'lemmatizer_type', 'style_notes'}
for code in sorted(L._CONFIGS):
    c = L._CONFIGS[code]
    on = [f.name for f in dataclasses.fields(c) if f.name not in skip and getattr(c, f.name) not in (None, '', {}, (), False)]
    print(f'{code:3} {c.language.name:10} locale={c.language.tts_locale}  lemmatizer={c.lemmatizer_type}  style={bool(c.style_notes)}')
    print(textwrap.indent(textwrap.fill(', '.join(on) or '(none: gloss language only)', 96), '      '))
"
```

```output
ceb Cebuano    locale=ceb-PH  lemmatizer=table  style=True
      preprocessor_factory, deck_name, mint_deck_name, notetype_profiles, vocab_notetype,
      syllabifier_fn, function_words_path, numbers_path, spatial_path, pronouns_path, calendar_path,
      frequency_table_path, a1_morphology, phoneme_planner_factory, ipa_for_drill_phrases,
      lemma_table_path
en  English    locale=en-US  lemmatizer=lowercase  style=False
      (none: gloss language only)
no  Norwegian  locale=nb-NO  lemmatizer=stanza  style=True
      preprocessor_factory, deck_name, vocab_notetype, l2_scorer, syllabifier_fn, slow_word_fn,
      definite_form_fn, lemma_plausible_fn, multiword_traps_fn, variant_separator, infinitive_marker,
      gender_articles, noun_gender_fn, function_words_path, numbers_path, spatial_path, pronouns_path,
      calendar_path, breakdown_spans_fn, alignment, planner_example, wordfreq_lang, a1_morphology,
      lexicon_factory, phoneme_planner_factory, lemma_table_path, built_data
sl  Slovene    locale=sl-SI  lemmatizer=classla  style=True
      preprocessor_factory, deck_name, vocab_notetype, l2_scorer, syllabifier_fn, morphology_profile,
      function_words_path, numbers_path, spatial_path, pronouns_path, calendar_path, planner_example,
      wordfreq_lang, a1_morphology
tl  Tagalog    locale=fil-PH  lemmatizer=table  style=True
      preprocessor_factory, deck_name, mint_deck_name, notetype_profiles, vocab_notetype,
      syllabifier_fn, story_text_normalizer, verb_headword_fn, function_words_path, numbers_path,
      spatial_path, pronouns_path, calendar_path, planner_example, wordfreq_lang, a1_morphology,
      phoneme_planner_factory, ipa_read_in_voice_locale, phrase_match_exact_form, lemma_table_path
```

Reading it as design rather than inventory:

| | Slovene `sl` | Norwegian `no` | Tagalog `tl` | Cebuano `ceb` |
|---|---|---|---|---|
| Lemmatizer | classla (laptop only) | stanza, or its table in prod | table | table |
| Native TTS | Azure sl-SI (2 voices) | Azure nb-NO (3 voices) | Azure fil-PH (2 voices) | Gemini-TTS (no Azure voice exists) |
| Distinguishing feature | the plain baseline; planner example, A1 case/dual vocabulary | compounds, NST lexicon, gender articles, lemma guards | pronunciation-first syllabifier, affix-hyphen normalizer, root-keyed verbs | spelling-read IPA, drill-phrase IPA, frequency table |
| Mints into | the imported deck | the imported deck | `2. Pimsleur Tagalog::TunaTale` | `3. Bisaya::TunaTale` |
| Has `planner_example` | yes | yes | yes | deliberately no |

Why Cebuano has no planner example: `get_planner_example(code)` shows each target the example of the lowest-sorting *other* language that supplies one, so the example can never be in the target's own language (an example in the target gets copied into the model's reply, the planner-language-contamination class). `ceb` sorts before every other code, so giving it an example would replace the one every other language is shown.

Codes are ISO 639-1 where one exists and 639-3 otherwise (`ceb` is TunaTale's first three-letter code). The code is simultaneously the `X-TT-Language` header value (§2), the `DATABASE_URLS` key, the plugin directory name and `lessons.language_code`.

### 3.3 Voices: who speaks, in which language, how loud

A language's `Language.tts_voice_map` maps a *role* to a voice id. Roles are `narrator`, `female-1`, `female-2`, `male-1`, `male-2` (Norwegian adds `-3` and `-4`), the legacy `female`/`male` aliases, an optional `key-phrases` slot, and `narration`. Dialogue speakers are assigned roles by the story generator (§6), and `section_builder._resolve_voice` raises `ValueError` listing the known roles when a speaker has no entry, which is why an unmapped speaker is loud rather than silently read by the wrong voice.

```bash
cd backend && uv run python -c "
from app import languages as L
L.discover()
for code in ('sl', 'no', 'tl', 'ceb'):
    lang = L.get_language(code)
    print(f'== {code}  ({len(lang.tts_voice_gain_db)} voices have a measured gain)')
    for role, voice in lang.tts_voice_map.items():
        if role in ('female', 'male'):
            continue
        en = lang.tts_en_voice_map.get(role, '(narrator)')
        print(f'  {role:11} {voice:40} reads English as {en}')
"
```

```output
== sl  (5 voices have a measured gain)
  narrator    en-US-DavisMultilingualNeural            reads English as (narrator)
  narration   en-US-DavisMultilingualNeural            reads English as en-US-DavisMultilingualNeural
  female-1    sl-SI-PetraNeural                        reads English as en-US-AmandaMultilingualNeural
  female-2    en-US-EmmaMultilingualNeural             reads English as en-US-EmmaMultilingualNeural
  male-1      sl-SI-RokNeural                          reads English as en-US-AdamMultilingualNeural
  male-2      de-DE-FlorianMultilingualNeural          reads English as de-DE-FlorianMultilingualNeural
== no  (10 voices have a measured gain)
  narrator    en-US-DavisMultilingualNeural            reads English as (narrator)
  narration   en-US-DavisMultilingualNeural            reads English as en-US-DavisMultilingualNeural
  female-1    nb-NO-PernilleNeural                     reads English as en-US-NancyMultilingualNeural
  female-2    nb-NO-IselinNeural                       reads English as en-US-AmandaMultilingualNeural
  female-3    en-US-EmmaMultilingualNeural             reads English as en-US-EmmaMultilingualNeural
  female-4    en-US-ShimmerTurboMultilingualNeural     reads English as en-US-ShimmerTurboMultilingualNeural
  male-1      nb-NO-FinnNeural                         reads English as en-US-AdamMultilingualNeural
  male-2      en-US-DerekMultilingualNeural            reads English as en-US-DerekMultilingualNeural
  male-3      it-IT-GiuseppeMultilingualNeural         reads English as it-IT-GiuseppeMultilingualNeural
  male-4      en-US-DustinMultilingualNeural           reads English as en-US-DustinMultilingualNeural
== tl  (6 voices have a measured gain)
  narrator    en-US-DavisMultilingualNeural            reads English as (narrator)
  narration   en-US-DavisMultilingualNeural            reads English as en-US-DavisMultilingualNeural
  female-1    fil-PH-BlessicaNeural                    reads English as en-US-AmandaMultilingualNeural
  female-2    en-US-EmmaMultilingualNeural             reads English as en-US-EmmaMultilingualNeural
  male-1      fil-PH-AngeloNeural                      reads English as en-US-AdamMultilingualNeural
  male-2      en-US-SamuelMultilingualNeural           reads English as en-US-SamuelMultilingualNeural
  key-phrases de-DE-SeraphinaMultilingualNeural        reads English as (narrator)
== ceb  (4 voices have a measured gain)
  narrator    en-US-DavisMultilingualNeural            reads English as (narrator)
  narration   ceb-PH-OrusGemini                        reads English as en-US-DavisMultilingualNeural
  female-1    ceb-PH-KoreGemini                        reads English as en-US-EmmaMultilingualNeural
  female-2    ceb-PH-DespinaGemini                     reads English as en-US-NancyMultilingualNeural
  male-1      ceb-PH-CharonGemini                      reads English as en-US-LewisMultilingualNeural
  male-2      ceb-PH-OrusGemini                        reads English as en-US-DustinMultilingualNeural
```

Several design decisions are visible in that output.

**The voice id picks the vendor.** `app/audio/tts_router.py::RoutingTTSService` dispatches on the id's suffix: `...Neural` goes to Azure, `...Gemini` to the Gemini-TTS adapter. Gemini ids deliberately mirror Azure's `<locale>-<Name>` shape (`ceb-PH-KoreGemini`), so every registry invariant that slices a locale off a voice id works unchanged. Routing is by id, never fallback. Azure Neural HD (Dragon) voices are forbidden because they bill from the first character even on the free tier; `tests/test_languages.py::test_no_voice_map_names_a_paid_hd_voice` enforces it for every registered map (§7 prices renders).

**Azure ships fewer native voices than there are dialogue roles.** sl-SI has two voices (Petra, Rok) for four roles, fil-PH two (Blessica, Angelo), nb-NO three. The remaining roles are Multilingual Neural voices speaking the target language under a `<lang xml:lang="...">` wrapper, which is what `Language.tts_locale` exists for: it is handed to the adapter as `speak_locale`, and the adapter emits the wrapper only when the voice's own locale differs (a Multilingual voice left to auto-detect was measured misidentifying a real Slovene line). Casts were chosen by measurement, in this order: STT word error rate on A1 sentences, then median F0 distance between same-gender voices, then loudness. The pitch map does not transfer between languages (Dustin measured 160.0 Hz on Norwegian and 136.5 on Tagalog), so each language is measured on its own text. The Slovene comment block in `app/plugins/languages/sl/__init__.py` is the worked record of the method.

**Narration is not a character.** `narration` is a *dialogue* role the story writer uses for what no character says aloud ("They walked to the house."). Before it existed the model put narration in a character's mouth, and the transcript lettered it like a speaker. It is distinct from `narrator`, the structural role of titles and English translation lines that cues and key-phrase grouping key on. Narration is read by the narrator's voice, Davis, in both languages (the L2 under that language's `<lang>` wrapper) so it matches the titles. Cebuano is the exception: its L2 narration keeps a Gemini voice, because Davis cannot speak Cebuano, and Davis reads only its English. The transcript marks a narration line with an "N" chip rather than a speaker letter.

**English is read by a speaker-matched voice.** `Language.tts_en_voice_map` makes each speaker read their own English translation in the four translated sections, so a line's English sounds like its speaker. A role absent from the map falls back to the narrator, `NARRATOR_VOICE` (`en-US-DavisMultilingualNeural`, single-sourced in `app/models/language.py`; Davis replaced Guy on 2026-09-29 by ear). The translation phrase keeps `role="narrator"` because that role is structure (cue pairing, key-phrase groups); only its `voice_id` changes. Cebuano's four Gemini voices cannot read English, so each gets a pitch-matched Azure stand-in for its English lines (the live table above shows the current pairs).

**Loudness is a per-voice constant.** `tts_voice_gain_db` is keyed by voice id (two roles can share one voice) and pins each voice to -20.0 LUFS at assembly, after the content-addressed TTS cache, so a gain change never invalidates cached audio. Per-clip normalisation was rejected because spread within one voice exceeds the gap between voices. The table is looked up under the language *of the text*, which is why `Language.english()` carries its own table: an English translation line inside a Norwegian lesson resolves its gain against `en`, and for a while the narrator's gain never reached any English line because only the `no` table had it. A voice's English level is not its Norwegian level (Giuseppe is -0.7 dB in `nb`, +0.9 in `en`), so the tables are separate by design.

**The `key-phrases` slot.** Only Tagalog sets it (`de-DE-SeraphinaMultilingualNeural`): `section_builder` prefers `l2_voice_map["key-phrases"]` over `female-1` for the key-phrase section. Azure's fil-PH voices ignore `<phoneme>` outright (the same "salamat" is byte-identical under its own IPA and under the IPA of "kumusta"), so the breakdown is voiced by a Multilingual voice that honours IPA. §3.8 and §7 pick up the rest.

### 3.4 Syllabifiers

The Pimsleur backward buildup (§7) cuts a word into syllables and rebuilds it from the end. The algorithm is language-agnostic and lives in `app/generation/syllabify.py::syllabify`: find vowel nuclei, and for each consonant cluster between two nuclei give the following vowel the *longest suffix of the cluster that is a legal onset*; the rest closes the previous syllable. A language supplies only its phonotactics, as arguments: a vowel set, a set of valid onsets, optionally `diphthongs` (vowel+glide pairs that count as one nucleus, so `bøy|de`) and `initial_only_onsets` (clusters legal word-initially but never medially, like Norwegian `kn`). A word with one nucleus or none (Slovene syllabic-r `prst`) is a single syllable. Each plugin's `syllabify.py` is therefore short: tables plus a one-line wrapper registered as `syllabifier_fn`.

`get_syllabifier(code)` returns the plugin's function, or `app.generation.syllabify.default_syllabifier` (English-like vowels, no onset rules) for a language with none. The fallback is a quiet degradation on purpose: the breakdown is a pedagogical audio aid, so a reasonable cut beats an exception. It is also why Cebuano needed its own: the default cut `pan|ga|lan`, and `ng` is one consonant.

```bash
cd backend && uv run python -c "
from app.languages import get_syllabifier
cases = [('sl', ['dober', 'prosim', 'postaja', 'prst']),
         ('no', ['skygge', 'person', 'bøyde', 'kniv']),
         ('tl', ['siya', 'kailan', 'pangalan', 'problema']),
         ('ceb', ['pangalan', 'eskwelahan', 'balay']),
         ('en', ['pangalan'])]
for code, words in cases:
    f = get_syllabifier(code)
    print(f'{code:3}', '  '.join('|'.join(f(w)) for w in words))
"
```

```output
sl  do|ber  pro|sim  po|sta|ja  prst
no  skyg|ge  per|son  bøy|de  kniv
tl  siya  kai|lan  pa|nga|lan  pro|ble|ma
ceb pa|nga|lan  es|kwe|la|han  ba|lay
en  pan|ga|lan
```

Three things in that output are not accidents:

- **Tagalog is pronunciation-first.** `syllabify_tagalog_word` cuts where the word's Wiktionary reading puts the boundaries (`tl/pronunciation.py::resolve_reading`), because the key-phrase breakdown plays each piece as that reading's IPA and a caption must name the syllable its audio says: `siya` is *one* syllable, `[ˈʃa]`, though it is spelled with two vowels. Only when Wiktionary lacks the word, or the reading cannot be laid onto the spelling (`mga` is two syllables from one written vowel), does it fall to `syllabify_tagalog_spelling`, which is onset maximization tuned for audio rather than KWF hyphenation (`pro|ble|ma`, not `prob|le|ma`).
- **`y` and `w` are consonants** in Tagalog and Cebuano, so no diphthong set is needed (`ba|lay`), and `ng` is a single legal onset (`pa|nga|lan`). The glide rule and the loan-cluster onsets (`tra|ba|ho`, `es|kwe|la|han`) are the whole Cebuano design; no Cebuano pronunciation table is wired, so spelling is the whole answer.
- **Norwegian's orthographic cut is only a fallback.** `skyg|ge` above is the spelling rule's answer; the audio path asks the NST lexicon first (§3.7) and gets `sky|gge`. The invariant across languages is that **boundaries and phonemes for a word come from the same source and are never crossed**: a caption cut by spelling over audio synthesized from a reading with a different syllable count names a sound the audio does not make.

The syllabifier whose output `BreakdownChunk.span` indexes (§4.6) must be the one the planner uses, which is why `AlignmentConfig.syllabify_fn` is typed as "the SAME function" and may return `None` for a word whose pieces do not rejoin its surface form (meaning: do not slice).

### 3.5 Preprocessors and the story-text normalizer

Two hooks transform text, at different points in a lesson's life.

**`TextPreprocessor`** (`app/audio/preprocessing/base.py`) is a one-method protocol, `preprocess(text, section_type) -> str`, applied by the renderer to each phrase immediately before synthesis (and by `render_cost` so the price quote sees the same text). The registered `preprocessor_factory` must be a class, and `renderer.render_section` raises if a language has none. All four plugins ship the same twelve-line pass-through, a deliberate placeholder: the 2026-03 prototype carried a thousand-line Tagalog preprocessor (number clarification, abbreviation expansion), and the rewrite's rule is to add a replacement only when someone *hears* a TTS quirk. Slow-speed pauses are not a preprocessor job either: `section_builder` inserts `" ... "` between words at build time, so the slow and natural renders are the same kind of TTS request. Where a language needs to respell for the voice it uses `slow_word_fn` (Norwegian morpheme pauses) or the breakdown's spoken-form rules instead.

**`story_text_normalizer`** is applied once, at the *start* of `build_lesson_from_story` (`app/generation/story.py`), to the target-language fields of an LLM story on a deep copy: `scenes[*].lines[*].text`, `key_phrases[*].phrase` and `dialogue_glosses[*].word/.lemma`, never translations, titles or any English. Only Tagalog registers one. The LLM writes affixed forms with a U+2011 non-breaking hyphen (`mag‑kape`) where standard spelling is `magkape`; a hyphen after `mag nag pag mang nang` at a word start is correct only before a vowel or a capital (`mag-aral`, `nag-Facebook`), so the rule drops it before a lowercase consonant and ASCII-normalizes it elsewhere. Doing it once, before anything reads the story, means the lemmatizer, the cloze builder and the audio all see the same string.

```bash
cd backend && uv run python -c "
from app.languages import get_story_text_normalizer
n = get_story_text_normalizer('tl')
print(repr(n('Gusto kong mag‑kape at mag‑aral.')))
print(get_story_text_normalizer('no'), get_story_text_normalizer('ceb'))
"
```

```output
'Gusto kong magkape at mag-aral.'
None None
```

### 3.6 From words to cards: lemmas, headwords, notetypes, data files

Each plugin's remaining facets decide how a lesson's words become SRS cards (§8, §11).

**Lemmatizer wiring.** `lemmatizer_type` names the engine a language's own transcripts are analysed with (`classla`, `stanza`, `table`, else `lowercase`). It is a property of the *language*, not the process, because multi-language mode runs several languages in one process and a global singleton would analyse a Norwegian transcript with the Slovene model. `settings.lemmatizer_type` remains the global switch (`lowercase` turns all off; `table` is production and serves a language from its `lemma_table_path`). The table is a gzipped `surface, upos, lemma, is_default` TSV that reproduces the real model without PyTorch. Norwegian's is distilled from stanza, Tagalog's and Cebuano's from kaikki.org Wiktionary extracts by scripts under `backend/scripts/`. Slovene registers none, so on prod it lowercases; classla runs only where its optional dependency group is installed. Tables are built by `python -m app.build_data` (the Dockerfile and `switch.sh` run it), which also builds each plugin's `BuiltData` registration: a committed extract, a gitignored build beside it, and a function joining them, stamped with the extract's sha256 so a regenerated extract can never be served from a stale build. `--check` fails a start when something is missing. It exists because the NST lexicon had no registered builder and its absence silently re-enabled compound over-splitting on prod.

```bash
cd backend && uv run python -c "
from app.languages import all_built_data, all_lemma_table_paths
for a in all_built_data():
    print('built data :', a.extract.parent.parent.name, a.extract.name, '->', a.db.name)
for p in all_lemma_table_paths():
    print('lemma table:', p.parent.parent.name, p.name)
"
```

```output
built data : no nst_lexicon.tsv.gz -> nst_lexicon.sqlite3
lemma table: ceb cebuano_lemmas.tsv.gz
lemma table: no stanza_lemmas.tsv.gz
lemma table: tl tagalog_lemmas.tsv.gz
```

**Card headwords.** When TT mints a vocab card the front is not a bare lemma. `format_vocab_headword(lemma, upos, code)` applies the language's rules: a `verb_headword_fn` first (Tagalog fronts the actor-focus infinitive, because its table keys verbs by root: `kain` becomes `kumain`, `aral` becomes `mag-aral`, with a hand override file for homograph roots like `kita`), else `infinitive_marker` (Norwegian `å lyve`). Nouns get a display-only article through `get_gender_article`, from `gender_articles` and a gender that is the tagger's in-context answer when present, else `noun_gender_fn(lemma)`. That fallback exists because production's table lemmatizer tags no gender, so every Norwegian noun minted there once had a blank article. The article never enters `text`, which feeds the card GUID.

```bash
cd backend && uv run python -c "
from app.languages import format_vocab_headword, get_gender_article, card_surface_variants
for code, lemma in [('tl', 'kain'), ('tl', 'aral'), ('no', 'lyve'), ('sl', 'pes')]:
    print(f'{code:3} VERB {lemma:5} ->', format_vocab_headword(lemma, 'VERB', code))
for lemma in ('morder', 'hus', 'jente'):
    print(f'no  NOUN {lemma:7} article:', get_gender_article('no', '', lemma=lemma))
print('no front \"mot, imot\" ->', card_surface_variants('no', 'mot, imot'))
print('sl front \"mot, imot\" ->', card_surface_variants('sl', 'mot, imot'))
"
```

```output
tl  VERB kain  -> kumain
tl  VERB aral  -> mag-aral
no  VERB lyve  -> å lyve
sl  VERB pes   -> pes
no  NOUN morder  article: en
no  NOUN hus     article: et
no  NOUN jente   article: ei/en
no front "mot, imot" -> ['mot', 'imot']
sl front "mot, imot" -> ['mot, imot']
```

`variant_separator` (Norwegian `,`) lets one card front carry alternate accepted spellings (`mot, imot`); a language that sets nothing has single-surface fronts.

**Notetypes and mint decks.** `vocab_notetype` is the TT-managed notetype new cards are minted into (`app/cards/vocab_notetype.py`: one `VocabNotetype(name, l2_field, l2_css_class)` per language). `deck_name` is what sync *reads*, including all its subdecks; `mint_deck_name` is where TT-minted notes *go* when different. Tagalog and Cebuano mint into a `::TunaTale` subdeck, which must already exist in Anki, and failure to find it is loud. `l2_scorer` lets the importer pick the L2 field out of an unmarked Anki note by scoring how language-like a string is. Slovene and Norwegian have one (Norwegian's counts only `æøå`, after a Slovene scorer once ranked `snøm` at 0.0 and a headword became an example sentence); Tagalog and Cebuano cannot, because no letters distinguish them from English, so they register `notetype_profiles` instead, field-role maps by notetype name, and TT-minted notes are read by field name. Skipping that is a documented trap: the first sync after minting fails with "No L2 scorer".

**Static data per plugin** (all optional, all registered as paths): `style_notes` loaded from `data/style.md` into the story system prompt (its first job is keeping the *confusable* language out: Croatian/Serbian from Slovene, Nynorsk/Danish/Swedish from Bokmål, Tagalog from Cebuano, with `po`/`opo` and `ng` forbidden in the latter); `function_words_path` (a POS-first policy whose `include`/`exclude`/`glosses` lists decide which words become clozes rather than picture cards; Cebuano's `glosses` map pins `og` to "a / some (object marker)" because the lesson gloss pass kept calling it "and"); and four drawn-picture vocabularies, `numbers_path`, `spatial_path`, `pronouns_path`, `calendar_path` (§11). Calendar data carries `week_start` because which day opens a week is a local custom. Also `wordfreq_lang` (Tagalog `fil`, Norwegian `nb`) or a shipped `frequency_table_path` where wordfreq has no coverage (Cebuano, from FineWeb-2 news), used to rank new-card candidates, and `a1_morphology`, the per-language bundle mapping a UD analysis to a TT feature string, the whitelist of A1 features, and hint rendering. Slovene registers the shared default vocabulary (person/number/case); Norwegian adds definiteness, tense and adjective agreement; Tagalog and Cebuano turn affixed verb forms into inflection clozes on the root card. A language with no bundle gets **no** morphology, never Slovene's (Tagalog once silently inherited Slovene's case vocabulary).

### 3.7 Norwegian: compounds, the NST lexicon, and lemma guards

Norwegian is the language with the deepest plugin, because it is a compounding language and the generic per-syllable buildup reads compounds wrong. It is also the only plugin with `breakdown_spans_fn`, `alignment`, `lexicon_factory`, `slow_word_fn`, `definite_form_fn`, `lemma_plausible_fn` and `multiword_traps_fn`.

**Compound breakdown** (`no/norwegian_breakdown.py`). `segment_compound` cuts a word into frequency-ranked free stems before the Pimsleur steps are built. Its guards are the knowledge: a closed-class stoplist (so `sommer` never splits into `som|mer`), s-joint and geminate handling (`busstasjon` segments as `bus|stasjon` but is *spoken* `buss, stasjon`), initial-only homograph guards, preposition first-elements kept productive (`etter|forskning`), and derivational suffixes treated as syllable-level units rather than free parts. Inflections are peeled whole (`-ende` participle, `-ere` plural, `-ens` genitive) and folded onto their stem; each of those was added after a measured over-split such as `til|svar|ende`. `build_norwegian_breakdown_spans` returns `BreakdownChunk` objects with provenance (§4.6); `slow_norwegian_word` is the slow-speed respelling. The human-confirmed linguistic decisions are checked by ear through a preview CLI, `python -m app.plugins.languages.no.breakdown_preview <word>`, which prints segments, slow pronunciation and the buildup steps and can render the audio.

**The NST lexicon** (`no/lexicon.py`, `no/lexicon_syllables.py`, `no/sampa.py`). The National Library of Norway's pronunciation dictionary (CC0) ships as a committed 4.6 MB gzipped extract, `nst_lexicon.tsv.gz`, built into a gitignored SQLite database by the `BuiltData` mechanism above. `NstLexicon.resolve(word, upos)` returns a typed `LexiconResolution` whose outcome is `RESOLVED`, `AMBIGUOUS_NO_POS`, `AMBIGUOUS_POS_DIDNT_HELP` or `ABSENT`; the enum exists because a bare `None` made an encoding bug look like a coverage gap. Resolution reduces candidates to minimum certainty first, then lets sentence-context UPOS pick among survivors, and never guesses between readings it cannot separate (`seg` is /sæi/ as a pronoun and /seːɡ/ as a verb). `no/sampa.py` converts the lexicon's X-SAMPA to IPA.

Syllable boundaries then come from the same reading as the phonemes. `lexicon_syllables.py` infers the letter-to-phone correspondence by least-cost alignment so phoneme-space boundaries can be cut into the spelling (`sky|gge`, `ha|dde`, `pe|rson`), and when readings disagree it picks the one that elides least. Compound parts resolve as the words they are, and secondary stress in the lexicon is the signal that a word is genuinely a compound. §7 covers how `phoneme_plan.py` and the aligner use this; the plugin's contribution is that boundaries and phonemes cannot come from different sources.

**Cards and lemmas** (`no/noun_gender.py`, `no/morphology.py`, `no/multiword.py`). Noun gender comes from `noun_genders.tsv.gz` derived from the NST data (needed because the production lemmatizer is a lookup table). `is_lemma_plausible(surface, lemma)` rejects stanza's occasional non-word lemmas (`trøtt` to `trø`, `snømenn` to `snøm`, `gluten` to `glute`) before they become card fronts; `None` here means "cannot tell, keep the lemma", never a guess. `is_definite_form` lets generated glosses agree with the headword's definiteness (`morder` is not glossed "the murderer"), and `multiword_traps.txt` lists fixed expressions whose second word must not be carded alone (`i går` is not the verb `gå`). The plugin's cast is the largest: eight distinct dialogue voices, widened after measuring that male-1 and male-2 were 4.9 Hz apart.

### 3.8 Tagalog and Cebuano: the Philippine family

Tagalog was the pathfinder; Cebuano reused its core fixes and was wired in a day. What they share and where they differ:

- **Table lemmatizer and root-keyed verbs.** Both lemmatize from a kaikki-derived table with no NLP dependency group. A verb's table entry is its *root*; `phrase_match_exact_form` (Tagalog only) stops a multi-word card from matching a lesson phrase on a different form of the same verb (`Magdadala ako` is not graded as `magdala ako`), while single words still resolve by root. Cebuano's `closed_class.tsv` hand-curates readings the table gets wrong (`ang`, `mga`, `ka`, `si`, `ni`, `og`), and `spelling_variants.tsv` folds variants to one headword (`puwede` to `pwede`).
- **Voices that cannot be asked for IPA the usual way.** Two facets handle two failure modes. `ipa_read_in_voice_locale` (Tagalog) means the locale ignores `<phoneme>`, so IPA must reach the voice unwrapped, read by its own front end. `ipa_for_drill_phrases` (Cebuano) means a multi-word drill step should reach the voice with per-word IPA. Gemini rejects SSML `<phoneme>`, so IPA is delivered as a prompt instruction ("say only this one Cebuano syllable") whose language *name* is recovered from the voice's locale by `language_name_for_tts_locale`. That function returns `None` for a locale two languages declare, because a prompt that names neither beats one that misnames. The flag is per-language because the channel is the adapter's: on Azure a per-word map would re-speak every word of a drill. Dialogue stays plain for everyone.
- **IPA sources.** Tagalog's planner (`tl/phoneme_plan.py`) hands the key-phrase voice the chosen Wiktionary reading, giving whole words IPA too, since a German voice reading Tagalog text guesses; an unlisted word is read letter by letter. Cebuano has no lexicon, so `ceb/phoneme_plan.py` reads IPA off the spelling, with the hyphen mapped to a glottal stop (`kanus-a`). The shared logic is a core class, `app/audio/spelled_ipa.py::SpelledPhonemePlanner`, while the Latin letter table is *duplicated* in each plugin, since plugins cannot import each other (§3.9).
- **Style guards.** Tagalog: no Taglish, everyday English and Spanish loans kept, `po` sparingly, Spanish-derived numbers for clock time and prices. Cebuano's drift list marks Tagalog words as contamination (`hindi` becomes `dili`, `ano` becomes `unsa`).
- **Data for "what to learn next."** Cebuano ships a Fluent Forever base list, a reviewed next-words queue and a root-keyed frequency table. A script only *proposes* new words; a human reviews each root against sentences because about one in five proposals had the wrong sense.

### 3.9 Keeping it honest: the gates and adding a language

Two CI-enforced checks make "just import the Slovene thing" a build failure rather than a review comment, and a test proves a plugin can disappear.

```bash
cd backend
uv run python scripts/check_language_literals.py && echo "language-literal gate: clean"
uv run python scripts/check_plugin_imports.py && echo "plugin-import gate: clean"
grep -n "^def test_" tests/test_plugin_isolation.py | sed 's/(.*//'
```

```output
language-literal gate: clean
plugin-import gate: clean
53:def test_sl_only_direct_import
69:def test_no_only_direct_import
85:def test_tl_only_direct_import
101:def test_ceb_only_direct_import
122:def test_zero_plugins_hard_fail
```

- **`backend/scripts/check_language_literals.py`** is an AST walk of `backend/app/**` that fails on a *value* literal (not a docstring) that is a bare language code, an English or native language name (`slovene`, `norwegian`, `tagalog`, `cebuano`, `binisaya`, ...), an engine name (`classla`, `stanza`), or a voice id (`<locale>-<Name>Neural` or `...Gemini`), outside the allowlisted registry and plugin modules in `tests/language_literals_allowlist.txt`. There is no grandfather ledger; the allowlist is deliberately coarse and additions need sign-off. Adding a language means adding its names, native name included, to the checker.
- **`backend/scripts/check_plugin_imports.py`** forbids core from importing a concrete `app.plugins.*` module. The only sanctioned exceptions are the registry's own discovery import of the namespace package and function-level imports of `app.plugins.anki_sync` in `app/api/anki.py` and `app/api/admin.py`.
- **`tests/test_plugin_isolation.py`** copies `app/` to a temp directory, deletes plugin folders, and runs a subprocess: each plugin must work alone, and zero plugins must hard-fail. It also encodes the rule that *a plugin may not import another plugin*. When two related languages share logic, it moves to core, which is why `app/audio/spelled_ipa.py` exists.

The working rule for any new language: if wiring it seems to need `if code == "xx"` in core, add a registry facet instead. The minimum skeleton is a plugin `__init__.py` with a `Language(...)`, a pass-through preprocessor, one `VocabNotetype` line, the literal-gate names, registry tests, and the per-language database wiring in `docker-compose.yml`, `data-transfer.sh`, `switch.sh` and the env examples (§15). The schema is created on first open, so a new database file needs no manual step. [docs/adding-a-language.md](adding-a-language.md) is the checklist with measured costs, the facet-by-language matrix, the voice-casting method and the gotchas each language hit; the quick rule for voices is to price the first render before running it (`backend/scripts/report_render_cost.py`, §7).

## 4. Domain Models

`backend/app/models/` is the vocabulary the rest of the system speaks: a language, a lesson made of sections made of phrases, a curriculum of days, a collocation with two independently scheduled directions, and the enums that steer generation. The modules are plain dataclasses and enums with small serialisation helpers: no database, no network, no framework. Generation (§6) builds these objects, storage (§6) and the SRS database (§9) persist them, the audio pipeline (§7) renders them, and the API layer (§12) projects them into separate Pydantic response models (`app/api/models.py`, `app/api/_serializers.py`), which are a different thing: the dataclasses here are never exposed directly.

### 4.1 What belongs here, and what does not

The rule is "no I/O", which is why these modules are cheap to test and safe to import from anywhere. Two honest qualifications. `srs_item.py` imports one pure helper from `app/srs/anki_mirror/rollover.py` (`due_at_rollover_utc`) to turn a date into the learner-day boundary, so the layer is pure but not leaf-only. And several fields carry rationale that only makes sense with a neighbouring chapter (`DirectionState.anki_card_mod` is Anki's tiebreak key, §9); the models carry them because the models are where the SRS engine and the sync engine meet.

```bash
cd backend && ls app/models/*.py
```

```output
app/models/__init__.py
app/models/breakdown.py
app/models/curriculum.py
app/models/language.py
app/models/lesson.py
app/models/srs_item.py
app/models/strategy.py
app/models/syntactic_unit.py
```

One shared helper lives beside them in `app/common/titles.py::strip_day_prefix`, used by both `Lesson` and `CurriculumDay` (§4.4).

### 4.2 `Language`

`app/models/language.py::Language` is a dataclass: ISO `code`, English `name`, `native_name`, `script`, `tts_voice_map` (role to voice id), `tts_en_voice_map` (role to the voice that reads that role's English), `tts_voice_gain_db` (per-voice loudness) and `tts_locale`. It has exactly one factory, `Language.english()`; every other language is constructed inline by its own plugin at registration (§3), so core never names one. `NARRATOR_VOICE` is single-sourced in the same module and used as the default `narrator_voice` of every `Lesson`.

The reason `english()` carries a full voice map and gain table, although English is never a target, is that English phrases appear in every lesson (titles, translations, glosses) and the renderer resolves an English phrase's voice and gain against `en`, whatever language the lesson teaches.

```bash
cd backend && uv run python -c "
from app.models.language import Language, NARRATOR_VOICE
en = Language.english()
print('narrator :', NARRATOR_VOICE)
print('locale   :', en.tts_locale)
print('roles    :', sorted(en.tts_voice_map))
print('gain rows:', len(en.tts_voice_gain_db), '(English-reading voices)')
"
```

```output
narrator : en-US-DavisMultilingualNeural
locale   : en-US
roles    : ['female', 'female-1', 'female-2', 'male', 'male-1', 'male-2', 'narrator']
gain rows: 13 (English-reading voices)
```

### 4.3 `Lesson`, `Section`, `Phrase`

A lesson is the unit of audio. `app/models/lesson.py::Lesson` holds a `title`, `language_code`, the `narrator_voice`, a list of `Section`s, the `key_phrases` (as `KeyPhraseInfo(phrase, translation)`, kept on the lesson so SRS registration can be deferred), and a free-form `generation_metadata` dict that accumulates things like `sentence_translations` and the token-level glosses (§6). A `Section` is a `SectionType` plus an ordered list of `Phrase`s, and validates that its type is the enum rather than a string.

A `Phrase` is one thing to synthesize: `text`, `voice_id`, `language_code` (the *text's* language, which is what decides pause rules and gain tables), SSML-style `rate`/`pitch`/`volume`, a `role` (`narrator`, `female-1`, ...), the part-of-speech tag `upos`, and two breakdown provenance fields, `source_word` and `syllable_span`, explained in §4.6. `role` is structure and `voice_id` is presentation: an English translation keeps `role="narrator"` even when a speaker-matched English voice reads it (§3.3), so cue pairing and key-phrase grouping never depend on which voice was chosen.

`SectionType` has seven members, and the Pimsleur design is visible in their names: one drill section, then the dialogue at natural speed, then slow ("enunciated") and bilingual variants in both orders. The narrator announces each section by its display title, which lives in `app/generation/section_builder.py::SECTION_TITLES` rather than on the enum, because it is spoken content and changes with listening tests (the slow pass is now "Enunciated" because it is respelled speech with pauses, not a rate change).

```bash
cd backend && uv run python -c "
from app.models.lesson import SectionType
from app.generation.section_builder import SECTION_TITLES
for st in SectionType:
    print(f'{st.name:20} {st.value:20} {SECTION_TITLES[st]!r}')
"
```

```output
KEY_PHRASES          key_phrases          'Key Phrases'
NATURAL_SPEED        natural_speed        'Natural Speed'
SLOW_SPEED           slow_speed           'Enunciated'
TRANSLATED           translated           'English After'
SLOW_TRANSLATED      slow_translated      'Enunciated, English After'
EN_TRANSLATED        en_translated        'English Before'
SLOW_EN_TRANSLATED   slow_en_translated   'Enunciated, English Before'
```

Persistence is JSON: `Lesson.to_json()` / `from_json()` round-trip through a string stored in the lesson row (§6). The one wrinkle is that JSON has no tuples, so `syllable_span` goes out as a list and `_phrase_from_dict` restores the tuple on the way back, which is what keeps `Phrase` equality exact after a reload. `from_json` defaults `narrator_voice` for blobs that predate the field, and a stored lesson pins a *resolved* `voice_id` per phrase, so changing a language's voice map affects only lessons generated afterwards. Titles are normalised on construction (§4.4).

```bash
cd backend && uv run python -c "
from app.models.lesson import Lesson, Section, SectionType, Phrase, KeyPhraseInfo
lesson = Lesson(
    title='Day 3: At the Cafe', language_code='no',
    sections=[Section(SectionType.KEY_PHRASES,
        [Phrase('en kaffe', 'nb-NO-FinnNeural', 'no', source_word='kaffe', syllable_span=(0, 2))])],
    key_phrases=[KeyPhraseInfo('en kaffe', 'a coffee')])
back = Lesson.from_json(lesson.to_json())
print('title after strip_day_prefix:', repr(back.title))
print('span restored as tuple      :', back.sections[0].phrases[0].syllable_span)
print('round trip equal            :', back == lesson)
"
```

```output
title after strip_day_prefix: 'At the Cafe'
span restored as tuple      : (0, 2)
round trip equal            : True
```

`extract_sentence_translations_from_translated` in the same module recovers `{L2 sentence: English}` from a stored lesson's `TRANSLATED` section by pairing each L2 phrase with the English phrase that follows it. It exists to backfill `generation_metadata['sentence_translations']` on lessons generated before that field existed, and it is the reason the section's L2/English alternation is a contract rather than an accident.

### 4.4 `Curriculum` and `CurriculumDay`

A curriculum is a plan: `id`, `topic`, `language_code`, `cefr_level`, a list of `CurriculumDay`s, and a `metadata` dict. A day carries `title`, `focus`, a list of target `collocations`, a `learning_objective` and optional `story_guidance`. It serialises with `to_json` / `from_json` (stored by `ContentStore`, §6).

**`day` is a key, not an ordinal.** Lessons, pipeline jobs and planner feedback all reference `CurriculumDay.day`, so deleting a day leaves a permanent gap (`[1, 2, 3, 4, 6]`), and nothing renumbers. Anything the learner sees is therefore derived from `Curriculum.day_positions()`, which maps each key to its 1-based position in sorted order. Showing the raw key would produce "Day 6" after "Day 4".

**Titles never carry a day number.** The planner and story LLMs habitually write "Day 5: The Trail Ends in the Garden", and that embedded number drifts from both the key and the position the UI shows (a day keyed 6 could render as "Day 6 · Day 5: ..."). `app/common/titles.py::strip_day_prefix` removes a leading `Day N` followed by punctuation, requiring the punctuation so "Day 5 Reasons to Visit" survives, and leaves a title that is *only* a prefix untouched. It runs in `__post_init__` of both models, which also cleans titles already persisted, since `from_json` rebuilds every day through the constructor.

**Plan-level settings live in `metadata`, deliberately.** Review pressure (`metadata["review_pressure"]`) and the words each exported prompt asked for (`metadata["review_requests"]`) are stored there instead of on `CurriculumDay` because a column would need a migration and would make the dial a per-day decision the planner takes, changing the planner prompt and invalidating its cassettes (§5) for a feature it never asked for. Two traps are documented in the code and worth knowing here:

- `Curriculum.review_pressure(override)` treats `None` as "unspecified", falling through to the stored setting, then to NATURAL. Conflating `None` with NATURAL silently disables the setting at every call site that passes nothing, which is all of them by default. An unrecognised stored value degrades to NATURAL rather than raising, because `metadata` is a free-form JSON blob that older builds and hand edits can write.
- `review_requests` is keyed by `str(day)`. JSON turns int dict keys into strings, so an int key would work in memory and silently find nothing after one reload. An empty `review_request(day)` means *unmeasurable* (never exported, or exported before the field existed), not "nothing was requested".

```bash
cd backend && uv run python -c "
from app.models.curriculum import Curriculum, CurriculumDay
days = [CurriculumDay(d, f'Day {d}: Title {d}', 'focus', ['x'], 'objective') for d in (1, 2, 3, 4, 6)]
c = Curriculum(id='c1', topic='cafe', language_code='no', cefr_level='A1', days=days)
print('keys     :', [d.day for d in c.days])
print('positions:', c.day_positions())
print('title    :', repr(c.days[4].title))
print('pressure :', c.review_pressure().name, c.review_pressure('INSISTENT').name, c.review_pressure('bogus').name)
c.metadata['review_pressure'] = 'BALANCED'
print('stored   :', c.review_pressure().name, '| explicit override wins:', c.review_pressure('NATURAL').name)
c.record_review_request(3, ['kaffe'])
c2 = Curriculum.from_json(c.to_json())
print('request  :', c2.review_request(3), c2.review_request(4), '(empty = unmeasurable)')
"
```

```output
keys     : [1, 2, 3, 4, 6]
positions: {1: 1, 2: 2, 3: 3, 4: 4, 6: 5}
title    : 'Title 6'
pressure : NATURAL INSISTENT NATURAL
stored   : BALANCED | explicit override wins: NATURAL
request  : ('kaffe',) () (empty = unmeasurable)
```

### 4.5 `SyntacticUnit`, `SRSItem` and directions

The unit of vocabulary is a **collocation**, not a word: a target-language chunk (`text`) with its translation. `app/models/syntactic_unit.py::SyntacticUnit` carries the text and translation, `word_count` (at least 1) and `difficulty` (1 to 5), both validated in `__post_init__`; a `source` (`corpus`, `llm`, `anki`, `user`, `test`); the `lemma`, the Anki-compatible `guid` and a `disambig_key` (so homographs get separate identities); and presentation fields: a display-only `article` (never part of `text`, which feeds the GUID), `grammar`, `note`, the `source_sentence` and its translation with the lesson and line it came from, and `extras`. The word-count upper bound was removed after it dropped legitimate phonics cards whose front is a question of more than eight words; only the lower bound filters out empty extractions.

`extras` is a tuple of `BackField(label, html, tier)`: rich back-of-card material (IPA, inflections, a dictionary entry) pulled from an Anki notetype's secondary fields. Display-only and already sanitised at extraction. `tier` is `summary` (always inline), `details` (in a collapsed disclosure) or `deep` (its own nested disclosure). `serialize_extras` / `deserialize_extras` store them as JSON, and the reader is tolerant by design: blank, malformed or wrongly-shaped input yields `()`, because a bad row must never break a card render.

`card_type` is `"vocab"` or `"cloze"`. A cloze unit is production-only, because sync writes it through Anki's built-in single-template Cloze notetype (§11).

**Two directions, scheduled independently.** `app/models/srs_item.py::SRSItem` wraps a unit with a `directions` dict of `DirectionState`, one per `Direction`: `RECOGNITION` (L2 to L1, Anki card ordinal 0, which powers lesson transcripts) and `PRODUCTION` (L1 to L2, ordinal 1, the production drill). Each direction has its own FSRS memory (`stability`, `difficulty`, `reps`, `lapses`), its own `state`, and its own `due_at`. The set of directions is "whatever rows exist": a recognition-only imported deck simply has no production row, and a cloze item has only a production one. `due_at` is a UTC datetime, NOT NULL for every state including new, the single source of truth for when a direction is due.

`SRSState` has seven members (`new`, `learning`, `review`, `relearning`, `suspended`, `buried`, `known`), `Rating` is Anki's four buttons (`AGAIN`=1 to `EASY`=4), and `DirectionState` also carries the bookkeeping the sync engine needs, grouped by purpose:

- **Anki identity and ordering**: `anki_card_id`, `anki_due`, `anki_card_mod` (the secondary key Anki sorts by under retrievability ordering).
- **Burying**: `bury_kind` is `user` (manual, persists across the day rollover) or `sched` (a sibling bury, released at the next rollover).
- **Dirty tracking for push**: `dirty_fsrs`, `last_synced_at`, `last_rating`, `left`, and a snapshot of the prior grade (`prior_state`, `prior_left`, `prior_stability`) captured before each `replace` so a correct Anki revlog row can be built at push time and cleared once pushed.
- **"New today" accounting**: `introduced_at` is set once, on the first NEW-to-non-NEW transition, to mirror Anki's counter, which increments on the first-grade event only.
- **A TT-only force flag**: `fsrs_force_next` makes the next push write stability and difficulty into Anki even for a state that would not normally, so a restored card keeps its pre-known memory.

`RevlogRow` is the frozen model of the `tt_revlog` table, mirroring Anki's revlog schema; `budget_neutral` marks a lesson "check your work" re-grade of a card the listen already reviewed that day, which still replays through FSRS and syncs as an ordinary review but is not charged against the day's review budget twice.

The constructor accepts two styles. The two-direction form passes `directions` explicitly. The flat legacy form passes `due_date`, `stability`, `state` and so on, which populate recognition and seed production with defaults, and flat properties (`item.state`, `item.reps`, `item.due_date`) read and write the recognition direction (the production direction for a cloze item). They are compatibility shims for callers that predate the two-direction schema; new code addresses `directions[Direction.X]`. How these fields map to database columns (`DIRECTION_FIELDS`), scheduling and queue rules are §9; which fields sync pushes and pulls are §10.

```bash
cd backend && uv run python -c "
from app.models.srs_item import SRSItem, SRSState, Direction, DirectionState
from app.models.syntactic_unit import SyntacticUnit
item = SRSItem(SyntacticUnit('en kaffe', 'a coffee', 2, 1, 'llm'))
print('directions   :', {d.name: s.state.value for d, s in item.directions.items()})
print('flat shim    :', item.state.value, item.reps, item.due_date == item.directions[Direction.RECOGNITION].due_at.date())
cloze = SRSItem(SyntacticUnit('x', 'y', 1, 1, 'llm', card_type='cloze'),
    directions={Direction.PRODUCTION: DirectionState(Direction.PRODUCTION, item.directions[Direction.PRODUCTION].due_at)})
print('cloze items  :', [d.name for d in cloze.directions], '-> shims read', cloze._rec.direction.name)
print('states       :', [s.value for s in SRSState])
try:
    SyntacticUnit('a', 'b', 0, 1, 'test')
except ValueError as e:
    print('validation   :', e)
"
```

```output
directions   : {'RECOGNITION': 'new', 'PRODUCTION': 'new'}
flat shim    : new 0 True
cloze items  : ['PRODUCTION'] -> shims read PRODUCTION
states       : ['new', 'learning', 'review', 'relearning', 'suspended', 'buried', 'known']
validation   : word_count must be ≥ 1, got 0
```

### 4.6 `ContentStrategy`, `ReviewPressure` and `BreakdownChunk`

**`ContentStrategy`** (`app/models/strategy.py`) selects which story prompt template a lesson is generated from (`prompts.get_strategy_prompt`):

| Member | Meaning |
|---|---|
| `WIDER` | new scenarios from familiar vocabulary (breadth) |
| `DEEPER` | enhance an existing scenario with more advanced L2 (depth); the prompt is given the previous stored day's real dialogue as its source |
| `REVIEW` | no scenario at all: the content *is* the learner's decaying vocabulary |

`REVIEW` is a strategy, not a setting, because `WIDER` and `DEEPER` both take a theme, a focus and story guidance and a review session has none to trade against. A review session is generated from no curriculum and no day (§6). The dated 8-new/2-review versus 3-new/7-review ratios and the weighted collocation-scoring config of the prototype no longer exist at HEAD; which review words appear is chosen by the learner's SRS state and a pressure dial.

**`ReviewPressure`** (`NATURAL`, `BALANCED`, `INSISTENT`) sets how hard a story prompt pushes to use those review words. It is a range from "today's thematic unity" to "aggressively incorporate the words at the cost of theme, but not coherence". Two properties matter. `NATURAL` is the default and reproduces the pre-feature behaviour: words are *offered*, and declining one is a correct answer, since the user's phrasing was "how/if to incorporate" and a low setting that cannot decline is not the low end of anything. And coherence is the **floor under the whole scale, not the top of it**: theme gives way first, and a scene that stops making sense is a failure at every setting, `INSISTENT` included. The dial reaches the prompt via the request, `Curriculum.review_pressure()` (§4.4) or the forced `INSISTENT` of a review session (§6).

**`BreakdownChunk`** (`app/models/breakdown.py`) is one rung of a Pimsleur backward buildup and where its audio comes from: `text` (what to say), `source_word` (the whole word to render and cut from) and `span` (a half-open range of **raw** syllable indices of that word). `text` is the *spoken* form and is not always the raw substring: a fragment the voice would misread is respelled for isolated synthesis (`bus` to `buss`, geminate lengthening `et` to `ett`). `span` indexes the raw syllables. They disagree on purpose: `text` feeds the fallback TTS path, `span` feeds slicing and IPA planning. Both provenance fields are `None` when a chunk cannot be sliced (a multi-word partial, a one-syllable word, a word whose syllables do not rejoin its surface form), and `None` always means "synthesize `text`". `section_builder` copies the two fields onto the `Phrase`s it emits (`Phrase.source_word`, `Phrase.syllable_span`), which is how provenance survives into the stored lesson JSON, and the renderer's IPA planner takes `(source_word, span)` rather than text (§7).

Languages without their own spans function (everything but Norwegian, §3.7) get the generic builder, `section_builder._generic_breakdown_spans`: per word, right to left, one syllable at a time, with the growing tail between (`ngalan` after `nga`). Sentence punctuation is stripped before syllabifying so a fragment cut from `lubong?` does not carry a question into its audio, and the hyphen and apostrophe are kept because in Tagalog and Cebuano they are part of the word (a glottal stop, a contraction). A losslessness check (pieces must rejoin the lowercased word) decides whether a word is sliceable at all.

```bash
cd backend && uv run python -c "
from app.generation.section_builder import build_word_breakdown_spans
for code, phrase in [('sl', 'hvala lepa'), ('ceb', 'pangalan')]:
    print(code, repr(phrase))
    for c in build_word_breakdown_spans(phrase, code):
        print(f'   {c.text!r:14} source={c.source_word!r:12} span={c.span}')
"
```

```output
sl 'hvala lepa'
   'hvala lepa'   source=None         span=None
   'pa'           source='lepa'       span=(1, 2)
   'le'           source='lepa'       span=(0, 1)
   'lepa'         source='lepa'       span=(0, 2)
   'la'           source='hvala'      span=(1, 2)
   'hva'          source='hvala'      span=(0, 1)
   'hvala'        source='hvala'      span=(0, 2)
   'hvala lepa'   source=None         span=None
ceb 'pangalan'
   'pangalan'     source=None         span=None
   'lan'          source='pangalan'   span=(2, 3)
   'nga'          source='pangalan'   span=(1, 2)
   'ngalan'       source='pangalan'   span=(1, 3)
   'pa'           source='pangalan'   span=(0, 1)
   'pangalan'     source=None         span=None
```

## 5. The LLM Layer

TunaTale asks a language model for four kinds of text: curriculum plans, lesson stories, per-word glosses and the short helper calls behind cards (translations, cloze sentences, picture queries, lemma disambiguation). Every one of them goes through `app.state.llm`, a single object that is either a real Groq client or a cassette wrapper around one. This chapter is that object: how it talks to Groq, how it keeps a free-tier account alive, how its spend is metered and attributed, how tests replay it, and how its failures reach HTTP. The callers are in §6 (planner, story, glossing) and §11 (cards and cloze); the budget it enforces is shown to the learner by the frontend (§13).

### 5.1 Shape of the layer

There is one real client class, `app/llm/client.py::LLMClient`, and one wrapper, `app/llm/cassette.py::CassetteLLMClient`, which exposes the same `complete()` coroutine. The lifespan hook in `main.py` builds both and publishes the result on `app.state.llm`; the planner and story generator are constructed around that same object. Production refuses to boot unless `llm_mode == "live"` (`config.py::prod_profile_problems`), so a cassette can never answer a real user.

```bash
sed -n '/real_client = LLMClient(/,/llm = real_client/p' backend/app/main.py
```

```output
    real_client = LLMClient(
        groq_api_key=settings.groq_api_key,
        groq_model=settings.llm_model,
        groq_extra_body_params=reasoning_params_for_model(settings.llm_model),
        usage_ledger=UsageLedger(settings.llm_usage_ledger_path),
        # Wrapped, not raw: LLMClient warns on some failure paths and not
        # others (a hard failure with allow_fallback=False raises silently), and
        # every outcome passes through this callback. See llm_failure_mirror.
        on_call=llm_failure_mirror(activity_log.record_llm_call),
        allow_fallback=settings.llm_allow_fallback,
        tokens_per_day_limit=settings.groq_tokens_per_day_limit,
        requests_per_day_limit=settings.groq_requests_per_day_limit,
    )
    _BACKEND_DIR = Path(__file__).parent.parent
    cassette_path = _BACKEND_DIR / "tests/cassettes/e2e.json"

    # Wrap with cassettes unless explicitly in live mode
    if settings.llm_mode != "live":
        llm = CassetteLLMClient(
            mode=settings.llm_mode,
            cassette_path=cassette_path,
            real_client=real_client,
            miss_log=settings.llm_cassette_miss_log,
        )
    else:
        llm = real_client
```

Everything that calls `complete()` is a *call site* and must name itself with a `call_site=` label (§5.6). The helpers around the client are small and fail-soft: `llm/translate.py::translate_term` and `generate_word_gloss` return an empty string rather than raise, because a card must still be creatable when the model is down. `llm/cloze_quality.py` is the blind fill-in judge used by the cloze tier; it is covered in §11 and only its failure convention matters here: an LLM error yields the verdict `"unknown"`, never a guess.

### 5.2 `LLMClient.complete`

The default model is `openai/gpt-oss-120b` (`GROQ_DEFAULT_MODEL`; `llama-3.3-70b-versatile` was deprecated by Groq on 2026-06-30). It is a reasoning model, and that shapes the request body. At the default effort it spends the whole completion budget on hidden reasoning tokens and returns empty `content`, so `reasoning_params_for_model` pins `reasoning_effort="low"`, and the presence of any extra params flips the body from `max_tokens` to `max_completion_tokens`. A model-only construction derives these itself, so a caller cannot forget them.

```bash
cd backend && uv run python -c "
from app.llm.client import GROQ_DEFAULT_MODEL, reasoning_params_for_model
print(GROQ_DEFAULT_MODEL)
print(reasoning_params_for_model(GROQ_DEFAULT_MODEL))
print(reasoning_params_for_model('llama-3.3-70b-versatile'))
from app.config import settings
print(settings.llm_model, settings.groq_tokens_per_day_limit, settings.groq_requests_per_day_limit, settings.llm_allow_fallback)
"
```

```output
openai/gpt-oss-120b
{'reasoning_effort': 'low'}
None
openai/gpt-oss-120b 200000 1000 False
```

`complete(prompt, system_prompt, temperature, max_tokens, *, now, call_site)` behaves as follows:

- **Groq first, and by default only Groq.** The fallback chain (an injected `fallback_client`, then local Ollama) runs only when `allow_fallback` is true, and `llm_allow_fallback` defaults to false. This reverses an earlier design: a silent Ollama answer used to come back as junk JSON, surfacing as a `StoryGenerationError` far from the cause. Now a hard Groq failure raises `LLMError` carrying an `attempts` list (`provider`, `model`, `status`, `error`, `latency_ms`).
- **Output hygiene.** `<think>...</think>` blocks are stripped. `last_provider`, `last_finish_reason` and `last_usage` record the latest success; callers use `finish_reason == "length"` to tell truncation from junk (the story retry in §6.5 depends on it).
- **Failure taxonomy.** Transport errors (timeout, DNS, refused) become `LLMError` with status `timeout` or `connect_error`; a 2xx with an unparseable body is `malformed`; other non-2xx carries the first 200 characters of Groq's error message. Each updates `consecutive_primary_failures`, which backs `GET /api/llm/health`.
- **An `on_call` callback** receives one dict per outcome. `main.py` wraps it in `logging_sink.py::llm_failure_mirror`, so every failure also reaches the durable WARNING sink; wrapping the callback rather than adding log lines at raise sites means a new raise site cannot be forgotten.

```bash
grep "^class \|^def \|^    def \|^    async def " backend/app/llm/client.py | head -24
```

```output
def reasoning_params_for_model(model: str) -> dict | None:
def _parse_reset_duration(s: str) -> float:
class LLMError(Exception):
    def __init__(self, message: str, attempts: list[dict] | None = None) -> None:
class LLMQuotaExceededError(LLMError):
class LLMClient:
    def __init__(
    def _admission_gate(self) -> PriorityGate:
    def _fire_callback(
    def _make_attempt(provider: str, model: str, status: str | int, error: str, latency_ms: int) -> dict:
    def _update_health_after_groq(
    async def complete(
    async def _call_groq(
    def _snapshot_rate_limits(response: httpx.Response) -> dict | None:
    async def probe_rate_limits(self) -> dict | None:
    async def _start_ollama(self) -> bool:  # pragma: no cover
    async def _call_ollama(
    async def health(self) -> dict:
```

### 5.3 Pacing, 429s and the per-request ceiling

Free-tier Groq has three walls, and the client handles each differently.

1. **Requests and tokens per minute.** Every response carries `x-ratelimit-remaining-*` and `x-ratelimit-reset-*` headers. After each success the client computes a proactive delay (`reset / remaining` for requests; for tokens, only once under 20% remain) and stores the deadline in `_next_call_at`. This is smoother than reacting to 429s alone.
2. **A 429 anyway.** `retry-after` is honoured up to `max_retry_after_s` (10 s) and `max_retries_429` (3) attempts. A longer wait is not slept inside the request: the call raises, and the pipeline (§6.9) decides whether to wait out a rate-limit window. The 429 timestamp and the last header snapshot are kept for `GET /api/llm/rate-limit`.
3. **8,000 tokens per request, reserved up front.** Groq holds `prompt_tokens + max_completion_tokens` against the per-request ceiling, and going over is a hard 413, not a retryable 429. This single fact explains several sizes elsewhere: the story completion cap (4096), the prompt token budget and the review-word limit in §6, and the separate gloss call. Nothing in the client enforces it; callers size their requests.

Failed calls still count: a 429, an HTTP error and an unparseable 2xx each write a zero-token ledger line, because Groq counted the request against its requests-per-day wall regardless of what TT could parse.

### 5.4 One at a time, foreground first

Pacing used to be one shared timestamp: each caller computed its own wait, slept to the same instant, and all fired together in whatever order they woke. On 2026-09-19 a user-facing import queued behind about 30 background cloze/image prestage calls from a sync that had just finished; the burst drew a 429, which in turn pushed the lemma resolver onto its fallback (`57423fc2`, bd `rwkz.3`).

`app/llm/priority_gate.py::PriorityGate` replaces the timestamp-sleep with admission control. It admits one caller at a time, chooses *who* at the moment the slot opens rather than when the wait began, and sleeps out the pacing deadline for the head of the queue only. Priority comes from `common/background_work.py::in_background`, a context variable set by `BackgroundWork.track`; anything scheduled through `track` is deprioritised without its call site declaring it. A ticket survives a caller's retries, so FIFO order within a priority class holds across a 429.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
import asyncio
from app.llm.priority_gate import PriorityGate, FOREGROUND, BACKGROUND

async def main():
    gate = PriorityGate(ready_at=lambda: 0.0)
    order = []
    await gate.acquire(BACKGROUND, gate.ticket())      # a call is already in flight
    async def caller(name, prio):
        await gate.acquire(prio, gate.ticket())
        order.append(name)
        gate.release()
    tasks = [asyncio.create_task(caller(f"background-{i}", BACKGROUND)) for i in range(3)]
    await asyncio.sleep(0.01)
    tasks.append(asyncio.create_task(caller("foreground", FOREGROUND)))  # arrives last
    await asyncio.sleep(0.01)
    gate.release()
    await asyncio.gather(*tasks)
    print(order)
asyncio.run(main())
E
```

```output
['foreground', 'background-0', 'background-1', 'background-2']
```

The gate orders work; it does not pace less. The delays that keep TT inside the free tier are applied exactly as before, now by the gate instead of by each caller.

### 5.5 The usage ledger

Groq's daily token cap (TPD) appears in no response header, so TT counts its own spend in `app/llm/usage_ledger.py::UsageLedger`, an append-only file (`settings.llm_usage_ledger_path`, `llm_usage.log` under `tt_home()`, which is `$TT_HOME` or `~/.tunatale`). One line per request: `<ts> <total> <prompt> <completion> <reasoning> <call_site>`. Field 1 is always the total, so legacy two-field lines and new six-field lines are read by the same code; a two-field line means "total known, split unknown", never zero.

The bucket is **continuous**, not a rolling 24-hour sum and not a calendar day. This was measured against the live API on 2026-08-13: `x-ratelimit-reset-requests` read `1m26.4s`, `2m52.8s`, `4m19.2s` after one, two and three requests, exactly `86400/1000`, `2*86400/1000`, `3*86400/1000`. The limit is the bucket's capacity, it refills at `limit/86400` per second, and the header means "time until the bucket is full again". `_consumed` replays the log in order, draining between entries and never banking idle time, so a quiet week does not buy a doubled day.

```bash
cd backend && uv run python - <<'E'
import pathlib, tempfile
from app.llm.usage_ledger import UsageLedger

ledger = UsageLedger(pathlib.Path(tempfile.mkdtemp()) / "llm_usage.log")
t0 = 1_000_000.0
ledger.record(100_000, prompt_tokens=60_000, completion_tokens=39_000,
              reasoning_tokens=1_000, call_site="story", now=t0)
for hours in (0, 6, 12):
    now = t0 + hours * 3600
    print(f"+{hours:>2}h  used={ledger.tokens_used(200_000, now=now):>7}"
          f"  full-again-in={ledger.tokens_reset_in_s(200_000, now=now)/3600:.1f}h")
ledger.record(150_000, call_site="story", now=t0 + 1)
print("exceeded:", ledger.budget(tokens_limit=200_000, requests_limit=1000, now=t0 + 2).exceeded)
E
```

```output
+ 0h  used= 100000  full-again-in=12.0h
+ 6h  used=  50000  full-again-in=6.0h
+12h  used=      0  full-again-in=0.0h
exceeded: tokens per day
```

Before every Groq request `_call_groq` asks `ledger.budget(...)` and, if either ceiling is blown, raises `LLMQuotaExceededError` (a subclass of `LLMError`) *without sending anything*. The message deliberately avoids the substrings "rate-limited" and "Ollama" that the pipeline's backoff retries on: a day-budget wall cannot be waited out in 90 seconds. "tokens per day" wins when both ceilings are exceeded. The limits are `settings.groq_tokens_per_day_limit` (200,000) and `groq_requests_per_day_limit` (1,000); both are organization-level and per-model on Groq, so changing `llm_model` changes every number.

`split_used` is the one deliberate exception to the bucket model. It is a flat 24-hour sum of prompt, completion and reasoning tokens, answering "which half should we trim" rather than "how close is the cap". Measured 2026-09-13: 100k tokens spread evenly across a day give `tokens_used == 0` and a split summing to 100,000. The API names its fields `_24h` versus `_day` to stop anyone comparing the two.

### 5.6 Call-site labels

The ledger's sixth field says which route spent the budget. The labels are literal constants in `app/llm/call_sites.py::CallSite`, so a test can assert the exact set, and each must match `^[a-z0-9_.]+$` because the ledger line is whitespace-split. Single-route sites use a flat label; the three cloze helpers are shared by the background prestage and the interactive API, so their label is composed as `<caller>.<operation>`.

```bash
cd backend && uv run python -c "
from app.llm.call_sites import CallSite
print(sorted(CallSite.FLAT_LABELS))
print(sorted(CallSite.CALLERS), sorted(CallSite.OPERATION_SUFFIXES))
print(CallSite.compose('prestage', 'cloze_judge'))
"
```

```output
['glossing', 'lemma_resolve', 'media_choose', 'media_query', 'planner', 'regloss', 'srs_translate', 'story', 'translate_term', 'word_gloss']
['api', 'prestage'] ['cloze_generate', 'cloze_judge', 'cloze_translate']
prestage.cloze_judge
```

`complete(call_site="")` has a default so legacy callers keep working, which is exactly the hole the label exists to close. `backend/scripts/check_llm_call_sites.py` (wired into `./test.sh` and CI) is an AST scan of `backend/app/**` that fails any `<receiver>.complete(...)` without an explicit `call_site=` keyword. A `**kwargs` splat does not satisfy it, because a splat cannot prove the label is forwarded; `CassetteLLMClient._patch` therefore forwards it by name. There is no allowlist.

### 5.7 Cassettes

`CassetteLLMClient` is a VCR for prompts, with four modes that mirror pytest's `--llm-mode` and the server's `llm_mode` setting:

| Mode | Behaviour |
|---|---|
| `mock` | Replay only. A prompt with no recorded entry raises `RuntimeError`. The default, and the only mode in CI. |
| `record` | Call the real client, append every response, save. |
| `patch` | Replay what exists, call the real client for anything new and save it. For adding one test case. |
| `live` | Pass through, save nothing. (In the app, `live` skips the wrapper entirely.) |

Lookup is by hash, not sequence, so scenarios can share a cassette and tests are order-independent. The hash covers **both** the system and user prompts (cassette `version: 2`; a v1 file raises on load): editing a system prompt must invalidate its recordings, or a changed prompt would silently replay an answer to the old one. The same value repeated in a cassette is replayed in order, and running past the recorded count is a miss.

```bash
cd backend && uv run python -c "
from app.llm.cassette import CASSETTE_VERSION, _hash_prompt
print(CASSETTE_VERSION)
print(_hash_prompt('hello'))
print(_hash_prompt('hello', ''))      # None and '' mean the same: no system prompt
print(_hash_prompt('hello', 'sys'))
"
```

```output
2
sha256:8a2a5c9b768827de
sha256:8a2a5c9b768827de
sha256:7a3aee6c0637c63f
```

Two failure-handling details came from CI incidents. A cassette miss prints both prompts in full plus the hashes the file holds (`_prompt_dump`), because an 80-character preview could not discriminate four e2e prompts that share their opening. And a miss is also appended to `miss_log` (`LLM_CASSETTE_MISS_LOG`, set per Playwright worker), because a fail-soft caller such as the gloss pass swallows the exception by design; from `96878b8e` every e2e run missed its gloss entry and stayed green until the teardown began failing on any line in that log (bd `1l26.7`).

In mock or patch mode a missing cassette file is a `FileNotFoundError` at construction, not a lazy miss, so a production image that ships no `tests/cassettes` fails loudly. The app's own cassette is `backend/tests/cassettes/e2e.json`. Unit tests mostly avoid cassettes altogether and hand the code under test a small fake with an `async def complete`; the `cassette_llm` fixture in `tests/conftest.py` (cassette name `{ClassName}__{test}.json`, skip when absent in mock mode) is for the few tests that pin real model output. Rules for adding and recording are in §14.

### 5.8 Errors to HTTP

Failure maps to status codes once, in app-level handlers in `main.py`, not in each route.

```bash
grep -A1 "@app.exception_handler" backend/app/main.py | grep -v "^--"
```

```output
@app.exception_handler(LLMQuotaExceededError)
async def llm_quota_exceeded_handler(request: Request, exc: LLMQuotaExceededError) -> JSONResponse:
@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError) -> JSONResponse:
@app.exception_handler(NoReviewVocabularyError)
async def no_review_vocabulary_error_handler(request: Request, exc: NoReviewVocabularyError) -> JSONResponse:
```

| Exception | Status | Meaning |
|---|---|---|
| `LLMQuotaExceededError` | 429 | TT declined to call; the day budget is spent. A 502 would invite retries that cannot succeed. |
| `LLMError` | 502 | Upstream failed (or exhausted its 429 retries); the detail carries the attempts. |
| `NoReviewVocabularyError` | 409 | A review story was requested with nothing due (§6.10). Not a failure. |

Routes that parse model output add their own mapping: `StoryGenerationError` and `PlannerError` become 502 with nothing persisted, so the user simply retries. Background paths convert instead of raising: `LessonPipeline` (§6.9) records `failed` with `retryable=True` on the job.

### 5.9 Observability

Three views of the same client, none of which call the model:

- `app/llm/activity.py::ActivityLog` is a 300-event ring buffer fed by the `on_call` callback and by the pipeline's state changes; the UI polls `GET /api/llm/activity?since=<seq>`.
- `app/api/llm.py` exposes `/health` (consecutive failures, last error), `/rate-limit` (header snapshot, last 429, ledger fill for tokens and requests, the 24-hour split, and the Azure character tally from §7) and `POST /rate-limit/probe`.
- The durable WARNING sink (§2) receives every non-success outcome through `llm_failure_mirror`.

```bash
cd backend && uv run python -c "
from app.api.llm import router
for r in router.routes:
    print(','.join(sorted(r.methods)).ljust(5), r.path)
"
```

```output
GET   /api/llm/health
GET   /api/llm/rate-limit
GET   /api/llm/activity
POST  /api/llm/rate-limit/probe
```

## 6. Content Generation & Storage

This chapter follows text from the learner's intent to a stored lesson: a chat-planned curriculum, a story the model writes for one day, a separate glossing pass, a deterministic expansion of the story into seven Pimsleur sections, and the publish step that tags, writes and schedules audio. Review sessions, which are lessons with no curriculum, are generated by the same machinery and described here (their reader and player are §13). The chapter ends with `ContentStore`, the SQLite repository everything above writes into. The model client and its budgets are §5; the audio that the stored sections become is §7; the words and cards a lesson feeds are §8 and §11.

### 6.1 The shape of the pipeline

    chat planner ──> Curriculum (days)            LLM, one call per turn, human in the loop (6.3)
                        │ commit
                        ▼
            story prompt (system + strategy)      deterministic, shared with the export endpoint (6.4)
                        │ LLM, ≤ 4096 completion tokens
                        ▼
                  Story JSON ──> glossing         second LLM call, fail-soft (6.6)
                        │
                        ▼  build_lesson_from_story    deterministic, the ONE build step (6.5, 6.7)
                     Lesson (7 sections + generation_metadata)
                        │
                        ▼  publish_lesson             lemmas, UPOS, write, invalidate, prewarm, render (6.8)
              ContentStore: lessons / review_sessions + audio_files (6.11)

The model contributes only creative text: a title, 3 to 8 key phrases, and four to six scenes of dialogue with English translations. Everything structural (which sections exist, their order and titles, the syllable buildups, voices, slow-speed spacing) is code, so a lesson is reproducible from its Story JSON. That JSON is itself stored (`generation_metadata["story"]`) and is the unit a human can export, edit and re-import (`docs/lesson-authoring.md`).

There are four ways in, all converging on the same build and publish steps: the background `LessonPipeline` (automatic, per curriculum day), the synchronous `POST /api/story/generate`, **manual mode** (the learner copies an exported prompt into a chat, pastes the reply back at `/api/story/import`), and review sessions (§6.10). The prompt each path sends is built by one function, so manual and automatic modes cannot drift.

### 6.2 Strategies and review pressure

`models/strategy.py` holds two small enums that steer the story prompt.

```bash
cd backend && uv run python -c "
from app.models.strategy import ContentStrategy, ReviewPressure
for e in (ContentStrategy, ReviewPressure):
    print(e.__name__, [m.name for m in e])
"
```

```output
ContentStrategy ['WIDER', 'DEEPER', 'REVIEW']
ReviewPressure ['NATURAL', 'BALANCED', 'INSISTENT']
```

- **WIDER** puts new collocations into new scenarios at the same difficulty. **DEEPER** keeps the scenario but raises language complexity; its prompt carries the previous stored day's dialogue as source text (§6.4). **REVIEW** has no theme at all: the content *is* the learner's decaying vocabulary. It is a strategy rather than a pressure setting because the other two take a theme, a focus and story guidance for review words to trade against, and REVIEW has none.
- **ReviewPressure** (`NATURAL`, `BALANCED`, `INSISTENT`) says how hard a themed prompt pushes the learner's review words (`bec97b17`, bd `ow7t`). The user's range was from "today's state of thematic unity" up to "aggressively incorporating the words at the cost of theme (but not coherence)". NATURAL is the default and keeps the old behaviour: words are *offered* and declining all of them is a correct answer. Coherence is not the top of the scale but the floor under all of it, stated unconditionally as the last line of every block.

Pressure is one dial per curriculum, stored in `curriculum.metadata["review_pressure"]` and set by `POST /api/curriculum/{id}/review-pressure`, not on `CurriculumDay` (that would need a migration and would turn it into a per-day planner decision). `Curriculum.review_pressure(override)` resolves it. `None` means "the caller did not specify", never "NATURAL": conflating them silently disables the setting at every call site that passes nothing. An unknown stored value degrades to NATURAL rather than 500ing a generation.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.models.curriculum import Curriculum
from app.generation.prompts import build_review_block
from app.models.strategy import ReviewPressure

c = Curriculum(id="x", topic="t", language_code="no", cefr_level="A1")
print("unset:", c.review_pressure().name, "| override:", c.review_pressure("INSISTENT").name)
c.metadata["review_pressure"] = "BALANCED"
print("stored:", c.review_pressure().name, "| override wins:", c.review_pressure("INSISTENT").name)
c.metadata["review_pressure"] = "bogus"
print("bad value:", c.review_pressure().name)
print("---")
print(build_review_block(["kaffe", "god dag"], ReviewPressure.BALANCED))
print("--- empty set:", repr(build_review_block([], ReviewPressure.INSISTENT)))
E
```

```output
unset: NATURAL | override: INSISTENT
stored: BALANCED | override wins: INSISTENT
bad value: NATURAL
---
These are the words this learner is closest to forgetting, most urgent first.
- kaffe
- god dag
Look actively for openings — reshape a line, add a beat, or choose a detail that gives one of these words a natural home. Leave out only the ones that would still distort the scene.
Whatever you include, the scene must still be one real people could plausibly be having — never trade coherence to place a word.
--- empty set: '(none yet)'
```

The empty-set rendering `"(none yet)"` is a compatibility constant, not a courtesy. Every recorded story cassette was made when that literal was sent, and the cassette key is a hash of the whole prompt (§5.7); an empty review set must therefore render byte-identically at every pressure. The same reasoning is why `build_story_prompts(srs_db=None)` renders exactly the pre-feature prompt.

### 6.3 The curriculum planner

A `Curriculum` is a topic, a CEFR level and a list of `CurriculumDay` (`day`, `title`, `focus`, `collocations`, `learning_objective`, `story_guidance`). `day` is a stable key, not an ordinal: deleting day 5 leaves `[1, 2, 3, 4, 6]` forever, because lessons, pipeline jobs and planner feedback all reference it. Anything the learner sees comes from `Curriculum.day_positions()`.

Plans are made by chat (`docs/curriculum-planning.md`); the one-shot generator no longer exists. The flow in `api/curriculum.py` is:

1. `POST /api/curriculum/plan` mints an empty curriculum with empty planner state. No LLM.
2. `POST /{id}/plan/turn` runs one turn: `generation/planner.py::CurriculumPlanner.turn` makes a single `complete()` call with a deterministic user prompt built from the committed plan (last 14 days in full, older as titles), the learner snapshot, per-day feedback and the last 12 chat messages. The reply is prose with at most one fenced `json` block; `split_reply_and_json` extracts the *last* parseable fence, tolerates reasoning blocks, and raises only when a fence tagged `json` exists and none parse.
3. `parse_turn` validates the proposal (`storage/plan_io.py::validate_plan_days`: required fields, no unknown keys, non-empty collocations without duplicates) and **renumbers** the days from the next free number, so the model cannot collide with existing keys. A wrong day count is a `PlannerError`, which becomes a 502 with nothing persisted.
4. `POST /{id}/plan/commit` appends the proposed batch. It refuses (409) a proposal whose first day no longer matches the next free number, which happens after a plan re-import. Unless the curriculum's `generation_mode` is `manual`, it enqueues a `generate` job per new day.

The planner is a pure function of its inputs: `build_turn_prompt` returns `(system, user)` and `POST /{id}/plan/turn/prompt` exports exactly the same pair, so pasting it into a chat and bringing the reply back through `pasted_response` runs the identical validation.

The system prompt's worked example is resolved through the registry to a language *other* than the target (`prompts.py::build_planner_system_prompt`, `LanguageConfig.planner_example`). A model shown an example in the target language copies that language into its replies; a Norwegian plan once got a Norwegian example (`1b7d09b3`).

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.languages import discover; discover()
from app.generation.prompts import build_planner_system_prompt
from app.generation.planner import parse_turn, PlannerError
from app.models.curriculum import Curriculum

for code in ("no", "sl"):
    s = build_planner_system_prompt(code)
    print(code, "plans see:", s[s.index("Example ("):].splitlines()[0])

cur = Curriculum(id="c", topic="cafe", language_code="no", cefr_level="A1")
reply = ('Here you go.\n```json\n{"days":[{"day":9,"title":"Cafe","focus":"ordering",'
         '"collocations":["en kaffe"],"learning_objective":"order a drink"}]}\n```')
turn = parse_turn(reply, curriculum=cur, batch_size=1)
print("renumbered:", [d.day for d in turn.proposed_days], "| prose:", turn.reply)
try:
    parse_turn(reply, curriculum=cur, batch_size=2)
except PlannerError as e:
    print("PlannerError:", e)
E
```

```output
no plans see: Example (Slovene curriculum):
sl plans see: Example (Norwegian curriculum):
renumbered: [1] | prose: Here you go.
PlannerError: Expected 2 days, got 1
```

The learner snapshot (`srs/planner_snapshot.py::build_learner_snapshot`) is deliberately a pure function of DB contents, with no dates or row-order leakage, so identical decks give identical prompts. That purity is what a due-aware review selector could not honour, and is why the selector (§6.4) is a sibling module and not a parameter on it.

One stale spot: the planner's system text still describes "the Pimsleur 4-section lesson shape". A lesson has seven sections (§6.7); the model is only told the shape is fixed, so this does not change what it plans, but the wording predates the split into English-before and English-after variants.

### 6.4 Story prompts

`generation/story.py::build_story_prompts(curriculum_day, language, strategy, cefr_level, *, srs_db, review_pressure, content_store, curriculum_id)` returns a `StoryPrompts(system_prompt, user_prompt, review_words)`. It is shared by `StoryGenerator.generate` and by `GET /api/story/prompt`, so what a learner pastes into a chat is byte-identical to what Groq would receive. Review words and the DEEPER source are selected *inside* it: if each caller selected its own, the caller that forgot would send "(none yet)" forever with no error.

The **system prompt** (`prompts.py::build_story_system_prompt`) is assembled from a template plus registry facets, never from language literals:

- per-language authenticity rules (`get_style_notes`, from the plugin, with a generic fallback);
- the voice line, built from the language's actual numbered speaker roles in `tts_voice_map` (`- Use ONLY these N L2 voices: ...`);
- a morphology-tagging block (`morphology_focus`, §6.7) only when the language's registry `morphology_profile` is `"slavic"`. A language that registers no profile gets no block; Tagalog once silently received Slovene's case vocabulary (`0f34ec3b`);
- a rule that the model must **not** emit `dialogue_glosses`. The incident is recorded in code comments, not in the prompt: the model needs the rule, not the story, and pays for every word of the prompt on every request.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.languages import discover, get_language; discover()
from app.generation.prompts import build_story_system_prompt
print("lang  morphology_focus  voice-line")
for code in ("sl", "no", "tl", "ceb"):
    p = build_story_system_prompt(get_language(code))
    line = next(l for l in p.splitlines() if "Use ONLY these" in l)
    print(f"{code:4}  {str('\"morphology_focus\"' in p):16}  {line}")
E
```

```output
lang  morphology_focus  voice-line
sl    True              - Use ONLY these 4 L2 voices: female-1, female-2, male-1, male-2
no    False             - Use ONLY these 8 L2 voices: female-1, female-2, female-3, female-4, male-1, male-2, male-3, male-4
tl    False             - Use ONLY these 4 L2 voices: female-1, female-2, male-1, male-2
ceb   False             - Use ONLY these 4 L2 voices: female-1, female-2, male-1, male-2
```

The **user prompt** comes from one of three templates (`get_strategy_prompt`), filled with the day's objective, focus, guidance, new collocations, a CEFR block (only the level in play is described; the other three cost ~75 tokens per request) and the review block from 6.2. Three constraints shape it:

- **The 8,000-token request reservation (§5.3).** The completion cap is 4096, so a prompt may cost `PROMPT_TOKEN_BUDGET = 8000 - 4096` tokens, estimated at chars/4. Nothing in WIDER or REVIEW approaches it. DEEPER's source transcript is what grows with the corpus, so `_fit_source_transcript` drops lines from the *end* until it fits and appends `[transcript trimmed to fit the request budget]`, because the opening of a dialogue sets its scene and a silently shortened source would read as the whole story.
- **DEEPER's source is the previous *stored* day.** `prior_day_transcript` looks up the latest lesson of the largest earlier stored day (not `day - 1`, since days have gaps), takes its natural-speed dialogue as `[scene]` and `role: line`, and returns `None` for a first day, in which case the source block is omitted entirely. For a long time DEEPER was handed the literal text "(not available)" inside a fenced block and asked to enhance nothing (bd `g8mu`, `95b84fe8`).
- **Review words are small on purpose.** `srs/review_selector.py::select_review_collocations` returns at most 12 (`DEFAULT_REVIEW_LIMIT`), ranked by *retrievability ascending*, over the same due pool the review queue uses (§8, §9). Because FSRS holds R above desired retention for cards not yet due, R-ascending is overdue-first by construction and a separate due window is redundant. It makes no Anki-parity claim, writes nothing, and is deterministic for a given DB and `now`. Its `horizon_days` argument is a no-op whenever the due pool exceeds the limit, which is the case it was filed for; the docstring records the measurement (78 due, limit 12, every horizon returned the same twelve).

`REVIEW` prompts (`_build_review_prompts`) refuse an empty set with `NoReviewVocabularyError` while the prompt is still being assembled, before any model call, because with no theme an empty set is a prompt with no content. Pressure is forced to INSISTENT there; NATURAL's "including none of them is a correct answer" contradicts a story whose only content is those words. The REVIEW template also forbids key phrases that are a bare word or a copy of a review word, since the key-phrase drill needs a chunk to build up to.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.languages import discover, get_language; discover()
from app.generation.story import build_review_session_prompts, NoReviewVocabularyError, PROMPT_TOKEN_BUDGET
lang = get_language("no")
sp = build_review_session_prompts(lang, "A1", review_words=["kaffe", "takk"])
print("review_words pinned:", sp.review_words)
print("theme fields in prompt:", "Theme/Focus" in sp.user_prompt, "| strategy line:", sp.user_prompt.splitlines()[3])
print("INSISTENT wording:", "matters MORE than staying close" in sp.user_prompt)
try:
    build_review_session_prompts(lang, "A1", review_words=[])
except NoReviewVocabularyError as e:
    print("refused:", e)
print("prompt budget (tokens):", PROMPT_TOKEN_BUDGET)
E
```

```output
review_words pinned: ('kaffe', 'takk')
theme fields in prompt: False | strategy line: **Strategy:** REVIEW (No Theme — Reinforce Decaying Vocabulary)
INSISTENT wording: True
refused: REVIEW needs due review vocabulary and none is due for this language today
prompt budget (tokens): 3904
```

### 6.5 StoryGenerator and the one build step

`StoryGenerator._complete` is shared by the day path (`generate`) and the session path (`generate_review_session`), so the budget logic exists once:

1. Call `complete(call_site=CallSite.STORY, max_tokens=4096)`. The number is not arbitrary: with the ~2,800-token system prompt a 5,500 cap totalled ~8,300 and drew a hard 413 (§5.3), which then fell through to the Ollama junk-JSON path. Measured at `reasoning_effort=low`, the JSON payload is ~1,900 tokens, so 4096 leaves ample headroom.
2. Parse with `json_parsing.py::parse_json_object`: strip `<think>` blocks and code fences, try the whole string, then the first-`{` to last-`}` span. Reasoning models prepend prose, and this hardening survived the model experiments that provoked it.
3. On a parse failure, if `last_finish_reason == "length"` the cap is re-derived from the *measured* `prompt_tokens` (`8000 - prompt - 128`, never shrinking) and the call is retried once; otherwise the error is enriched (an Ollama-served reply is called out as such) and retried. Two attempts, then `StoryGenerationError`.
4. `ensure_dialogue_glosses(data, llm, language)` (§6.6) fills the glosses in place, *before* the build. This keeps the build, the stored story blob and the paste round-trip all seeing the shape they always had.
5. `build_lesson_from_story(data, language, review_words=...)`.

`story.py::build_lesson_from_story` is the single Story-JSON-to-`Lesson` function. Generation, manual import (`lesson_io.import_lesson` and the API import routes), regloss and review-session import all call it, so authored and generated lessons have an identical shape. In order, it:

- applies the language's `story_text_normalizer` once, on a deep copy, to target-language text only (Tagalog joins the U+2011 affix hyphen in "mag‑kape" so it reads "magkape"); English fields are never touched;
- builds the seven sections (§6.7) and the `KeyPhraseInfo` list;
- builds a **sentence-aware surface-to-lemma map** from the real dialogue (`glossing.py::dialogue_surface_lemmas`) so a gloss for "hotel" is keyed to the noun, not whatever a POS-blind lookup guessed;
- derives `token_glosses` (surface key keeps the conjugated meaning, lemma key is the generic fallback), lets a curated `fixed_gloss` override the model for grammatical function words (Cebuano `og` came back "and"), and a separate verb-only `verb_base_glosses` map so the transcript's in-context gloss stays apart from the card back;
- measures **review coverage** (`review_coverage.py::review_word_usage`): single words match on tokens *and* lemmas, never substrings ("kava" written as "kavo" counts; "dan" inside "danes" does not), multi-word collocations by phrase search, deliberately under-counting inflected ones. The result lands in `review_requested` / `review_used` and is logged at INFO, not WARNING: at NATURAL pressure skipping is a correct answer, so the ratio is the product, not an alarm.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.languages import discover, get_language; discover()
from app.generation.story import build_lesson_from_story
lang = get_language("no")
story = {"title": "Kaffe",
  "key_phrases": [{"phrase": "Jeg vil ha kaffe", "translation": "I want coffee"}],
  "scenes": [{"label": "At a cafe", "lines": [
     {"speaker": "female-1", "text": "God dag", "translation": "Good day"},
     {"speaker": "male-1", "text": "Jeg vil ha kaffe", "translation": "I want coffee"}]}],
  "dialogue_glosses": [{"word": w, "translation": t} for w, t in
     [("god","good"),("dag","day"),("jeg","I"),("vil","want"),("ha","have"),("kaffe","coffee")]]}
lesson = build_lesson_from_story(story, lang, review_words=["kaffe", "takk"])
md = lesson.generation_metadata
print(sorted(md))
print("requested:", md["review_requested"], "used:", md["review_used"], "gloss entries:", md["gloss_entry_count"])
print("token_glosses:", md["token_glosses"])
E
```

```output
['gloss_entry_count', 'morphology_focus', 'review_requested', 'review_used', 'sentence_translations', 'story', 'token_glosses', 'verb_base_glosses']
requested: ['kaffe', 'takk'] used: ['kaffe'] gloss entries: 6
token_glosses: {'god': 'good', 'dag': 'day', 'jeg': 'I', 'vil': 'want', 'ha': 'have', 'kaffe': 'coffee'}
```

### 6.6 Glossing

Hover translations need an entry for *every* unique dialogue word, so the gloss array grows with the dialogue while the completion cap cannot. Measured on the 2026-09-02 Norwegian review session, the array was 3,213 of the story's 5,511 completion tokens (58%), and review-session generation 502'd twice. The ceiling could not be raised (prompt 2,396 + completion 5,476 + margin 128 is exactly the 8,000 reservation), so the glosses moved to their own call (`07cfd759`, bd `yet7`). `generation/glossing.py::ensure_dialogue_glosses` is the only place the pass is invoked, shared by generation and both paste-import paths.

Its design rules:

- **Never fatal.** The story is expensive and already in hand; glosses are a cheap, re-runnable enrichment. Any failure logs a warning and leaves the lesson without hover translations, rather than throwing away a valid story and 502ing the button the split was made to fix. The create responses carry a `warnings` entry ("no hover translations") when `gloss_entry_count == 0`.
- **Skip when already glossed.** A story that arrives with `dialogue_glosses` (paste route, legacy cassette, authored lesson) skips the call, so the change is backward compatible by construction rather than by a flag.
- **Sized from the work.** `gloss_max_tokens` is `unique_words x 24` clamped to what the request reservation allows; 24 is a measured 1.5x margin over the worst case seen (9.4 and 10.4 tokens per entry live, 16.1 historically). Raising it is cheap; lowering it truncates the array, which is the failure being avoided.
- **Recover, do not discard.** `parse_gloss_array` falls back to recovering `{...}` objects one at a time, so one bad character no longer empties the array (`96878b8e`); it also strips HTML the model put inside a word (`vz<span></span>amem`, bd `vayt`), logging each repair.
- **One retry, one top-up.** An empty reply is retried once. A *partial* reply gets one targeted call for exactly the `uncovered_surfaces`, with context limited to the lines that contain them. The top-up's cap includes a 1,500-token reasoning allowance, since a 5-word top-up came back truncated mid-entry at an entries-only size. Never a loop: whatever is still missing is logged.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.languages import discover; discover()
from app.generation.glossing import parse_gloss_array, gloss_max_tokens, uncovered_surfaces
raw = ('[{"word": "hei", "translation": "hello"}, '
       '{"word": "vz<span></span>amem", "translation": "I take"}, '
       '{"word": "bro", "translation": "bri')           # truncated mid-entry
print(parse_gloss_array(raw))
print("short dialogue cap:", gloss_max_tokens(["Hei, jeg heter Anna."]),
      "| 40 lines:", gloss_max_tokens([f"ord {i} en to tre" for i in range(40)]))
print("uncovered:", uncovered_surfaces({"hei": "hei", "heter": "hete"}, [{"word": "hei", "translation": "hello"}]))
E
```

```output
[{'word': 'hei', 'translation': 'hello'}, {'word': 'vzamem', 'translation': 'I take'}]
short dialogue cap: 256 | 40 lines: 1056
uncovered: ['heter']
```

Two repair paths exist for lessons stored without glosses: `POST /api/story/{lesson_id}/regloss` (`api/generation.py::regloss_lesson_story`) and `POST /api/review-sessions/{id}/regloss`. Both drop the stored `dialogue_glosses`, rerun the pass against the stored story and rebuild via `build_lesson_from_story`, then call `story.py::with_glosses_from`, which keeps the stored lesson and merges only the rebuilt `generation_metadata`. That narrowness matters: a lesson rebuilt around new glosses also picks up today's voice cast and key-phrase drills, and storing it would put a transcript out of step with audio that still speaks the old ones (67 of 170 drills had drifted on one live lesson). They write with `update_lesson_data` / `update_review_session_data`, never the `save_*` methods (§6.11). The older offline `storage/regloss_lessons.py` remains as a one-shot migration for lessons predating glosses keyed by surface.

### 6.7 Sections, key phrases and morphology focus

`generation/section_builder.py` expands the story into seven `Section`s, always in this order. Titles are spoken by the narrator as each section's first phrase and are the learner-visible names; the enum values are persisted (in stored JSON and `audio_files.section_type`), so renaming titles needed no migration and the display titles moved independently (`ea162179`, `backend/scripts/rename_section_titles.py` retitles stored lessons).

| `SectionType` value | Spoken title | Contents |
|---|---|---|
| `key_phrases` | Key Phrases | per key phrase: L2 line, English, then a backward buildup |
| `natural_speed` | Natural Speed | scenes and dialogue, L2 only |
| `slow_speed` | Enunciated | same, words joined by ` ... ` |
| `translated` | English After | each L2 line, then its English |
| `slow_translated` | Enunciated, English After | enunciated L2, then English |
| `en_translated` | English Before | English, then the L2 line |
| `slow_en_translated` | Enunciated, English Before | English, then enunciated L2 |

"Slow" is a legacy name. The pass is *enunciated* speech, not slower speech: `get_slow_word(code)` supplies a per-language respelling (Norwegian's is morpheme-aware), and the words are joined with a literal ` ... ` that the TTS reads as a pause, with no rate change. Each English line is read by `tts_en_voice_map[speaker]` when the language has one and by the narrator otherwise, and keeps `role="narrator"` either way.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.languages import discover, get_language; discover()
from app.generation.section_builder import SECTION_TITLES, build_natural_speed_section, build_slow_speed_section
lang = get_language("no")
scenes = [{"label": "At a cafe", "lines": [{"speaker": "male-1", "text": "Jeg vil ha kaffe", "translation": "I want coffee"}]}]
narr = lang.tts_voice_map["narrator"]
for build in (build_natural_speed_section, build_slow_speed_section):
    s = build(scenes, lang.tts_voice_map, narr, lang.code)
    print(s.section_type.value, [p.text for p in s.phrases])
print({t.value: title for t, title in SECTION_TITLES.items()})
E
```

```output
natural_speed ['Natural Speed', 'At a cafe', 'Jeg vil ha kaffe']
slow_speed ['Enunciated', 'At a cafe', 'jeg ... vil ... ha ... kaffe']
{'key_phrases': 'Key Phrases', 'natural_speed': 'Natural Speed', 'slow_speed': 'Enunciated', 'translated': 'English After', 'slow_translated': 'Enunciated, English After', 'en_translated': 'English Before', 'slow_en_translated': 'Enunciated, English Before'}
```

**Key phrases and breakdown.** The Pimsleur backward buildup is built by `build_key_phrases_section`: L2 phrase, English, then a sequence of progressively longer fragments from the end of the phrase back to the whole. `build_word_breakdown_spans` returns `BreakdownChunk(text, source_word, span)` records. A language that registers a `breakdown_spans_fn` (Norwegian, with its compound handling, §3) supplies its own; everyone else gets `_generic_breakdown_spans`, which syllabifies through the registry and guards each word with a **losslessness check**: provenance (`source_word`, `syllable_span`) is attached only when the syllables rejoin the word exactly, so a slicer (§7) can cut chunks from one whole-word render instead of asking the voice for a fragment it would misread. A `None` span always means "synthesize `text`". The plain-text sequence of `build_word_breakdown` is, by invariant, identical to the spans version, since the audio cue manifest is built from the plain one and any divergence would desynchronise every cue.

Two details come from bugs. Words are stripped of sentence punctuation before syllabifying, because a chunk sliced from "bong?" says the fragment as a question (bd `w4m7.19`); the strip set excludes `'` and `-`, which in Tagalog and Cebuano mark a glottal stop or contraction and belong to the word. And the closing rung of the buildup is written once: the loop used to append the phrase twice in a row.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.languages import discover; discover()
from app.generation.section_builder import build_word_breakdown, build_word_breakdown_spans
for code, phrase in (("sl", "dober dan"), ("no", "jeg vil ha kaffe"), ("tl", "magkape ako")):
    print(code, build_word_breakdown(phrase, code))
print([(c.text, c.source_word, c.span) for c in build_word_breakdown_spans("kaffe", "no")])
E
```

```output
sl ['dober dan', 'dan', 'ber', 'do', 'dober', 'dober dan']
no ['jeg vil ha kaffe', 'ffe', 'ka', 'kaffe', 'ha', 'ha kaffe', 'vil', 'vil ha kaffe', 'jeg', 'jeg vil ha kaffe']
tl ['magkape ako', 'ko', 'a', 'ako', 'pe', 'ka', 'kape', 'mag', 'magkape', 'magkape ako']
[('kaffe', None, None), ('ffe', 'kaffe', (1, 2)), ('ka', 'kaffe', (0, 1)), ('kaffe', None, None)]
```

`key_phrase_groups(section, l2_code)` is the structural inverse: it reads each group's phrase-index range off the *stored* section (a head is an L2 phrase whose successor is a narrator phrase) instead of recomputing from today's rules. A section is a snapshot laid out under the rules of its day, and re-deriving counts from current rules made stored lessons fail to re-render after all the TTS was done (8 of 11 Norwegian lessons, measured 2026-09-29). Migrations that rebuild or annotate old sections live in `storage/` (`resync_key_phrases.py`, `backfill_breakdown_provenance.py`, `caption_staleness.py`; the last only detects), and re-rendering is §7.

**`morphology_focus`.** For languages whose profile is `"slavic"` (Slovene), the story schema asks the model to build a `morphology_focus` array *last*, tagging inflected words already present in the dialogue. Form coverage is the generator's job, not the card maker's: a cloze can only be made for a form the lesson contains. The prompt steers to forms a beginner should *produce*: verb conjugations and accusative or locative nouns, nominative-singular nouns excluded because the dictionary form gives the answer away, and cases derived from the governing word, not from the English gloss. Steering raised the live card yield from 52% to 91%. The array is stored in `generation_metadata` and in the exported Story JSON; the A1 morphology cards a learner actually meets are now derived from the lemmatizer's features and each plugin's `a1_morphology` bundle (§8, §11), so this array is generation-side data rather than a runtime input.

### 6.8 Publishing: the one write seam

Six call sites write lesson-shaped content: the pipeline's generate path, `POST /api/story/generate`, `POST /api/story/import`, and the review-session create, paste-import and in-place import routes. Each used to perform the post-generation steps by hand and drop different ones; the observable bug was that creating a review session never rendered audio while creating a lesson always did. `generation/publishing.py::publish_lesson` is the one ordering they now share, and the ordering is load-bearing:

1. `resolve_lesson_lemmas` (awaited): disambiguates words for table-lemmatizer languages (§8); a no-op for others. `llm` is a required keyword so a new writer cannot skip it.
2. `annotate_chunk_upos_for_lesson` (awaited, **before** the write): tags breakdown chunks with part of speech so the IPA planner (§7) can choose pronunciations. A detached task races the write, so the tags land on an in-memory object nobody persists again. On 2026-08-26 a fresh lesson had 0 of 47 chunks tagged, and re-annotating the stored copy tagged all 47.
3. `target.write(lesson)`.
4. `target.invalidate_audio(id)` when replacing.
5. Pre-warm the sentence-analysis cache as an *anchored* background task: the event loop keeps only a weak reference, so an unanchored task can be garbage-collected mid-flight. `_background_tasks` is the strong reference, and tests drain it.
6. `target.schedule_render(...)`.

The destination is a `ContentTarget` protocol (`write`, `invalidate_audio`, `schedule_render`) with two implementations. `CurriculumDayTarget` mints a fresh id, saves under `(curriculum_id, day)`, syncs the day's title to the lesson title, and enqueues a `render` job; a regenerate therefore **inserts** a second row and `invalidate_audio` drops only the *superseded* lesson's audio, keeping its row (a row is cheap and a possible undo, orphaned audio is not). `ReviewSessionTarget` writes under its own id and renders directly (§6.10).

```bash
grep -rc "await publish_lesson(" backend/app --include=*.py | grep -v ":0$" | sort
```

```output
backend/app/api/generation.py:2
backend/app/api/review_sessions.py:3
backend/app/generation/pipeline.py:1
```

### 6.9 LessonPipeline

`generation/pipeline.py::LessonPipeline` is the background queue behind a curriculum. It is a single worker on purpose: generation shares one Groq budget and rendering one TTS throttle, so a second worker would only contend for them. Jobs are keyed `(user_id, language_code, curriculum_id, day)`; `user_id` is a required keyword on every public method, so a caller that forgets it is a `TypeError` instead of a lesson generated into the owner's store (`d3f99818`). `None` means the owner's flat per-language stores; any other id resolves that account's own files through `storage/user_dbs.py` (§2).

- **States** are `queued`, `generating`, `rendering`, `ready` and `failed`, kept in memory with an `error`, a `retryable` flag and a `detail` string for the card.
- **`enqueue` is idempotent** (a no-op while a job for the key is active). **`reconcile`** enqueues whatever a curriculum is missing: `generate` for a day with no lesson, `render` for a lesson with no audio rows. It skips failed jobs (failure stickiness) so a broken day does not loop forever, and skips generation for `manual` curricula. The status poll `GET /api/curriculum/{id}/pipeline` calls `reconcile` first, so merely opening a curriculum heals interrupted work.
- **`retry`** prefers a render when the lesson exists, so it never spends LLM quota to regain missing audio. **`regenerate`** forces a new generation with a strategy.
- **Backoff.** `StoryGenerationError` or `LLMError` is checked for a rate limit (a 429 newer than the attempt began, or the substrings "rate-limited"/"Ollama"). A rate limit waits `min(max(retry_after, tokens-reset, 15s), 90s)` and retries, up to 4 attempts. A `LLMQuotaExceededError` message contains neither substring, so a spent day budget fails fast instead of waiting.
- **Render progress.** `render_service.render_lesson_audio` reports `(done, total)` clips through a callback. `render_progress.py::RenderProgress` turns it into an ETA over a one-minute sliding window, with no estimate in the first 30 seconds or with fewer than 3 completions in the window: the early moments are all cache hits and a running average from the start reports a render of seconds. Counts are cleared the moment a day leaves `rendering`, on failure too, because a bar frozen at 116/171 is a claim about work that did not happen.

```bash
grep -o '"state": "queued"\|\["state"\] = "[a-z]*"' backend/app/generation/pipeline.py | sort -u
```

```output
"state": "queued"
["state"] = "failed"
["state"] = "generating"
["state"] = "ready"
["state"] = "rendering"
```

### 6.10 Review sessions (generation)

A **review session** is a lesson-shaped story with no theme, no curriculum and no day, built from the learner's most-decayed vocabulary across the whole language deck. It has its own router, `api/review_sessions.py` under `/api/review-sessions`, and the prefix is part of the decision: creating one under the curriculum-shaped `/api/story` would repeat the placement error that once put a "Review story" button beside Regenerate on a lesson, which replaced the lesson. Its reader, list and player are §13.

- **Create** (`POST ""`, 201) takes no identifiers at all (`extra="forbid"`). `_generate_and_store` calls `StoryGenerator.generate_review_session`, which raises `NoReviewVocabularyError` (409, reworded for the learner as "Nothing to review right now...") before any LLM call when nothing is due. The CEFR level comes from the newest curriculum in the language (`_latest_cefr_level`), falling back to A2 when there are none, since a learner with no plans must still be able to review. Generation happens *before* any write, so a refusal on regenerate leaves the existing dialogue intact.
- **Idempotency.** The create and paste routes are single-flighted on an `Idempotency-Key` header (`api/idempotency.py::once`; a header and not a body field because the body forbids extras and a browser's resend repeats headers). On 2026-09-19 the paste route was called twice with the same paste 75 seconds apart and each call minted an id and started a render on a box that could not afford one.
- **Regenerate** (`POST /{id}/regenerate`) rewrites the dialogue in place, keeping id and date, and re-selects words: a session's claim is that it is about what has decayed *now*. **Prompt export** (`GET /{id}/prompt`) is the opposite: it is pinned to the session's stored `review_requested`, because the learner takes it to a chat and pastes it back, and coverage must be scored against the set that was asked for. The rule is "import paths pin, generation paths select". The same applies to curriculum days: `GET /api/story/prompt` records what it handed out in `curriculum.metadata["review_requests"][str(day)]` (a write on a GET, deliberately; the key is a string because metadata round-trips through JSON), and import measures against that.
- **Manual mode at create time** (`GET /prompt`, `POST /import`) is stateless: no draft row is minted, and the pinned word list rides in the client between the two calls. These two routes must be declared above `/{session_id}`, because `GET /{session_id}` happily matches "prompt" and would answer a misleading 404; a test guards the order.
- **Rendering** is a direct `render_lesson_audio` call guarded by the shared `review_renders` in-flight set (409 on a second render; `render-status` lets a page that navigated away pick the render back up). The `render-estimate` and `rerender` routes take a section selection and share that set, so a render and a re-render exclude each other.
- **Delete** removes the row and its audio but **not** SRS history (`lesson_reviews`, `tt_revlog`, direction state). The reviews really happened and their grades already propagated to FSRS and Anki; unwinding them would diverge TT from Anki for no benefit (user decision, 2026-09-13).

The two content surfaces must keep the same verbs. `backend/scripts/check_content_surface_parity.py` holds a verb map of (lesson route, session route) pairs and fails when a route on either router is unclassified or a verb is missing without a recorded reason in `tests/content_surface_allowlist.txt`.

```bash
cd backend && uv run python -c "
from app.api.review_sessions import router
for r in router.routes:
    print(','.join(sorted(r.methods)).ljust(6), r.path.removeprefix(router.prefix) or '/')
"
```

```output
GET    /prompt
POST   /import
POST   /
POST   /{session_id}/regenerate
GET    /{session_id}/prompt
GET    /{session_id}/source
POST   /{session_id}/import
GET    /
GET    /{session_id}
POST   /{session_id}/render
POST   /{session_id}/render-estimate
POST   /{session_id}/rerender
GET    /{session_id}/render-status
POST   /{session_id}/regloss
DELETE /{session_id}
```

### 6.11 ContentStore

`storage/store.py::ContentStore` is a small SQLite repository. It opens **the same file as `SRSDatabase`** (one `tunatale_<code>.db` per language, §2): `main.py` builds `SRSDatabase(path)` and `ContentStore(path)` from the same path, and `storage/user_dbs.py` does the same for a learner's files. A file-backed store opens a fresh connection per operation (`_file_conn`) and holds nothing between them, so there is no handle to leak and no cap to enforce.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.storage.store import ContentStore
with ContentStore(":memory:") as s:
    with s._get_conn() as c:
        for (name,) in c.execute("select name from sqlite_master where type='table' order by name"):
            print(name.ljust(16), [r[1] for r in c.execute(f"pragma table_info({name})")])
try:
    ContentStore("sqlite:///x.db")
except ValueError as e:
    print("refused:", str(e)[:58])
E
```

```output
audio_files      ['id', 'lesson_id', 'file_path', 'section_index', 'section_type', 'created_at', 'cues_json']
curricula        ['id', 'data_json', 'created_at']
lessons          ['id', 'curriculum_id', 'day', 'data_json', 'created_at']
review_sessions  ['id', 'language_code', 'session_date', 'title', 'data_json', 'review_requested_json', 'review_used_json', 'created_at']
refused: ContentStore wants a filesystem path, got a URL 'sqlite://
```

Four tables hold everything. `curricula` and `lessons` are JSON blobs (`Curriculum.to_json()`, `Lesson.to_json()`) because the shapes evolve quickly and a blob needs no ALTER TABLE churn; `lessons` adds `curriculum_id` and `day`. `audio_files` is normalised because it is queried by `lesson_id` with ordering (the full-lesson row first, then sections by `section_index`). `review_sessions` is the newest.

**Decisions worth knowing:**

- **Review sessions are their own table.** `lessons.curriculum_id` and `day` are `NOT NULL`, SQLite cannot drop a constraint in place, and widening them meant rebuilding the table that holds every real lesson. The accepted cost is a parallel read path. `audio_files` needed nothing, because it joins on an id and a session id is an id. `get_readable_content(id)` returns a lesson *or* a session, lessons first, and is why the `/api/srs/content/{id}/...` routes serve both. The coverage pair is stored as nullable JSON, and `None` ("never measured", no readout) is deliberately distinct from `[]` ("measured zero").
- **Row writes versus file deletes.** The storage layer owns rows; **the caller owns the filesystem**. Every delete method (`delete_curriculum`, `delete_lessons_for_day`, `delete_lesson`, `delete_audio_files_for_lesson`, `delete_review_session`, `delete_review_session_audio`) returns the paths of audio files it orphaned and unlinks nothing. They used to return `None` or `bool`, and every deleted day, curriculum or lesson left its render on disk (33 unreferenced files, 19 MB on one real store).
- **`update_*_data` versus `save_*`.** `save_lesson` and `save_review_session` are `INSERT OR REPLACE`, which assigns a new `rowid` and resets `created_at`. `get_lesson_days` surfaces `MAX(rowid)` per day, so a blanket re-save of an old row could change which version the UI shows; for sessions it also writes NULL over the coverage pair, so the meter silently vanishes. Repairs (regloss, key-phrase resync) therefore use `update_lesson_data` / `update_review_session_data`.
- **Latest wins.** A day can hold several versions (a regenerate inserts). `get_latest_lesson_by_day` orders by `created_at DESC, rowid DESC`, and `list_curricula` and `list_review_sessions` carry the same `rowid` tie-break: `created_at` has one-second granularity, so ties come back oldest-first, and a review session was once pitched at an older plan's level because of it.
- **Audio paths are stored as basenames.** `save_audio_file` keeps only the filename, resolved at read time by `audio/paths.py::resolve_audio_path` against `settings.audio_dir`. Rows once held `/Users/<author>/...` paths that 404'd on the Linux box (100 of 104). `_normalize_audio_paths` heals on every open rather than once, because a database that has moved machine may come from a backup taken before the write side was fixed. `file_path` may be NULL: a full-lesson row can keep its cue timeline with no encoded file.
- **Idempotent migrations on open.** `_migrate_audio_files` adds missing columns and rebuilds the table inside an explicit `SAVEPOINT` to relax `file_path NOT NULL` (Python's `sqlite3` only opens its implicit transaction before DML, so the caller's transaction does not cover the DDL).
- **A `sqlite:` URL is refused.** `Path()` would treat `sqlite:///...` as a relative directory and silently create a throwaway store beside the CWD (bd `zjjo`, twice). The error names `app.languages.resolve_db_path`, the converter; refusing rather than stripping makes the caller's bug visible.

```bash
cd backend && uv run python - <<'E' 2>/dev/null
from app.languages import discover, get_language; discover()
from app.generation.story import build_lesson_from_story
from app.storage.store import ContentStore
lang = get_language("no")
story = {"title": "Kaffe", "key_phrases": [{"phrase": "Jeg vil ha kaffe", "translation": "I want coffee"}],
         "scenes": [{"label": "At a cafe", "lines": [{"speaker": "male-1", "text": "Jeg vil ha kaffe", "translation": "I want coffee"}]}]}
lesson = build_lesson_from_story(story, lang)
with ContentStore(":memory:") as s:
    s.save_lesson("L-old", "c1", 1, lesson)
    s.save_lesson("L-new", "c1", 1, lesson)                      # a regenerate INSERTS a second row
    s.save_audio_file("a1", "L-old", "/Users/someone/x.opus", section_index=0, section_type="key_phrases")
    print("shown for day 1:", s.get_lesson_days("c1"))
    print("stored path:", s.list_audio_files_for_lesson("L-old")[0]["file_path"])
    print("orphaned paths returned, nothing unlinked:", [p.name for p in s.delete_lesson("L-old")])
    s.save_review_session("rs1", "no", "2026-10-01", lesson, review_requested=["kaffe"], review_used=[])
    print(s.get_review_session_row("rs1"))
    print("readable by id:", s.get_readable_content("rs1").title, "|", s.get_readable_content("L-new").title)
E
```

```output
shown for day 1: [{'day': 1, 'lesson_id': 'L-new'}]
stored path: x.opus
orphaned paths returned, nothing unlinked: ['x.opus']
{'id': 'rs1', 'language_code': 'no', 'session_date': '2026-10-01', 'title': 'Kaffe', 'review_requested': ['kaffe'], 'review_used': []}
readable by id: Kaffe | Kaffe
```

## 7. The Audio Pipeline

The audio pipeline turns a stored `Lesson` (sections of `Phrase` rows, each carrying text, a voice id and a rate) into per-section audio files plus a cue manifest that lets the player highlight the right caption at the right millisecond. It sits downstream of content generation (§6, which builds the sections and the syllable-breakdown chunks) and upstream of the player (§13) and the SRS reader (§8), which both consume the cues. Everything here is organised around three constraints: Azure's free monthly allowance is the budget (§7.4), the production box has under 1 GB of RAM (§7.9), and a lesson is a timeline in which any drift between audio and cues is a bug the learner hears.

The package is `backend/app/audio/`. `ports.py` defines the contract, `azure_tts.py` and `gemini_tts.py` implement it, `tts_router.py` and `tts_factory.py` choose between them, `renderer.py` assembles audio, `render_service.py` persists it, and `cues.py` / `assembly.py` own the timeline.

### 7.1 The TTS port and its two providers

Everything synthesises through the `TTSService` protocol in `app/audio/ports.py`: one `synthesize(text, voice_id, output_path, rate, phonemes, speak_locale)` coroutine and a `list_voices`. Two optional arguments carry the interesting behaviour. `phonemes` is a per-token IPA mapping (not whole-text IPA), and `speak_locale` declares what language the text is written in when the voice is not named for it. For both, `None` must behave identically to a provider without the capability, down to the cache key, so that adding a capability never orphans audio already on disk.

There are exactly two providers, and no provider switch:

| Voice id ends in | Adapter | Notes |
|---|---|---|
| `...Neural` | `azure_tts.py::AzureTTSService` | Azure Speech REST, SSML, deterministic output |
| `...Gemini` | `gemini_tts.py::GeminiTTSService` | Google Cloud TTS, service-account OAuth, nondeterministic output |

An earlier unofficial Edge Read Aloud adapter and its `TTS_PROVIDER` setting were deleted (`3f94eb25`). The reason is worth remembering: Edge silently ignored `<phoneme>` markup yet shared a cache directory and key space with Azure, so one Edge render would have poisoned the IPA cache entries.

`tts_router.py::RoutingTTSService` dispatches on the voice-id suffix and nothing else. The rule lives in one public function, so the cost report can ask "whose bill is this voice on?" without building an adapter:

```bash
cd backend && uv run python -c "
import inspect
from app.audio.tts_router import provider_for
print(inspect.getsource(provider_for))
for v in ('nb-NO-FinnNeural', 'ceb-PH-KoreGemini'):
    print(v, '->', provider_for(v))
try:
    provider_for('en-US-Mystery')
except ValueError as e:
    print('ValueError:', e)
"
```

```output
def provider_for(voice_id: str) -> Literal["azure", "gemini"]:
    """Which provider owns *voice_id*, or a ``ValueError`` naming it.

    The whole routing rule, as a public function, because not every caller wants
    an adapter and only wants to know whose bill a voice lands on — the
    render-cost report prices each provider in its own unit and must split the
    keys before it can price either. The match stays exact and case-sensitive
    and the refusal stays the same one :meth:`RoutingTTSService._adapter_for`
    raises: two copies of one rule would be one rule too many.
    """
    if voice_id.endswith(_GEMINI_SUFFIX):
        return "gemini"
    if voice_id.endswith(_AZURE_SUFFIX):
        return "azure"
    raise ValueError(
        f"{voice_id!r} names no known TTS provider: a voice id must end in {_GEMINI_SUFFIX!r} or {_AZURE_SUFFIX!r}"
    )

nb-NO-FinnNeural -> azure
ceb-PH-KoreGemini -> gemini
ValueError: 'en-US-Mystery' names no known TTS provider: a voice id must end in 'Gemini' or 'Neural'
```

This is routing, not fallback, and the distinction is load-bearing. If the named adapter fails, the render fails. A silent swap would splice a second provider's rendition of the "same" voice into one curriculum (voice-id parity is not voice parity), and retrying a throttled request on a different provider turns one clipped render into two billed requests. `tts_factory.py::get_tts_service` builds both adapters behind the router, sharing one cache directory (each in its own key space). An unset `AZURE_SPEECH_KEY` does not stop the app booting; it fails at the first synthesis, where the message is actionable.

Two exception types let callers tell failures apart. `TTSExhausted` means a clip's retry ladder ran out (throttling passes, so a whole-render retry is worthwhile). `TTSQuotaExceeded` means the monthly allowance is spent (it will not pass, so retrying only burns the budget). Neither is a subclass of the other; both are `RuntimeError`, which the audio routes map to HTTP 503 with the message intact.

### 7.2 The Azure adapter

`AzureTTSService.synthesize` does four things in a fixed order, and the order is the design:

1. Check credentials (`_require_credentials`).
2. Look in the file cache (`_cache_path`). A hit copies the file and returns: no request, no quota check, no ledger entry. A warm cache is the only thing that lets a render finish after the allowance is spent.
3. Check the character ledger (§7.4). A spent allowance raises `TTSQuotaExceeded` before any request is made.
4. Post SSML through `_synthesize_with_retry`, then write the output and populate the cache.

The cache key is `sha256(voice|rate|text)`, extended with `|token:ipa,...` only when a phoneme map is present, and with `|lang:<locale>` only when a `<lang>` wrapper is actually emitted. "Only when" is the compatibility rule: hundreds of MB of audio were keyed on the three-part form, and an unconditional extension would have re-synthesised the corpus.

The SSML is built by `_build_ssml`, whose inner part is `_billable_body`. The split exists because Azure bills every character of a successful request except the `<speak>` and `<voice>` tags, so `len(_billable_body(...))` is the billable count for one request (§7.4). The nesting is `<lang>` outside `<prosody>` outside optional `<phoneme>` elements:

```bash
cd backend && uv run python -c "
from app.audio.azure_tts import AzureTTSService as A
cases = [
  ('plain', ('Hei', 'nb-NO-FinnNeural', '+0%')),
  ('ipa chunk', ('sky', 'nb-NO-FinnNeural', '-20%', {'sky': 'ˈʃyː'})),
  ('multilingual', ('Kavno pivo', 'en-AU-WilliamMultilingualNeural', '+0%', None, 'sl-SI')),
  ('native, same locale', ('Kavno pivo', 'sl-SI-PetraNeural', '+0%', None, 'sl-SI')),
]
for label, args in cases:
    body = A._billable_body(*args)
    print(f'{label:20} {len(body):3} chars  {body}')
"
```

```output
plain                 33 chars  <prosody rate="+0%">Hei</prosody>
ipa chunk             78 chars  <prosody rate="-20%"><phoneme alphabet="ipa" ph="ˈʃyː">sky</phoneme></prosody>
multilingual          70 chars  <lang xml:lang="sl-SI"><prosody rate="+0%">Kavno pivo</prosody></lang>
native, same locale   40 chars  <prosody rate="+0%">Kavno pivo</prosody>
```

The `<lang>` wrapper (`_lang_locale`) exists because a Multilingual Neural voice auto-detects the language of each utterance, and sometimes detects wrongly. Measured on a Slovene line read by `en-AU-WilliamMultilingualNeural`, speech-to-text read back "manager" for "meniju" (8 of 9 words wrong); with the wrapper, 0 of 9. A native voice already speaking the locale gets no wrapper (byte-identical PCM, so emitting one would only change cache keys). A Multilingual voice is always told, even in its own locale, because detection is per utterance there too: `en-US-EmmaMultilingualNeural` read the English "Come in, come in." as Tagalog without it.

Pacing and retries are settings, and their comments record the measurements behind them: one request in flight (`tts_max_concurrent_requests = 1`, never tested higher), 0.2 s between requests (`tts_min_request_delay_s`), and `MAX_RETRIES = 6` with a 429 backoff of `retry_base * 4 * 2**attempt` plus up to 50% jitter. Six rungs, not three, because a measured 64% per-request success rate under sustained throttling gives a 3-rung ladder a 4.7% chance of exhausting a clip, which over ~169 clips is a near-certain aborted render; six rungs make it 0.22%. A clip in backoff does not hold the semaphore, and the pacing delay is paid on every attempt, success or failure, because a throttled request that exited without sleeping freed its slot instantly and amplified a burst into a 429 cascade. 401 and 403 are fatal and raise immediately.

On top of the per-clip ladder, `render_service.py::_with_render_retries` re-runs a whole render when it raises `TTSExhausted` (`tts_render_max_attempts`, default 3, with a `tts_render_retry_cooldown_s` pause). Each pass strictly advances because every clip that did synthesise is already in `tts_cache_dir`. It retries `TTSExhausted` and nothing else: a missing key will not fix itself in fifteen seconds.

### 7.3 The Gemini adapter (Cebuano)

Cebuano's four dialogue voices are Gemini voices (`ceb-PH-KoreGemini` and friends), served by `GeminiTTSService` through Google Cloud Text-to-Speech. Three properties of the provider shape the module:

- **Auth is a service account.** These voices reject API keys; the adapter mints an OAuth token from the JSON at `settings.google_application_credentials` (a path, because Pydantic loads `.env` into settings, not into `os.environ`, so google-auth's own lookup would never see it).
- **Output is nondeterministic.** Two renders of one utterance differ, so the file cache is load-bearing, not an optimisation, and the model id is in the cache key. A bad take is served forever; `backend/scripts/reroll_tts_clip.py` is the narrow lever (it moves one cached Gemini clip aside, dry-run by default, and refuses Azure keys because re-rolling a deterministic voice would bill byte-identical audio twice). The wider lever, `PROMPT_VERSION`, invalidates every Gemini clip at once.
- **The quota is small and per-minute.** Roughly 30 fast requests trigger a 429, so pacing is 2 s between starts (`gemini_tts_min_delay`) and a 429 sets a flat 20 s cooldown that every caller shares (a ladder would climb past the very minute it is waiting for).

Gemini voices have no `<phoneme>` support in Cloud TTS. IPA reaches the model as an instruction in `input.prompt` instead, and the two instruction shapes are chosen by `resolve_ipa`: one entry for a one-token text gets "Say only this one syllable or word...", and one entry per token of a multi-word text gets the phrase variant. Any other shape returns `(None, False)`, logs one warning and renders plain. `speak_locale` is accepted and ignored (the voice's own `languageCode` is explicit):

```bash
cd backend && uv run python -c "
from app.audio.gemini_tts import resolve_ipa, _ipa_prompt
print(resolve_ipa('ugma', {'ugma': 'ʊɡˈma'}))
print(resolve_ipa('ilubong ugma', {'ilubong': 'ʔiluˈbɔŋ', 'ugma': 'ʊɡˈma'}))
print(resolve_ipa('ko ang ko', {'ko': 'kɔ', 'ang': 'ʔaŋ'}))
print(_ipa_prompt('Cebuano ', 'ʊɡˈma'))
"
```

```output
('ʊɡˈma', False)
('ʔiluˈbɔŋ ʊɡˈma', True)
(None, False)
Say only this one Cebuano syllable or word, exactly once, with nothing before or after it. Pronounce it exactly as the IPA /ʊɡˈma/.
```

The third line is the "refuse rather than guess" rule: "ko ang ko" is three tokens over two map entries, so the step stays plain instead of being rendered from a reading with a word missing. The model is `gemini-2.5-flash-tts`, chosen by listening test: it follows the IPA instruction, whereas the 3.x family treats the text as a verbatim transcript.

### 7.4 Cost, quota and the render-cost instrument

The Azure Speech resource is on the **F0 free tier**: standard and Multilingual Neural characters draw on a 500K/month allowance, and going over throttles rather than bills. **Azure Neural HD (Dragon) voices are banned** by the user: HD bills from the first character, outside the allowance, and is nondeterministic. It is enforced by `test_languages.py::test_no_voice_map_names_a_paid_hd_voice` (an HD voice id is the only kind containing a colon). Full detail, including the two unexplained rate-limit observations, is in `.claude/rules/paid-vendors.md`.

Because F0 throttles instead of erroring, the first symptom of an exhausted allowance would be a render that mysteriously stops working. And there is no usable Azure-side meter: `SynthesizedCharacters` is listed but not queryable, and the queryable metrics have returned 0 for days with hundreds of syntheses. So TunaTale counts its own spend. `char_ledger.py::AzureCharacterLedger` is an append-only file of `<unix_ts> <chars>` lines, tallied by calendar month, written only on a successful non-cached request (retries record once), and consulted before every cache miss:

```bash
cd backend && uv run python -c "
import tempfile, datetime as dt
from pathlib import Path
from app.audio.char_ledger import AzureCharacterLedger
t = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC).timestamp()
with tempfile.TemporaryDirectory() as d:
    led = AzureCharacterLedger(Path(d) / 'usage.log')
    led.record(499_900, now=t('2026-09-30T12:00'))
    for when in ('2026-09-30T13:00', '2026-10-01T00:00:01'):
        s = led.budget(chars_limit=500_000, now=t(when))
        print(when, 'used', s.chars_used, 'exceeded', s.exceeded)
    led.record(100, now=t('2026-09-30T14:00'))
    s = led.budget(chars_limit=500_000, now=t('2026-09-30T15:00'))
    print('after +100:', s.chars_used, s.exceeded, 'resets in', round(s.reset_in_s/3600, 1), 'h')
"
```

```output
2026-09-30T13:00 used 499900 exceeded None
2026-10-01T00:00:01 used 0 exceeded None
after +100: 500000 characters per month resets in 9.0 h
```

Azure documents the allowance but not when the month turns over, so the reset timezone is a setting (`azure_tts_quota_reset_tz`, default UTC), and the clock is injected into the adapter so tests pin it without patching `time.time`. One honest caveat: `tts_factory.py::get_tts_service` constructs the enforcing ledger without passing that setting, while `api/llm.py` (the usage readout) does pass it, so a non-UTC value would currently change the display but not the enforcement. At the shipped default the two agree.

**Pricing a render before running it** is a standing rule (AGENTS.md "Paid vendors"). The instrument is `backend/scripts/report_render_cost.py`, whose pricing core now lives in `app/audio/render_cost.py` so the in-app estimate and the script cannot disagree. It mirrors the renderer's own dedupe key and decides hit-or-miss with the adapter's own `_cache_path` against the real `tts_cache_dir`, then sums `len(_billable_body(...))` over the misses only. Counting the billable body, not the text, matters: SSML markup bills, and across thousands of short utterances the ~36-character `<prosody>` wrapper roughly doubles a text-length estimate. Quote the incremental figure for "what will this run cost" and the cold figure (`--cache-dir "$(mktemp -d)"`) only for "from empty", and say which; they have differed about 7x. Measured numbers are deliberately not written into docs because they rot as lessons are added. The command regenerates them:

```bash
cd backend && uv run python scripts/report_render_cost.py --help | sed -n '1,3p;/^options/,$p'
```

```output
usage: report_render_cost.py [-h] [--language LANGUAGE] [--lesson ID] [--all]
                             [--cache-dir CACHE_DIR]

options:
  -h, --help            show this help message and exit
  --language LANGUAGE   default: settings.target_language
  --lesson ID           a stored lesson id to price (repeatable)
  --all                 price every stored lesson of the language (the default
                        when no --lesson is given)
  --cache-dir CACHE_DIR
                        TTS cache dir to test hits against (default:
                        settings.tts_cache_dir; point at an EMPTY dir for the
                        cold price)
```

Gemini keys are priced differently, and loosely: there is no billable-body unit because there is no SSML, and Cloud TTS bills returned audio tokens. `render_cost.py` derives an estimate from three named constants (characters per second of speech, tokens per second, USD per million tokens) so a reader can disagree with a term. Hit-or-miss is not estimated; it is the same `.exists()` check for both providers. `render_cost.py::estimate_render` also backs the "Re-render audio" estimate in the UI (§7.10), using the app's own renderer configuration: no slicer leg, because the app sends none.

### 7.5 Voices, narrator and loudness

Voice assignment is data on `Language` (`app/models/language.py`) and is built per language plugin (§3). `Language.tts_voice_map` maps a role (`narrator`, `female-1`, `male-2`, ...) to a voice id; `Language.tts_en_voice_map` does the same for the English translation spoken by that role, so each character reads their own English line (the translation phrase keeps `role="narrator"`; only its `voice_id` moves). The dialogue role `narration` (§3.3) maps to Davis in both maps, except Cebuano's L2 narration, which stays on Gemini. The narrator is `NARRATOR_VOICE = en-US-DavisMultilingualNeural`. The table below is generated from the live registry:

```bash
cd backend && uv run python -c "
from app import languages as L
L.discover()
for code in sorted(L._CONFIGS):
    lang = L.get_language(code)
    vm = lang.tts_voice_map
    roles = [r for r in vm if '-' in r and r[-1].isdigit()]
    print(f'{code:4} locale={lang.tts_locale}  roles={len(roles)}  providers=', end='')
    print(sorted({('gemini' if v.endswith('Gemini') else 'azure') for v in vm.values()}))
    for r in roles[:2]:
        print(f'       {r}: {vm[r]}')
"
```

```output
ceb  locale=ceb-PH  roles=4  providers=['azure', 'gemini']
       female-1: ceb-PH-KoreGemini
       female-2: ceb-PH-DespinaGemini
en   locale=en-US  roles=4  providers=['azure']
       female-1: en-US-AriaNeural
       female-2: en-US-AriaNeural
no   locale=nb-NO  roles=8  providers=['azure']
       female-1: nb-NO-PernilleNeural
       female-2: nb-NO-IselinNeural
sl   locale=sl-SI  roles=4  providers=['azure']
       female-1: sl-SI-PetraNeural
       female-2: en-US-EmmaMultilingualNeural
tl   locale=fil-PH  roles=4  providers=['azure']
       female-1: fil-PH-BlessicaNeural
       female-2: en-US-EmmaMultilingualNeural
```

Several slots are filled by Multilingual voices from another locale (Slovene `male-2` is `de-DE-FlorianMultilingualNeural`, because `sl-SI` ships only two native voices for four roles). That is why `Language.tts_locale` exists and why the renderer passes `speak_locale` for every phrase (§7.2). Tagalog adds a `key-phrases` role bound to a German Multilingual voice for a reason covered in §7.6.

**Loudness.** Voices differ in level, so two characters in one dialogue would otherwise sit at different volumes. `Language.tts_voice_gain_db` holds a constant dB gain per voice id, and `renderer.py::_apply_voice_gain` applies it at assembly with a peak clamp at -1.0 dBFS (it only ever lowers a clip's own gain, never raises it). Gains are applied after the TTS cache, never inside synthesis, because the cache is content-addressed on `(voice, rate, text, phonemes, locale)`. Per-clip normalisation was rejected: the spread within one voice exceeded the gap between voices. A latent bug is the reason English gains live in `Language.english()`: the renderer looks a phrase's gain up under that phrase's own language code, so a narrator or English-translation clip resolves against the `"en"` table whatever lesson it belongs to, and the narrator gain once silently never applied. The lesson title is gained the same way.

### 7.6 Pronunciation: IPA only where it helps

A voice reading an isolated syllable runs word-level grapheme-to-phoneme on a fragment, and a fragment that spells a real word is read as that word. The motivating case was Norwegian: `gen` (from `hagen`) came out as the word /ɡeːn/ rather than /ɡən/, and `ret` (from `sporet`) as /reːt/. These are the `-en` / `-et` definite endings, the most common syllable shape in Bokmål, not a tail case. The fix is to hand the voice the correct IPA.

The current rule (`0e2b1808`, set by the user after listening) is narrow:

- **Whole phrases and whole-word chunks never get `<phoneme>`.** The voice's own front end reads a complete word better than a transcription of it does.
- **Only sub-word breakdown chunks do**, with IPA taken from the language's lexicon, via `PhonemePlanner.plan_chunk(source_word, span, upos, chunk_text)` (protocol in `app/languages.py`). A planner returning `None` means "synthesise the text plainly" and is the only failure signal.
- **Two exceptions are per-language opt-ins.** Cebuano and Tagalog also pass whole words, for reasons below.

Where the chunk's identity comes from: `models/breakdown.py::BreakdownChunk` carries `text`, `source_word` (the whole word, never a piece of one) and `span` (a `(start, stop)` range into the word's syllables). These are stored on `Phrase` as `source_word`, `syllable_span` and `upos`, defaulting to `None`/`""` so every older stored lesson deserialises and renders as before. `section_builder.py::build_word_breakdown_spans` is the single implementation of breakdown sequencing; the plain-text `build_word_breakdown` is `[c.text for c in spans]`. That inversion was deliberate: `cues.py` derives phrase counts from the text sequence, and two parallel implementations that had to agree byte for byte were upheld by tests rather than by structure.

The Norwegian planner (`plugins/languages/no/phoneme_plan.py::NorwegianPhonemePlanner`) is the most careful, and its docstring is worth reading for the incident references. `plan_chunk` first rejects a whole-word span and an unbuilt lexicon, then tries the whole source word, then falls back to the compound part that hosts the span. `_plan_against` runs five gates: the caption's split must equal the lexicon's split (count agreement is not boundary agreement: `undersøke` is four syllables both ways but spelling and pronunciation attach the `n` differently); the word must resolve (or be ambiguous in a way that does not touch this span); X-SAMPA must convert; candidate readings must agree at this span apart from stress, unless the most-enunciated tiebreak already chose one reading for both boundaries and sound; and the chunk text must match the syllables at the span, so a lesson stored before boundaries moved never plays a syllable its caption does not name. The compound fallback is strictly additive by measurement: over nine stored lessons it gained 12 chunks, lost none and changed none, whereas resolving every compound per part would have lost 10 and changed 22.

Wiring: `renderer.py::LessonRenderer._synthesize_section` builds a phoneme map per phrase, keyed by the bare word (`_bare_word` strips punctuation, because the adapter matches word tokens and a chunk stored as `bing?` would otherwise match nothing and silently play as text). That map is part of the renderer's memo key and of the adapter cache key.

The other languages differ in how IPA can reach the voice:

| Language | Planner source | Channel |
|---|---|---|
| Norwegian | NST lexicon (§7.7) | Azure `<phoneme>` on sub-word chunks |
| Tagalog | Wiktionary readings (`tl/pronunciation.py`), spelled fallback | Azure `<phoneme>`, voiced by a Multilingual `key-phrases` voice |
| Cebuano | Spelling-based (`ceb/phoneme_plan.py` over `app/audio/spelled_ipa.py::SpelledPhonemePlanner`) | Gemini `input.prompt` instruction |
| Slovene | none | plain text |

Tagalog exists because Azure's `fil-PH` voices ignore `<phoneme>` entirely ("salamat" came back byte-identical with and without IPA), so the key-phrase breakdown is voiced by a Multilingual voice that honours it, and whole words get IPA there because a German voice reading Tagalog text guesses. Two registry flags express the Tagalog and Cebuano exceptions: `ipa_read_in_voice_locale` (fil-PH ignores IPA, so IPA-bearing utterances go out without the `<lang>` wrapper) and `ipa_for_drill_phrases`, which lets `_drill_phrase_phonemes` give a multi-word KEY_PHRASES step a per-word map. It refuses on any single unreadable word rather than leaving a gap, because a half-read phrase is aligned to the wrong readings. That path came from a blind A/B on "ilubong ugma", which was misheard as "ilubong uglak" 2 of 3 times plain and right 2 of 2 times with the reading given.

### 7.7 The NST lexicon and syllable boundaries (Norwegian)

Norwegian's pronunciation data is the CC0 NST lexicon from the National Library of Norway. `plugins/languages/no/lexicon.py` opens it lazily and read-only; the committed artifact is a lean gzipped extract (`data/nst_lexicon.tsv.gz`) and the indexed SQLite database is a gitignored build artifact produced by `python -m app.build_data` (the Dockerfile and `switch.sh` run it, §15). A missing build raises loudly in `lexicon.py`, and the planner degrades to plain synthesis with one warning, because a fresh clone must still render. (The module docstring still calls itself "stage 1, called by nothing"; that predates the planner and is stale.) `no/sampa.py` converts the lexicon's X-SAMPA to IPA.

Resolution never guesses. Candidate rows are reduced to minimum certainty, a sentence-context UPOS tag then selects among survivors (`UPOS_TO_NST`; an unmapped tag logs rather than silently becoming "absent"), and a remaining disagreement is an explicit `AMBIGUOUS_*` outcome: `seg` is /sæi/ as a pronoun and /seːɡ/ as a verb.

The lexicon also now supplies syllable boundaries. `no/lexicon_syllables.py` aligns the lexicon's phoneme-space boundaries to letter space by a least-cost alignment over a grapheme table measured against the real lexicon. The invariant, stated in the module header, is that **boundaries and phonemes come from the same source, per word, never crossed**: a word gets both from the lexicon or neither. Before this, audio followed the lexicon while captions followed spelling. When readings cut a word differently, the one that elides least (the "most enunciated") is chosen once and supplies both. This is also why `_plan_against` gate 1 exists. Compound parts are resolved as the words they are, secondary stress decides what counts as a compound, and `-ende` is treated as an inflection rather than a constituent. A morpheme must contain a vowel, and a vowel-only inflection takes the stem's final consonant. The respelling workaround that `de` once used (`deh`) is gone from the breakdown (it existed only because the retired Edge adapter exposed no IPA channel); geminate lengthening such as `ett` stays, because a doubled consonant is genuinely ambisyllabic and `ett` is a real spelling. (`models/breakdown.py`'s docstring still mentions the old `deh` example.)

### 7.8 Slicing and forced alignment: in the tree, off the render path

This is the part of the old walkthrough most likely to mislead, so exact status matters. Slicing was built to fix the same `gen`/`ret` problem differently: render the whole word once at `-40%` (`slicer.py::PARENT_RATE`), forced-align it with a wav2vec2 CTC model, and cut each breakdown chunk out of that one render. The machinery is real, tested and still present:

- `app/audio/slicer.py`: `ChunkSlicer`, `SliceSpec`, `build_slicers`, `alignment_installed()`.
- `app/audio/alignment.py`: model-agnostic CTC Viterbi, frame-to-sample mapping.
- `app/audio/slicing.py`: pure DSP (splice refinement, fades, WSOLA stretch, RMS match).
- `app/plugins/languages/no/alignment.py`: the only module importing `transformers`/`torch`; registered through `AlignmentConfig` on the language config.
- `LessonRenderer._apply_slicing` and the `slicers=` constructor argument.

It was **retired from the app's render path** (`ab35609e`, the k318 epic). The lexicon `<phoneme>` path replaced it for every chunk that wants IPA, and the one remaining chunk class was judged by ear to sound better from plain synthesis than from a slice, which had audible speed artifacts. What is and is not wired, verified against the tree:

```bash
cd backend && grep -rn "build_slicers(" app scripts --include=*.py | grep -v "^app/audio/slicer.py"
echo ---
sed -n '/NOT WIRED, deliberately/,/Keep them/p' app/main.py
echo ---
grep -n "_slicers == {}" tests/test_main_lifespan.py
```

```output
scripts/render_slicing_ab.py:105:        ("SLICED", build_slicers([_LANGUAGE_CODE], tts, settings)),
---
    # NOT WIRED, deliberately (tunatale-k318.4). The audio slicer cut breakdown
    # chunks out of one whole-word render; the lexicon <phoneme> path replaced it
    # for every chunk that wants IPA, and the one chunk still left over was
    # judged BY EAR to sound better from plain synthesis than from a slice --
    # the slice had audible speed artifacts (tunatale-k318.2).
    #
    # ``app.audio.slicer`` and ``app/plugins/languages/no/alignment.py`` remain
    # in the tree, tested, and are reachable by re-adding ``build_slicers`` here
    # and passing the result to the renderer. Keep them: combining the lexicon
---
332:        assert test_app.state.renderer._slicers == {}, "the slicer was rewired into the default render path"
404:        assert test_app.state.renderer._slicers == {}
```

- **Not wired:** `app/main.py` calls `build_lesson_renderer(tts, db_map, settings)` with no `slicers`, so `app.state.renderer._slicers` is `{}`. `tests/test_main_lifespan.py` asserts this even with the capability gate open, so it cannot creep back. There is no settings flag (`audio_slicing_enabled` was deleted). `render_cost.py::estimate_render` passes `slicer_enabled=False` for the same reason.
- **Still wired, in one offline script:** `render_slicing_ab.py`, the A/B ear-check tool, passes `slicers=build_slicers(...)` into `build_lesson_renderer`, because comparing a sliced render with a plain one is its purpose. The re-render scripts (`regen_key_phrases.py`, `rename_section_titles.py`, `rebuild_lessons_from_story.py`) used to wire it too, so on a machine with `transformers` and `torch` installed a script re-render sliced Norwegian chunks that the app synthesised plainly. They now build the renderer exactly as the app does, and `tests/test_build_lesson_renderer.py::test_only_the_ab_tool_wires_the_slicer` fails if any other file names `build_slicers` or passes `slicers=`.
- **Fallback contract, if re-enabled:** failure is always fallback, never an exception. A word that will not syllabify losslessly, an out-of-vocab character, a degenerate alignment or a raising model all return `False` from `slice_to_file`, and the isolated-TTS clip is kept. The lossless-syllable precondition (`"".join(syllables) == word.lower()`) is what keeps the character-index walk in range.
- **Pacing stays honest:** `_assemble_section_parts` takes `pace_files` (the isolated render) separately from `play_files`, because a KEY_PHRASES pause equals the chunk's own duration and a shorter sliced chunk would shorten the gap the learner repeats into (measured on a real lesson: 6.5 to 5.1 minutes).

Combining the lexicon with the slicer is recorded in `main.py` as a live future idea that has not been designed. Diagnostics for tuning the DSP constants live in `backend/scripts/slice_report.py` and `backend/scripts/mutation_sweep_slicing.py`.

### 7.9 The renderer

`LessonRenderer` (`app/audio/renderer.py`) is built only through `build_lesson_renderer(tts, language_codes, settings)`, "the ONE way": it resolves each language's preprocessor, phoneme planner and SSML locale from the registry, and `tests/test_build_lesson_renderer.py` fails any other constructor call under `app/` or `scripts/`. A hand-built renderer silently drops whatever keyword it forgets, and an omitted locale means "declare nothing": a script re-render of the Tagalog key phrases once sent "ng abuloy" to a German voice as German, and it spelled "ng" out.

`render` has a deliberate shape, driven by memory:

1. **Title.** The lesson title is synthesised in the narrator voice and gained, then used as the first piece of the timeline.
2. **Synthesise every section concurrently** (`_synthesize_section`). Each section preprocesses its phrases (every shipped preprocessor is a pass-through; the slow-speed ellipses are inserted earlier, by `section_builder`, as `" ... "`), computes phoneme maps, and gathers one `_synth` per phrase. Concurrency is bounded by the adapter's semaphore, not here. A render-scoped memo keyed by `(text, voice, rate, phoneme-map, speak-locale)` makes an identical utterance (the same L2 line in the translated and en-translated sections) synthesise once. The phoneme map and locale are in the key because two phrases can share text and deserve different audio: measured on a real lesson, "en" collided six ways and the plain render won, so a planned buildup rung silently played untagged audio.
3. **Cancel, don't drain, on failure.** `asyncio.gather` stops waiting on the first failure but does not cancel siblings; left alone they synthesise into a temp directory that is being deleted. The `except BaseException` block cancels every section task and every memoised clip task. A TTS failure is nearly always provider-level, so ~150 queued clips would each fail in turn. Nothing is lost: finished clips are already in `tts_cache_dir`.
4. **Assemble one section at a time** (`_assemble_section_parts`): read each clip, apply voice gain, compute the pause that follows it from the clip's real duration, and record `(phrase_index, start_frame, end_frame)`. Offsets are accumulated in frames, not milliseconds, to avoid cumulative drift. The pieces are streamed to the encoder unjoined (`_write_audio_stream` consumes the list destructively, so each clip is released as it is handed over) and only the frame count and relative cues are kept.
5. **Layout and cues.** `assembly.py::lesson_layout` owns the piece order (`title, boundary, sec0, boundary, sec1, ...`, one boundary after the title and one between each pair of sections) and the boundary value; `cues.py::build_cue_manifest` turns the frame timings into `Cue` rows.

```bash
cd backend && uv run python -c "
from app.audio.assembly import lesson_layout
lay = lesson_layout([60_000, 90_000, 30_000], title_ms=2_000, boundary_ms=3_000)
for d, o, n in zip(lay.piece_descriptions, lay.piece_offsets_ms, lay.piece_durations_ms):
    print(f'{d:10} start={o:>7} dur={n:>6}')
print('boundaries:', lay.n_boundaries)
"
```

```output
title      start=      0 dur=  2000
boundary   start=   2000 dur=  3000
section_0  start=   5000 dur= 60000
boundary   start=  65000 dur=  3000
section_1  start=  68000 dur= 90000
boundary   start= 158000 dur=  3000
section_2  start= 161000 dur= 30000
boundaries: 3
```

**Pauses** (`pause_calculator.py::NaturalPauseCalculator`) are the learner's thinking and repeating time. A KEY_PHRASES line in the target language pauses for the clip's own duration (floor 500 ms) so the learner can say it back; slow-speed and slow-translated target-language lines pause 600 ms; everything else, and every English line, pauses 500 ms. Sections are separated by 3 s:

```bash
cd backend && uv run python -c "
from app.audio.pause_calculator import NaturalPauseCalculator
from app.models.lesson import SectionType as S
c = NaturalPauseCalculator()
print(f\"{'section':20}{'L2, 2.5 s clip':>15}{'English':>9}\")
for st in S:
    print(f'{st.value:20}{c.get_phrase_pause(2.5, 2, st, \"sl\"):>15}{c.get_phrase_pause(2.5, 2, st, \"en\"):>9}')
print('section boundary', c.get_section_boundary_pause(), 'ms')
"
```

```output
section              L2, 2.5 s clip  English
key_phrases                    2500      500
natural_speed                   500      500
slow_speed                      600      500
translated                      500      500
slow_translated                 600      500
en_translated                   500      500
slow_en_translated              600      500
section boundary 3000 ms
```

**Memory and CPU are first-class.** Production is a 953 MB box. A lesson held as float32 PCM is hundreds of MB, and these are the incident-driven rules:

- **One render at a time per process** (`render_service.py::_render_gate`, `max_concurrent_renders = 1`). On 2026-09-19 two renders ran at once, went to swap, took 47 and 58 minutes, and starved the machine badly enough that Docker's DNS timed out and Caddy returned 502. The gate is a lazily created semaphore keyed per event loop (a module-level one would bind whichever loop imported the module). A caller that arrives while another render holds it waits rather than being refused. This is a different guard from the per-id "already rendering" refusal in `app_state` (§12): the 2026-09-19 pair were two different ids.
- **Stream, never join.** Section writes and the full-lesson export go through `transcode.py::encode_audio_stream`, which feeds raw `f32le` to one ffmpeg process as chunks arrive. Peak production memory fell from about 493 MB to 130 MB (pinned by `tests/test_renderer_memory.py`). It is one encode, not a join of encoded parts: concatenating separately encoded Opus segments adds about 20 ms of padding per seam (cumulative, so every cue after the first seam drifts) and yields chained Ogg that soundfile will not open.
- **Lower CPU priority.** ffmpeg runs `ffmpeg_nice = 10` below the API, because a render is ~96% libopus CPU and a render in flight made the site very slow to respond. Opus runs at libopus effort 5, about 3.2x less CPU than the default 10 and indistinguishable in the user's blind ear test (8 cost the same as 10).
- **Sections only.** Since `guzo.3`, production renders pass `output_path=None`: the ~140 s concatenation of a ~345 s render is skipped because nothing plays the full file (a lesson is played section by section).

Delivery is `settings.audio_delivery_codec` (default Opus at 28 kbit/s, roughly 10-20x smaller than WAV, which matters to a phone on mobile data); `transcode.CODEC_EXT` and `EXT_MEDIA_TYPE` map codec to extension and HTTP type, keyed on the on-disk suffix so old WAV files and new Opus files both serve correctly:

```bash
cd backend && uv run python -c "
from app.audio.transcode import CODEC_EXT, EXT_MEDIA_TYPE, _FFMPEG_ARGS
print(CODEC_EXT)
print(EXT_MEDIA_TYPE)
print(_FFMPEG_ARGS['opus'])
from app.config import settings
print(settings.audio_delivery_codec, settings.audio_delivery_bitrate, settings.max_concurrent_renders, settings.ffmpeg_nice)
"
```

```output
{'opus': 'opus', 'aac': 'm4a', 'mp3': 'mp3', 'wav': 'wav'}
{'.opus': 'audio/ogg', '.m4a': 'audio/mp4', '.mp3': 'audio/mpeg', '.wav': 'audio/wav'}
['-c:a', 'libopus', '-compression_level', '5', '-f', 'ogg']
opus 28k 1 10
```

### 7.10 Persistence, re-render and reassembly

`render_service.py::render_lesson_audio` is shared by the HTTP route and the pipeline (§6, publish seam). It runs under the render gate and `_with_render_retries`, calls `renderer.render(lesson, None, section_paths=...)`, derives per-section cues (`derive_section_cues`), and replaces the lesson's `audio_files` rows (§6, `ContentStore`):

- One row per section, with its section index and type and its **section-relative** cues.
- One "full" row whose `file_path` is `NULL` but whose `cues_json` holds the full-timeline cues. The column is nullable for this (an existing DB is rebuilt inside a SAVEPOINT on open), every delete path skips `NULL` paths, and the zip download omits the `_00_Full` member unless a legacy row still has its file (`api/audio.py::download_lesson_zip`).
- Paths are stored as **basenames**, resolved on every read by `paths.py::resolve_audio_path` under `settings.audio_dir`. The recorded absolute path of a render did not survive leaving the machine that wrote it (of 104 real rows, 100 were absolute `/Users/...` paths); the audio bytes migrated fine, so a 404 was purely a bookkeeping failure. The resolver only locates a candidate and does not check existence, so a genuinely missing render still 404s.

**Partial re-render** (`reassemble_lesson_audio`) re-renders chosen section types (default: KEY_PHRASES) and reuses every other section's file byte for byte. It calls `renderer.render_section`, never `render`, because `render` would synthesise every phrase and, handed the existing section paths, overwrite the learner's real audio in place. The full-lesson timeline is rebuilt by decoding the pieces to PCM and encoding once (`_write_full_lesson_pcm`). An earlier version joined with ffmpeg's concat demuxer under `-c copy`, reasoning that copying packets cannot change audio; it can. Each stream-copied Opus seam adds about one frame (+20 ms), a 7-section lesson ran +112 ms ahead of its own captions by the end, and `ffprobe` could not see it because container duration cancels the pre-skip on both sides.

`api/audio.py` and `api/rerender.py` expose this:

- `POST /api/audio/render` renders a whole lesson; `POST /api/audio/rerender` takes a section selection (`resolve_section_selection`: nothing sent or every section ticked means the whole lesson; an empty or unknown selection is a 422 before any work).
- `POST /api/audio/render-estimate` prices the same selection through `render_cost.estimate_render` (billable characters, new versus cached clips, Gemini USD) so the UI can show the cost before the click.
- Review sessions have their own `render-estimate` and `rerender` endpoints (§6, §12). A second click while a render for the same id is in flight returns 409, and the in-flight marker is dropped in a `finally` so a failed render does not wedge the button. `ValueError` preconditions (no audio yet, section count changed) also map to 409, because the whole-lesson render is the fix.

`GET /api/audio/{audio_id}` serves a track from whichever language's store holds it (`_stores_to_search`), so a request made under one language header still finds a track from another.

### 7.11 Cues

A `Cue` (`app/audio/cues.py`) is `(index, start_ms, end_ms, section_index, section_type, phrase_index, role, language_code, text, ref)`. The `ref` ties a cue back to the lesson content: dialogue sections carry the line index and handle both orderings of the bilingual sections (L2 first, or English first, with a lookahead so a translation pairs with the right L2 line); KEY_PHRASES cues carry `{"kind": "key_phrase", "target_index": k}` or `{"kind": "narration"}`.

Key-phrase grouping is read off the stored section (`section_builder.key_phrase_groups`), not recomputed from today's breakdown rules. A stored section is a snapshot laid out under the rules of its day; re-deriving the phrase count from current rules made 8 of 11 stored Norwegian lessons die with a phrase-count mismatch after all the TTS had already run (2026-09-29). Structural inconsistency is still loud: a group count that disagrees with `lesson.key_phrases`, or a timing entry that lands in no group, raises. The cues feed the player's caption highlighting and sentence-level seeking (§13) and the reader's word/audio association (§8).

### 7.12 Other synthesis callers

Not everything speaks through the lesson renderer. `app/cards/media/tts.py` synthesises vocab-card audio through the same `get_tts_service()` and the same per-language voice and locale (§11), and `app/audio/cloze_tts.py` plus `backfill_cloze_tts.py` synthesise cloze sentence and word audio (the backfill un-clozifies the sentence before synthesis, so the voice reads the full sentence, not the blank). All of it shares the one cache directory, so it is subject to the same character ledger and the same pricing rule.

### 7.13 Working on audio: a checklist

- Before any render that can miss the cache, run `report_render_cost.py` and put the incremental number in your report (§7.4). Never print or log `AZURE_SPEECH_KEY`; compare keys by hash.
- A new `phonemes` or `speak_locale` behaviour must leave the cache key unchanged when its argument is `None`, or it re-synthesises the corpus.
- Anything that adds a TTS call site must go through `get_tts_service()` and `build_lesson_renderer` so the ledger, router and locale wiring come with it.
- Gains belong in `tts_voice_gain_db`, applied at assembly; never inside synthesis.
- If a chunk sounds wrong, check in this order: is a planner returning `None` (plain render)? is it a stale stored lesson whose caption text no longer matches the span (gate 5)? is it a Gemini take worth re-rolling (`reroll_tts_clip.py`)? only then suspect the slicer, and only in a script-rendered lesson.

## 8. Words & the Learning Loop

This chapter is the learner-facing half of the SRS: how the words in a lesson transcript become cards, how a card's strength is shown back on the page, and how listening, reading and checking your work move a word along. It starts where §6 (content generation) and §7 (audio) stop, with a stored `Lesson` whose `NATURAL_SPEED` section holds the dialogue, and ends where §9 (the SRS engine: FSRS, the queue, Anki parity) and §10/§11 (sync, card minting and media) take over. Almost everything here is keyed on a **lemma**, so the lemmatizer is the foundation. Everything the learner sees (colour, rails, bold, blur) is derived from per-direction card state and never stored.

### 8.1 The loop in one page

     lesson (NATURAL_SPEED text)
       │  tokenize  ──►  lemmatize in sentence context  ──►  resolve each lemma to a card
       ▼
     transcript  (WordToken per word: state, bands, due, inflectable ...)   GET /content/{id}/transcript
       │
       ├─ READ    tap a word      → create base card / grade Good / read-ahead / undo
       ├─ LISTEN  listen preview  → POST /listen: create ≤ budget, stage ratings
       └─ CHECK   "Check your work" → review the staged cards → commit-pending / per-card grade
       ▼
     cards (collocations + two directions)  ──►  review queue (§9)  ──►  sync to Anki (§10)
       ▲                                              │
       └────────── mastery: bands / rails / lesson roll-up ◄─┘

Three design commitments shape everything below.

- **The lemma is the unit.** A card is the dictionary form of a word; `mize`, `mizo` and `mizi` are all `miza`. Lemmatizer accuracy is therefore a hard dependency, which is why the engine is a per-language property (§8.2, §8.3).
- **Listening and reading are recognition evidence.** Hearing or reading a word can only ever grade its *recognition* direction. Production is drilled in the review queue, never by listening (§8.6, §8.9).
- **Preview and commit are one predicate.** Every decision the listen preview shows (what a listen would create, stage or defer) is made by the same functions the commit path calls. Two copies of a rule drift, and that drift shipped once as a bug class (`6a5c718`: the preview offered rows the commit would not act on). The chapter points at these shared functions as it goes.

### 8.2 Tokenizing and lemmatizing

Tokenization is deliberately dumb: split on whitespace, strip leading and trailing punctuation, keep interior hyphens.

```bash
cd backend && sed -n '/^_PUNCT/,$p' app/srs/tokenizer.py && uv run python -c "
from app.srs.tokenizer import tokenize
print(tokenize('Koliko stane? – Dve kavi, prosim!'))
print(tokenize('Hun er «sjef» i Sør-Norge.'))"
```

```output
_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Split text on whitespace and strip leading/trailing punctuation from each token.

    Interior punctuation (e.g. hyphens in compound words) is preserved.
    Returns only non-empty tokens.
    """
    return [t for raw in text.split() if (t := _PUNCT.sub("", raw))]
['Koliko', 'stane', 'Dve', 'kavi', 'prosim']
['Hun', 'er', 'sjef', 'i', 'Sør-Norge']
```

Punctuation is not thrown away for display: `transcript.py::_extract_punct_pairs` walks the raw text and records each token's prefix and suffix punctuation, so a reconstructed sentence keeps its `?`. That matters because the sentence becomes a cloze card's `source_sentence`.

The `Lemmatizer` protocol in `app/srs/lemmatizer.py` has three methods, and the sentence-level one is the load-bearing one:

```bash
cd backend && grep "^class \|^def \|^    def " app/srs/lemmatizer.py | grep -v "    def _\|_parse"
```

```output
class TokenAnalysis:
class Lemmatizer(Protocol):
    def lemmatize(self, word: str, language_code: str) -> str: ...
    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
class LowercaseLemmatizer:
    def lemmatize(self, word: str, language_code: str) -> str:
    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
class _StanzaFamilyLemmatizer:  # pragma: no cover — requires PyTorch pipeline; opt-in only
    def lemmatize(self, word: str, language_code: str) -> str:
    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
class ClasslaLemmatizer(_StanzaFamilyLemmatizer):  # pragma: no cover — requires classla/PyTorch; opt-in only
class StanzaLemmatizer(_StanzaFamilyLemmatizer):  # pragma: no cover — requires stanza/PyTorch; opt-in only
def get_lemmatizer(language_code: str) -> Lemmatizer:
def headword_lemma(word: str, language_code: str) -> str:
def model_version_for(lemmatizer: Lemmatizer) -> str:
def _serialize_analyses(analyses: list[TokenAnalysis]) -> str:
def _deserialize_analyses(data: str) -> list[TokenAnalysis]:
def analyze_sentence_cached(
def lemmatize_surfaces_in_context(
```

`analyze_sentence(sentence, language_code)` returns one `TokenAnalysis` per token: surface, lemma, UPOS and UD features (case, number, person, gender, definiteness, tense, verb form). Lemmas are POS-dependent and only resolvable in context. Slovene `dobro` is the adverb `dobro` alone but the adjective `dober` in *Vse je dobro*, and `hotel` is the verb `hoteti` alone but a noun in *To je hotel*. `lemmatize_surfaces_in_context` therefore analyses the whole sentence once, maps each surface to its in-context lemma, and falls back to a single-word `lemmatize` only when a surface is missing from the analysis (a tokenization mismatch). Lemmas are lowercased on the way out: the card keyspace is lowercase (`import_seed` stores `front.lower()`), and classla capitalises proper-noun lemmas (`Ženeve` becomes `Ženeva`), which would otherwise miss the lowercase card.

**Engines are a property of the language, not a global.** The plugin declares `LanguageConfig.lemmatizer_type`; `get_lemmatizer(language_code)` in `app/srs/lemmatizer.py` resolves it, caches one engine per language (a Norwegian request is never analysed by the Slovene model), and applies `settings.lemmatizer_type` as an override:

| `settings.lemmatizer_type` | Behaviour |
|---|---|
| `lowercase` (default, test pin) | every language gets `LowercaseLemmatizer`: deterministic, no NLP dependency |
| `table` (production) | each language's shipped lemma table (§8.3); a language with no table stays lowercase |
| anything else (a laptop's opt-in) | the language's own engine, with a logged fallback to lowercase if its package is missing |

```bash
cd backend && uv run python - <<'EOF'
from app.languages import known_language_codes, get_lemmatizer_type, get_lemma_table_path, get_lemma_plausible
print("code  engine     lemma table                 plausibility guard")
for c in sorted(known_language_codes()):
    p = get_lemma_table_path(c)
    print(f"{c:5} {get_lemmatizer_type(c):10} {(p.name if p else '-'):27} {'yes' if get_lemma_plausible(c) else '-'}")
EOF
```

```output
code  engine     lemma table                 plausibility guard
ceb   table      cebuano_lemmas.tsv.gz       -
en    lowercase  -                           -
no    stanza     stanza_lemmas.tsv.gz        yes
sl    classla    -                           -
tl    table      tagalog_lemmas.tsv.gz       -
```

Slovene uses `ClasslaLemmatizer` and Norwegian `StanzaLemmatizer` (both subclass `_StanzaFamilyLemmatizer`, both `# pragma: no cover` because they need PyTorch). They are never imported at module level: the heavy import lives inside `_ensure_pipeline()`, so a process that never opts in never loads torch. Models are downloaded once by hand (`classla.download("sl")`, `stanza.download("nb")`); the package is managed by uv's default dependency groups, the model files are not. Tagalog and Cebuano have no sentence model at all; their engine *is* the table.

Three things sit around the engines.

- **Persistent analysis cache.** `analyze_sentence_cached` stores each analysed sentence in `lemma_analysis_cache` keyed `(sentence, language_code, model_version)` (`db_lemma_cache.py`). `model_version_for` returns `""` for cheap engines, which skips the database entirely, and appends `_ANALYSIS_SCHEMA_REV` for expensive ones, so growing `TokenAnalysis` invalidates old rows instead of replaying empty new fields. A warmed transcript costs a lookup rather than seconds of NLP; the heavy callers still offload to a worker thread (`anyio.to_thread.run_sync`) so they cannot stall the event loop.
- **Lemma plausibility.** Stanza sometimes returns a fragment (`trøtt` becomes `trø`, `snømenn` becomes `snøm`). A plugin may register `lemma_plausible_fn`; Norwegian does, checked against the NST word list. `api/srs.py::_card_key_for_lemma` is the single place that decides what a card is keyed on: an implausible lemma falls back to the surface as it appeared, and function words never take the fallback. The commit path and the preview both call it, so they cannot disagree about a word's identity (bd `tunatale-q5pl`: the preview once offered the non-word `snøm` for a real `snømenn` card). A language with no predicate (Slovene, English) means "cannot tell" and keeps the lemma.
- **Fixed pairs.** `app/srs/multiword.py::is_trapped_occurrence` suppresses one occurrence of a word that is half of a fixed expression, so *i går* ("yesterday") is not carded as the verb `gå`. Only that occurrence is suppressed; *Han går hjem* still yields the verb. The pair list is plugin data (`multiword_traps.txt`).

`headword_lemma` covers the opposite direction. When an Anki deck is imported, a table-engine language keys its single-word cards on the root the table gives (`kumain` on `kain`) so that cards TunaTale mints later for the same verb hit the imported one. Every other language keeps `word.lower()`.

### 8.3 Torch-free table lemmatizers

The production host is a small VM that cannot carry the PyTorch pipeline. Measured on the e2-micro (`tunatale-kbb.18`): 218 s to load the model, 453 ms per sentence, and the live API paged out while it ran. `app/srs/lemma_table.py` is the replacement, and its premise is a measured fact. A Stanza lemmatizer is a function of `(word, UPOS)` alone; context matters only through *which UPOS the tagger picks*. Lemmatizing each cached `(surface, upos)` out of context reproduced in-context Stanza on 1,439 of 1,439 distinct triples. So a plugin ships a gzipped TSV of `surface, upos, lemma, is_default` rows produced offline by the real model, with exactly one default row per surface (the reading the tagger gives the bare word). `TableLemmatizer` serves the default unless the caller supplies a context-chosen tag.

```bash
cd backend && uv run python - <<'EOF'
import gzip, pathlib, tempfile
from app.srs.lemma_table import TableLemmatizer
rows = ["#source_version=demo-1",
        "så\tCCONJ\tså\t1", "så\tVERB\tse\t0", "jeg\tPRON\tjeg\t1", "ser\tVERB\tse\t1",
        "deg\tPRON\tdu\t1", "noe\tPRON\tnoe\t0", "noe\tDET\tnoen\t1"]
p = pathlib.Path(tempfile.mkdtemp()) / "demo.tsv.gz"
with gzip.open(p, "wt", encoding="utf-8") as f:
    f.write("\n".join(rows) + "\n")
lem = TableLemmatizer("no", p)
s = "Jeg ser deg, så jeg ser noe."
print("default readings:", [(a.surface, a.lemma) for a in lem.analyze_sentence(s, "no")][:8])
print("så tagged VERB  :", [a.lemma for a in lem.analyze_sentence_with_tags(s, "no", {4: "VERB"}) if a.surface == "så"])
print("så tagged NOUN  :", [a.lemma for a in lem.analyze_sentence_with_tags(s, "no", {4: "NOUN"}) if a.surface == "så"], "(not a reading: ignored)")
print("OOV / digits    :", [(a.surface, a.lemma, a.upos) for a in lem.analyze_sentence("Hei 42 !", "no")])
lem.close()
EOF
```

```output
default readings: [('Jeg', 'jeg'), ('ser', 'se'), ('deg', 'du'), (',', '$,'), ('så', 'så'), ('jeg', 'jeg'), ('ser', 'se'), ('noe', 'noen')]
så tagged VERB  : ['se']
så tagged NOUN  : ['så'] (not a reading: ignored)
OOV / digits    : [('Hei', 'hei', ''), ('42', '42', 'NUM'), ('!', '$!', 'PUNCT')]
```

Points worth knowing:

- **Format and build.** The committed artifact is the `.tsv.gz`; its first line is `#source_version=<v>`. The indexed SQLite beside it is a gitignored build artifact, created on first use, rebuilt when the extract's SHA changes (`ensure_lemma_table_db`), or built ahead of time with `python -m app.srs.lemma_table` (the Dockerfile does this). A table with a surface lacking exactly one default row refuses to build: a table that cannot answer must not ship.
- **Cache compatibility.** A table records the model version it was built from. Rows the *real* model cached under that version are exact, so `analyze_sentence_cached` reads them first (`compatible_cache_versions`) and the table's own rows live under a separate key. A dev machine running the real model never mistakes table output for its own.
- **Where the tables come from.** Norwegian: `backend/scripts/build_stanza_lemma_table.py` lemmatizes every NST surface under each UPOS its NST tag can stand for (about 756k rows; the NST pronunciation lexicon is covered in §3). Tagalog: `backend/scripts/build_kaikki_lemma_table.py` from the kaikki Wiktionary extract, with verb lemmas as ROOTS (`kumain` becomes `kain`); the card *fronts* the actor-focus infinitive through `LanguageConfig.verb_headword_fn` (`tl/verb_headword.py`). Cebuano: `backend/scripts/build_cebuano_lemma_table.py`, plus a hand-curated `closed_class.tsv` and spelling-variant folding. Slovene has no table yet, so under production's `table` setting (`docker-compose.yml` pins `LEMMATIZER_TYPE: table` and serves `sl` alongside the other three) Slovene falls back to `LowercaseLemmatizer`, and its inflected forms do not resolve to their lemma cards there.
- **Context from an LLM, once, at publish time.** About 7% of lesson tokens have readings with different lemmas (`så` is *se* or *så*; `noe` is *noe* or *noen*), and the default agreed with in-context Stanza only 415 of 516 times. `app/srs/lemma_resolver.py` closes that gap: at publish (`generation/publishing.py::publish_lesson`) one batched LLM call per 20 sentences picks the *tag* for each ambiguous token (call site `CallSite.LEMMA_RESOLVE`) and writes finished analyses into `lemma_analysis_cache`. The choice is closed (a tag among the table's own readings, and `parse_reply` drops anything else), so the LLM can never put into the card keyspace a lemma the model would not have produced. Every later reader (transcript, listen preview, card matching) hits the cache and never waits. It never raises; on failure the default readings stand.
- **Boot guard.** In production, `settings.lemmatizer_type != "table"` is a startup problem (§2): under `lowercase`, `deg` is not `du` and a known word shows as NEW.

### 8.4 Function words, closed classes and clozes-only verbs

`app/srs/function_words.py` decides whether a word is carded as a vocabulary card (recognition plus production) or as a **production-only cloze**. A preposition has no meaningful recognition card. The policy is data: one `data/function_words.json` per language plugin, with a UPOS `pos` set, an `include` list, an `exclude` list, curated `glosses` and a `clozes_only_verbs` list.

`is_function_word(token, language_code, upos=...)` is POS-first. When the analyser supplies a UPOS in the language's closed-class set, the token counts, so the whole Slovene `biti` AUX paradigm (`sem`, `si`, `je`, `smo`, `ste`, `so`) is caught without enumerating surfaces. `include` adds words the tagger misses or mistags, and is the *only* signal under `LowercaseLemmatizer`, which emits no UPOS. `exclude` force-removes. `is_function_word_for` ORs the check over a lemma and all its surfaces, because the dictionary lemma may not itself be a function word (classla maps `sem` to `biti`) while an inflected surface carries a closed-class tag.

Two vetoes run first. A drawn spatial word and a drawn personal pronoun are never function words, whatever the tag: the user decided those are picture cards, not clozes (`tunatale-hvj0`, `tunatale-3rxu`). The pronoun veto applies only when the tag is `PRON` or absent, since Norwegian `den`/`de` tagged `DET` are articles and keep the cloze route.

A **clozes-only verb** (`is_clozes_only_verb`, Slovene `biti`) is suppletive: it has no base card of any kind, only per-person conjugation clozes, and those are ungated. `/items/base` refuses to mint a base for one (409). `make_cloze_text(surface, sentence)` wraps every word-bounded occurrence of the surface in `{{c1::...}}`, case-insensitive but case-preserving and idempotent, and the *surface as it appeared* is blanked, not the lemma. How clozes are generated, judged and written to Anki is §11; this chapter only needs to know that a clicked or listened-to function word becomes one.

Each plugin also registers an `A1Morphology` bundle (`app/srs/a1_morphology.py`): which UD analyses map to which TT feature strings (`verb:1sg` for Slovene, `noun:def:sg` for Norwegian) and which count as A1. It gates which inflected surfaces can be offered as inflection clozes (§8.8).

### 8.5 Resolving a lemma to a card

"Is this word already a card?" is asked by the transcript, the listen preview and `/listen`. They must answer identically, or the reader shows a word as tracked that a listen then creates a duplicate of (the `6a5c718` class again). One resolver chain in `app/srs/transcript.py` serves all three, tried in this order:

1. **Lemma lookup, UPOS-aware.** `resolve_lemma_card(db, lemma, upos)`. One spelling can be several cards (Norwegian `om` is "if", "again" and "about", three vocab rows told apart by the deck's Word class in `disambig_key`). When exactly one vocab row's Word class matches the sentence's UPOS, that is the card; any less certain case (no UPOS, a single card, no match, two matches) keeps first-by-id, so nothing that resolved before resolves worse. `SCONJ` and `CCONJ` are one deck class.
2. **Surface fallback.** A surface-keyed row (a greeting `dobrodošli` whose lemma `dobrodošel` has no card) is graded instead of spawning a duplicate. It has its own cache: sharing one dict with the lemma cache once let a verb surface hand its card to a later token whose lemma was genuinely that surface (`tunatale-klh`).
3. **Spelling-variant card.** A front listing variants (`mot, imot`) is indexed by each accepted spelling (`_build_variant_index`), for languages with a `variant_separator`.
4. **The deck's own Inflections table**, as a last resort (`_build_inflection_index`). Norwegian decks spell out `fersk / ferskt / ferske`, and Stanza reduces some neuter forms wrongly, so the deck's table answers "is this surface carded?" for words no lemmatizer handles. It is consulted last so a form that is another card's headword resolves to *that* card (16 such forms in the real deck), and forms claimed by more than one card are dropped rather than arbitrated, because picking a winner would grade a card the learner never met.

Two refinements prevent duplicate rows. A listen also detects **two keys for one card** (`_lemmas_losing_a_shared_card`, bd `tunatale-og4d`): Stanza over-strips `mappen` to `mapp`, `_card_key_for_lemma` rejects the fragment and keys on the surface, and that surface resolves through the Inflections table to the card `mappe` already has a row for. The winner is the lemma whose card key equals the card's own stored lemma, so the surviving row carries a real headword. And a **single-word key phrase** resolves to the same card as the lemma row for that word (always true in a review session, whose key phrases are individual vocabulary items), so `_kp_claimed_collocation_ids` makes the word pass stand down and the key-phrase row survives. In both cases **identity is the collocation id, never the text**: `ta hensyn` and `hensyn` are two cards.

**A spelling with several cards resolves by word class, then by the lesson's gloss** (bd `tunatale-ceuc`). `transcript.py::choose_lemma_card` is the one resolver the reader and a listen share. Two kinds of `disambig_key` exist in the decks. A word-class key (Norwegian `om`: conjunction / adverb / preposition) is decided by the token's tag when exactly one card's class matches and no other card could. A sense key (Slovene `ura`: hour / clock; Tagalog `linggo`: week / Sunday) has no class, so the lesson's own gloss decides, by sharing a word with one card's translation (`srs/sense_match.py`: content words outrank grammar words, which still count because for a function-word card they are the sense — `vår` = spring / our). A sense-keyed card stays a candidate beside a class-keyed one. A gloss that matches no candidate, or two equally, is *undecided*: the reader keeps the old first-by-id answer so the word still shows as tracked, and a listen grades none of the cards. A lone card is never judged against the gloss, and a lesson with no glosses resolves exactly as before. The reader resolves per token; a listen pools every gloss the lesson gave the lemma's surfaces and decides once per lesson.

**A word inside a phrase card belongs to the phrase** (bd `tunatale-ceuc`). `_analyze_lesson_words` runs the reader's own span index (`transcript.py::build_phrase_index`, so the two cannot disagree about where a phrase is) and leaves every occurrence inside a matched phrase card out of the lemma maps: `gang` in *med en gang* ("at once") is not a use of the card "hall". The exclusion is per occurrence, so a word the lesson also uses on its own is graded as before; a word used only inside phrases is neither graded nor offered for creation. The phrase card is graded in its place through `_listen_key_phrases`, which appends each matched card to the lesson's key phrases so it rides the same gates, budget and `kp_ratings`. `match_spans` is greedy longest-first, so nested phrases yield the outermost card. Two limits: a matched card that was never introduced (recognition NEW) gets no row, because on the key-phrase rails a NEW row leads the daily introduction budget ahead of every frequency-ranked word; and before this, a listen graded a phrase card only if the lesson named it a key phrase, which on the live Norwegian deck was 0 of 75.

### 8.6 The transcript serializer

`extract_transcript(lesson, db, lemmatizer, today)` turns a `Lesson` and the SRS database into a `TranscriptData`: one `DialogueLine` per L2 phrase of the `NATURAL_SPEED` section (narrator and English lines are skipped by language code), one `WordToken` per word. It runs the lemmatizer once per phrase and joins to card state. `build_transcript_payload` in `api/srs.py` serialises it, and the same builder serves a lesson or a review session, since both are stored as a `Lesson` (`GET /api/srs/content/{content_id}/transcript`, resolved by `ContentStore.get_readable_content`).

```bash
cd backend && uv run python - <<'EOF'
from app.models.lesson import Lesson, Section, SectionType, Phrase
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import LowercaseLemmatizer
from app.srs.transcript import extract_transcript

with SRSDatabase(":memory:") as db:
    db.add_collocation(SyntacticUnit(text="dober", translation="good", word_count=1, difficulty=1, source="llm", lemma="dober"))
    lesson = Lesson(title="demo", language_code="sl", sections=[Section(section_type=SectionType.NATURAL_SPEED, phrases=[
        Phrase(text="Dober dan!", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")])])
    for w in extract_transcript(lesson, db, LowercaseLemmatizer()).dialogue_lines[0].words:
        print(f"{w.surface:6} srs={w.srs_state:8} active={w.active_direction} understand={w.understand_band} produce={w.produce_band} suffix={w.suffix_punct!r}")
EOF
```

```output
Dober  srs=new      active=recognition understand=new produce=new suffix=''
dan    srs=unknown  active=None understand=None produce=None suffix='!'
```

`dober` has a card (NEW, never studied), `dan` has none. Resolution per token is **inflection-first**: an exact-surface inflection cloze wins over the base card, which wins over "unknown"; a lemma on the card-less ignore list reads `ignored`. A multi-word collocation overlapping the token is attached by `match_spans` (longest match first, up to five tokens), keyed on lemmas or, for languages with `get_phrase_match_exact_form` (Tagalog), on casefolded surfaces. Each spanning word carries the enclosing collocation's state, progress, due flag and bands, so the frontend can draw one styled span.

The `WordToken` fields fall into four groups:

| Group | Fields | Used for |
|---|---|---|
| Identity | `surface`, `lemma`, `prefix_punct`, `suffix_punct`, `srs_item_id`, `translation`, `card_type` | display, and which card a click targets |
| Reader paint | `understand_band`, `produce_band`, `*_stability`, `*_progress`, `is_due`, `overdue_ratio`, `production_due` | rails, bold weight, blur-as-cloze (§13) |
| Lesson roll-up | `recognition_state`, `recognition_is_due`, `well_known`, `progress` | the mastery line (§8.7) |
| Click affordances | `active_direction`, `active_state`, `recognition_reviewable`, `inflectable`, `inflection_feature`, `known_marked` | what the popover offers (§8.8, §8.11) |

Three behaviours are easy to get wrong.

- **The reader reads and grades RECOGNITION.** `resolve_active_direction` returns `RECOGNITION` whenever the item has one, and `PRODUCTION` only for clozes and production-only cards. An earlier version handed over to production once recognition reached REVIEW; the 2026-09-15 reader-bolding report was exactly that bug, because the frontend bolds on `is_due`, computed on the active direction. On one session, 16 words were in that day's review queue and only 3 were bold. Reading and listening are recognition evidence by construction, so the review queue stays the only surface that drills production.
- **Due uses the Anki day, not midnight.** `today` defaults to `anki_today()` (§9), so a card due tomorrow by calendar date is not bolded early in the `[midnight, 04:00)` window. A buried card has `due_at` today but is *not* due, so it is not bolded. `is_due` for a bold uses `_is_due`, which requires an on-the-ramp state (learning, review, relearning).
- **A cloze can carry a word's production.** A word that cannot be pictured gets its production card as a separate cloze note, and therefore a separate collocation. Without joining it back (`db.get_covering_cloze`) the word is measured on its vocab row alone, whose production direction is absent, and it would read half-mastered forever with the completing card sitting unread in the next row. The same join feeds the `inflectable` gate.

### 8.7 Mastery: bands, stability and "well known"

`app/srs/mastery.py` is a pure module that turns direction state into what the learner sees. Two principles carry the design.

**Mastery reads stability, never retrievability.** FSRS regulates R toward desired retention, so a review card's R lives in a narrow band and cannot tell a freshly graduated card from a long-mastered one; every reviewed word would render the same green. Stability grows monotonically as a word is learned, so it is what the colour ramp tracks. `component_mastery` maps a REVIEW card's stability onto `[0, 1]` on a log curve with a 120-day ceiling; NEW is 0.0, learning and relearning sit at a fixed 0.15 floor, and KNOWN is 1.0. It deliberately ignores `last_review`, so a marked-known card with high stability and no review timestamp still reads mastered.

**A word has two sides.** Understand (the recognition card) and Produce (the production card) are shown as separate *bands* named by how long the memory holds, not as a percentage (bd `tunatale-yh47`):

```bash
cd backend && uv run python - <<'EOF'
from datetime import datetime, UTC
from app.models.srs_item import DirectionState, SRSState, Direction
from app.srs.mastery import direction_band, component_mastery
def ds(state, s=1.0):
    return DirectionState(direction=Direction.RECOGNITION, state=state, stability=s, due_at=datetime(2026, 10, 1, 4, tzinfo=UTC), reps=1)
print("state / stability   band       mastery")
for label, x in [("new", ds(SRSState.NEW)), ("learning", ds(SRSState.LEARNING)), ("review s=3", ds(SRSState.REVIEW, 3)),
                 ("review s=10", ds(SRSState.REVIEW, 10)), ("review s=45", ds(SRSState.REVIEW, 45)),
                 ("review s=200", ds(SRSState.REVIEW, 200)), ("known", ds(SRSState.KNOWN))]:
    print(f"{label:19} {direction_band(x):10} {component_mastery(x):.2f}")
EOF
```

```output
state / stability   band       mastery
new                 new        0.00
learning            learning   0.15
review s=3          days       0.23
review s=10         weeks      0.48
review s=45         months     0.80
review s=200        solid      1.00
known               solid      1.00
```

Band edges are inclusive at the lower end: stability 7.0 is "weeks", 30.0 "months", and 180.0 is the top ("solid", "half a year +"). A `None` direction reads `"none"` (no card), which is distinct from `"new"` (a card never studied); the frontend treats both, and an absent band, as one "unstarted" state (`masteryBands.ts::isUnstarted`), because whether a row exists is not something the learner can act on. A buried card that has been reviewed keeps its band: burying hides a card for a day and does not weaken the memory. `band_stability` only quotes a number for a real strength band and never for KNOWN, so the reader and the listen preview cannot quote different stabilities.

`compute_mastery_progress` is the mean over a word's whole component set (recognition, production, every inflection cloze, any covering cloze), with suspended components excluded. An **absent production component scores 0.0**, exactly like a NEW one. Without that, any recognition-only card past the 120-day ceiling clamped to a flat 100%, which described 2,990 of 3,017 collocations in the Norwegian deck (18.3% read as fully mastered, against 0.3% of the two-direction Slovene deck). Cloze notes are production-only by design and are never penalised. Presence is checked before the suspended filter, so one deliberately suspended production card is not scored twice. Adding an inflection cloze adds an `m ≈ 0` component, so learning a new form *lightens* the lemma: the end state is expandable, never "100% and done".

**Two different "well known"s, on purpose** (bd `tunatale-38z9`). The top colour band is a stability rule (`WELL_KNOWN_STABILITY_DAYS = 180`). The listen preview's stop-asking horizon, `is_well_known`, is a *due-date* rule: next review at least `WELL_KNOWN_DUE_DAYS_AHEAD = 90` days out. They answer different questions. The band says "how well do you know this" and must not move while you are not reviewing, so it reads stability. The horizon says "when will I next see this", a schedule fact, so it reads the schedule. Welding them let a 15-day difference in an FSRS *estimate* decide whether TunaTale kept quizzing a word. The earlier due-date rule used 365 days, which at desired retention 0.95 matched 2 cards of 1,607 on the live Norwegian deck; 90 matches 199, against 194 under the stability rule it replaced, so the change is a change of rule rather than of how much TunaTale asks. Learning and relearning cards are never well known however far out they are parked, KNOWN always is, and a missing `due_at` reads as not-well-known.

On the page, `frontend/src/lib/mastery.ts::lessonMastery` dedupes the transcript by lemma (first occurrence wins), excludes ignored words, and returns an overall percent plus per-side percents and band histograms. It buckets words by *recognition-side* state into new, learning, due, review and known, which is what the lesson page's mastery line shows. Frontend rendering (rails, hue ramp, colour-blind hedges) is §13.

### 8.8 The word-learning state machine

Each lemma moves `BASE (recognition, then production) → INFLECTIONS`, and not every lemma has every stage. The locked principle: **gates govern introduction only, never review.** Once introduced, recognition, production and every inflection cloze review in parallel.

| Word type | Enters as | Then |
|---|---|---|
| Content word | vocab note: recognition and production directions, both NEW | recognition first; production is held (below) |
| Function word | production-only cloze, the surface blanked in its sentence | no recognition stage |
| Clozes-only verb | no base card | per-form conjugation clozes only, ungated |
| Inflected form of a learned word | on click: a morphology cloze (production only) | reviews in parallel with the base |

- **Recognition before production** (Layer 65, `docs/anki-parity-layers.md`). The production direction is held out of the new pool until its recognition sibling graduates past the learning arc: `SRSDatabase.get_new_items` appends a `NOT EXISTS` clause to the production direction only. Recognition is never gated, and a cloze note has no recognition row so the clause is trivially true for it. This is parity-restoring, not a TunaTale-only rule: the user's Anki orders new cards by deck position and `create_note` puts the recognition card at the lower position, so real Anki introduces recognition first (604 vs 36 across the user's 640 paired notes). The earlier production-first behaviour was the bug. A related, newer mechanism is *just-in-time production minting* on a pacing budget for recognition-only Anki notes, run as a sync phase (§10/§11); it extends the same recognition-then-production order to decks that start with recognition only.
- **Inflection clozes are click-only** (Layer 66). `/listen` stopped auto-minting morphology clozes: a rare form that never gets clicked should never become a card, and auto-minting on every listen flooded the deck. The only mint path is `POST /api/srs/inflection-clozes`, called when the user clicks an inflected surface that appeared in a lesson. It is gated on the base word's *production* being REVIEW or KNOWN, looked up on the vocab row or, failing that, on its covering cloze (409 otherwise), refuses `surface == lemma` (422, nothing to cloze), is idempotent by guid, and asks the LLM to gloss the specific inflected form (`boste` is "you will be", not the base meaning). The two 409 messages are deliberately different facts: "has no production card yet" means the sync's promotion phase has not minted it, "not yet learned" means it is waiting on the learner. The transcript's `inflectable` flag is true on exactly the words where that endpoint would succeed: surface differs from lemma, the form is an A1 feature for the language, base production is REVIEW or KNOWN, and no cloze for that surface exists.
- **Clicking an unknown word creates its base card.** `POST /api/srs/items/base` branches on word type, using the surface's UPOS from the cached sentence analysis (falling back to the lemma plausibility rule for the headword and to `get_gender_article` for nouns). A VERB gets a fresh LLM gloss of the dictionary form, because the transcript gloss is the conjugated in-context meaning ("I will show"); the gloss is generated for the card *front* in its sentence, since a Tagalog root `punta` is also a Spanish loan meaning "point". Creation goes through `_persist_new_card` and the card-adding contract (`add_collocation`, no Anki ids; `sync_create_new` mints and links them, §10). A cloze created this way carries the sentence's English as its Back Extra.

### 8.9 Listening: the preview and `POST /listen`

"I listened to this lesson" is a deliberate, previewed action rather than a checkbox. `GET /api/srs/content/{content_id}/listen-preview` (`get_listen_preview`) is strictly read-only and classifies every candidate; `POST /api/srs/listen` (`mark_lesson_listened`) then acts on the user's edits to that preview. It resolves a lesson or a review session through `ContentStore.get_readable_content`, so the same flow serves both.

**Classification.** `_listen_grade_class` places a card's *recognition* direction into one of five classes, and the day window is the Anki-day rollover from `_listen_day_window`, never local midnight:

```bash
cd backend && uv run python - <<'EOF'
from datetime import datetime, date, timedelta, UTC
import datetime as dt
from app.models.srs_item import DirectionState, SRSState, Direction
from app.api.srs import _listen_grade_class, _listen_deferred_reason
today = date(2026, 10, 1)
start = datetime(2026, 10, 1, 4, tzinfo=UTC); end = start + timedelta(days=1)
eod = datetime.combine(today, dt.time.max).isoformat()
def ds(state, due, lr=None):
    return DirectionState(direction=Direction.RECOGNITION, state=state, due_at=due, last_review=lr, reps=3)
rows = {"NEW": ds(SRSState.NEW, start), "LEARNING": ds(SRSState.LEARNING, start),
        "REVIEW due today": ds(SRSState.REVIEW, start), "REVIEW graded today": ds(SRSState.REVIEW, start, lr=start + timedelta(hours=2)),
        "REVIEW due +10d": ds(SRSState.REVIEW, start + timedelta(days=10)), "REVIEW due +120d": ds(SRSState.REVIEW, start + timedelta(days=120)),
        "SUSPENDED": ds(SRSState.SUSPENDED, start)}
print("recognition state     class     deferred")
for k, r in rows.items():
    g = _listen_grade_class(r, start, end, end_of_day_utc=eod)
    print(f"{k:21} {g!s:9} {(_listen_deferred_reason(r, g, today) if g else None)!s}")
EOF
```

```output
recognition state     class     deferred
NEW                   new       None
LEARNING              learning  learning
REVIEW due today      due       None
REVIEW graded today   None      None
REVIEW due +10d       ahead     None
REVIEW due +120d      ahead     known
SUSPENDED             None      None
```

`create` is a sixth row kind, for a lemma with no card yet. The preview groups rows new, learning, due, ahead and orders them with `_tracked_sort_key`: learning cards by ripening time at full precision, due and ahead cards by due *day* then mastery ascending (least known first). NEW-state rows carry no schedule, so they all tie and keep the introduction pool's frequency order, which is the contract the commit mirrors.

**Deferral.** `_listen_deferred_reason` decides which rows a listen does *not* act on by default: `"known"` (an ahead card whose next review is at least 90 days out) and `"learning"` (a step exists to test recall at a specific interval, and a listen is not that test; `hage` was introduced at 10:09, due at 10:20 and rated "good" by a listen in between). A deferred row is still shown, collapsed, rated `skip`, and staged only when the client sends an explicit rating. It is one field, `deferred_reason`, rather than a boolean per population, because these rows invert the polarity of every other row (absent from the ratings map means skip here and good everywhere else) and a second ad-hoc copy of an inverted rule is how the third one gets written wrong. `well_known` is derived from it for older clients, never maintained in parallel. `production_unpractised` flags a deferred-known row whose production side is not itself well known, so the row does not read as "nothing left here" for a word the learner cannot yet produce.

**The introduction budget.** One listen introduces at most one Anki day's worth of new cards: `resolve_daily_new_cap` minus `count_new_introduced_today` minus `count_new_created_today`, so listening to three lessons back to back does not flood the queue and a same-day re-listen creates roughly nothing more. `_allocate_intro_pool` spends that single budget across NEW-state rows *and* creation candidates:

```bash
cd backend && uv run python - <<'EOF'
from app.api.srs import _allocate_intro_pool
zipf = {"og": 6.5, "hus": 5.1, "snømann": 2.0, "bil": 5.5, "kafé": 3.9}
new_rows = [("hus (NEW card)", False, "hus", False), ("key phrase 'spor i snøen'", False, "spor i snøen", True),
            ("bil (created today)", True, "bil", False)]
live_new, tail_new, ranked, live_creates = _allocate_intro_pool(
    new_rows, ["og", "kafé", "snømann"], budget=2, zipf=lambda w: zipf.get(w, 0.0),
    occurrences={"og": 9, "kafé": 1, "snømann": 1, "hus": 2})
print("live NEW rows :", live_new)
print("tail NEW rows :", tail_new)
print("ranked creates:", ranked)
print("live creates  :", live_creates)
EOF
```

```output
live NEW rows : ['bil (created today)', "key phrase 'spor i snøen'"]
tail NEW rows : ['hus (NEW card)']
ranked creates: ['og', 'kafé', 'snømann']
live creates  : ['og']
```

Budget 2 is spent on the key phrase first (never frequency-ranked: a multi-word phrase is out-of-vocabulary in wordfreq, so ranking it would sink every key phrase below every word) and then on the most frequent word in the pool, `og`. `hus` falls into the tail even though its card already exists. A card created today is free: it already holds a slot through `count_new_created_today`, and charging it again would double-count. Creation candidates and NEW-state rows compete in **one pool ranked by corpus frequency** (wordfreq zipf via `zipf_for`, with in-lesson occurrence count as tie-break; out-of-vocabulary lemmas such as proper nouns sink to the end), a deliberate abandonment of the older "finish cards already in the deck first" rule; the two kinds differ in cost and the decision is that this does not matter. For a language with no `wordfreq_lang`, ranking falls back to occurrence count. Both the preview and the commit make the *same* call with the *same* `zipf` object, resolved once per request.

The over-budget tail renders as a read-only "N more, next listen" list with a stated cut. `will_create` is a static flag on the preview response and is **not** recomputed in the browser: un-checking a live row does not promote the next-ranked tail row, because a skipped create consumes its slot server-side (`15350716`). One deliberate per-row opt-in, `over_cap_words` / `over_cap_creates` / `over_cap_kps` on `ListenRequest`, carries a row past the cap, the analogue of Anki's "Increase today's new limit". It is a separate list from the ratings map because presence in `word_ratings` is already overloaded.

**What the commit does, and the pending bucket.** `mark_lesson_listened` first clears this lesson's pending rows, because a listen *is* the lesson's current assessment and not an addition to the last one (without that, skipping everything on a re-listen still offered the old autograde rows). Then, for each tracked row:

| Row | Result |
|---|---|
| user confirmed it in the preview (`confirmed_words`, `confirmed_kps`) | grade applied immediately through `_apply_grade_now` |
| auto-rated remainder | **staged** in `pending_listen_grades` (keyed per lesson since migration v42, so a listen on one lesson does not re-parent cards it shares with another) |
| deferred, no explicit rating | skipped |
| rated `skip` | skipped (a NEW row still consumes its pool slot) |

The split exists so a user who graded a card by hand in the preview is not asked the same question again in "Check your work". `_apply_grade_now` is the single place a listen-originated grade is applied (`schedule`, then a `tt_revlog` row, then `dirty_fsrs`), shared by the confirmed path and every pending-release path, because a grade picked in the preview must land byte-identically to the same grade released later (the `b0a4b8a` inline-a-phase-subset class). The response is `{status, staged, applied, created, remaining_candidates, listen_count}`; `staged` and `applied` are disjoint. Staging is **recognition-only**, so a cloze (production-only) can never be staged.

Untracked lemmas are created in rank order up to the live set, each through `add_collocation` with the card key from `_card_key_for_lemma`, the gloss from `_resolve_gloss_translation` (the *same* helper the preview calls, so the previewed and stored gloss agree by construction), a gender article for nouns, and, for function words, a production-only cloze. Cards that need media or a gloss are collected into `pending_vocab`, `pending_cloze` and `pending_regloss` and completed by `_complete_listen_media` as a background task tracked by `app_state.background_work`, so the request does not pay a TTS or Pixabay round trip per new word. A card created without a gloss is held out of Anki until it has one (the gloss is retried at the next listen). An **Ignore** control on a create row writes a lemma to the card-less ignore list (`POST/DELETE /api/srs/ignored-lemmas`); the list suppresses *creation only*, and the check lives inside the untracked branch so a carded ignored lemma is not hidden from the preview while the commit still stages it.

Finally `db.record_listen` appends a `lesson_listens` row, and `GET /api/srs/listens` returns per-lesson listen state. The frontend store (`lib/stores/listened.svelte.ts`) is a thin cache over it, with a one-time import of the old localStorage state through `POST /api/srs/listens/import`. If any grade was confirmed, the learning cutoff advances once at the end (queue parity rule 11, §9).

**The pending bucket is not hidden from the queue.** An earlier design (Layer 81) excluded staged cards from the review badge and served queue; it was introduced in 2026-07 and retired on 2026-08-10. At HEAD a staged card that is due is counted and served exactly as Anki would, and it charges the review budget. Grading it in the main queue applies a real grade and releases the staging: `drill_feedback` clears the pending row unconditionally, and the revlog's review kind (Anki's review-ahead kind 3) is re-derived from the card's dueness *now* by `_release_review_kind`, not from the class stored at stage time, because a sync or day rollover in between can have moved `due_at`. Do not add a pending-grade clause to a badge query; read Layer 81 and `tests/test_pending_grade_inclusion.py` first.

### 8.10 Check your work: the lesson-scoped queue

After a listen, `GET /api/srs/content/{content_id}/review-queue` serves exactly that lesson's staged cards, in the shape of the main queue's items plus a `pending_rating` so the UI can pre-fill what the listen staged (learning cards first, then by due date). It is **strictly read-only with respect to parity state**: no learning-cutoff advance, no `session_main_queue` write, no unbury sweep, no queue-engine involvement. The frozen main-queue order must survive this endpoint unchanged, pinned by a parity-guard test. Because its inclusion is exactly this lesson's pending rows, the served queue and what "Sync it" would release are the same set by construction.

Three ways out of the bucket, all clearing the row:

- **Per-card grade** through the ordinary `drill_feedback` endpoint with `lesson_review: true`. An Again on an auto-Good'ed card is an ordinary same-day lapse; the flag only prevents re-charging the daily review budget for a card already counted today (`has_counting_review_today`).
- **`POST /content/{content_id}/commit-pending`** ("Sync it"): applies every staged row at its provisional rating through `_apply_grade_now`, with one shared load-balancer and a monotonic grade clock across the batch (`tt_revlog.id` is a millisecond primary key and `append_revlog` is INSERT OR IGNORE, so two grades in the same millisecond would silently drop one). It does not sync to AnkiWeb by itself; the next normal sync pushes the dirty grades. One sync path only (§10).
- **An Anki-side grade** arriving through `sync_pull`.

`POST /content/{content_id}/reviewed` records completion (`lesson_reviews`), and `has_unreviewed_listen` (latest listen strictly newer than the latest review) gates the lesson page's "Check your work" link to one shot per listen. Orphaned rows (card deleted after staging) are skipped by the queue and cleared by commit.

### 8.11 Reading actions: click, grade, undo

In Read mode a tap on a word does one thing, chosen by `frontend/src/lib/reading/readingActions.svelte.ts::onWordClick` and mirrored in the popover's grade-button label (`WordSpan.svelte::gradeLabel`). It is one implementation shared by the lesson page and the review-session reader (the session reader first shipped a hand-rolled transcript and was visibly worse within a day, bd `tunatale-9p9d`); only the content id and language differ.

| Word state | Tap does | Button label |
|---|---|---|
| `unknown` | create the base card, then record a first **Good** review so it enters learning now rather than parking at NEW; a function word grades the *production* direction its cloze actually has (grading a missing recognition card 500'd) | Start learning |
| due and tracked | grade **Good** on `active_direction` | Got it |
| not due, recognition on the ramp | **read-ahead**: a Good on the literal recognition direction, never `active_direction` | Review |
| carded but NEW (a sync-minted cloze) | the same one-tap introduction | Start learning |
| anything else | nothing (no button) | none |

A just-graded word flips its button to **Undo**: `POST /api/srs/items/{id}/direction/{direction}/undo` (`app/srs/grade_undo.py`) restores the verbatim pre-grade `DirectionState` and deletes the `tt_revlog` row, but only while the grade is still TunaTale-local, meaning it is the direction's latest and still `dirty_fsrs`. After a sync the review lives in Anki and undo is refused with 409, since the next pull would re-clobber it. It is single-level by design (one snapshot in `anki_state_cache`), and the learning-cutoff advance is deliberately not unwound.

The popover also carries state overrides: `POST /items/{id}/state` (`new`, `learning`, `known`; `learning` calls `promote_to_learning`), **mark known** with a reversible snapshot (`mark_known` stores `known_prior_*`; `POST /items/{id}/restore-known` undoes it), **untrack** (`POST /items/{id}/untrack`: a never-synced row is deleted outright, a synced one has both directions suspended with `dirty_fsrs` so the next push suspends the Anki card), and un-ignore. The override set deliberately excludes lapse and restore-to-review, so it never rewrites FSRS scheduling state. `promote_to_learning` writes `state='learning'` without `left`/`due_at`: TunaTale shows LEARNING while Anki still has the card as new, a documented TunaTale-only asymmetry (queue-parity rule 12 and the learning-badge caveat in `.claude/rules/anki-queue-parity.md`).

A per-device **Produce** toggle (default off) turns on blur-as-cloze: words whose production direction is due (`WordToken.production_due`) are blurred, tap to reveal, then one of the review queue's four ratings (Again / Hard / Good / Easy) grades the production card. The reader stays recognition-based, and this flag does not move `is_due`. Drag-selecting a phrase offers a translate button (`POST /api/srs/translate`, 422 on empty text or an unknown language code, 503 when no LLM is configured) and creates a multi-word collocation through `POST /api/srs/items`. Rendering details are §13.

### 8.12 The review selector: what the next lesson reinforces

The loop also feeds back into generation. `app/srs/review_selector.py::select_review_collocations(db, now=..., limit=12)` returns the words the learner is closest to forgetting, and story generation (`generation/story.py`, §6) hands them to the LLM as review vocabulary. It ranks the review queue's own due pool (`get_due_items`) by **retrievability ascending**, with a content-based tie-break (text, then row id) so the sample is a pure function of the database and `now`, which keeps prompt cassettes stable.

Retrievability and not due date is the point. FSRS holds R above desired retention for cards not yet due, at it on the due day and below it once overdue, so R-ascending is overdue-first by construction and a separate due-window filter is redundant. R adds the *rate* of decay a due date cannot see: a two-day-stability card two days overdue is far likelier gone than a 600-day card forty days overdue. The `horizon_days` parameter is a no-op unless the due pool is smaller than the limit, because a wider horizon only appends cards that sort last (measured 2026-09-03 on the real deck: 78 due against a limit of 12, so every horizon from 0 to +29 days returned the same twelve words). There is no topical filter, by the user's decision: off-theme old words are the intended diversity, and licensing the model to skip what does not fit is the prompt's job. It makes no queue-parity claim, writes nothing, and must never touch SRS state; selecting a word to *appear* in a lesson is not reviewing it. See §6 for review pressure and `docs/curriculum-planning.md`.

### 8.13 Endpoint index

```bash
cd backend && uv run python - <<'EOF'
from app.api.srs import router
want = ("listen", "transcript", "review-queue", "commit-pending", "reviewed", "items/base", "inflection", "untrack",
        "ignored", "/translate", "/items/{item_id}/state", "restore-known", "undo")
for r in sorted(router.routes, key=lambda r: r.path):
    if any(w in r.path for w in want):
        print(",".join(sorted(r.methods)).ljust(7), r.path)
EOF
```

```output
POST    /api/srs/content/{content_id}/commit-pending
GET     /api/srs/content/{content_id}/listen-preview
GET     /api/srs/content/{content_id}/review-queue
POST    /api/srs/content/{content_id}/reviewed
GET     /api/srs/content/{content_id}/transcript
POST    /api/srs/ignored-lemmas
DELETE  /api/srs/ignored-lemmas
POST    /api/srs/inflection-clozes
POST    /api/srs/items/base
POST    /api/srs/items/{item_id}/direction/{direction}/undo
POST    /api/srs/items/{item_id}/restore-known
POST    /api/srs/items/{item_id}/state
POST    /api/srs/items/{item_id}/untrack
POST    /api/srs/listen
GET     /api/srs/listens
POST    /api/srs/listens/import
GET     /api/srs/review-queue
POST    /api/srs/translate
POST    /api/srs/translate-missing
```

Every `content/{content_id}` route accepts a lesson id or a review-session id. `GET /api/srs/review-queue` (the global queue) and the grading endpoint are §9; the Cards viewer's cloze propose/set endpoints and image routes are §11; the full router map is §12. Related reading: `docs/learning-modes.md` (the Review / Listen / Read postures and which interaction each owns), `docs/anki-parity-layers.md` Layers 65, 66 and 81, and `.claude/rules/anki-queue-parity.md` before changing anything queue-adjacent.

## 9. The SRS Engine

The SRS engine decides what the learner should review next and when each card comes back. It is a Python re-implementation of the scheduler in the user's Anki desktop app (FSRS in f32, learning steps, fuzz, load balancing, sibling burying, the study-queue builder), because the same deck is graded in both apps and the two must stay interchangeable between syncs. It sits on top of the per-language SQLite DB (§2) and the domain models (§4), is fed grades by the listen and review endpoints (§8, §12, §13), and is reconciled with Anki's collection by the sync plugin (§10). Cards, notetypes and cloze minting are §11.

The one idea to keep in your head: **Anki is the reference, not a dependency.** TT reproduces Anki's algorithms, reads `collection.anki2` only at sync time, and never imports `anki` in `backend/app/**`. Everything on a request path is reconstructed from TT's own state (`collocation_directions` and the `anki_state_cache` key/value table).

### 9.1 Where the engine lives

    app/models/srs_item.py        SRSItem, DirectionState, SRSState, Direction, Rating, RevlogRow  (pure, no I/O)
    app/srs/fsrs.py               schedule(), FSRSParams, compute_retrievability, build_revlog_row
    app/srs/anki_mirror/          the "eventually-removable" Anki-mirror boundary
        queue_engine.py             study-queue assembly (R-ascending, sibling bury, spread, freeze)
        queue_stats.py              daily caps / FSRS params / steps resolved from the cache
        cache_registry.py           one spec per anki_state_cache key
        rollover.py, protobuf_wire.py   the 04:00 day arithmetic; protobuf + col-day helpers
        load_balancer.py, _anki_rng.py  bit-exact ports of Anki's balancer and fuzz RNG
        preset_watch.py             detects an un-rescheduled FSRS preset change (see §10)
    app/srs/database.py           SRSDatabase = composition facade over db_* mixins
    app/srs/db_*.py               per-concern mixins (collocations, directions, queue, counts, revlog, sync, ...)
    app/srs/direction_fields.py   the per-direction column registry
    app/srs/migrations.py         versioned schema chain (PRAGMA user_version)
    app/api/srs.py                HTTP layer only: grade, undo, queue-stats, review-queue, listen, admin

`SRSDatabase` is deliberately thin. Its body is empty; behaviour comes from mixins listed in MRO order, with `SRSDatabaseBase` (connection handling, schema bootstrap, `_DIR_COLUMNS`) last. New SQL goes in the mixin that owns the concern, and everything is imported and patched through `app.srs.database` so the mixin split stays invisible to callers (and to the mock-boundary checker, §14).

```bash
cd backend && uv run python -c "
from app.srs.database import SRSDatabase
print('MRO:', ' > '.join(c.__name__ for c in SRSDatabase.__mro__ if c is not object))
with SRSDatabase(':memory:') as db:
    with db._get_conn() as c:
        print('tables:', ', '.join(sorted(r[0] for r in c.execute(\"select name from sqlite_master where type='table' and name not like 'sqlite_%'\"))))
"
```

```output
MRO: SRSDatabase > DbCollocationsMixin > DbDirectionsMixin > DbQueueMixin > DbCountsMixin > DbRevlogMixin > DbSyncMixin > DbMediaMixin > DbKvCacheMixin > DbHistogramMixin > DbLemmaCacheMixin > DbListensMixin > DbPendingGradesMixin > DbReviewsMixin > DbIgnoredLemmasMixin > DbSyncConflictsMixin > SRSDatabaseBase
tables: anki_state_cache, cloze_sentence_cache, collocation_directions, collocation_tags, collocations, ignored_lemmas, image_query_cache, lemma_analysis_cache, lesson_listens, lesson_reviews, media, pending_listen_grades, sync_conflicts, tt_revlog, violations
```

The package boundary matters more than the mixin split. `app/srs/anki_mirror/__init__.py` states it outright: if the "mirror Anki" strategy were ever dropped, that package and the `test_parity_*` goldens would go together. Each of its modules has exactly one import path (`app.srs.anki_mirror.<name>`); the older `app.srs.*` aliases were deleted so a second path cannot grow back unnoticed.

The tables that carry SRS state, all in the same per-language file as the content store (§6):

| Table | Role |
|---|---|
| `collocations` | One row per card-able item: text, translation, lemma, `guid`, `anki_note_id`, media pointers, `card_type`, cloze link `base_collocation_id`, `dirty_fields` (per-field push marker) |
| `collocation_directions` | Two rows per collocation, one per `Direction`: all FSRS state plus the Anki identity (`anki_card_id`, `anki_due`, `anki_card_mod`) |
| `tt_revlog` | One event row per grade, shaped like Anki's `revlog` (§9.9) |
| `pending_listen_grades` | Provisional grades staged by a listen (§9.8) |
| `anki_state_cache` | Key/value mirror of Anki config and TT session state (§9.4) |
| `sync_conflicts`, `media`, `ignored_lemmas`, `lesson_listens`, `lesson_reviews`, caches | Sync bookkeeping and listen-side state |

### 9.2 Two directions per item

Every collocation owns two independent FSRS states, because Anki models a note with two templates as two cards: **RECOGNITION** (L2 to L1, `cards.ord` 0 by default) and **PRODUCTION** (L1 to L2). `SRSItem.directions` maps `Direction` to a `DirectionState`; `schedule()` always updates exactly one direction and leaves the other alone. Which Anki `ord` means which direction is not hardcoded: the notetype profile says (`recognition_ord`, §11), which is how Tagalog's genanki notetype puts recognition on ord 1.

`SRSState` is `NEW`, `LEARNING`, `REVIEW`, `RELEARNING`, `SUSPENDED`, `BURIED`, `KNOWN`. The first four are the FSRS lifecycle; `SUSPENDED` and `BURIED` map to Anki queues -1 and -2/-3; `KNOWN` is a TT-only terminal "I already know this" state that snapshots the prior state so it can be restored (`known_prior_*` columns). The flat `item.stability`/`item.state` properties on `SRSItem` are legacy shims that read the recognition direction; new code uses `item.directions[...]`.

Cloze notes only have a PRODUCTION direction. A paired note's production card is not introducible until its recognition sibling has graduated past the learning arc (the Layer 65 gate inside `SRSDatabase.get_new_items`). This matches the user's Anki, whose deck positions put recognition first, and the new-card badge already agrees with it.

**The field registry.** Which columns exist on a direction, and which of them matter for sync, is declared once in `app/srs/direction_fields.py::DIRECTION_FIELDS`. `_DIR_COLUMNS` (the SELECT list) and `_direction_differs` (the sync diff that decides whether a pull must write) are both derived from it. This exists because three separate parity layers (17 `left`, 35 `bury_kind`, 37 `anki_card_mod`) were the same bug: a column added to the schema and the model but forgotten in the diff, so a self-heal write silently never fired. Each entry carries an explicit `sync_comparable` decision and a `reason`, and two column-level invariants are data rather than prose: a `WritePolicy` (`STICKY_NEW` for `prior_state`, `ONE_SHOT` for `introduced_at`) and an at-rest `domain` that is single-sourced into both a pure validator and the SQL `CHECK` constraint (migration v35).

```bash
cd backend && uv run python -c "
from app.srs.direction_fields import DIRECTION_FIELDS
print(f'{\"column\":22}{\"in sync diff\":14}{\"write policy\":14}domain-checked')
for f in DIRECTION_FIELDS:
    print(f'{f.column:22}{str(f.sync_comparable):14}{f.write_policy.value:14}{\"yes\" if f.domain else \"\"}')
"
```

```output
column                in sync diff  write policy  domain-checked
stability             True          free          
fsrs_difficulty       True          free          
due_at                True          free          
reps                  True          free          
lapses                True          free          
state                 True          free          
last_review           True          free          
last_review_time_ms   False         free          
anki_card_id          True          free          
anki_card_mod         True          free          
anki_due              True          free          
dirty_fsrs            True          free          
last_synced_at        False         free          
last_rating           False         free          
left                  True          free          
prior_state           True          sticky_new    yes
prior_left            False         free          
prior_stability       False         free          
introduced_at         False         one_shot      
bury_kind             True          free          yes
fsrs_force_next       False         free          
```

Three of those semantics are worth stating, because each was learned from a user-visible divergence:

- **`prior_state='new'` is sticky.** It is set when a card is introduced and survives same-class grades and LEARNING-to-REVIEW graduation. It is released only by a lapse (REVIEW to RELEARNING). It exists so the revlog row's `type` is right for every grade of the introduction arc.
- **`introduced_at` is a one-shot stamp** written exactly once, on the first NEW-to-non-NEW transition (by `fsrs.schedule` for a TT grade, or `sync_pull` from `MIN(revlog.id)` for an Anki grade). `count_new_introduced_today` counts this column. It is a different thing from the sticky marker: `prior_state` lives for the whole arc, `introduced_at` is a fixed timestamp that anchors Anki's `newToday`.
- **`bury_kind` is tri-state:** `NULL`, `'sched'` (sibling bury, released by the daily sweep) or `'user'` (a manual bury, which survives rollover and is skipped by the sweep). At HEAD no route writes `'user'`: the value exists in the domain and the SQL `CHECK`, the sweep honours it, and the v35 backfill stamped legacy buried rows with it, but `sync_pull` maps every Anki bury queue to `'sched'`. A `bury_kind` on a non-buried row is a coupling violation, swept per sync into `INVARIANT_TRACE` soak lines.

A starter card seeded from another language (Cebuano from Tagalog cognates, §11) is a REVIEW card with `reps = 0` and no revlog row, written by `SRSDatabase.seed_review_state`. Because "has a schedule" stopped meaning `reps > 0`, the unbury sweep and unsuspend restore REVIEW when `reps > 0 OR last_review IS NOT NULL` (Layer 85).

### 9.3 FSRS scheduling

`app/srs/fsrs.py::schedule` is the single grading function. Its signature carries everything a caller must resolve for the request: `params` (`FSRSParams`), `now`, `col_crt`, an optional `load_balancer`, and `learn_steps` / `relearn_steps`. It dispatches on the direction's current state to `_schedule_new`, `_schedule_with_steps` (LEARNING/RELEARNING), `_schedule_review_again` (REVIEW + Again) or the passing-review path, and returns a new `SRSItem` with `dirty_fsrs=True` and `last_rating` set on the touched direction. The dirty flag is what the next sync push reads.

`FSRSParams` is a frozen dataclass of weights plus `desired_retention` and `maximum_review_interval`. It accepts 19 weights (FSRS-5, decay fixed at 0.5) or 21 (FSRS-6, decay is the last weight) and nothing else. The weights come from Anki's deck config, not from TT: `resolve_fsrs_params(db)` reads the cached protobuf-decoded values (§9.4), falling back to the built-in FSRS-5 defaults.

Several implementation choices exist only to match Anki to the last bit:

- **f32 end to end.** fsrs-rs computes stability and difficulty in `f32` through Burn tensors. `fsrs.py` casts every operand and intermediate to `numpy.float32` and returns a Python float only at storage boundaries. Doing it in `f64` drifted by single ULPs at four-decimal storage precision, which showed up as false divergences in the soak. Three details had to match Rust exactly, not just the width: the forgetting-curve factor `exp(ln(0.9)/decay) - 1`, the operation order inside `_next_difficulty`, and `f32::round` rounding half away from zero rather than banker's (`_rust_round_half_away`). `test_parity_fsrs_f32.py` pins this against `fsrs_rs_python`. The practical consequence for the soak: a stability divergence of 0.0001 is a regression signal, not noise.
- **Learning-step fuzz uses Anki's RNG.** `_learning_step_fuzz_seconds` seeds a ChaCha12 port (`_anki_rng.py`) with `anki_card_id + reps`, so TT's `due_at` after a learning grade matches Anki's `cards.due` to the second. Review intervals get the same treatment (`_review_interval_fuzz`, `_constrained_fuzz_bounds`, the Layer 48/51/52 interval cascade).
- **Same-day Hard.** `_stability_short_term` clamps `sinc` to at least 1 for any passing rating including Hard, because Anki 26.8.1 made Hard non-decreasing (`785673f6`, pinned through the real answer path by `e72e9153`). Again stays unclamped, since a lapse must be able to lower stability. When Anki's pin moves, this moves with it.
- **Stability clamp and quantisation.** Stability is clamped to `[S_MIN, S_MAX]` like fsrs-rs `step` (Layer 63), then quantised on store.
- **Load balancer.** If Anki's `loadBalancerEnabled` is on, Anki moves each graded interval to a less-loaded day within its fuzz range. TT ports it bit-exactly (`anki_mirror/load_balancer.py`), builds the histogram from its own `collocation_directions` (`build_live_load_balancer` in `queue_stats.py`), and passes it into `schedule`. The consequence is a rule worth remembering: a residual `due_at` difference of one or two days now means a real configuration mismatch, not an accepted gap.

The schedule below, for a brand-new card at a fixed instant, shows the learning arc. Again and Good enter LEARNING with `left` (steps remaining, packed with the today-remaining count the way Anki stores it); Easy graduates straight to REVIEW; the production direction is untouched. The `due` seconds include the step fuzz.

```bash
cd backend && uv run python -c "
from datetime import date, datetime, UTC
from app.models.srs_item import SRSItem, Rating, Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import schedule

u = SyntacticUnit(text='Dober dan', translation='Good day', word_count=2, difficulty=1, source='llm')
item = SRSItem(syntactic_unit=u, due_date=date(2026, 3, 25))
now = datetime(2026, 3, 25, 12, 0, tzinfo=UTC)
rec = Direction.RECOGNITION
print('before:', item.directions[rec].state.value, '/', item.directions[Direction.PRODUCTION].state.value)
for r in (Rating.AGAIN, Rating.GOOD, Rating.EASY):
    n = schedule(item, r, review_date=date(2026, 3, 25), now=now)
    s = n.directions[rec]
    print(f'{r.name:5} -> {s.state.value:9} left={s.left} s={s.stability:.4f} due={s.due_at.isoformat()} dirty={s.dirty_fsrs}')
print('production after Easy:', n.directions[Direction.PRODUCTION].state.value)
"
```

```output
before: new / new
AGAIN -> learning  left=2 s=0.4072 due=2026-03-25T12:01:12+00:00 dirty=True
GOOD  -> learning  left=1 s=3.1262 due=2026-03-25T12:12:00+00:00 dirty=True
EASY  -> review    left=None s=15.4722 due=2026-04-11T00:00:00+00:00 dirty=True
production after Easy: new
```

Learning steps are never read from a global. Layer 82 (`80fbee1d`) is the reason: `_get_steps_for_state` and the retrievability sort used to call the step and param resolvers without a db, so each opened an `SRSDatabase` from the singular `settings.database_url`. In a multi-language deployment the request's db comes from the plural `database_urls[code]` map, so a Norwegian grade used Slovene steps (`[1, 10]` where Norwegian's were `[25, 55]`) and the Norwegian queue sorted with Slovene FSRS params. The fix is injection: the caller that holds the request's db calls `resolve_learning_steps(db)` / `resolve_relearning_steps(db)` / `resolve_fsrs_params(db)` and passes the results into `schedule()`, keeping `fsrs.py` pure. Follow-ups made the revlog replay use the request language's steps (`130ac195`), removed every db-less resolver fallback (`044bc103`), and made `db` a required argument on the ten `queue_stats` resolvers (`8b93b313`). If you write a new grade call site, copy the shape in `app/api/srs.py::drill_feedback`.

```bash
cd backend && uv run python -c "
from app.srs.fsrs import FSRSParams, DEFAULT_FSRS5_PARAMS as p, _rust_round_half_away
import numpy as np
q = FSRSParams(weights=p.weights + (0.0, 0.1542))
print('weights ->  version/decay:', len(p.weights), '->', p.version, p.decay, '|', len(q.weights), '->', q.version, q.decay)
print('rust round(2.5), round(-2.5):', _rust_round_half_away(2.5), _rust_round_half_away(-2.5), '| python round(2.5):', round(2.5))
print('f32 0.1+0.2 =', float(np.float32(0.1) + np.float32(0.2)), '| f64 =', 0.1 + 0.2)
"
```

```output
weights ->  version/decay: 19 -> 5 0.5 | 21 -> 6 0.1542
rust round(2.5), round(-2.5): 3 -3 | python round(2.5): 2
f32 0.1+0.2 = 0.30000001192092896 | f64 = 0.30000000000000004
```

**Retrievability** (`compute_retrievability`) is the sort key for the review queue. It has two branches because Anki's `extract_fsrs_retrievability` does: with a sub-day `lrt` in `cards.data`, elapsed is fractional days; without it, elapsed is the integer day count from `due - ivl`. TT recognises the second case by a midnight-UTC `last_review` (`is_day_level_last_review`). A card with no memory state returns `desired_retention`, because Anki places it in R-ascending order exactly where that value falls, neither first nor last (Layers 38/43). The *grading* path uses a different elapsed (`_grade_elapsed_days`, Layer 50), covered in §9.5.

### 9.4 Config mirrored from Anki: the cache registry

TT does not own its scheduling knobs. Daily new and review caps, learning and relearning steps, FSRS weights, desired retention, bury flags, new-spread, new-card sort and gather order, easy-days percentages, the load-balancer switch, `newCardsIgnoreReviewLimit`, the maximum interval, and the collection creation time are all Anki settings. `sync_pull` reads them from `collection.anki2` with `refresh_*` functions in `queue_stats.py` and writes them into `anki_state_cache`. A request-time `resolve_*` function reads them back, returning a `(value, source)` pair where source is `cache`, `config` (a `settings` default) or `default`, so the UI can show provenance.

Modern Anki stores deck config as a protobuf blob in `deck_config`, not JSON. Rather than depend on a generated stub, `anki_mirror/protobuf_wire.py` and the `_pb_*` helpers in `queue_stats.py` walk the wire format for the specific field numbers (steps 1 and 2, FSRS-5 weights 5, FSRS-6 weights 6, new/day 9, reviews/day 10, bury flags 27/28, spread 30, sort order 32, gather priority 34, retention 37). The retention field number is a known trap: it is **37**, and 40 is `historical_retention`.

One subtlety is load-bearing. Config is proto3 with implicit presence, so Anki omits a field that holds its default, which makes "absent" wire-identical to "the default". An early version skipped the cache write when a field was absent, so a stale non-default survived forever after the user set the value back (`tunatale-6kl`). The refreshes now write unconditionally, falling back to Anki's own defaults (`736209ea` fixed the last stragglers).

`app/srs/anki_mirror/cache_registry.py::REGISTRY` declares every cache key's contract in one place: `source` (`ANKI_CONFIG`, `TT_SESSION`, `TT_STATE`), whether it is `day_scoped`, a `max_age_days`, and a `logic_version`. `set_anki_state_cache` and `get_anki_state_cache` raise on an unregistered key. `_config_row_fresh` takes its max age from the registry, so resolver and registry cannot disagree. A deck that can never sync (another learner's seeded deck, §11) is built with `anki_config_expires=False`, so its seeded config never ages out into defaults.

```bash
cd backend && uv run python -c "
from collections import Counter
from app.srs.anki_mirror.cache_registry import REGISTRY
print(dict(Counter(s.source.name for s in REGISTRY.values())))
for s in REGISTRY.values():
    if s.source.name != 'ANKI_CONFIG':
        print(f'{s.name:22}{s.source.name:12}day_scoped={s.day_scoped!s:6}logic_version={s.logic_version}')
print('ANKI_CONFIG:', ', '.join(s.name for s in REGISTRY.values() if s.source.name == 'ANKI_CONFIG'))
"
```

```output
{'TT_STATE': 3, 'TT_SESSION': 2, 'ANKI_CONFIG': 18}
last_unbury_day       TT_STATE    day_scoped=True  logic_version=None
last_grade_undo       TT_STATE    day_scoped=False logic_version=None
last_preset_change    TT_STATE    day_scoped=False logic_version=None
learning_cutoff       TT_SESSION  day_scoped=True  logic_version=None
session_main_queue    TT_SESSION  day_scoped=True  logic_version=2
ANKI_CONFIG: daily_new_cap, daily_review_cap, desired_retention, new_spread, new_card_sort_order, new_card_gather_priority, bury_new, bury_review, col_crt, fsrs_params, fsrs_preset_snapshot, learn_steps, relearn_steps, easy_days_percentages, load_balancer_enabled, new_cards_ignore_review_limit, fsrs_short_term_with_steps_enabled, maximum_review_interval
```

`TT_SESSION` and `TT_STATE` keys are not Anki config: the frozen queue, the learning cutoff, the unbury day, the single-level grade-undo snapshot, and the preset-change alert. `logic_version` is how a deploy invalidates a cached queue: `session_main_queue` is at version 2 (bumped by the Layer 83 order change), and a cached payload with a different version is discarded exactly like one from yesterday. Every `ANKI_CONFIG` key must be rewritten by every non-dry-run `sync_pull`; `tests/test_sync_cache_conservation.py` derives that check from the registry, so a missing `refresh_*` fails a test rather than a user.

### 9.5 Three day rules

Anki has no single "day number". It has three answers to three questions, and they coincide for most of the day, which is why mixing them up survives: every failure is a one-day slip that self-heals. The seen symptoms were an FSRS elapsed off by one, a daily cap silently uncharged, about 8 to 9 percent stability drift on grades near the boundary, and review cards scheduled a day late in both apps.

| Question | Function | Domain |
|---|---|---|
| What study day is it right now? | `protobuf_wire.py::anki_today_col_day(col_crt, now)` and `rollover.py::anki_today(now)` | Local calendar dates, minus one until today's rollover has passed. Independent of `col.crt`'s time of day. Use for anything meaning "today". |
| What col-day does this stored day-level marker decode to? | `protobuf_wire.py::compute_anki_day_index` | Index arithmetic on `col.crt`. Not Anki's `today`. It is the exact inverse of the marker `_compute_last_review` writes; re-anchoring it would shift every stored `last_review`. |
| How long since the last review, at grade time? | `fsrs.py::_grade_elapsed_days` via `rollover.py::local_next_rollover` | A duration back from the next rollover (`next_day_at`), integer-divided by 86400. Neither of the above. |

The rollover hour is `app.config.ANKI_ROLLOVER_HOUR` (4), single-sourced for the whole local-day domain in `app/srs/anki_mirror/rollover.py`. The same module owns the day-bounds window used by every "graded today" filter (`anki_day_bounds_utc`) and the `due_at` convention for day-level cards (`due_at_rollover_utc`: the rollover hour in UTC on the due date).

The probe below fixes a collection created at 04:00 New York time and asks all three at three instants. At 02:00, inside `[local midnight, 04:00)`, Anki is still on June 10 and `anki_today_col_day` agrees, but the index arithmetic has already ticked over. That four-hour daily window is where every off-by-one in this codebase came from.

```bash
TZ=America/New_York bash -c 'cd backend && uv run python -c "
from datetime import datetime, UTC
from zoneinfo import ZoneInfo
from app.srs.anki_mirror.rollover import anki_today, local_next_rollover
from app.srs.anki_mirror.protobuf_wire import anki_today_col_day, compute_anki_day_index
ny = ZoneInfo(\"America/New_York\")
crt = int(datetime(2024, 1, 1, 4, 0, tzinfo=ny).timestamp())
for label, local in [(\"23:30 Jun 10\", datetime(2026, 6, 10, 23, 30, tzinfo=ny)),
                     (\"02:00 Jun 11\", datetime(2026, 6, 11, 2, 0, tzinfo=ny)),
                     (\"05:00 Jun 11\", datetime(2026, 6, 11, 5, 0, tzinfo=ny))]:
    now = local.astimezone(UTC)
    print(f\"{label}: anki_today={anki_today(now)} today_col_day={anki_today_col_day(crt, now)} \"
          f\"day_index={compute_anki_day_index(crt, now=now)} next_rollover={local_next_rollover(now):%m-%d %H:%M}\")
"'
```

```output
23:30 Jun 10: anki_today=2026-06-10 today_col_day=891 day_index=891 next_rollover=06-11 04:00
02:00 Jun 11: anki_today=2026-06-10 today_col_day=891 day_index=892 next_rollover=06-11 04:00
05:00 Jun 11: anki_today=2026-06-11 today_col_day=892 day_index=892 next_rollover=06-12 04:00
```

The history here is `b684d826` (due dates landed a day late between midnight and 04:00), then the September sweep that found `compute_anki_day_index` was pure UTC arithmetic: `77cf421c` made "study day" a local calendar date, `a18c0301` fixed the studied-today marker and grade elapsed, `2ba4919c` made TT-native review grades schedule from Anki's day (`_review_due_at_from_interval` now uses `anki_today_col_day`), and `5b4fd20b` wrote the rules down. The test discipline that came with it is in §14: the oracle job `anki-gates` runs at the 04:00 rollover precisely so this class fails loudly, and any test asserting an absolute day index pins its timezone.

Two call-site rules follow. Never use `date.today()` for "which Anki day is it": `backend/scripts/check_date_today.py` enforces `anki_today()` repo-wide. And when chasing a one-day discrepancy, first check which of the three day rules the site uses.

### 9.6 The queue engine

`app/srs/anki_mirror/queue_engine.py` rebuilds Anki's study queue from TT state. There are two public entry points: `build_and_freeze_main_queue(db)` and `assemble_review_queue(db, session_start=...)`, which `GET /api/srs/review-queue` calls and shapes for the wire. The queue it returns is `ready_learning + ordered_main + pending_learning`.

**`_compute_live_main`** builds the main queue (reviews plus new cards) from current DB state:

1. Run the daily unbury sweep (`db.unbury_if_needed(today)`, §9.7).
2. Resolve caps and flags from the cache: `daily_new_cap`, `daily_review_cap`, `new_spread`, `bury_new`, `bury_review`, the FSRS params, `col_crt`, and `new_cards_ignore_review_limit`.
3. Gather due reviews for both directions and merge them R-ascending (`_merge_by_retrievability_ascending`, tiebreak `_fnv1a_64_i64` of `(card id, card mod)`, which is why `anki_card_mod` is marked sync-comparable).
4. Gather the whole new pool per direction in the deck's gather order, merge both directions in one pass (`_merge_directions`), and bury the later sibling of each note (the higher `anki_due` wins).
5. Sibling-bury reviews, then **cap reviews first, then cap new, then Template-sort the new slice**. The order of those three is the parity point (Layers 75 to 77, 83; see §9.7).
6. Interleave per `new_spread`: reviews first, new first, or Anki's intersperser.

The intersperser is a port of `rslib/.../intersperser.rs`. It uses the continuous ratio `(reviews + 1) / (new + 1)` over the natural list lengths, with no session-start override (that was tried as Layer 9 and reverted at Layer 14). For ten reviews and two new cards the first new card lands at position 3:

```bash
cd backend && uv run python -c "
from app.srs.anki_mirror.queue_engine import _spread_mix
print(_spread_mix([f'R{i}' for i in range(1, 11)], ['N1', 'N2']))
"
```

```output
['R1', 'R2', 'R3', 'N1', 'R4', 'R5', 'R6', 'R7', 'N2', 'R8', 'R9', 'R10']
```

**`assemble_review_queue`** wraps that with the session-scoped parts:

- **Learning cards** are gathered separately (both directions), sorted by `due_at`, then `anki_due`, then `anki_card_id`, and split into ready and pending against the **learning cutoff**, not live `now`.
- **The main queue is frozen.** Anki builds `main` once and pops from it; it never re-sorts mid-session. TT mirrors that by caching the order as `session_main_queue` and reconciling it against a freshly computed live pool on every call. A card that has left the pool (graded, buried) drops out; only a NEW-state latecomer is tail-appended (a mid-day `/listen` add is a TT-only allowance). A REVIEW card newly in the pool is a state transition Anki would also drop from today's queue.
- **The collapse.** Anki shifts a just-graded learning card past the next-soonest pending card when main is empty, so the same card does not reappear immediately. TT swaps `pending_learning[0]` and `[1]` under the same conditions.

**The learning cutoff** (`resolve_learning_cutoff` / `advance_learning_cutoff`) is a frozen timestamp that decides which intraday-learning cards are ready. It moves forward only, and only on four triggers, which together are Anki's `current_learning_cutoff`:

1. a grade (`drill_feedback`, the batch `commit-pending`, and listen applies);
2. a session start (`/review-queue?session_start=1`, sent by the frontend on `/review` mount, which is TT's "deck open");
3. `sync_pull` ingest, advancing to the latest revlog timestamp pulled;
4. the end-of-session auto-bump, when ready learning and main are both empty and some pending card has ripened.

```bash
grep -rn "advance_learning_cutoff(" backend/app --include=*.py | grep -v "def advance_learning_cutoff" | sed -E 's/^backend\/app\/([^:]+):[0-9]+: */\1: /' | cut -c1-100
```

```output
api/srs.py: advance_learning_cutoff(db, now)
api/srs.py: advance_learning_cutoff(db, grade_ctx["now"])
api/srs.py: advance_learning_cutoff(db, now)
srs/anki_mirror/queue_engine.py: advance_learning_cutoff(db, now)
srs/anki_mirror/queue_engine.py: advance_learning_cutoff(db, now)
plugins/anki_sync/sync_engine.py: def _pull_advance_learning_cutoff(self, max_revlog_ms: int, dry_ru
plugins/anki_sync/sync_engine.py: advance_learning_cutoff(self._db, datetime.fromtimestamp(max_revlo
plugins/anki_sync/sync_engine.py: self._pull_advance_learning_cutoff(max_revlog_ms, dry_run)
```

The stickiness is intentional: while main or ready learning has anything in it, a learning card that ripens mid-session does not preempt the card on screen. Do not add a "live now" or per-poll advance.

**When the frozen queue is rebuilt.** TT rebuilds on far fewer triggers than Anki does, and this asymmetry is the most common source of a "TT and Anki disagree on the head card" report (§9.10). TT's triggers are:

- a non-dry-run `sync_pull`, which clears and then eagerly rebuilds `session_main_queue` via `build_and_freeze_main_queue` (Layer 29, so the freeze moment is sync time);
- `session_start=1`, which also does `clear_session_main_queue` then rebuild; this is the refresh-rebuild behaviour users rely on and the reason a sync-time "anchor queue" was rejected as a design (it cannot be re-derived on the request path, because the collection is off-limits there).

The cache is DB-backed, so it survives a backend restart. After changing queue-assembly code, run `clear_session_main_queue` before concluding a fix does not work, and bump `session_main_queue`'s `logic_version` so deployed caches are discarded.

**The badges.** `GET /api/srs/queue-stats` computes `new`, `learning`, `review` from TT state alone, with the same `effective_review_budget` as the served queue so the two cannot disagree:

- `learning`: `count_learning()`, every learning/relearning direction.
- `review`: `min(count_review_due_collocations(today), budget)`.
- `new`: `min(new_cap - introduced_today, available)`, where `available` is `count_new_available_collocations(today)` when `bury_new` is on, else the raw count; further capped by the review budget unless the ignore-limit flag is on.

### 9.7 Burying, unburying, and daily caps

**Sibling bury, in both directions of the mirror.** With `bury_reviews` on, a note leaves today's *review* pool when a sibling was graded today or sits in the learning queue (including interday learning steps graded on an earlier day). `count_review_due_collocations` encodes both clauses, and it counts *collocations*, not directions, so grading one direction of a dual note decrements the badge by one. A review card is **not** buried by a merely-NEW sibling. The converse is Layer 64: with `bury_new` on, a NEW card is buried when a sibling is graded today, learning, or review-due today (`count_new_available_collocations`); a sibling whose review is due in the future does not bury it. Layer 56 added the interday-learning trigger to the review count, Layer 47 made `sync_push` replicate Anki's grade-time sibling bury, and Layer 67 moved the "graded today" window from local midnight to the 04:00 rollover (`_anki_day_bounds_utc`), fixing a 66-vs-73 badge gap caused by siblings graded between midnight and 04:00.

**The daily unbury sweep.** `db.unbury_if_needed(today)` runs at the top of `/queue-stats`, `/review-queue` and `sync_pull`. It restores `state='buried' AND bury_kind='sched'` rows to REVIEW (or NEW if never scheduled), tracked by the `last_unbury_day` cache key so it is idempotent within the day; a second sweep would un-bury today's fresh sibling buries. It mirrors Anki's `unbury_on_day_rollover`. Anki writes `queue=-2` for a sibling bury (the binary does this, whatever the source suggests), and `_bury_kind_from_queue` therefore maps both -2 and -3 to `'sched'`. A `'user'` row (legacy from the v35 backfill; nothing in the API writes one today) sticks. Never write an unconditional `UPDATE ... WHERE state='buried'`; it wipes manual buries on every poll.

**Daily caps limit the served queue, not only the badge** (Layers 75 to 79). Anki gathers at most `new_limit - introduced_today` new cards and `review_limit - reviews_today - introduced_today` review cards, and the review limit also caps the new cards unless `newCardsIgnoreReviewLimit` is set. Interday learning (queue 3) charges the review budget; intraday learning (queue 1) does not. Both the badge and `_compute_live_main` call one helper:

```bash
cd backend && uv run python -c "
from app.srs.anki_mirror.queue_stats import effective_review_budget as b
print('cap 200, 20 reviews, 5 new introduced      ->', b(200, 20, 5))
print('  ... with new_cards_ignore_review_limit   ->', b(200, 20, 5, new_cards_ignore_review_limit=True))
print('  ... and 7 interday-learning cards due    ->', b(200, 20, 5, interday_learning_due=7))
print('cap 10, 9 reviews, 5 new introduced        ->', b(10, 9, 5), '(floored at 0)')
"
```

```output
cap 200, 20 reviews, 5 new introduced      -> 175
  ... with new_cards_ignore_review_limit   -> 180
  ... and 7 interday-learning cards due    -> 168
cap 10, 9 reviews, 5 new introduced        -> 0 (floored at 0)
```

The freeze model stays consistent under tightening caps: `reviews_today` grows as you grade, but graded cards leave the due pool, so the surviving frozen reviews always equal the remaining budget and nothing drops mid-session. Layer 76 (new introductions charge the review budget) shows up as the review badge sitting above Anki's by exactly the number of new cards introduced today. Layer 83 is the cautionary tale for the *order* of the new-card steps: TT used to Template-sort the whole new pool and truncate afterwards, which turns a ranking into a filter. With TEMPLATE ranking every ord-0 card ahead of every ord-1 card, no production card could ever survive the truncation while any new recognition card existed anywhere, so 284 minted production cards were never served. Two mechanisms predicted the same ~478 days of waiting; only the real Anki binary could tell them apart. The fix: truncate to the quota in gather order first, then rank the slice, and mirror `new_card_sort_order` and `new_card_gather_priority` per deck instead of hardcoding them.

The other half is where TT-created cards are *written*: Layer 84 gave `OfflineWriter` two position allocators (front-of-queue for TT additions, a reserved production band `[-1_000_000, 0)` filled upward), covered with the writer in §10.

### 9.8 The pending-listen bucket

A listen (§8) does not grade the learner outright. For each word it can confirm, it either applies a grade immediately, or **stages** a provisional grade in `pending_listen_grades` with a rating and a grade class (`due`, `ahead`, `learning`, `new`). Staged grades are TT-only: FSRS, the revlog, `dirty_fsrs` and sync see nothing until release. The lesson's "Check your work" queue (`GET /content/{id}/review-queue`) serves exactly the staged rows, so the queue and what the bulk "Sync it" button (`POST .../commit-pending`) would release are one query.

Three rules make this safe, and each came from an incident:

- **The bucket is per lesson (migration v42).** The key is `(lesson_id, collocation_id, direction)`. The earlier global key let one listen re-parent every card it shared with another lesson: listening to day 4 took day 5's bucket from 145 rows to 85 with nothing in the UI saying so (`6b967ccb`).
- **Insert is lesson-scoped; delete is card-scoped, on purpose.** Grading a word means it no longer needs review, so `clear_pending_grade` removes it from every lesson's bucket at once. Scoping the delete "for consistency" with the insert reintroduces the bug above. `clear_pending_grades_for_lesson` is the lesson-scoped exception, called at the top of a listen so its own staging pass replaces the last one.
- **A staged card is *not* withheld from the main queue.** Layer 81 once held pending cards out of the review badge and the served queue so the learner would not grade them twice. That was retired as F-14 (`48b0efb6`, `4ae12795`) on the user's decision: a due staged card is counted and served exactly as Anki would. The double-grade is impossible anyway, for three reasons: any real grade clears the pending row unconditionally in `drill_feedback`; the revlog kind is re-derived from the card's dueness at release time (`_release_review_kind`), never read from the stale stored class; and the frozen queue is intersected with a fresh live pool on every call, so a released card cannot be re-served. The accepted cost is that staged cards consume review slots, which is a parity gain because Anki charges them too. The oracle is `test_pending_grade_inclusion.py`, which pins absolute membership against the table rather than comparing the badge to the queue (those mirror each other by construction). If a badge gap tempts you to add a pending clause, read Layer 81's retirement note first.

There are three release paths, all of which clear the row: a per-card grade, the bulk `commit-pending` (one shared load balancer and a monotonic grade clock, because `tt_revlog.id` is a millisecond primary key), and `sync_pull` when Anki graded the card instead (gated on `_is_anki_grade`: reps up, or `last_review` moved forward, not on `_direction_differs`, which also fires on bury flips).

```bash
cd backend && uv run python -c "
from app.srs.database import SRSDatabase
from app.models.syntactic_unit import SyntacticUnit
with SRSDatabase(':memory:') as db:
    db.add_collocation(SyntacticUnit(text='Dober dan', translation='Good day', word_count=2, difficulty=1, source='llm'), 'sl')
    cid = db.list_collocations()[0][0][0]
    db.stage_pending_grade('day-4', cid, 'recognition', 'good', 'due')
    db.stage_pending_grade('day-5', cid, 'recognition', 'hard', 'due')
    print('staged in two lessons   :', db.count_pending_grades('day-4'), db.count_pending_grades('day-5'))
    db.clear_pending_grades_for_lesson('day-4')
    print('lesson-scoped clear day-4:', db.count_pending_grades('day-4'), db.count_pending_grades('day-5'))
    db.stage_pending_grade('day-4', cid, 'recognition', 'good', 'due')
    db.clear_pending_grade(cid, 'recognition')
    print('card-scoped clear (a grade):', db.count_pending_grades('day-4'), db.count_pending_grades('day-5'))
"
```

```output
staged in two lessons   : 1 1
lesson-scoped clear day-4: 0 1
card-scoped clear (a grade): 0 0
```

### 9.9 Grades leave an event trail: `tt_revlog`

A snapshot merge cannot represent events: if both apps graded the same card today at different moments, a field-by-field merge keeps one grade's values and loses the other. `tt_revlog` (migration v26) mirrors Anki's `revlog` so each grade is an event row. Its primary key is `(id, collocation_id, direction)`, with `id` the wall-clock milliseconds, like Anki's.

The entry point from the UI is `app/srs/feedback.py::rating_from_input`: the four review buttons (`again`, `hard`, `good`, `easy`) or an implicit player signal (`no_help` is Good, `slowdown` Hard, `translation_request` Again, `fast_forward` Easy) map to a `Rating`. `drill_feedback` then follows a fixed sequence: resolve params, steps, `col_crt` and a live load balancer from the request's db; `schedule()`; `update_direction_by_id`; `build_revlog_row` and `append_revlog`; `clear_pending_grade`; `record_grade_snapshot`; advance the learning cutoff.

Three kinds of row are written. TT grades (`drill_feedback`, the listen paths and `commit-pending`) via `build_revlog_row`. Anki grades are ingested during `sync_pull` by reconciling against Anki's revlog ids (Layer 58 replaced a wall-clock watermark that lost interior sync-gap grades), with a provenance-aware near-duplicate guard (`has_revision_near`, Layer 60) so rapid same-button Anki grades survive. Manual state changes such as `promote_to_learning` write `review_kind=4`. Layer 78 fixed the rows to mirror the *pre-answer* state (`lastIvl`, kind), and the `factor` is Anki's `round(difficulty_shifted * 1000)` computed in f32. Layer 80 made `sync_push` push one Anki revlog row per TT grade from `tt_revlog` rather than a collapsed row per card; the push and ingest mechanics are in §10.

```bash
cd backend && uv run python -c "
from datetime import date, datetime, UTC
from app.models.srs_item import SRSItem, Rating, Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import schedule, build_revlog_row
u = SyntacticUnit(text='Dober dan', translation='Good day', word_count=2, difficulty=1, source='llm')
item = SRSItem(syntactic_unit=u, due_date=date(2026, 3, 25))
now = datetime(2026, 3, 25, 12, 0, tzinfo=UTC)
rec = Direction.RECOGNITION
new = schedule(item, Rating.EASY, review_date=date(2026, 3, 25), now=now)
row = build_revlog_row(1, rec, item.directions[rec], new.directions[rec], Rating.EASY, 4200, now=now)
from dataclasses import asdict
for k, v in asdict(row).items():
    print(f'{k:16}{v}')
"
```

```output
id              1774440000000
collocation_id  1
direction       Direction.RECOGNITION
button_chosen   4
interval        16
last_interval   0
factor          354
taken_millis    4200
review_kind     0
anki_card_id    None
budget_neutral  False
```

Two TT-only refinements sit on top. `budget_neutral` marks a "Check your work" re-grade of a card the listen already reviewed today: it still replays through FSRS and syncs as an ordinary review, but `count_reviews_completed_today` skips it so the review budget is not charged twice. And `app/srs/grade_undo.py` keeps a single-level undo snapshot (the `last_grade_undo` cache key): `POST .../undo` restores the verbatim pre-grade `DirectionState` and deletes the revlog row, but only while the grade is still TT-local (still the direction's latest revlog row, `dirty_fsrs` still set). After a sync clears the flag, the review lives in Anki and undoing it in TT would just be clobbered by the next pull, so undo refuses.

`SRSDatabase.rebuild_from_revlog` replays a direction's rows through `schedule()` from NEW. It is the *detector* half of the soak (§9.10), not a source of truth: sync takes Anki's `cards.data` verbatim, and the replay only reports a `recompute_divergence` when they disagree. The `anki_card_id` argument is required, because the fuzz seeds off `card.id + reps`.

### 9.10 Holding the mirror: parity layers, the oracle, the soak

"Parity" means the user grades the same deck in both apps. Between syncs they run independently, so the head card and the three badge counts need to stay close enough that switching apps does not feel discontinuous; **sync is the alignment moment** and bounded drift between syncs is accepted. Every divergence found is recorded as a numbered *Layer*. `docs/anki-parity-layers.md` holds the history (bug first, then mechanism, files, tests), and `.claude/rules/anki-queue-parity.md` holds the principles and the decision tree for a divergence report. This section does not re-narrate the layers; it indexes them by what they pin.

| Family | Layers | The question it settles |
|---|---|---|
| Learning cutoff, queue freeze, rebuild | 1 to 5, 7, 23, 29, 36 | When does TT's frozen order change, and when is a learning card "ready" |
| Retrievability and elapsed days | 11 to 13, 15, 40, 45, 50, 54 | Fractional vs integer elapsed, the col-day helpers, the grade-time elapsed |
| FSRS arithmetic and intervals | 42, 44, 48, 51, 52, 57, 59, 62, 63 | Lapse stability, graduation, the interval cascade, f32, the clamp |
| Learning steps and fuzz | 6, 41 | Bit-exact RNG, single-step Hard delay |
| Load balancer | 53, 55 | The residual `due_at` gap, the live port |
| New-card gather, bury and order | 14, 24, 25, 28, 32, 33, 64, 65, 83, 84 | Gather order, cross-direction bury, Template sort, production gate, writer positions |
| Sibling bury and unbury | 27, 35, 47, 56, 64, 67 | `bury_kind`, the daily sweep, the 04:00 window |
| Badges and daily caps | 8a, 16, 26, 36, 73, 75 to 77, 79 | Counts from TT state only, `introduced_at`, review budget, new cap |
| Sync merge and push/pull seams | 17 to 22, 30, 37, 58, 60 to 61, 68 to 74, 80, 86, 87 | What the diff compares, Anki-ahead deferral, graves, revlog ids, per-grade push, suspension round trip |
| Pending bucket and isolation | 81 (retired), 82 | Staged grades, per-language injection |
| Seeded starter cards | 85 | A review card with no reps |

```bash
grep "^## Layer" docs/anki-parity-layers.md | tail -4 | cut -c1-110
```

```output
## Layer 84 — every card TunaTale writes was allocated to the BACK of the new queue (front-of-queue allocato
## Layer 85 — a seeded starter card is a REVIEW card with no reps, and "has a schedule" stopped meaning `rep
## Layer 86 — suspending and un-suspending a never-reviewed card from TunaTale was lossy in both directions
## Layer 87 — suspending a REVIEWED card through TunaTale wrote a review nobody made
```

**The oracle harness** pins TT against the real Anki scheduler rather than against TT's idea of it. A pytest fixture (`synthetic_collection`) builds a minimal modern `collection.anki2`, and a subprocess driver (`backend/tests/anki_oracle/oracle.py`, run with `uv run --with anki python`) opens it, enables the V3 scheduler, and answers JSON ops: queue order and counts, post-grade stability, `get_today`, `deck_today`, and so on. Tests are `backend/tests/test_parity_*.py`, marked `@pytest.mark.oracle`, opt-in via `--run-oracle`. The subprocess boundary is the architectural point: backend code and backend tests never `import anki`. Where the oracle and the Anki source disagree, trust the binary (Layer 38 was found that way). The harness mechanics, its fifteen gotchas and the CI wiring are in §10 and §14; the rule file is `.claude/rules/anki-oracle-harness.md`. Two CI facts belong here: `anki-gates` runs the oracle at the 04:00 rollover instead of the workflow's UTC, so a boundary-only failure there means TT and Anki genuinely disagree about the day (suspect product code first); and a finding that the harness surfaces is first filed `xfail(strict=True)` and fixed in its own commit.

**Three benign divergences** account for most head-card reports; check all three before suspecting the algorithm. All resolve at the next sync.

1. *Cutoff frozen at last grade.* Anki serves a learning card TT does not (or the mirror), because the learning cutoff only advances on the four triggers above. Refresh `/review` or grade a card.
2. *Independent grading drift.* Grading the same card in both apps seconds apart yields `due_at` differences of a few seconds, enough to swap two learning cards one second apart. Sync converges both to the later grader's timestamp.
3. *Asymmetric queue-rebuild cadence.* Anki rebuilds on a long list of triggers (reopen, undo, deck or preference changes, any non-grade card mutation), TT only on `sync_pull` and session start. Near-tied R values with very different stabilities can invert between two rebuild moments. TT's own sync triggers an Anki rebuild, because `safe_open` requires Anki closed and the reopen rebuilds with current R, so even "I only synced once" can produce divergent heads. The fix is to sync more often, not to re-sort mid-session.

**The soak.** Because `sync_pull` takes `cards.data` verbatim, the health signal is `recompute_divergences` approximately 0 per sync. Every sync appends a `SYNC_SOAK` heartbeat to `~/.tunatale/logs/sync.log` plus a `RECOMPUTE_DIVERGENCE` line per hit; a hit means a genuine Anki recompute (Optimize, a preset or retention change, a restore) that the forward-step replay could not reproduce. `FSRS_PRESET` and `PRESET_CHANGE` lines record preset drift, and `NOT_RESCHEDULED` marks the dangerous case where weights moved but Anki wrote no rescheduling revlog rows, so due dates are decoupled from stability (detection only; see §10). Do not "fix" TT's replay to match a Check Database or forced restore: Anki's `card.data` is not a pure replay of its own revlog.

**Why the mirror is kept, and what is rejected.** The Layers are encoded SRS correctness, not tech debt, and "fewer Layers" is not a goal. The goal is to lower the cost of holding the mirror without changing behaviour: single-source duplicated logic (the rollover module is the pattern, the field registry another) and keep decomposing by concern opportunistically. A sync-time "anchor queue" (run Anki's SQL ordering at sync, persist the card-id sequence, serve it filtered) was considered and rejected: it would delete the freeze and intersperser code but also kill refresh-rebuild, which re-sorts by current R on every `/review` mount and is Anki-faithful behaviour the user wants.

Before opening a new Layer, the rule file's pre-Layer checklist applies: name the divergence, look for an existing helper to extend (`docs/anki-parity-diagnostics.md` has the load-bearing-helper table), check whether the harness already covers it, and append the Layer to `docs/anki-parity-layers.md`. A queue-order change also bumps `session_main_queue`'s `logic_version`.

### 9.11 Schema and migrations

The schema is a chain of functions in `app/srs/migrations.py`, keyed on `PRAGMA user_version` and run by `migrate()` when `SRSDatabase` opens a file. Each migration is idempotent (guarded by `_column_exists` / `_table_exists`) so a half-applied step can be re-run. The recent chain, read from the module itself:

```bash
grep -n "^CURRENT_VERSION" backend/app/srs/migrations.py
cd backend && uv run python -c "
import inspect, app.srs.migrations as m
for n in range(41, 48):
    f = getattr(m, f'migrate_v{n}_to_v{n+1}')
    print(f'{f.__name__}: {inspect.getdoc(f).splitlines()[0]}')
"
```

```output
21:CURRENT_VERSION = 48
migrate_v41_to_v42: Make the pending-listen bucket per-lesson: add ``lesson_id`` to the key.
migrate_v42_to_v43: Link a base cloze to the word it covers (``base_collocation_id``).
migrate_v43_to_v44: Record that a word's image search came back empty (``image_unavailable_at``).
migrate_v44_to_v45: LLM-written cloze sentences, cached per word (``cloze_sentence_cache``).
migrate_v45_to_v46: Delete media rows whose collocation no longer exists.
migrate_v46_to_v47: A translation OF THE SENTENCE for the LLM cloze tier (``sentence_translation``).
migrate_v47_to_v48: Blank the 18 cloze rows whose sentence gloss is a copy of the word gloss.
```

| Version | Purpose |
|---|---|
| v42 | Pending-listen bucket becomes per-lesson (§9.8) |
| v43 | `collocations.base_collocation_id`: link a cloze to the word it covers |
| v44 | `collocations.image_unavailable_at`: remember an image search came back empty (§11) |
| v45 | `cloze_sentence_cache`: LLM-written cloze sentences per word (§11) |
| v46 | Delete media rows whose collocation is gone |
| v47 | `sentence_translation` for the LLM cloze tier |
| v48 | Blank cloze rows whose sentence gloss was a copy of the word gloss |

Opening a database is itself guarded, because the app opens every language DB at boot and a deploy can overlap two backends:

- **Each migration runs under `BEGIN IMMEDIATE`, with the version re-read inside the lock** (`eb0be6c8`). Without it, two processes both pass the version check and both run migration N, and the loser dies on `duplicate column name`. The cheap "already current" check stays outside the lock, so merely opening an up-to-date DB never blocks on a writer. SQLite only opens a transaction implicitly for DML, so a DDL-only migration would otherwise auto-commit statement by statement and could leave a half-applied step.
- **A pre-migration snapshot** `<stem>.pre-vN.db` is taken (by `app.storage.db_backup.snapshot_before_migration`) before the first pending migration, only when the DB has rows to lose. It is tagged with the version being left, one per version (the first wins, so a half-failed retry cannot overwrite the good copy), it raises rather than swallowing errors, and only the newest two per DB are kept (`PRE_MIGRATION_KEEP = 2`).
- **`SchemaTooNewError`** refuses to open a DB whose `user_version` exceeds the code's `CURRENT_VERSION`. `migrate`'s loop is one-directional, so older code would otherwise sail past and serve requests against columns it was never written for. Image rollback is not schema rollback; this says so out loud.
- `_configure_connection` sets `foreign_keys`, a 5 s `busy_timeout`, and WAL (read first and tolerate a lost race, because setting the journal mode does not honour `busy_timeout`).

Migrations are the one SRS change that needs a deliberate deployment story: a new column must also be registered in `direction_fields.py` (with a `sync_comparable` decision) if it lives on `collocation_directions`, and the writer sites that enumerate columns by hand (`update_direction`, `add_collocation`, the `db_sync` UPDATEs) must be updated; the schema-coverage test in `tests/test_direction_fields.py` sends you there. Deployment mechanics, including the pre-migration snapshot directory, are §15.

## 10. Anki Integration

TunaTale does not replace the learner's Anki deck; it is a peer of it. TT keeps its own copy of the deck (the SRS database of §9), its own throwaway Anki collection file, and a reconcile step that moves cards, schedules, review history, deck settings and media between the two and then lets AnkiWeb (or a self-hosted sync server) carry the result to the learner's phone and desktop. This chapter covers how that bracket is built, the invariants that keep the learner's real collection safe, and the places where TT and Anki must agree to the last digit. The scheduler arithmetic itself is §9; the cards that sync mints are §11.

The safety rules here are not style. Anki sync state lives in a few integer columns, and a wrong write to one of them silently forces a full re-upload on every later sync. The always-loaded summary is `.claude/rules/anki-safety-core.md` and the full protocol is `.claude/rules/anki-sync.md`; this chapter explains the reasoning and the code, and does not replace either.

### 10.1 The shape of the integration

There are three copies of the data, and keeping them apart is the first thing to understand.

| Copy | Where | Who writes it |
|---|---|---|
| The learner's real Anki collection | `settings.anki_collection_path` (desktop profile) | Anki itself. TT reads one value from it (the selected deck, see §10.4) and otherwise never opens it on a request path |
| TT's mirror collection | `settings.tt_collection_path`, `~/.tunatale/tt_collection.anki2` | TT, through `OfflineWriter`, and the sync driver subprocess |
| TT's own SRS database | `settings.database_url` (one per language, §2) | TT. This is what the review UI serves |

AnkiWeb is the meeting point. The mirror collection is a full Anki collection that TT syncs against the server exactly as a second device would, which is why it works while the desktop Anki is open. Nothing under `backend/app/` imports `anki`: the one module that does is `app/plugins/anki_sync/sync_driver.py`, a self-contained script run as a subprocess under its own interpreter (`settings.anki_subprocess_python`), because the `anki` wheel does not import on the backend's Python. The driver speaks one JSON command per line on stdin/stdout (`login`, `sync`, `full_download`, `full_upload`, `create_collection`, `media_pending` and a few test helpers), and `sync_orchestrator.py` keeps a persistent driver process alive between legs.

The plugin directory, by role:

```bash
cd backend && uv run python -c "
import ast
for f in 'sync sync_engine sync_reader sync_writer sync_common sync_orchestrator sync_driver safety secrets sqlite_reader'.split():
    doc = ast.get_docstring(ast.parse(open(f'app/plugins/anki_sync/{f}.py').read())) or ''
    print(f'{f + \".py\":22}', doc.splitlines()[0][:80])
"
```

```output
sync.py                Sync facade + runner — the single sync sequence ``run_full_sync``.
sync_engine.py         AnkiSync — the TT↔Anki reconcile engine (pull / push / create-new / orphans).
sync_reader.py         OfflineReader — read NoteRecords from a raw sqlite3 connection to collection.ank
sync_writer.py         OfflineWriter — write notes/cards/config/media into collection.anki2 via raw sql
sync_common.py         Leaf helpers shared across the sync modules — no internal sync imports.
sync_orchestrator.py   Anki peer-sync orchestrator (anki-free core module).
sync_driver.py         Anki sync driver subprocess.
safety.py              Safety envelope for opening an Anki collection.anki2.
secrets.py             Where the AnkiWeb password comes from, and in what order.
sqlite_reader.py       Read-only helpers for querying a collection.anki2 SQLite database.
```

`sync.py` is a runner and re-export facade. The `AnkiSync` engine is `sync_engine.py`, collection reading and writing are `sync_reader.py` (`OfflineReader`) and `sync_writer.py` (`OfflineWriter`), leaf helpers are `sync_common.py`, and tests import and patch everything through `app.plugins.anki_sync.sync`. There is no AnkiConnect, no "online" reader or writer and no mode detection any more: every path is an offline read and write of a collection file that TT owns.

### 10.2 The safety envelope

`app/plugins/anki_sync/safety.py::safe_open` is the only sanctioned way to open an Anki collection file. It exists because `collection.anki2` is production data, and a bare `sqlite3.connect()` has no lock probe, no backup and no audit. The sequence is fixed and visible in the source as numbered gates:

```bash
sed -n '/^def safe_open(/,/^def snapshot_collection/p' backend/app/plugins/anki_sync/safety.py | grep -n "# Gate\|# Retention"
```

```output
15:    # Gate 1: lock probe
18:    # Gate 2: SHA256 before open
29:    # Gate 3: backup via Connection.backup()
47:    # Gate 4: validate backup
50:    # Retention: bound the backup directory so it can't grow without limit.
55:    # Gate 5: open source connection (ro or rw per mode)
68:        # Gate 6: post-run SHA256 re-check (only in ro — rw writes *expect* change)
```

- **Lock probe.** `_probe_exclusive_lock` takes an exclusive lock; if Anki holds the file, `AnkiRunningError` is raised and the API turns it into a 409. This is why TT works on its mirror, not the live file.
- **SHA256 before open**, and for `mode="ro"` again after: any difference raises, because a read-only session during which the file changed is a torn read.
- **Backup** through SQLite's online `Connection.backup()` into `settings.anki_backup_dir`. The file name carries a per-call token (pid plus random) because two syncs in the same second used to share a name and validate each other's backup.
- **Validation**: the backup is opened independently, `integrity_check` runs and the note count must equal the source's. A mismatch aborts before the caller's transaction starts.
- **Retention**: `_prune_old_backups` keeps `settings.anki_backup_keep` (30). A failure to prune never affects the open.

`AnkiContext.audit_changes` is the post-write hook that diffs row counts and flags a write the caller did not intend (a new note appearing during a metadata update, for example). For recovery from a bad write see `docs/anki-recovery.md`.

### 10.3 USN, `col.mod`, graves and schema changes

Anki decides what to push by integer bookkeeping, and three rules follow from how it does so (the full derivation is in `.claude/rules/anki-sync.md`):

1. **Every touched row gets `usn = -1` and `mod = now`.** `usn = -1` is the per-row dirty flag. Without it Anki's integrity check re-detects the change at next open and bumps `col.scm` itself, forcing a full upload.
2. **`col.mod` is bumped after a batch; `col.usn` is never set to -1.** `col.usn` is the sync anchor (the server's last USN), not a dirty flag. Setting it to -1 is invisible on one device and then, as soon as a phone advances the server's USN, AnkiWeb cannot reconcile and demands a full sync. This was Layer 61, and the delete paths repeated it later (`d9c6fa2b`).
3. **`col.mod` is in milliseconds.** Unlike `cards.mod` and `notes.mod` (seconds), it is Anki's `TimestampMillis`. A seconds stamp is smaller than `col.ls`, so rslib decides the collection is not newer than the server and withholds its changes. `bump_col_mod` writes `MAX(now_ms, mod + 1, ls + 1)` so the value can equal neither (`026a699e`).

```bash
sed -n '/^def bump_col_mod/,/^class OfflineWriter/p' backend/app/plugins/anki_sync/sync_writer.py | grep 'UPDATE col'
```

```output
    conn.execute("UPDATE col SET mod = MAX(?, mod + 1, ls + 1)", (now_ms,))
```

**Deletes go through `graves`.** To remove a note, write one `type=0` grave per card, one `type=1` grave for the note, then delete the rows, all with `usn = -1` and only `col.mod` bumped. A bare `DELETE` makes AnkiWeb re-send the note on the next pull. The reading side is as important: `AnkiSync.detect_and_reset_orphans` consults `OfflineReader.get_grave_note_ids()` before deciding what a missing card means. A note in `graves` was deleted on purpose, so its TT collocation is hard-deleted; a note missing without a grave looks like a force-full-download wipe, so TT resets its pointers and re-mints. Simplifying that to "recover every missing card" makes deleted cards resurrect forever. The same method refuses to act if more than 25% of TT's linked cards look orphaned, raising `OrphanThresholdExceededError`, because that ratio almost always means `anki_collection_path` points at the wrong file.

**Schema-changing migrations** (anything that touches `col.scm`: adding a field or a template to a notetype) force AnkiWeb to demand a full upload, and then need three follow-ups, in order:

1. The learner uploads from Anki (File, Sync, Upload to AnkiWeb).
2. After Anki closes, `uv run python -m app.plugins.anki_sync.normalize_usns` clamps `cards.usn`, `notes.usn` and `revlog.usn` that exceed `col.usn` back to `col.usn`. A forced upload keeps local row USNs but resets `col.usn`, so any row left above it is dirty forever.
3. TT's mirror collection is now behind the server, and the next peer-sync correctly aborts on its pull leg with `AnkiWeb requires a one-way FULL_SYNC`. `uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap` re-downloads the mirror (download only, never an upload, never the desktop file).

Data-only migrations (rewriting GUIDs with `usn = -1`) stay inside incremental sync and need none of this. The one-shot modules that exist today are `add_vocab_notetype`, `add_production_template`, `add_image_field`, `reposition_production_cards`, `migrate_number_clozes` (retires number-word clozes, written but not run against production) and `replay_fsrs_from_revlog` (a read-only replay). The schema-changing ones open the collection through `safe_open(mode="rw")`. Older one-shots (GUID backfill, homonym migration, duplicate merging, grave scripts) live in `backend/scripts/anki_archive/`. `add_production_template` is what gives an imported recognition-only notetype the capability that §11.6 relies on.

### 10.4 One sync path: peer-sync, `main`, `run_full_sync`

`POST /api/anki/peer-sync` (`app/api/anki.py::trigger_peer_sync`) is the only HTTP sync endpoint and there is no sync CLI. It runs `sync_orchestrator.py::peer_sync`, a three-leg bracket:

1. **Pull leg.** Log in (the auth token is cached, and refreshed once if it has gone stale), mirror the learner's real selected deck into the mirror collection (`_mirror_real_curdeck_into_tt`; Anki uploads its whole config blob on every sync, so without this TT would switch the learner's current deck out from under them), then call the driver's `sync` with media enabled. This leg is bidirectional, so it is also where dirty collection rows go up and where server media comes down. A `required` field demanding a full sync raises `PeerSyncError`, which the API reports as 409, and TT does not clobber anything.
2. **Reconcile.** `sync.py::main` runs against the mirror with settings from `_tt_settings(language_code)`: `anki_collection_path` is replaced by `tt_collection_path`, and the language's database, deck and `target_language` replace the `.env` defaults. The language comes from `X-TT-Language` (§2), so syncing two languages takes two syncs. `main` resolves the mint notetype first (a language with nowhere to mint exits 1 before paying for a backup), opens the mirror with `safe_open(mode="rw")`, and runs `run_full_sync`. A non-zero exit aborts before the push leg so a half-reconciled collection is never uploaded.
3. **Push leg.** Skipped when `_has_pending_push` finds nothing dirty (the common case, since the learner mostly grades in Anki); otherwise the curDeck is re-mirrored and the driver syncs again, with media only if `_media_pending` says some is waiting.

Each run appends a `PEER_SYNC_TIMING` line and, from `main`, a `RECONCILE_TIMING` line to the sync log. The reconcile is timed per phase because it once reported as a single opaque 90-second figure (`tunatale-byw`).

**The phase list.** `run_full_sync` in `sync.py` is the one definition of what a sync does. This is its order at HEAD, extracted from the source rather than remembered:

```bash
sed -n '/^async def run_full_sync/,/^class NoMintNotetypeError/p' backend/app/plugins/anki_sync/sync.py | grep -o '_phase(timings, "[a-z_]*")' | sed 's/_phase(timings, "\(.*\)")/\1/' | nl
```

```output
     1	guid_collisions
     2	recognition_only
     3	orphans
     4	create_new
     5	push
     6	pull
     7	promote
     8	refresh_col_crt
     9	refresh_daily_new_cap
    10	refresh_daily_review_cap
    11	refresh_desired_retention
    12	refresh_fsrs_params
    13	watch_fsrs_preset
    14	refresh_fsrs_short_term_flag
    15	refresh_maximum_review_interval
    16	refresh_review_settings
    17	refresh_learning_steps
    18	refresh_load_balancer_enabled
    19	refresh_new_cards_ignore_review_limit
    20	refresh_easy_days
    21	warn_if_multi_deck_preset
    22	media_refresh
    23	soak_log
```

Reading it as a story:

- **Tripwires first** (`guid_collisions`, `recognition_only`). Both are read-only and both run on dry-runs. `warn_if_guid_collisions` logs `GUID_COLLISION` when two Anki notes share one `(text, disambig)` and therefore one TT guid; it keys on the disambig as well as the text because POS homonyms (Norwegian `løfte` noun and verb) are legitimately separate. `warn_if_recognition_only_deck` fires when over half a deck's notes sit on single-template notetypes, since every production-capable code path then degrades silently (the 2,990-word Norwegian gap that motivated §11.6).
- **`orphans`** runs unconditionally, then **`create_new`** (TT-added collocations become Anki notes, minted into the language's mint deck), **`push`** (dirty FSRS state, field edits, one Anki revlog row per TT grade) and **`pull`** (Anki's state wins, see §10.5).
- **`promote`** runs after the pull on purpose: the pull is what brings in graduations made in Anki since the last sync, so the trigger sees fresh state. It is covered in 11.6.
- **The non-dry block.** Only on a real sync: `refresh_col_crt` and the deck-config refreshes (daily new and review caps, desired retention, FSRS parameters, short-term flag, maximum interval, review settings, learning steps, load balancer, easy days, the new-cards-ignore-review-limit flag), each a no-op when the config is absent. `watch_fsrs_preset` is placed straight after the FSRS parameters (§10.8). `media_refresh` copies pulled note media into TT's media table when the peer path supplies a media directory. `soak_log` writes the `SYNC_SOAK` heartbeat.

The rule that keeps this list honest is the b0a4b8a lesson. When the Sync button was repointed at peer-sync, the peer reconcile ran only push and pull and silently dropped `sync_create_new` and every `refresh_*`. Each function was green in isolation and the orchestrator tests patched `main`, so nothing crossed the seam. Now there are three nets, all in CI: `tests/test_anki_sync_main.py::TestRunFullSync` pins the ordered phase set (the only sanctioned place to pin it), `tests/test_anki_sync_orchestrator.py::TestSociableSync` runs real `peer_sync` internals against an on-disk synthetic collection so dropping a phase turns an unlinked collocation red, and `tests/test_anki_peer_sync_selfhost.py` (`--run-peer-sync`) round-trips against a real throwaway sync server including both media directions. A new phase goes in `run_full_sync` and in the first of those tests. If you find yourself editing an entry point's body, stop.

After a real sync the API schedules two background tasks, `prestage_production_images` and `prestage_cloze_sentences` (§11.5, §11.6). Neither opens the collection; they only fill TT-side caches that the next sync's `promote` reads. Both are wrapped by `common/background_work.py::BackgroundWork.track`, which counts the job while it runs, logs one `BACKGROUND_DONE` line when it finishes (`GET /api/admin/background-work` reads the counts) and marks it as background so the LLM client lets a foreground request go first. The sync request itself returns before either finishes.

### 10.5 Push, pull and what "fidelity" means

TT's SRS state and Anki's must be able to replace each other without drift, so the engine is built around one asymmetry: **Anki is the source of truth for scheduling, and `sync_pull` takes its values verbatim.** The old three-mode switch that shadow-compared an event-replay against a field-by-field merge did its job (the soak ran clean) and was removed. What survives is the forward-step replay as a divergence detector: when TT's incremental replay of the revlog disagrees with `cards.data`, the report counts a `RecomputeDivergence` and the log gets a `RECOMPUTE_DIVERGENCE` line. The soak health bar is zero, and `grep RECOMPUTE_DIVERGENCE ~/.tunatale/logs/sync.log` should come back empty. Two incidents shaped that detector and are worth carrying: a divergence can mean the replay is missing an input (a grade inside a long sync gap was never ingested, so ingest now reconciles against Anki's full revlog), and Anki's `cards.data` is not a pure function of its revlog (a Check-Database restore re-stamped rows Anki never applied). The classifier notes are in `.claude/rules/anki-queue-parity.md`.

```bash
grep -o "def [a-z_]*" backend/app/plugins/anki_sync/sync_engine.py | grep "sync_pull\|sync_push\|sync_create\|promote\|detect_and\|warn_if\|_direction_differs\|_queue_to_state\|_tt_memory_newer\|_is_anki_grade\|_anki_step_ahead\|_push_revlog"  
```

```output
def _direction_differs
def _anki_step_ahead
def _tt_memory_newer
def _is_anki_grade
def _queue_to_state
def warn_if_guid_collisions
def warn_if_recognition_only_deck
def detect_and_reset_orphans
def sync_pull
def _run_sync_pull
def _push_revlog_for_direction
def sync_push
def sync_create_new
def promote_production_cards
```

Rules that look arbitrary until you know the incident:

- **TT grades that Anki has not seen are kept.** A direction with `dirty_fsrs = 1` is not overwritten by pull, because the next push overwrites Anki anyway. When Anki is ahead (`_tt_memory_newer`, `_anki_step_ahead`) pull defers to it instead.
- **One revlog row per TT grade.** `sync_push` writes each `tt_revlog` event through `_push_revlog_for_direction` instead of one collapsed row per dirty direction (Layer 80); the factor and review kind are derived from the card's prior state so Anki's graphs and FSRS optimizer see the real history.
- **A push to a missing note does not consume the edit.** `OfflineWriter.update_note_fields` returns whether it wrote, so `sync_push` leaves `dirty_fields` set and counts nothing when the note is absent from the mirror (`336ce968`). A discarded local edit says which card lost which field, and a push writes each edit into the note's own field.
- **Duplicate and GUID hygiene.** A TT guid is `(text, language, disambig)`. Two Anki notes sharing text and POS collapse to one collocation with two candidate cards, and `anki_card_id` could alternate between them (the foran incident, `1dd88359`). `db_sync.set_anki_ids` traces re-points as `RELINK_TRACE`, and `backend/scripts/anki_archive/reanchor_crossed_collocation.py` repairs a crossed pair. Ignore-list lemmas have their Anki cards graved (`grave_ignored_lemma_cards.py`, and `grave_named_cards.py` for specific words).
- **A vocab note's TT-side note survives pull** and "no Article field" is distinguished from "Article is blank", so a notetype that cannot carry an article is not told to write one.

### 10.6 Where TT mints, and into what

`sync_create_new` mints into a deck and a notetype that are both per-language data:

```bash
cd backend && uv run python -c "
from app.languages import get_mint_deck_name, get_vocab_notetype
for c in ('sl', 'no', 'tl', 'ceb'):
    v = get_vocab_notetype(c)
    print(c, '|', v.name, '| L2 field', v.l2_field, '| mint deck', get_mint_deck_name(c, default='(the read deck)'))
"
```

```output
sl | Slovene Vocabulary | L2 field Slovene | mint deck (the read deck)
no | Norwegian Vocabulary | L2 field Norwegian | mint deck (the read deck)
tl | Tagalog Vocabulary | L2 field Tagalog | mint deck 2. Pimsleur Tagalog::TunaTale
ceb | Cebuano Vocabulary | L2 field Cebuano | mint deck 3. Bisaya::TunaTale
```

`_resolve_model_name` takes `settings.anki_model_name` if set, else the language's registered vocab notetype, else raises `NoMintNotetypeError`. The earlier discovery fallback cached one notetype name globally and so quietly minted a Norwegian word into the Slovene notetype; failing loudly is the fix. A language's deck includes its subdecks when reading, but `LanguageConfig.mint_deck_name` redirects only the minting (Tagalog and Cebuano each mint into a `::TunaTale` subdeck of the imported Pimsleur and Bisaya decks); a missing subdeck is an error, not a silent fall back to the parent.

Card direction is no longer `ord == 0 means recognition`. `NotetypeProfile.recognition_ord` (default 0) and `field_map.direction_for_ord` decide it per notetype, because Pimsleur's genanki notetype has Card 1 as production (`1a8f5189`; the profile mechanism is §11.1). Per-language L2 scorers are the same kind of fix: the Slovene character scorer used to run on every language's heuristics, and a language with no scorer now refuses loudly (`41bdbe09`).

New cards are also held back when they would be bad cards. A vocab row with an empty translation and no extras is skipped by `sync_create_new` and stays NEW and studiable in TT until a later listen glosses it (an empty card reached Anki once and was failed twelve times). Rows whose directions are all suspended or buried are skipped, which is how "Ignore" works before a card ever leaves TT.

### 10.7 New-card positions (Layer 84)

Anki orders its new queue by `cards.due`, which for a new card is a position. TT-written cards must land at the end of that range the deck actually gathers from, or they exist but never surface. `OfflineWriter` has two allocators, and the choice depends on the deck's mirrored new-card gather priority (`new_cards_gather_descending`, passed to the writer by `main`):

```bash
cd backend && sed -n '/^_PRODUCTION_BAND_FLOOR/,/^_PRODUCTION_BAND_CEILING/p' app/plugins/anki_sync/sync_writer.py; grep -o "def _next_[a-z_]*position" app/plugins/anki_sync/sync_writer.py
```

```output
_PRODUCTION_BAND_FLOOR = -1_000_000
_PRODUCTION_BAND_CEILING = 0
def _next_front_position
def _next_production_position
```

- **`_next_front_position(slots)`** is for TT's own additions (listen adds, clozes), which should surface immediately: `MAX(due)+1` under HighestPosition gather, `MIN(due)-slots` under DECK gather, written at `base + ord` so a note's templates stay contiguous.
- **`_next_production_position()`** is for production cards minted by `promote`. Under DECK gather they fill the reserved band `[-1_000_000, 0)` upward, so the first card minted is the first served and mint order survives across syncs. Under HighestPosition there is no band (`MAX(due)+1` is already the front), which is documented residual behaviour.

Negative positions are load-bearing, not a hack. `cards.due` is an i32 and no Anki UI offers a negative start, so `tests/test_parity_front_positions.py` pins against the Anki binary that the scheduler gathers `[-1_000_000, -999_999, -5, 0, 7, 1518]` in that order. The assumption that `MAX(due)+1` was "the front" had been true for Slovene and was wrong for Norwegian, where it placed every fresh add behind 1,400 imported words; the diagnosis and the one-shot repair (`reposition_production_cards`, which refuses a second run keyed on the band) are recorded as Layers 83 and 84 in `docs/anki-parity-layers.md`.

### 10.8 Secrets and the FSRS preset watch

**The AnkiWeb password** resolves through `app/plugins/anki_sync/secrets.py`. The chain is an ordered list of `SecretSource` implementations built by `build_secret_sources`; first hit wins and `resolve_secret` returns it. The platform is a parameter rather than a read of `sys.platform` so a test can ask for the Linux chain on a Mac:

```bash
cd backend && uv run python -c "
from pathlib import Path
from app.plugins.anki_sync.secrets import build_secret_sources
for p in ('darwin', 'linux'):
    print(p, [type(s).__name__ for s in build_secret_sources(static_value='', file_path=Path('/run/secrets/pw'), platform=p)])
"
```

```output
darwin ['StaticSecretSource', 'FileSecretSource', 'KeychainSecretSource']
linux ['StaticSecretSource', 'FileSecretSource']
```

The Keychain source exists only on macOS, which is what unblocked the Linux deployment (§15): elsewhere the `security` binary does not exist, and a lookup would cost a doomed subprocess and an error message advising a command the box cannot run. Production uses `sync_password_file` so the secret stays out of the environment. Secrets never reach a log or an exception: `resolve_secret` names only the sources it tried. `SecretRequest` carries the AnkiWeb username as `account`, which is also the seam a future per-user encrypted password would key on. The macOS error text is deliberately unchanged because the learner's own setup notes depend on it.

**The FSRS preset watch** exists because of one bad week. An Anki Optimize, or a desired-retention edit, changes no card, so Anki writes no revlog row. Due dates stay where they were while the stabilities beneath them move, and `(due - last_review) / stability` silently decouples (the Slovene deck sat at a median 7.06 where about 1.5 was expected, and went undiagnosed for three days). `app/srs/anki_mirror/preset_watch.py::watch_preset` snapshots the preset (weights, retention, the median due ratio) each sync under the `fsrs_preset_snapshot` cache key and diffs against the last one. It counts the `type=5` (Rescheduled) revlog rows Anki wrote since, because "Reschedule cards on change" writes exactly those, and weights changed with no such rows is the dangerous shape. An unrescheduled change is stored as a `PresetChangeAlert` under `last_preset_change` and surfaced as an in-app banner; `GET /api/anki/preset-change` and `POST /api/anki/preset-change/dismiss` serve it. A dismissal belongs to one change, so the next unrescheduled one replaces the record. The module is detection only: nothing in it may write a due date.

### 10.9 Parity, the oracle and the tools

TT does not claim to match Anki; it measures. The parity harness runs Anki's real scheduler (through a subprocess oracle, `backend/tests/anki_oracle/`) against the same synthetic collection TT reads, and asserts queue order, retrievability, post-grade states and learning steps. The `backend/tests/test_parity_*.py` files each pin one behaviour (queue order, bury, daily caps, load balancer, FSRS f32 arithmetic, grade elapsed, front positions, studied-today marker and so on). Two rule files are required reading before touching them: `.claude/rules/anki-oracle-harness.md` for the harness and `.claude/rules/anki-queue-parity.md` before debugging any TT versus Anki divergence (the three most common causes are benign and documented at its top). The layer-by-layer history of what each fix was is `docs/anki-parity-layers.md`, summarised in §9.

Several day rules are anchored to Anki's local calendar date rather than UTC arithmetic. `anki_today()` replaced crt arithmetic for due dates between local midnight and 04:00 (`b684d826`), `compute_anki_day_index` was found to disagree with Anki's `days_elapsed` for one to four hours a day (`77cf421c`), and TT-native review grades now schedule from Anki's day (`2ba4919c`). The rules are written down in `5b4fd20b`; the CI jobs that run at the 04:00 rollover are in §14.

Language isolation is part of sync correctness too. Layer 82 was a cross-language leak: two resolvers called without a db each built an `SRSDatabase` from the singular `settings.database_url`, so Norwegian grades used Slovene learning steps and FSRS parameters (`80fbee1d`). The fix resolves in the caller that holds the request's db and injects the result, and `db` is now required on the `queue_stats` resolvers.

The modules that remain beside the sync engine, most of them one-shot tools run as `python -m app.plugins.anki_sync.<name>`:

```bash
cd backend && ls app/plugins/anki_sync/*.py | xargs -n1 basename | grep -v "^__init__\|^sync"
```

```output
add_image_field.py
add_production_template.py
add_vocab_notetype.py
import_seed.py
migrate_number_clozes.py
normalize_usns.py
replay_fsrs_from_revlog.py
reposition_production_cards.py
safety.py
secrets.py
sqlite_reader.py
```

Finished one-shots live under `backend/scripts/anki_archive/`; they are kept as worked examples of the `graves` and USN recipes (the canonical delete pattern mirrors `delete_phonology_demos.py`) rather than as tools to run again.

## 11. Cards, Media & Cloze

A TunaTale card is more than a word and a gloss. It has a notetype in Anki, an image, sentence or word audio, sometimes a cloze sentence, and a production direction that is minted only when the learner is ready for it. This chapter covers how those cards are shaped (`app/cards/`), where their pictures and audio come from, the pacing machinery that creates production cards just in time, the cloze subsystem and the two ways a learner's deck is seeded from another deck. It sits between the SRS engine (§9), which schedules whatever cards exist, and the Anki integration (§10), which carries them to the learner's devices.

Most of this subsystem is shaped by one fact: a bad card is expensive and a missing card is cheap. A wrong picture, a cloze whose blank admits six answers or a card with an empty back all get studied, failed and distrusted, while a word that waits another sync costs nothing. Nearly every router below declines when unsure.

### 11.1 Notetypes and field roles

A language registers a **vocab notetype**, the Anki notetype TT mints its own cards into, as plain data in its plugin (§3). `app/cards/vocab_notetype.py::VocabNotetype` names the notetype, the field that holds the L2 word and the CSS class for it. `create_vocab_notetype` builds the two-template notetype (recognition and production) with a hand-rolled protobuf encoder, because TT must not import `anki`; it is run by `app/plugins/anki_sync/add_vocab_notetype.py`, a schema-changing migration that follows the protocol in §10.3.

A second concept covers decks TT did not create. A `NotetypeProfile` in `app/cards/field_map.py` maps semantic roles onto an imported notetype's own field names: the L2 word, the translation, the disambiguation key, the article, the back fields shown on the card, and two roles that exist for clozes (`examples` and `inflections`). A notetype with a profile bypasses the reader's positional and HTML heuristics entirely. Profiles live in two places on purpose. A notetype whose name is specific enough to be global (Norwegian's `6000 Most Frequent Norwegian Words`) sits in `field_map._PROFILES`; a notetype with a generic name belongs to the language that owns the deck (`LanguageConfig.notetype_profiles`), because Pimsleur's `Basic (and reversed card) (genanki)` is what every genanki export is called and a global profile under that name would capture another language's deck. `get_profile(name, language_code)` consults the language first.

```bash
cd backend && uv run python -c "
from app.cards.field_map import _PROFILES
from app.languages import get_notetype_profiles
for n, p in _PROFILES.items():
    print('global     %-40s L2=%-15s recognition_ord=%d' % (n, p.l2, p.recognition_ord))
for code in ('sl', 'no', 'tl', 'ceb'):
    for n, p in get_notetype_profiles(code).items():
        print('%-10s %-40s L2=%-15s recognition_ord=%d' % (code, n, p.l2, p.recognition_ord))
"
```

```output
global     6000 Most Frequent Norwegian Words       L2=Norwegian word  recognition_ord=0
tl         Basic (and reversed card) (genanki)      L2=Back            recognition_ord=1
tl         Tagalog Vocabulary                       L2=Tagalog         recognition_ord=0
ceb        Cebuano Vocabulary                       L2=Cebuano         recognition_ord=0
```

`recognition_ord` is the template that reviews L2 to English. Pimsleur's second template is the recognition one, so `field_map.direction_for_ord` replaces the inline `ord == 0` test that used to swap every card of that deck. The Slovene deck deliberately has no profile: it mixes four notetypes (Vocabulary, Basic phonics, Pronunciation, Q&A) and its heuristic extraction is battle-tested, so adding a profile would risk a behaviour change for no benefit. A profile also carries `disambig_upos`, a map from the deck's own part-of-speech vocabulary to UPOS tags, which is how the closed-class test in §11.6 stays inside the language registry instead of matching deck labels in sync code.

`app/cards/l2_scoring.py::make_l2_scorer` is the per-language heuristic that tells an L2 field from an English one when there is no profile. It is a registry facet, not a Slovene default: the Slovene character scorer used to be applied to every language, so a language with no scorer now raises rather than guessing.

### 11.2 The media pipeline

`app/cards/media/pipeline.py::fetch_card_media` is the single entry point that produces a card's audio and image. It returns a `MediaResult` with the bytes, the chosen filenames and a status for each half, and it never raises for a missing asset: media is best-effort and must never block a card.

**Audio** is Forvo first, then synthesis, then loudness normalisation. Forvo is scraped (`forvo.py`, no API key; the paid API migration is written and parked on PR #16). `settings.forvo_enabled` gates it, and production sets `FORVO_ENABLED=false`: the same scraper that finds `takk` from a home connection gets HTTP 403 and a Cloudflare challenge from the datacenter box (`f2f9c834`, measured with a nonsense word as a control, which was blocked too, so the finding is about the network and not the word). When disabled, `audio_status` is `"disabled"`, a value kept separate from `"blocked"`, which means Forvo refused a call that was expected to work. Every non-found outcome falls back to synthesis (`tts.py`, through the same `get_tts_service()` the lesson renderer uses, §7) in the language's voice, and `normalize.py` runs ffmpeg `loudnorm` to a fixed target (-23 LUFS) so a deck has uniform volume. Synchronous work (Forvo, ffmpeg) is offloaded to threads so one slow fetch cannot stall the event loop.

```bash
sed -n '/^    if forvo_enabled is None:/,/audio_status = "disabled"/p' backend/app/cards/media/pipeline.py | grep -v "^ *#"
```

```output
    if forvo_enabled is None:
        from app.config import settings

        forvo_enabled = settings.forvo_enabled

    if not forvo_enabled:
        forvo = ForvoResult(ForvoOutcome.NO_PRONUNCIATION)
        result.audio_status = "disabled"
```

**Images** come from Pixabay by default (`pixabay.py`). The pipeline asks an LLM for a short sense-disambiguated query (`query_llm.py`, version-stamped `img-query-v2` and cached, so the same word is never paid for twice), searches, and filters out any URL already in the run's `used_image_urls` set. A search with no overlap with the query gets one retry with a simplified query. Among the survivors an LLM picks the best hit (`choose_llm.py`) and falls back to tag-overlap scoring if it declines. Search results are cached for 24 hours and the hand-curated English-to-query table is data (`cards/media/data/image_query_map.json`), not a module. `image_query == ""` is the documented skip sentinel for a word that should have no photo at all, and it is what a drawn picture (§11.4) passes so that no LLM call or Pixabay search is spent.

### 11.3 One picture per card

A learner told "this picture is the word for *know*" must not meet the same picture on the word for *know* in the other sense. Sharing a picture also destroys a production card, because the front then admits more than one right answer. Measured on the Norwegian deck, 16 images were shared by two or more different words, and the rules below came from that.

- **A digest guard runs at every store.** `SRSDatabase.image_digest_owner(sha256, exclude_collocation_id=...)` returns the card that already shows these exact bytes. `generate_vocab_media` (the add-time path) declines to store a duplicate and leaves the word imageless so the pre-stage can retry. The pre-stage adds the colliding URL to its used set and re-fetches once, serially; it is never a loop.
- **Filenames carry a digest suffix** (`img_<gloss>_<8 hex>.<ext>`). The old bare `img_<gloss>.<ext>` form let a second word with the same English gloss overwrite the first word's picture in place, silently changing a card the learner already knew. Three Slovene files had bytes that no longer matched their recorded hash before this was caught.
- **Image swaps never delete files.** `vocab_media._drop_image_rows` removes media rows only. One media directory is shared by every language database and every learner's database, and a caller sees only one of them, so "nothing here uses it" never means "nothing uses it". The Cebuano picture repair once deleted `img_side.jpg` while a Slovene card still showed it (`a80e9ec6`). An orphaned file costs disk; a wrongly deleted one breaks a card nobody was touching. The callers are `replace_item_image`, `DELETE /api/srs/items/{id}/image` (`app/api/srs_images.py`) and `picture_redraw.apply_redraws`.
- **Orphan media rows are swept** by migration v46 (the Norwegian deck had eight).
- **Restore drill caution.** One restored file (`img_cup.jpg`) was recovered from Anki. Do not repoint its media row at the hash-suffixed variant: that would swap the picture and orphan an `<img>` already written into the Anki note.

A word whose image search came back genuinely empty is stamped with `collocations.image_unavailable_at` (migration v44) and routed to a cloze (§11.6). A transient failure is not that: a Pixabay outage must never write a permanent "cannot be pictured" verdict, so the pre-stage uses `fetch_card_media`'s skipped, failed and empty classification to tell them apart (`c05097d0`).

### 11.4 Drawn pictures

Some words have a picture that is right by construction and that no photo can match: numbers, spatial words, personal pronouns, months and weekdays. These are rendered as SVG by TT itself. `app/cards/drawn_picture.py::drawn_picture` is the one dispatcher, and every path that gives a word an image asks it first: the add-time fetch, the mint in `sync_create_new`, the production pre-stage, the closed-class fork in `promote_production_cards` and the redraw repair.

```bash
sed -n '/^def drawn_picture/,$p' backend/app/cards/drawn_picture.py
```

```output
def drawn_picture(text: str, language_code: str, gloss: str) -> NumberPicture | None:
    """The drawn picture for *text*, or ``None`` to take the route it was on."""
    return (
        number_picture(text, language_code)
        or spatial_picture(text, language_code, gloss)
        or pronoun_picture(text, language_code, gloss)
        or calendar_picture(text, language_code, gloss)
    )
```

| Family | Module pair | What is drawn |
|---|---|---|
| Numbers | `number_scenes.py` / `number_picture.py` | A native cardinal is a heap of dots (13 is a rod and three dots). The Spanish-derived numbers of Tagalog and Cebuano are used as hours and prices, so 1 to 12 draw a clock and 13 up draw the coins and bills |
| Spatial words | `spatial_scenes.py` / `spatial_picture.py` | A box and a ball in the relation the word names. Each word gets its own picture, including the going/being split (`inn` versus `inni`) |
| Pronouns | `pronoun_scenes.py` / `pronoun_picture.py` | A conversation with the referent filled; possessives are the same scene plus a carried bag (and are gendered where the language is: min, mi, mitt) |
| Calendar | `calendar_scenes.py` / `calendar_picture.py` | A month is a 4 by 3 grid with its number lit; a weekday is one of seven columns |

The vocabularies are data facets of the language plugin (`numbers_path`, `spatial_path`, `pronouns_path`, `calendar_path`, each a JSON file in the plugin's `data/`), so adding a language adds JSON, not code. Three guards keep the dispatcher honest:

- A number is checked first and ignores the gloss, because its value is its meaning. Everything else needs the gloss to confirm the sense, because each family contains homographs the router cannot see: Norwegian `mars` is a month and a planet, Tagalog `linggo` is a week and a Sunday, Cebuano `wala` and `tuo` have non-spatial senses.
- Polysemous prepositions (Norwegian i, på, ved; Slovene na, v, za, med; Tagalog and Cebuano sa) are not drawn. They stay clozes, because no single scene is their meaning.
- `function_words.is_function_word` vetoes a drawn spatial word whatever its UPOS and a personal pronoun when tagged `PRON` or untagged. Without the veto, `fem` (five), a determinative that is perfectly closed-class, would be sent to a cloze whose blank admits every number. Norwegian `den` and `de` tagged `DET` keep the cloze route, since as articles they teach agreement, not a referent.

Cards minted before drawing existed keep whatever they were given. `app/cards/picture_redraw.py` plans and applies the swap and `backend/scripts/redraw_pictures.py` is the dry-run-first wrapper; it flags `image` dirty so the next sync writes the file into the Anki note through the ordinary push, and it opens no Anki file. It deliberately skips clozes (no Image field), leaves alone any card whose image already is the drawing (so a second run is a no-op) and leaves alone a number with no image (the mint now draws it).

### 11.5 Pre-staging: sync makes no network call

Fetching an image costs an LLM call, a Pixabay search and a download: a measured 10.0 seconds median per card, which was 88 to 97 percent of a 30 to 160 second sync before it moved (`tunatale-byw`). So the sync fetches nothing. `app/cards/media/prestage.py::prestage_production_images` runs as a FastAPI background task after every real peer-sync (§10.4), reading the queue of words awaiting a production card and storing their images into TT's own media for the next sync to use.

The mechanism has four passes:

1. **Triage, serially.** `_triage` sorts each candidate into fetch, draw or skip. It checks drawn pictures first, then the unpicturable marker, then `is_function_word` (a picture of `foran` is noise). Both queues share the same filters.
2. **Fetch, concurrently** under `PRESTAGE_CONCURRENCY = 5`. Both ends are rate-limited (Groq free tier, Pixabay), so this is a compromise: about 40 seconds for a 20-image refill, comfortably inside the gap between two syncs.
3. **Store, serially in deck order**, so the digest guard sees a stable sequence. Failures are counted apart from "no image" and never stamp the unpicturable marker.
4. **One recovery pass** for digest collisions.

Two details are there because they cost a month once. The gather uses `return_exceptions=True`: an escaped exception inside a background task is swallowed by the framework, which abandons the whole batch invisibly, and the measurement was `awaiting_image=173` with `minted=0` across six syncs. And there is a second queue, `list_production_cards_missing_images`, drained under `IMAGE_REPAIR_LIMIT = 3`, additive to the main limit (`settings.prestage_images_limit`, default 20) because sharing it would make the repair inert behind a 1,487-row backlog. The repair flags the stored image dirty, since the Anki card already exists and only `sync_push`'s vocab branch writes an `Image` that is flagged. The summary line `PRESTAGE_IMAGES` goes to the durable log with failure reasons de-duplicated, truncated and redacted of URL query strings and `key=` assignments, because the fetch chain's exceptions can echo an API key.

### 11.6 Just-in-time production minting

Most of the imported decks are recognition-only: one template, one card per note. TT's production direction (English to L2) is therefore created later, when the learner has earned it, and this is the biggest subsystem in the chapter. The logic is `AnkiSync.promote_production_cards` in `sync_engine.py`, run by the `promote` phase of `run_full_sync` (§10.4), after the pull.

**Selection.** `SRSDatabase.list_words_awaiting_production` returns words whose recognition has graduated and that have no production direction yet. The same query serves a fresh graduation (most recently graduated first, so it jumps the backlog) and the backlog already in review. **Pacing** is `PRODUCTIONS_PER_SYNC = 10`, a deliberate pedagogical choice settled with the learner: at three new cards a day a 1,500-word backlog is years of queue, so minting faster buys nothing and costs a fetch each. This is also why a bulk backfill of 2,990 cards was rejected. The budget is spent on work, not rows: a word that cannot be served costs one indexed read, and `PRODUCTION_SCAN_LIMIT = 200` candidates are read per sync so an unservable head of the queue cannot wedge the drain.

```bash
sed -n '/^PRODUCTIONS_PER_SYNC/p;/^PRODUCTION_SCAN_LIMIT/p;/^RECOGNITION_ONLY_WARN_SHARE/p' backend/app/plugins/anki_sync/sync_engine.py; sed -n '/^IMAGE_REPAIR_LIMIT/p;/^PRESTAGE_CONCURRENCY/p' backend/app/cards/media/prestage.py
```

```output
PRODUCTIONS_PER_SYNC = 10
PRODUCTION_SCAN_LIMIT = 200
RECOGNITION_ONLY_WARN_SHARE = 0.5
PRESTAGE_CONCURRENCY = 5
IMAGE_REPAIR_LIMIT = 3
```

**The router.** For each candidate the engine asks, in order:

1. Can the writer carry a production card? Capability is asked of the writer (`production_capable`, which looks at the notetype's templates), not of TT's mirror. A Slovene Basic phonics note or a cloze has no `Production` template, and minting into it would create an orphan that Check Database deletes. Imported notetypes gain the capability from `add_production_template` (an `Image` field plus a `Production` template).
2. Is it a **drawn** word (§11.4)? Then it is never a cloze candidate, and it waits for the pre-stage to draw it.
3. Is it **closed-class**? `is_function_word(text, language, upos=...)`, with the UPOS coming from the notetype profile's `disambig_upos`. Closed-class words go to a cloze before any fetch is spent, because a photo of `til` is noise.
4. Is the word marked unpicturable? That is a settled "cannot be pictured": it clozes, and costs one budget unit, since turning a word into a cloze is real, irreversible promotion work.
5. Does it have a staged image? If not, it is counted in `PromotionReport.awaiting_image` and left for a later sync. This is deliberately not a cloze fallback: "no image yet" and "cannot be pictured" are different claims, and only the pre-stage can tell them apart.
6. Otherwise `OfflineWriter.mint_production_card` writes the image field and creates the card as one transaction, and `db.add_production_direction` links it. Minting into an empty `Image` is forbidden, since Anki calls that an empty card.

Mint order is deck order, and positions come from the reserved band (§10.7). `PromotionReport` carries `awaiting`, `minted`, `adopted` (cards Anki already generated, merely linked), `clozed`, `unservable`, `no_template` and `awaiting_image`; the single `PRODUCTION_MINT` log line is a WARNING on purpose, since `start-dev.sh` runs uvicorn at warning level and an info line there is written nowhere a human reads (a 1,435-word backlog once sat for a month with the reason unreadable).

**The cloze fallback** (`_fallback_to_cloze`) writes a TT collocation with `card_type="cloze"` and stops; the next sync's `sync_create_new` mints the Anki note. It takes the sentence from the note's own example sentences (`cards/cloze_source.py::choose_cloze_sentence`), and otherwise from the cached LLM sentence (§11.7). Link rules, each one a bug found live:

- The cloze records which word it covers in `collocations.base_collocation_id` (migration v43). Rows stay separate because sync maps one note to one collocation, but every reader now resolves the word, and the selection query excludes by this link instead of a text match that cannot tell two homographs apart.
- A cloze already made from a lesson is adopted by linking, not minted twice. Each homograph meaning gets its own cloze, with a `sense:<Word class>` disambig (`d0ebb808`), and a displaced meaning moves once, deterministically.
- A variant-pair front (`fra, ifra`) clozes its more common spelling (`cloze_answer_spelling`); keying on the comma string had left four words unservable.
- A word with an empty disambig is `unservable` rather than minted, because its cloze row would share the vocab row's identity and `add_collocation` would merge them, stranding a direction with no card.

At sync time `warn_if_recognition_only_deck` and `PromotionReport.unservable` make the "nothing is happening" states visible: the words that can be neither pictured nor clozed from an example are the population the LLM tier below serves.

### 11.7 Clozes: kinds, text, LLM tier, judge

A cloze card is a sentence with a blank, on Anki's built-in Cloze notetype, with `card_type="cloze"` and only a production direction. It is made in three ways, and all three produce the same stored shape (`{{c1::answer}}` in `source_sentence`, with `sentence_translation` beside it, v21).

1. **Function-word clozes from a lesson.** `/listen` (§8) creates one when `is_function_word_for` matches. The blank is built from the surface as it appeared in the sentence, not the dictionary lemma, and the answer audio synthesises the surface too; otherwise a learner clozing `sem` would hear `biti`.
2. **Morphology clozes.** Following Fluent Forever, only the inflectional tail past the lemma and surface common prefix is blanked, leaving the stem visible. `POST /api/srs/inflection-clozes` creates one, gated on the lemma's base production being in REVIEW or KNOWN (clozes-only verbs like `biti` are ungated).
3. **Mint clozes** from `promote_production_cards` (§11.6).

```bash
cd backend && uv run python -c "
from app.srs.function_words import make_cloze_text, _ending_blank_split
print(make_cloze_text('bak', 'Han venter bak bygningen.'))
print(_ending_blank_split('Ljubljani', 'Ljubljana'), '<- stem kept, tail blanked')
print(_ending_blank_split('sem', 'biti'), '<- suppletive: LCP < 2, whole-word blank instead')
"
```

```output
Han venter {{c1::bak}} bygningen.
('Ljubljan', 'i') <- stem kept, tail blanked
None <- suppletive: LCP < 2, whole-word blank instead
```

`make_cloze_text(surface, source_sentence)` is idempotent, which is why sync can run it again over a pre-built cloze. The frontend masks with Unicode-aware lookarounds because ASCII `\b` does not match around š, č and ž. Cloze sentence audio is synthesised at mint (`app/audio/cloze_tts.py`, SHA256 of the sentence as the file name so cards sharing a sentence share a file), and `sync_common.py::build_cloze_back_extra` appends `[sound:...]` to Back Extra so notes reach Anki with their audio (`60b7c8ee`); the extractors strip the trailing tag via `_strip_sound_tags` so pull never sees a phantom field change. Cloze is always available: both of its old feature flags are gone, and creation is capability-driven (a curated function-word list, or an inflection-aware lemmatizer), never a toggle.

**The blind judge.** `choose_cloze_sentence` takes the first example containing the word and never asks whether the blank has one right answer. Measured over the live Norwegian clozes, 87 percent of 84 admitted another word: every personal-pronoun sentence accepts every pronoun, and *Er dette ___ bok?* accepts all six possessives. A word-class rule cannot be the gate, because the same classes produce the deck's best cards (`den` and `det` teach gender agreement, `seg` teaches reflexivity). So `app/llm/cloze_quality.py` is blind: it hides the answer, shows `___`, and asks what could fill it.

```bash
cd backend && uv run python -c "
from app.llm.cloze_quality import blank_out, UNJUDGED
print(blank_out('Han venter bak bygningen.', 'bak'), '| unjudged sentinel:', UNJUDGED)
"
```

```output
Han venter ___ bygningen. | unjudged sentinel: unknown
```

Asking "is this determined?" with the answer visible invites the model to rationalise a cue after the fact; making it fill the blank measures its real uncertainty, which is why one rule covers every word class. `ClozeVerdict` carries the competing fillers, not just a verdict, because the report and the UI both want to show what else fit. An LLM failure yields `"unknown"` (never a verdict), so a bad sentence is not silently kept and a good one is not regenerated. `backend/scripts/report_cloze_quality.py` judges a whole deck read-only as acceptance evidence; it exists because a verdict rule pinned against fixed replies proves nothing about the live model.

**The LLM tier.** `app/cards/cloze_prestage.py::prestage_cloze_sentences` is the second background task after a sync. For closed-class words with no clozable example it generates a sentence, judges it blind, translates it and caches the result in `cloze_sentence_cache` (migration v45, keyed `(word, language_code, model_version)`). The mint reads the cache and makes no network call. Its rules:

- A deck-authored example is preferred over a cached one: `Example sentences` is 98.7% populated, so preferring the cache would swap almost the whole deck for model output.
- An `underdetermined` sentence is still cached and minted, which is the learner's call (a card with a loose blank beats no card); the verdict rides along so `/review` can offer "try again" on exactly those.
- An **unjudged** sentence (the judge call failed, say a 429) never mints and is re-judged on a later pass, never regenerated (`c30032cb`). A live "hun snakker om." once minted unjudged.
- A generated sentence that already contains `___` blanks is never cached or minted (`carries_a_blank`, `4ea6b0c9`).
- The cache carries the sentence's own translation (v47), and the mint puts that, never the word's gloss, in the sentence slot. Writing the word's gloss into both slots rendered the same text twice on 18 live cards; migration v48 blanks those rows.

**The cloze API.** Two endpoints implement confirm-before-write. `POST /api/srs/items/{id}/cloze/propose` writes nothing and returns the stored and a proposed sentence, each with its verdict and a `recommended` flag; two draws at most, since "Suggest another" is the loop. `PUT /api/srs/items/{id}/cloze/sentence` stores a chosen or hand-typed sentence (422 when the answer is absent from it, because a blank over nothing is an empty card), and in one step regenerates the translation, drops and re-synthesises the sentence audio and marks the row dirty. The first version persisted on the click that produced the proposal, so a sync firing before the learner read it had already rewritten the Anki note; the write now sits behind a human. Neither endpoint opens the collection: `sync_push` rewrites the note in place through `OfflineWriter.update_cloze_text` (guid, `sfld` and `csum` included), so the card keeps its scheduling and revlog. The UI is the Cards viewer's row menu (§13).

### 11.8 Norwegian card quality

TT-minted Norwegian cards had a set of defects that all trace to the same cause: the production lemmatizer is a lookup table that tags no gender or definiteness. The repairs, each at the card boundary:

- **Gloss definiteness.** A generated gloss must agree with the headword's definiteness, and known multi-word traps (`no/data/multiword_traps.txt`) are skipped (`srs/gloss_definiteness.py`).
- **Noun gender.** Noun cards front with their article, "en morder", not a bare "morder". The article comes from `no/data/noun_genders.tsv.gz`, derived from the NST lexicon and read by `plugins/languages/no/noun_gender.py`, which is needed precisely because the lookup-table lemmatizer tags no gender.
- **Verbs** carry the infinitive marker "å". The story generator emits an optional `base` key per `dialogue_glosses` entry for the verb's dictionary form, and a verb card's gloss is reduced to its bare form as a fallback, which keeps the transcript's in-context gloss separate from the card back.
- **Lemma plausibility guards** reject a stanza lemma that is a different or non-word (trøtt to trø, snømenn to snøm, rør to rure). A one-shot repaired 13 defective rows.
- **Adjective agreement** (en fin bil, et fint hus, fine biler) is in the Norwegian A1 morphology table (§3).

### 11.9 Seeding decks

Two mechanisms start a learner with cards they have not earned the slow way.

**Cognate seeding.** A learner moving from Tagalog to Cebuano already knows words they have never studied. `app/srs/cognate_seed.py` is pure (no database, no Anki, no clock; `backend/scripts/seed_cognate_cards.py` is the wiring). It classifies each known word against a kaikki.org dictionary extract, which is the review gate:

```bash
cd backend && uv run python -c "
from app.srs import cognate_seed as c
print([r.name for r in c.Relation])
print('discounts: cognate x%s, near-cognate recognition x%s' % (c.COGNATE_DISCOUNT, c.NEAR_COGNATE_DISCOUNT))
print('source needs stability >= %s days; seed capped at %s days; at most %s a day' % (c.MIN_SOURCE_STABILITY, c.MAX_SEED_STABILITY, c.DEFAULT_PER_DAY))
"
```

```output
['COGNATE', 'NEAR_COGNATE', 'FALSE_FRIEND', 'UNRELATED']
discounts: cognate x0.5, near-cognate recognition x0.25
source needs stability >= 21.0 days; seed capped at 60.0 days; at most 10 a day
```

Same spelling plus a shared English content word is a `COGNATE`. A near-spelling needs an equivalent gloss (stricter, because the spelling is weaker evidence). The same spelling with no compatible gloss is a `FALSE_FRIEND`: reported, never minted. Seeded cards are REVIEW cards with the memory state carried over at a discount and, crucially, **no revlog rows**: Anki's FSRS optimizer trains on the revlog, and a fabricated review would be treated as a real answer and bias every parameter fitted after it. Only a direction the learner genuinely knows (REVIEW or KNOWN) carries over; production is not seeded for near-cognates; `schedule` spreads them at most ten a day and never on the same day as a sibling, since Anki buries a review whose sibling was answered that day. It is two passes with a sync between, because only a card that exists in Anki can be seeded (`seed_review_state` refuses an unlinked one): `--apply` mints the starters as NEW; sync; `--apply` again seeds the well-known ones; sync again to push the schedule. The script is a dry run by default. Seeded starter cards raised a small parity point of their own (Layer 85 in `docs/anki-parity-layers.md`: "has a schedule" stopped meaning `reps > 0`). `backend/scripts/seed_base_list.py` adds a Fluent Forever style base list as picture cards, after the cards already waiting; function words are never minted as picture cards.

**A second learner's deck.** `app/srs/user_deck_seed.py::seed_user_deck(source, dest)` copies the owner's deck for one language into the new learner's per-user database (§2) and resets everything that records the owner. The source is opened read-only; the copy is VACUUMed before it is published, because deleted rows survive in free pages and those pages would be the owner's review history; it publishes with a hard link and refuses to overwrite an existing deck.

```bash
cd backend && uv run python -c "
from app.srs import user_deck_seed as u
print('CLEARED :', ', '.join(sorted(u.CLEAR_TABLES)))
print('KEPT    :', ', '.join(sorted(u.KEEP_TABLES)))
print('RESET   :', ', '.join(sorted(u.TRANSFORM_TABLES)))
"
```

```output
CLEARED : audio_files, curricula, lesson_listens, lesson_reviews, lessons, pending_listen_grades, review_sessions, sync_conflicts, tt_revlog, violations
KEPT    : cloze_sentence_cache, collocation_tags, ignored_lemmas, image_query_cache, lemma_analysis_cache, media, sqlite_sequence
RESET   : anki_state_cache, collocation_directions, collocations
```

Every table and column is classified in that module, and one the seed does not know is a refusal (`SeedRefused`), not a silent copy: one learner's history travelling inside another's file has no visible symptom. Cards the owner already studied go in front of the new-card queue in the order the owner first reviewed them, which is the curriculum they were actually given; suspended cards stay suspended with their schedule reset; every direction returns to NEW with its Anki pairing stripped; FSRS parameters are the defaults, not the owner's trained weights, and deck configuration is kept in `anki_state_cache`. `backend/scripts/seed_user_deck.py` is the dry-run-first wrapper (the account must exist and must not be the owner). Run both scripts on the live side, under that side's environment, with the target database backed up first.

## 12. The API Layer

The API is a thin FastAPI shell over the services built in §6–§11: it resolves the caller's account and language, picks that caller's stores off `request.state`, delegates, and shapes the response. It is also the contract the SvelteKit frontend (§13) is typed against, so most of this chapter is about guarantees: who may call a route, which database a call lands in, what error a failure becomes, and how a type change in Python reaches TypeScript. Identity and per-language connections are set up in §2; this chapter is the routing and contract layer on top.

### 12.1 Wiring: routers, dependencies, middleware

`backend/app/main.py` builds one `FastAPI` app and mounts one router per module under `backend/app/api/`. The wiring is the access policy: each `include_router` line carries the dependency list that gates every route beneath it.

```bash
grep -n "^OWNER =\|^USER =\|include_router" backend/app/main.py
```

```output
482:OWNER = [Depends(require_user), Depends(require_owner)]
483:USER = [Depends(require_user)]
484:app.include_router(curriculum.router, dependencies=USER)
485:app.include_router(generation.router, dependencies=USER)
486:app.include_router(srs.router, dependencies=[Depends(require_user)])
487:app.include_router(srs_images_api.router, dependencies=[Depends(require_user)])
488:app.include_router(audio.router, dependencies=USER)
489:app.include_router(review_sessions_api.router, dependencies=USER)
491:    app.include_router(anki.router, dependencies=OWNER)
492:app.include_router(admin.router, dependencies=OWNER)
497:app.include_router(client_log_api.router, dependencies=[Depends(require_user)])
498:app.include_router(llm_api.router, dependencies=USER)
499:app.include_router(auth_api.router)  # NO router-level dependency — login/logout are unauthenticated
```

Three tiers result:

| Tier | Mounted with | Meaning |
|------|--------------|---------|
| open | no dependency | `auth` (login/logout/status) and `/api/health` |
| `USER` | `require_user` | any logged-in account (or everyone, when `auth_enabled` is off) |
| `OWNER` | `require_user` then `require_owner` | process-global state: Anki sync, admin tools |

`require_user` is `backend/app/auth/dependencies.py::require_user`; it reads `settings.auth_enabled` per request so tests can flip it after import. `require_owner` is listed second on purpose, so an anonymous caller gets 401 ("log in") rather than 403. The `anki` router is mounted only when `sync_enabled` is on and the optional `anki_sync` plugin is importable, so on a lean deployment those routes simply do not exist. Lesson surfaces (curriculum, generation, audio, review sessions, llm) are `USER`, not `OWNER`: a learner's pipeline jobs carry their account, so their lesson lands in their own files (`tunatale-98zf.3`).

Before any route runs, the `_resolve_language_state` middleware binds the caller's account and per-language stores onto `request.state`, and `_refuse_while_parked` answers 503 while an instance is parked. Routes never open a database themselves. Both middlewares, and the rule that an unconfigured `X-TT-Language` is a 400 rather than a silent fallback, are described in §2.3.

A guard test pins the "every route is behind a session" claim behaviourally rather than structurally: `backend/tests/test_auth_route_coverage.py` sweeps every route and demands a 401. Its docstring records two traps worth knowing before you write a similar test: included routers are lazy `_IncludedRouter` objects so `app.routes` shows only two routes, and `route.dependencies` is empty even for a guarded route.

CORS is built from settings by `main.py::cors_kwargs` (§2.9). It must keep three custom headers, `X-TT-Language`, `Range` and `Idempotency-Key` (§12.5).

### 12.2 The route table, generated from the app

This table is not hand-maintained: each block below loads the real app and walks `app.openapi()`, so it is the surface at HEAD. (`frontend/src/lib/api-schema.json` is the committed copy of the same dictionary; §12.4 covers keeping them equal.) Operation count first, then the families.

```bash
cd backend && uv run python -c "
from app.main import app
ops = [(m, p, o) for p, v in app.openapi()['paths'].items() for m, o in v.items()]
from collections import Counter
c = Counter((o.get('tags') or ['(untagged)'])[0] for _, _, o in ops)
print(len(ops), 'operations')
for t, n in sorted(c.items()): print(f'{n:3d}  {t}')
"
```

```output
102 operations
  6  (untagged)
  3  admin
  3  anki
  6  audio
  1  client-log
 16  curriculum
  6  generation
  4  llm
  3  pipeline
 15  review-sessions
 35  srs
  4  srs-images
```

Cards, queue and listen (the `srs` and `srs-images` tags). Both routers share the `/api/srs` prefix; the image routes (`backend/app/api/srs_images.py`) carry their own tag.

```bash
cd backend && uv run python -c "
from app.main import app
want = ['srs', 'srs-images']
rows = {}
for p, v in app.openapi()['paths'].items():
    for m, o in v.items():
        t = (o.get('tags') or ['(untagged)'])[0]
        if t in want: rows.setdefault((t, p), []).append(m.upper())
for (t, p), ms in sorted(rows.items()): print(f\"{'/'.join(ms):<12}{p.replace('/api/srs', '')}\")
" | sed 's/^/ /'
```

```output
 POST        /backfill-translations
 POST        /content/{content_id}/commit-pending
 GET         /content/{content_id}/listen-preview
 GET         /content/{content_id}/review-queue
 POST        /content/{content_id}/reviewed
 GET         /content/{content_id}/transcript
 GET         /due
 POST/DELETE /ignored-lemmas
 POST        /inflection-clozes
 POST/GET    /items
 POST        /items/base
 POST        /items/bulk-delete
 PATCH/DELETE/items/{item_id}
 POST        /items/{item_id}/cloze/propose
 PUT         /items/{item_id}/cloze/sentence
 POST        /items/{item_id}/direction/{direction}/feedback
 POST        /items/{item_id}/direction/{direction}/undo
 POST        /items/{item_id}/reset
 POST        /items/{item_id}/restore-known
 POST        /items/{item_id}/state
 POST        /items/{item_id}/suspend
 POST        /items/{item_id}/untrack
 POST        /listen
 GET         /listens
 POST        /listens/import
 GET         /media/{filename}
 GET         /new
 GET         /queue-stats
 GET         /review-queue
 GET         /stats
 POST        /translate
 POST        /translate-missing
 PUT/DELETE  /items/{item_id}/image
 GET         /items/{item_id}/image/candidates
 PUT         /items/{item_id}/image/upload
```

Lessons and planning: the curriculum planner, the generation pipeline status, and story/lesson fetch and import.

```bash
cd backend && uv run python -c "
from app.main import app
want = ['curriculum', 'pipeline', 'generation']
rows = {}
for p, v in app.openapi()['paths'].items():
    for m, o in v.items():
        t = (o.get('tags') or ['(untagged)'])[0]
        if t in want: rows.setdefault((t, p), []).append(m.upper())
for (t, p), ms in sorted(rows.items(), key=lambda kv: (want.index(kv[0][0]), kv[0][1])):
    print(f\"{t[:4]:<5}{'/'.join(ms):<12}{p.replace('/api/curriculum', '/c').replace('/api/story', '/story')}\")
"
```

```output
curr GET         /c
curr POST        /c/import
curr POST        /c/plan
curr GET/DELETE  /c/{curriculum_id}
curr DELETE      /c/{curriculum_id}/days/{day}
curr GET         /c/{curriculum_id}/days/{day}/lesson
curr POST        /c/{curriculum_id}/generation-mode
curr POST        /c/{curriculum_id}/plan/commit
curr POST        /c/{curriculum_id}/plan/feedback
curr POST        /c/{curriculum_id}/plan/reset
curr POST        /c/{curriculum_id}/plan/turn
curr POST        /c/{curriculum_id}/plan/turn/prompt
curr GET         /c/{curriculum_id}/progress
curr POST        /c/{curriculum_id}/review-pressure
curr GET         /c/{curriculum_id}/source
pipe GET         /c/{curriculum_id}/pipeline
pipe POST        /c/{curriculum_id}/pipeline/regenerate
pipe POST        /c/{curriculum_id}/pipeline/retry
gene POST        /story/generate
gene POST        /story/import
gene GET         /story/prompt
gene GET         /story/{lesson_id}
gene POST        /story/{lesson_id}/regloss
gene GET         /story/{lesson_id}/source
```

Review sessions and the remaining small families.

```bash
cd backend && uv run python -c "
from app.main import app
want = ['review-sessions', 'audio', 'llm', 'admin', 'anki', 'client-log', '(untagged)']
rows = {}
for p, v in app.openapi()['paths'].items():
    for m, o in v.items():
        t = (o.get('tags') or ['(untagged)'])[0]
        if t in want: rows.setdefault((t, p), []).append(m.upper())
for (t, p), ms in sorted(rows.items(), key=lambda kv: (want.index(kv[0][0]), kv[0][1])):
    print(f\"{t[:6]:<7}{'/'.join(ms):<12}{p.replace('/api/review-sessions', '/rs')}\")
"
```

```output
review POST/GET    /rs
review POST        /rs/import
review GET         /rs/prompt
review GET/DELETE  /rs/{session_id}
review POST        /rs/{session_id}/import
review GET         /rs/{session_id}/prompt
review POST        /rs/{session_id}/regenerate
review POST        /rs/{session_id}/regloss
review POST        /rs/{session_id}/render
review POST        /rs/{session_id}/render-estimate
review GET         /rs/{session_id}/render-status
review POST        /rs/{session_id}/rerender
review GET         /rs/{session_id}/source
audio  GET         /api/audio/lesson/{lesson_id}
audio  GET         /api/audio/lesson/{lesson_id}/zip
audio  POST        /api/audio/render
audio  POST        /api/audio/render-estimate
audio  POST        /api/audio/rerender
audio  GET         /api/audio/{audio_id}
llm    GET         /api/llm/activity
llm    GET         /api/llm/health
llm    GET         /api/llm/rate-limit
llm    POST        /api/llm/rate-limit/probe
admin  GET         /api/admin/background-work
admin  POST        /api/admin/refresh-media
admin  GET         /api/admin/tts-cache
anki   POST        /api/anki/peer-sync
anki   GET         /api/anki/preset-change
anki   POST        /api/anki/preset-change/dismiss
client POST        /api/client-log
(untag POST        /api/auth/login
(untag POST        /api/auth/logout
(untag GET         /api/auth/me
(untag GET         /api/auth/status
(untag GET         /api/health
(untag GET         /api/languages
```

Reading the families:

- **`srs`** is the largest surface because the frontend's review screen, reader and cards viewer all sit on it. `review-queue` serves the unified due-plus-capped-new queue (§9); `content/{id}/…` routes (transcript, review-queue, listen-preview, commit-pending, reviewed) are addressed by a content id that resolves either a lesson or a review session (`ContentStore.get_readable_content`), which is why the reader is shared (§13.5); `listen` marks content listened and registers its words (§8); the `items/…` family is the card admin behind `/cards`.
- **`curriculum`** carries the chat-style planner (`plan`, `plan/turn`, `plan/turn/prompt` for manual Claude-chat mode, `plan/commit`, `plan/reset`, `plan/feedback`), plus `generation-mode` and `review-pressure` settings. The **`pipeline`** routes live under the same prefix with their own tag; `curriculum.py` documents why it carries no router-level `tags=` (FastAPI prepends router tags, which would have double-tagged the pipeline routes).
- **`generation`** (`/api/story`) generates, imports and fetches one lesson, and exports its prompt and Story-JSON source for manual mode. `regloss` repairs a lesson that lost its glosses.
- **`review-sessions`** is the standalone, curriculum-free surface (§6): create, list, fetch, import, regenerate, regloss, render, render-estimate, render-status, rerender, source, prompt, delete.
- **`audio`** serves render metadata, the per-section ZIP and the audio file itself; `rerender` and `render-estimate` re-render selected sections and price them before spending (§7).
- **`anki`** (owner only) is `peer-sync` plus the FSRS preset-change banner; **`admin`** is media refresh, the TTS cache stats and the background-work snapshot.

### 12.3 Request and response models

Every request and response body is a Pydantic model in `backend/app/api/models.py`, one flat module of request and response classes.

```bash
grep "^class .*\(Request\|Response\)(" backend/app/api/models.py | sed -n 1,12p
```

```output
class ListenRequest(BaseModel):
class ImportListensRequest(BaseModel):
class ListenPreviewResponse(BaseModel):
class CommitPendingResponse(BaseModel):
class DrillRequest(BaseModel):
class TranslateRequest(BaseModel):
class CreateItemRequest(BaseModel):
class UpdateItemRequest(BaseModel):
class BulkDeleteRequest(BaseModel):
class SuspendRequest(BaseModel):
class SetStateRequest(BaseModel):
class IgnoreLemmaRequest(BaseModel):
```

Two conventions matter more than the class list. First, models are the migration path: `ListenRequest.word_ratings` is `dict[int, …]` keyed by collocation id, and the type change *is* the migration: a stale client sending text keys fails int coercion with a 422 instead of silently losing every grade (`tunatale-og4d`). Second, shared response shaping lives in `backend/app/api/_serializers.py::serialize_lesson`, used by both `GET /api/story/{id}` and the by-day lookup so the two cannot drift. It deliberately returns only the review meter from `generation_metadata`, not the glosses or Story-JSON source that sit beside it, which would bloat every lesson fetch on the reading path.

Binary endpoints are declared honestly: `audio.py::download_lesson_zip` uses `response_class=Response` with `responses={200: {"content": {"application/zip": {}}}}`, and the audio and media file routes use `FileResponse`, so the schema does not advertise JSON that is not there (`30c3f86f`).

### 12.4 The OpenAPI contract and type drift

Backend-to-frontend type safety is a committed artifact rather than a running server. `backend/scripts/dump_openapi.py` writes `app.openapi()` to `frontend/src/lib/api-schema.json`; `openapi-typescript` (the frontend's `gen:api` script) turns that into `frontend/src/lib/api-types.d.ts`; `frontend/src/lib/api.ts` aliases the generated types. Two gates keep the chain honest, both in `./test.sh` and CI:

```bash
sed -n '/^Two checks:/,/^Usage::/p' backend/scripts/check_openapi_snapshot.py; grep -n '"gen:api"\|"check:api"' frontend/package.json | cut -c1-110
```

```output
Two checks:

1. **Snapshot freshness** — regenerates the schema from ``app.openapi()`` in-
   memory and diffs it against ``frontend/src/lib/api-schema.json``.  A diff
   means the developer forgot to re-run ``dump_openapi.py``.

2. **Untyped endpoint gate — zero tolerance.** Every operation with a 2xx JSON
   response must declare a Pydantic response model.  There is no ledger and no
   escape hatch: any untyped operation fails.

   The shrink-only ``tests/openapi_untyped_grandfather.txt`` drained 70 -> 0 over
   eleven batches and was deleted on 2026-08-02, along with the ratchet that
   enforced it, exactly as the mock / language-literal / date-today ledgers were
   in ``7b34c73``.  "Shrink-only ledger, currently at zero" and "no additions,
   period" are the same rule; only the second needs machinery.

Usage::
17:		"gen:api": "openapi-typescript src/lib/api-schema.json -o src/lib/api-types.d.ts && oxfmt src/lib/api-typ
18:		"check:api": "mkdir -p node_modules/.tmp && openapi-typescript src/lib/api-schema.json -o node_modules/.t
```

The untyped-endpoint rule is zero tolerance: every 2xx JSON response must name a Pydantic model (`response_model=`), recursing through `list[...]`, optionals and `allOf`. This started as a shrink-only ledger of 70 untyped endpoints and drained to zero over eleven batches (`3621a152`..`9f9fdf71`); the ledger and its ratchet were then deleted because "a ledger at zero" and "no additions, period" are the same rule and only the second needs no machinery. When you add or change an endpoint the loop is: edit the model, `uv run python scripts/dump_openapi.py`, `bun run gen:api`, fix whatever `bun run check` now flags. `check_openapi_snapshot.py` runs `app.openapi()` in memory and diffs it against the committed file, so a forgotten dump fails the backend job, and `bun run check:api` fails the frontend job if `api-types.d.ts` is stale.

Limits of the types: a field the backend declares as plain `str` is a `string` in TypeScript. Section types are an example. The renderer's tokens are not enumerated by the schema, so the frontend keeps its own tuple in `frontend/src/lib/sectionTypes.ts` (§13.3).

### 12.5 Idempotency: one paste, one session

Routes that mint content have no natural duplicate refusal (every call is a valid request for a new thing), and a phone that drops a 2.5-minute import and retries would create a second review session. The caller therefore sends an `Idempotency-Key` header, and `backend/app/api/idempotency.py::once` makes the second call join the first. The 2026-09-19 incident that motivated it saw a duplicate arrive 75 seconds after its twin.

```bash
grep "^async def once\|^_TTL_SECONDS\|^    return await asyncio.shield\|registry_key =\|create_task\|^def _forget\|^def _evict" backend/app/api/idempotency.py
```

```output
_TTL_SECONDS = 15 * 60
def _evict_expired(registry: dict[tuple[str, int | None, str, str], _Entry], now: float) -> None:
def _forget_if_it_failed(
async def once(
    registry_key = (scope, getattr(request.state, "user_id", None), getattr(request.state, "language_code", ""), key)
        task = asyncio.create_task(work())
    return await asyncio.shield(entry.task)
```

Four properties, each guarding a failure mode:

1. **The work runs in its own task and every caller awaits it through `asyncio.shield`.** A disconnecting client cancels its own await, not the generation, and the retry that arrives later is handed the finished result.
2. **The key includes scope, user id and language.** Scope separates an import from a generation sharing a key; user id stops two accounts' replays from answering with each other's result; language mirrors why session listings are language-scoped.
3. **Failures are not memoised.** A done-callback drops the entry when the task failed or was cancelled, so a learner whose generation died on a 429 can really retry with the same key. A wedged button is worse than the duplicate.
4. **No key means no deduplication**, by design: inventing a key from the body would collapse two genuine imports of the same text.

The registry is in memory and deliberately not a table: its honest lifetime is the window in which a retry can arrive (15 minutes), and a restart ends every in-flight request anyway. On the client, `frontend/src/lib/api.ts::idempotencyHeader` spreads the header only when a key exists, because a conditionally set value could become the string `"undefined"` and dedupe every keyless caller together (§13.2).

### 12.6 `app.state` access and background work

Services created in the lifespan (§2) hang off `app.state`. `backend/app/api/app_state.py` is the one module allowed to read it defensively: every optional resource has a same-named accessor returning `None` when unconfigured, and callers fail soft on `None` (`llm(request) is None` means "no LLM", not an error).

```bash
grep "^def " backend/app/api/app_state.py | cut -d'(' -f1 | cut -c5- | tr '\n' ' '; echo
```

```output
_optional activity_log audio_dir auth_db content_stores language languages lemmatizer llm model_version pipeline renderer tts user_dbs idempotent_writes review_renders lesson_renders background_work 
```

Three accessors create their state on first use rather than in the lifespan: `idempotent_writes`, `review_renders` and `background_work`. The reason is test ergonomics: tests set `app.state.*` by hand and never run the lifespan, so a lifespan-only initialiser would `AttributeError`. `review_renders` is the in-memory in-flight set behind `GET /api/review-sessions/{id}/render-status`. `app/common/background_work.py::BackgroundWork.track` wraps fire-and-forget jobs (image prestage after a sync, for example), counting in-flight, completed and failed ones and logging `BACKGROUND_DONE`; `GET /api/admin/background-work` exposes the snapshot.

### 12.7 Error mapping

Domain errors become HTTP statuses in two places. Router-specific refusals are `HTTPException`s raised in the handler. Errors that any route can raise are mapped once, app-wide, in `main.py`:

```bash
grep -A1 "^@app.exception_handler" backend/app/main.py | grep -v "^--" | cut -c1-110; grep -c "return JSONResponse(status_code=\(429\|502\|409\)" backend/app/main.py
```

```output
@app.exception_handler(LLMQuotaExceededError)
async def llm_quota_exceeded_handler(request: Request, exc: LLMQuotaExceededError) -> JSONResponse:
@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError) -> JSONResponse:
@app.exception_handler(NoReviewVocabularyError)
async def no_review_vocabulary_error_handler(request: Request, exc: NoReviewVocabularyError) -> JSONResponse:
3
```

The distinctions are deliberate. `LLMQuotaExceededError` is a 429, not a 502: nothing upstream failed, TunaTale declined because the daily budget is spent, and a 502 would invite retries that cannot succeed. `LLMError` is a 502 with the retry detail instead of a raw 500 traceback. `NoReviewVocabularyError` is a 409: nothing failed and nothing is malformed, the learner asked for a review session with nothing due. The two auto-create paths in `review_sessions.py` catch it first to reword it ("no vocabulary is due in this language today" reads as a normal Tuesday, not a broken button). The frontend reads the status off the thrown error rather than matching message text (§13.2). See §5 for where these errors originate.

### 12.8 Health and the unauthenticated surface

A handful of routes answer without a session: the auth entry points (`status`, `login`, `logout`, described with the throttle in §2.6) and health.

`GET /api/health` is the container healthcheck and uptime target. `backend/app/api/health.py::check_health` runs four checks, each with a 2 s timeout: `database` and `content_store` (an indexed lookup on every language's store), plus `audio_dir` and `media_dir` (create and delete a real file, because `os.access` reports success on a read-only mount). It returns `{"status", "checks"}` and answers 503 (not 200 with an unhealthy body) on any failure, because every consumer reads the status code natively and one that forgot to parse the body would otherwise fail open. The body carries status only; paths and exception messages would leak filesystem layout on an unauthenticated route, and an absent dependency counts as a failure, since an unmounted volume must not read green. `GET /api/languages` is gated like the data routes but exempt from the unknown-language refusal, as §2.3 explains.

Because health takes its dependencies as arguments, its tests break a real directory instead of patching one, which is the mock-boundary rule from §14 applied to the one endpoint whose job is to fail when something real is broken.

## 13. The Frontend

The frontend is a SvelteKit single-page app in `frontend/`, built with `adapter-static` and served as plain files; every piece of data comes from the API of §12, typed by the generated schema. Its centre of gravity is the lesson page: a sticky player (§13.4) over a word-by-word reader (§13.5) whose colours, bolding and blur come from the learning state of §8–§9. Around it sit the review drill, the review-session reader, the cards viewer, and a handful of small stores that hold per-device preferences.

### 13.1 Shape of the app

Production needs no Node process. `frontend/svelte.config.js` uses `adapter-static` with `fallback: 'index.html'`: nothing is prerendered, every path gets the same shell and the client router takes over. The consequence is that the host must serve `index.html` for unknown paths or deep links 404 (§15 covers the Caddy side). The data routes also declare `export const ssr = false`, and `$lib/api.ts` speaks to relative `/api/...` paths, so the dev server's Vite proxy and production's reverse proxy are interchangeable.

```bash
cd frontend/src/routes && find . -name '+page.svelte' | sort; grep -n "adapter(" ../../svelte.config.js
```

```output
./+page.svelte
./c/[curriculumId]/+page.svelte
./c/[curriculumId]/l/[lessonId]/+page.svelte
./c/[curriculumId]/plan/+page.svelte
./cards/+page.svelte
./login/+page.svelte
./review-sessions/+page.svelte
./review-sessions/[sessionId]/+page.svelte
./review/+page.svelte
./settings/+page.svelte
49:		adapter: adapter({ fallback: 'index.html' })
```

| Route | What it is |
|-------|-----------|
| `/` | Lessons home: curricula, the plan-a-curriculum form, the dated review-session list, "new review session" and manual paste |
| `/c/[curriculumId]` | Curriculum overview and day picker, plus the generation pipeline card |
| `/c/[curriculumId]/plan` | Chat planner (`PlannerChat`, `ProposedBatch`, review-pressure select) |
| `/c/[curriculumId]/l/[lessonId]` | The lesson page: player, reader, listen actions, source panel |
| `/review-sessions` | Every review session, newest first: the page a session's back link and delete return to |
| `/review-sessions/[sessionId]` | A review session, rendered through the same reader shell as a lesson |
| `/review` | The SRS drill; with `?lesson=<id>` a read-only "check your work" pass over one lesson's words |
| `/cards` | Card viewer and admin; `?focus=<id>&q=<text>` deep-links from the drill |
| `/settings`, `/login` | Per-device preferences; sign-in |

`frontend/src/routes/+layout.svelte` owns the global chrome: brand, nav (Review, Lessons, Cards, Settings), the `LanguageSelector` code pill, the `SyncButton`, the theme and the banners. It also registers the two API-client callbacks from §13.2 and, when the instance is parked, replaces the whole UI with the parked screen. `LanguageSelector` stores the code and reloads, except on a page that belongs to one language (`/c/…`, `/review-sessions/…`), where it navigates home because that id would 404 in the other language's database.

### 13.2 The API client and generated types

`frontend/src/lib/api.ts` holds one class, `TunaTaleAPI`, and one instance, `api`. Its type surface is derived rather than copied: response types are aliases of `components["schemas"][…]` from `frontend/src/lib/api-types.d.ts`, which `bun run gen:api` generates from `api-schema.json` (the contract loop is §12.4). Where a hand-written interface remains, a comment says why; `CurriculumSummary` is the example, because the generated schema of the same name is a different shape (one element of the list endpoint) and aliasing it would drop fields.

```bash
cd frontend/src/lib && grep "unauthorizedHandler\|parkedHandler\|X-TT-Language\|retryAfter\|\.status = " api.ts | head -14
```

```output
  return code ? { "X-TT-Language": code } : {};
let unauthorizedHandler: (() => void) | null = null;
  unauthorizedHandler = handler;
let parkedHandler: ((url: string) => void) | null = null;
  parkedHandler = handler;
        unauthorizedHandler?.();
        if (res.status === 503 && typeof parkedAt === "string") parkedHandler?.(parkedAt);
      const retryAfter = res.headers.get("Retry-After");
      if (retryAfter) (err as Error & { retryAfter?: number }).retryAfter = Number(retryAfter);
      (err as Error & { status?: number }).status = res.status;
        ...(languageCode ? { "X-TT-Language": languageCode } : {}),
```

`TunaTaleAPI::request` is the single funnel and encodes four policies:

1. **Language.** Every request carries `X-TT-Language` from the `tt-language` localStorage key, so the backend middleware (§12.1) picks the right per-language connection. SSR or no selection sends no header and gets the default.
2. **Session expiry.** A 401 outside `/api/auth/*` fires a registered `unauthorizedHandler`. The client cannot navigate itself (it must stay importable in bare jsdom tests), so it reports and `stores/auth.svelte.ts` decides. `/api/auth/*` is excluded because a 401 there is an ordinary answer (wrong password; "not logged in").
3. **Parked.** A 503 whose body has `parked_at` fires `parkedHandler`, which swaps in the parked screen.
4. **Errors carry a status.** The thrown `Error` has `.status` and, from `Retry-After`, `.retryAfter`; the message is the server's `detail`, including FastAPI's 422 list flattened into `field: message` lines. Callers branch on `.status` to tell a refusal (409 nothing due, 429 quota) from a failure, never on the wording, which is written for the learner and changes.

Mint routes take an optional idempotency key through `idempotencyHeader`, which spreads the header only when a key exists. The home page holds one `crypto.randomUUID()` per creation attempt and clears it only on success, so a retry after a dropped connection is recognisably the same intent (§12.5).

The auth store has three states for "does this deployment need a login": on, off, and unknown. Unknown redirects nobody, because guessing "on" strands a developer whose backend is briefly down on a login page whose submit cannot work either. This is the client half of the `/api/auth/status` design in §12.8.

### 13.3 Stores and per-device preferences

Everything under `frontend/src/lib/stores/` is a Svelte 5 rune store. Two families:

- **Server-backed state** polled or hydrated through `api`: `listened.svelte.ts` (which lessons have been listened to, backed by `GET /api/srs/listens`; it also migrates the pre-server localStorage keys once), `queueStats`, `pipeline` (polls the generation pipeline every 2 s while active, 10 s otherwise, with a generation counter so a stale poll cannot write after `stop()`), `llmActivity`, `llmHealth`, `rateLimit`, `language`, `auth`, `parked`, `sync`.
- **Per-device preferences**, all built on `localPref.svelte.ts::createLocalPref`: hands-free mode, player collapsed, English-order mode, caption blur, listen countdown, wifi prefetch, voice, the reader's "Produce" toggle.

```bash
cd frontend/src/lib && sed -n '/^export function createLocalPref/,/^}/p' stores/localPref.svelte.ts | head -30; ls stores | grep -c Pref
```

```output
export function createLocalPref<T>(key: string, opts: LocalPrefOptions<T>): LocalPref<T> {
  // Undefined means "no value seeded", NOT a value: the default is parse(null),
  // and it is computed on the read that needs it rather than at module load.
  // Eagerly would be wrong twice over — the module loads before the environment
  // is ready (jsdom has no matchMedia, and a real load can race the same way),
  // and parse is the caller's, so its preconditions are its own business.
  let value = $state<T | undefined>(undefined);
  // Deliberately NOT $state: whether we have seeded is not a value a consumer
  // can depend on, and making it one would have every reader re-run on the seed.
  let initialized = false;

  function init(): void {
    initialized = true;
    if (typeof localStorage === "undefined") return; // SSR: keep the default
    let raw: string | null = null;
    try {
      raw = localStorage.getItem(key);
    } catch {
      // Private mode / blocked site data: degrade to the default rather than
      // breaking whatever the preference is attached to.
      raw = null;
    }
    value = opts.parse(raw);
  }

  function set(next: T): void {
    initialized = true;
    value = next;
    if (typeof localStorage === "undefined") return;
    try {
20
```

`createLocalPref` exists because each preference store had separately re-spelled three needs: a self-init on first read (a deep component like `LessonPlayer` mounts before the layout's `onMount` seeds anything), tolerance of blocked localStorage (private mode throws on get *and* set; a preference must never break the thing it is attached to), and a safe no-window default. The key and stored string format stay the caller's, because users already have those values on disk. Theme and language are deliberately not built on it: they act on the value, so they need to know when it is applied.

`frontend/src/lib/sectionTypes.ts` is the one place the renderer's section tokens are spelled. The API types `section_type` as plain `string`, so nothing can be derived from the schema; the tuple gives a compile error on a misspelling in the three places that must agree (player track choice, pill state, hands-free order).

```bash
cd frontend/src/lib && sed -n '/^export const SECTION_TYPES/,/^export type/p' sectionTypes.ts
```

```output
export const SECTION_TYPES = [
  "key_phrases",
  "natural_speed",
  "translated",
  "en_translated",
  "slow_speed",
  "slow_translated",
  "slow_en_translated",
] as const;

export type SectionType = (typeof SECTION_TYPES)[number];
```

Study-day arithmetic is likewise stated once: `frontend/src/lib/studyDay.ts` mirrors the backend's 04:00 local rollover (`ROLLOVER_HOUR`), so "due today" means the same day on both sides (§9).

### 13.4 The lesson player and its playback controller

`LessonPlayer.svelte` is the UI; `frontend/src/lib/playback/playbackController.svelte.ts::createPlaybackController` is the engine behind it, wrapping one `HTMLAudioElement`. The split exists so the controller is unit-testable with an injected audio element and `MediaSession`, and so its state machine has one home.

**Track mode.** Since the renderer emits per-section audio with per-section cue manifests (§7), the player works in *track mode*: it selects one section file at a time rather than seeking inside one long file. `LessonPlayer::trackMode` is true only when every section row carries its own cues. A lesson rendered before that existed (or one with no full-lesson file, since a render now produces only the section files) falls back: legacy lessons stay on the full-lesson track where sentence navigation works off the full manifest, because switching them would strand playback on one cue-less section.

**Pills and the English cycle.** The learner sees phase pills (key phrases, dialogue), an enunciation level (natural or slow) and an English mode; `LessonPlayer::resolveSectionType` folds the three into one section token. English mode is a three-state cycle, `off`, `l2_first` ("English After") and `en_first` ("English Before"), and lessons rendered without the English-first sections cycle just `off` and `l2_first`.

```bash
cd frontend/src/lib && sed -n '/^\tfunction resolveSectionType/,/^\t}/p' components/LessonPlayer.svelte
```

```output
	function resolveSectionType(phase: Phase, enunLevel: string, engMode: EnglishMode): SectionType {
		if (phase === 'key_phrases') return 'key_phrases';
		const natural = enunLevel === 'natural';
		if (engMode === 'off') return natural ? 'natural_speed' : 'slow_speed';
		if (engMode === 'l2_first') return natural ? 'translated' : 'slow_translated';
		return natural ? 'en_translated' : 'slow_en_translated'; // en_first
	}
```

**Hands-free.** `HANDS_FREE_SEQUENCE` is the pass order, and a hands-free run plays it end to end:

```bash
cd frontend/src/lib/playback && sed -n '/^export const HANDS_FREE_SEQUENCE/,/satisfies/p' playbackController.svelte.ts; sed -n '/^const SEQUENCE_STEP/,/^\]);/p' playbackController.svelte.ts
```

```output
export const HANDS_FREE_SEQUENCE = [
  "key_phrases",
  "natural_speed",
  "slow_speed",
  "translated",
] as const satisfies readonly SectionType[];
const SEQUENCE_STEP: ReadonlyMap<string, number> = new Map([
  ["key_phrases", 0],
  ["natural_speed", 1],
  ["slow_speed", 2],
  ["translated", 3],
  ["slow_translated", 3],
  ["en_translated", 3],
  ["slow_en_translated", 3],
]);
```

Every English variant is the single "EN" step, so the Section ▶ button from any of them hands off to what comes next. The chip is three-state (Off, On, Repeat) and the rules are:

- A run completes at the end of the last pass the lesson actually has (passes are found over the sections present, because `selectTrack` is a no-op on a missing section and a blind `idx + 1` would loop one track forever).
- `onHandsFreeEnd` reports the fact and stops; what comes next is the *page's* question. The lesson page's `onSequenceEnd` re-reads the curriculum progress map (a failed mount fetch or a newly generated day would otherwise end the run as if this were the last day), arms a one-shot baton in `sessionStorage` through `handsFreePref.armHandoff()`, and navigates to the next day; the arriving page consumes the baton and starts playing at Key Phrases. Review sessions use `reading/nextReviewSession.ts::nextSessionAfter`, which orders by date with an id tiebreak so a run cannot bounce between two sessions sharing a day.
- Repeat loops the lesson only on the *automatic* end of a run, and the 0.9x/0.8x enunciation speed applies only on `slow_*` tracks.
- Hands-free mode lives in a store, not on the controller, because the controller is per-lesson and is destroyed by the very navigation hands-free performs. The baton is separate from the setting on purpose: merely opening a lesson with hands-free left on must not start playing at you.

**Car and headset controls.** Lock-screen and steering-wheel buttons arrive as Media Session actions, registered in the controller with `setActionHandler`. The mapping is the same with hands-free on or off: next/previous track step by sentence (grouping cues by their shared ref so a sentence is one stop), seek-forward goes to the next *pass*, and seek-backward goes to the previous pass (restarting the first pass when there is none behind it).

```bash
cd frontend/src/lib/playback && grep -o 'setActionHandler("[a-z]*"' playbackController.svelte.ts | sort -u | tr '\n' ' '; echo
```

```output
setActionHandler("nexttrack" setActionHandler("pause" setActionHandler("play" setActionHandler("previoustrack" setActionHandler("seekbackward" setActionHandler("seekforward" setActionHandler("seekto" 
```

Two hard-won details. `navigator.mediaSession` is a global singleton, and on a client-side navigation the incoming controller initialises *before* the outgoing one tears down; measured on 2026-09-12, the new one set its metadata and the stale one nulled it a millisecond later, so the car showed nothing while the buttons still worked. Teardown is therefore ownership-scoped through a module-level `mediaSessionOwner` token. And because nobody can read a screen while driving, `frontend/src/lib/mediaTrace.ts` writes an on-device trace (every action, every refused `play()`, every hand-off decision) tagged with a per-controller id so a log can say which instance emitted a line.

Position is saved on `visibilitychange`/`pagehide` (throttled) and resumes into the right section. The Repeat latch loops the current sentence until switched off, and next/previous cancel it. The artwork shown on the lock screen is the app icon.

**Offline audio.** Audio is cached by `frontend/src/service-worker.ts`, deliberately a thin shell; all decisions live in the tested `$lib/sw/audio-cache.ts`. Strategy is cache-first on the full file with *synthesised* Range responses: lesson audio is immutable (keyed by a server-minted id), `<audio>` sends `Range` requests, Chromium stalls over a service worker unless it gets a `206`, and the server's own `206` cannot be cached, so the worker caches the full `200` and cuts `206` slices itself. `sw/prefetch.ts` can prefetch a lesson on wifi when the Network Information API says so and the user has not asked to save data. `sw/precache.ts` keeps debug pages (the voice probes) out of the app-shell cache, so a spike can stay in the tree safely. The design is in `docs/archive/offline-audio-plan.md`.

**Voice.** A voice-control spike lives under `frontend/src/lib/voice/`: `phraseTable.ts::resolveCommand` maps an exact utterance to a command, and `wakeLock.ts` holds a screen wake lock during playback. There is no recognizer wired in yet; the mic chip and `voicePref` are the groundwork.

### 13.5 The reader: one shell, two sources

`frontend/src/lib/components/LessonReader.svelte` is the reading surface: the sticky player card, the Read/Listen toggle, the player and the transcript. A lesson page and a review-session page both mount it. It exists to make divergence impossible rather than unlikely: the two had already drifted twice (a hand-rolled session transcript, then a missing Read/Listen toggle) because each page wrote its own shell. What legitimately differs enters through snippets (breadcrumb and day pager, mastery line, render button, listen actions for a lesson; nothing of that for a session), and anything not about that distinction belongs inside the component.

Behaviour is shared the same way. `reading/readingActions.svelte.ts::createReadingActions` and `reading/listenActions.svelte.ts::createListenActions` carry everything the reader *does* (create a base card, submit a drill grade, undo, mark listened, the queue count). Their only parameters are a content id and a language, because every call already keyed on one id and the backend's `/api/srs/content/{id}/…` routes resolve a lesson or a session (§12.2). The content id is a getter, read at call time, since SvelteKit reuses the component across navigations and a captured id would describe the page you just left.

Each word is a `WordSpan.svelte` rendered from a `WordToken` (§8). What the learner sees:

```bash
cd frontend/src/lib && grep -o "class:word-[a-z-]*" WordSpan.svelte | sort -u | tr '\n' ' '; echo; grep "^export function \(railPropsFor\|isUnstarted\|masterySides\)" masteryBands.ts
```

```output
class:word-blurred class:word-due class:word-overdue class:word-overdue-far class:word-selected class:word-wrapper-gloss 
export function isUnstarted(band: string | null | undefined): boolean {
export function railPropsFor(bands: {
export function masterySides(bands: {
```

- **Colour** is mastery hue from the word's progress; unknown, ignored and suspended words get fixed classes.
- **Twin rails** under a word show per-direction strength (recognition and production) as a coloured, length-proportional fill, from the backend's band vocabulary (`mastery.py::direction_band`); a never-studied card, no card and an absent band are one "not started" state, because whether a row exists is not something the learner can act on.
- **Weight** marks what is due: a due recognition direction renders bold, and `WordToken.overdue_ratio` (computed backend-side relative to stability, since a 2-day memory a week late is lost while a 6-month one is fine) steps it to 800 at one stability overdue and 900 at three.
- **Blur-as-cloze**: with the per-device "Produce" toggle on (default off, `stores/readerProductionPref.svelte.ts`), a word whose *production* direction is due blurs instead of bolding. Tapping reveals it and grades nothing; only the popover's four ratings (Again / Hard / Good / Easy) write, and they grade production through the review queue's own endpoint. With the toggle off the reader is exactly the recognition-only reader.
- **Popover actions** (`Tooltip.svelte`) create a base card or an inflection cloze, grade a due word, undo the single latest grade, and apply ignore, known and new overrides; the override set deliberately excludes lapse and restore so it never rewrites FSRS scheduling state.

### 13.6 Listen, review and review sessions

**Listen preview.** Marking content listened opens `ListenPreviewModal.svelte`, which shows the words a listen would register. It is a view over `GET /api/srs/content/{id}/listen-preview` (§8): rows ranked in one intro pool, a stated cut where the daily new-card budget ends, and an opt-in to carry one row past the cap (Anki's "Increase today's new limit"). Learning-state cards are deferred behind a single `deferred_reason`. An Ignore control on rows with a lemma calls the ignored-lemmas routes and shows a five-second Undo bar that survives Cancel. Confirmation and the pending count are scoped per lesson.

**The drill.** `/review` fetches `GET /api/srs/review-queue`, which blends due cards with a daily-capped slice of new ones (§9), and each grade is a per-direction `POST …/feedback`. Media URLs arrive in the queue payload. The grade buttons are docked at the bottom. The `?lesson=` mode reads `/api/srs/content/{id}/review-queue` instead and never advances the global session cutoff, so checking one lesson's words cannot disturb tomorrow's queue.

**Review sessions.** The home page lists them by date and creates them through `api.createReviewSession` with an idempotency key; the manual (Claude-chat) mode is `getReviewSessionDraftPrompt` then `createReviewSessionFromPaste`. A session page shows the shared reader and, if a render is in flight server-side, polls `GET …/render-status` with `setTimeout` rather than `setInterval` (a slow response must not overlap itself) and gives up after a bounded run of failures instead of polling a dead server forever. Deleting a session keeps its SRS history. A session that lost its glosses shows a shared `Banner` with a Restore glosses button (`api.reglossReviewSession`).

**Shared UI parts.** `ConfirmDialog.svelte` replaces `window.confirm()`; `Banner.svelte` is the one inline notice; popovers clamp to the nearest clipping ancestor so they cannot leave the viewport.

### 13.7 Internationalisation

All user-visible strings go through `t(key, params)` from `frontend/src/lib/i18n/i18n.svelte.ts`. The English catalog, `i18n/en.ts`, is one flat object whose keys are `<fileStem>.<purpose>`, and it covers every file under `src/lib` and `src/routes`. `t` interpolates `{name}` placeholders, picks a plural form with `Intl.PluralRules` when a `count` param is present, and falls back to English and then to the key itself.

```bash
cd frontend/src/lib/i18n && sed -n '/^export function t(/,/^}/p;/^export function registerCatalog/,/^}/p' i18n.svelte.ts
```

```output
export function registerCatalog(
  code: string,
  messages: Partial<Record<MessageKey, Message>>,
): void {
  const existing = catalogs[code];
  catalogs[code] = existing ? { ...existing, ...messages } : messages;
}
export function t(key: MessageKey, params?: Params): string {
  const message = resolve(key);
  if (message === undefined) return key;
  return format(message, params);
}
```

The locale is `$state`, so templates re-render on `setLocale`. Today only `en` is registered; the machinery is groundwork for a UI that switches into the target language, and `MessageKey` (derived from the catalog) makes a misspelled key a compile error.

### 13.8 Toolchain and the coverage gate

Bun is the package manager; Vite builds; Vitest runs unit and component tests under jsdom; Playwright runs end-to-end specs. Lint is two layers, Oxlint for `.ts`/`.js` and ESLint with `eslint-plugin-svelte` for templates, and Oxfmt formats the TypeScript.

```bash
cd frontend && grep '"fmt:check"\|"lint"\|"check"\|"test:coverage"\|"test:e2e"' package.json | cut -c1-150
```

```output
		"check": "SVELTEKIT_OUT_DIR=.svelte-kit-test svelte-kit sync && SVELTEKIT_OUT_DIR=.svelte-kit-test svelte-check --tsconfig ./tsconfig.test.json",
		"test:coverage": "SVELTEKIT_OUT_DIR=.svelte-kit-test vitest run --coverage && bun scripts/coverage-gate.ts",
		"test:e2e": "playwright test",
		"fmt:check": "oxfmt --check 'src/**/*.ts' 'src/**/*.js'",
		"lint": "bun run lint:fast && bun run lint:svelte"
```

`check` and `test` set `SVELTEKIT_OUT_DIR=.svelte-kit-test`. `svelte-kit sync` runs at the start of both, and rewriting the dev server's `.svelte-kit` made Vite reload the page the developer was looking at; the comment in `svelte.config.js` records the measurement, including that redirecting the output is necessary but not sufficient (a `server.watch.ignored` entry in `vite.config.ts` is the half that stops the reload).

**The 100% gate.** Coverage must be 100% lines, branches, functions and statements per file, enforced by `frontend/scripts/coverage-gate.ts`, not by Vitest's `thresholds:` block, which is intentionally absent. The reason is the Svelte 5 compiler: it injects template fragments (`'} created, {'`, folded ternary literals, `?? ''` defaults) that v8 reports as uncovered branches no test can reach, so a plain threshold would have to sit near 75% to absorb the noise. The gate reads `coverage/coverage-final.json`, classifies each uncovered sub-location with `isPhantom(branchType, text, synthetic, duplicateRange)`, logs the drops to `coverage/dropped-branches.json`, then asserts 100% on what is left.

```bash
cd frontend/scripts && sed -n '/^export function isPhantom/,/^): boolean {/p' coverage-gate.ts; grep -o 'branchType === "[a-z-]*"' coverage-gate.ts
```

```output
export function isPhantom(
  branchType: string,
  text: string,
  synthetic: boolean,
  duplicateRange = false,
): boolean {
branchType === "cond-expr"
branchType === "binary-expr"
branchType === "if"
```

Three rules follow from `.claude/rules/frontend-coverage-gate.md`:

- **No escape comments.** `c8 ignore` and `istanbul ignore` are not read. The answers are to write the test, refactor the dead branch out, or extend `isPhantom` with a case pinned in `frontend/tests/coverage-gate.test.ts` against real TunaTale shapes.
- **After any `svelte`, `@sveltejs/kit`, `vite-plugin-svelte` or `@vitest/coverage-v8` bump, compare the gate's "dropped N phantom branch(es)" line on the same tree before and after.** More than a 20% change either way means the compiler's output shape moved: fewer drops means the filter misses new phantoms, more means it is hiding real gaps. The drop count also grows with feature code, which is why only a same-tree comparison isolates drift. Fix the heuristic, never the threshold.
- **Do not restructure markup to dodge a phantom;** extend the filter.

`src/routes/+layout.svelte` is excluded from coverage and covered by Playwright instead. Which tier a given test belongs in is `test-tiers.md`'s question (§14); end-to-end specs under `frontend/tests/` give each Playwright worker its own backend, database and frontend port and serve a production build through `vite preview` (§14).

## 14. Testing & Quality Gates

TunaTale's tests are not only a regression net; they are how the architectural rules in the other chapters (no `import anki` at runtime, no hardcoded language logic, one sync sequence) are kept true as the code changes. One script, `test.sh`, runs the local gate; one workflow, `.github/workflows/ci.yml`, runs the authoritative one. This chapter describes what each runs, how the test infrastructure is wired (cassettes, fixtures, per-worker e2e servers, peer-sync), and why the gate is shaped the way it is. The operating rules live in `AGENTS.md` and `.claude/rules/`; this chapter summarises and links to them rather than restating them (§16.4 lists the rule files).

### 14.1 The gate and CI: one superset, one subset

`./test.sh` is the pre-commit gate. It runs three groups concurrently, each in its own subshell with buffered output, and aggregates exit codes at the end so one red group does not hide another: **backend** (ruff, the checker scripts, pytest with coverage), **frontend** (format, lint, OpenAPI type check, svelte-check, vitest with a coverage gate, Playwright e2e) and **peer-sync** (round-trips against a throwaway `anki.syncserver`). It pins `TZ=UTC` and `SYNC_ENABLED=true` so the gate does not inherit a developer's host offset or `.env`; both pins came from incidents (a UTC+5 fixture bug nobody could see from UTC-4, and an `.env` that unmounted the Anki router and reddened the OpenAPI check).

CI is authoritative and `test.sh` is a strict subset of it. Adding a check to `test.sh` obliges the same commit to add it to `ci.yml`; the reverse is not required. The CI-only checks are the clock and offset jobs, which vary the timezone, and nothing is local-only. The reasoning (job count, not job speed, drives tail latency; why a matrix at the extremes misses the one offset bug this repo has found) is in `.claude/rules/gate-and-ci.md` and `.claude/rules/testing.md` § "What a green gate means".

The jobs, parsed from the workflow:

```bash
awk '/^jobs:/{j=1;next} j && /^  [a-z0-9-]+:$/{sub(":","",$1); print $1}' .github/workflows/ci.yml
```

```output
backend
backend-hostile-tz
backend-hostile-hour
frontend
e2e
anki-gates
```

The workflow runs on pushes to `main` and on every pull request. What each job is for:

| Job | Purpose |
|---|---|
| `backend` | ruff, checkers, pytest at 100% coverage, no `--run-oracle` |
| `backend-hostile-tz` | whole suite at `Etc/GMT-3` (the UTC+2..+4 band where the known offset bug reproduces) |
| `backend-hostile-hour` | whole suite in a zone computed so local time is 04:00, the Anki rollover |
| `frontend` | format, lint, `check:api`, svelte-check, vitest and the coverage gate |
| `e2e` | Playwright against per-worker backends |
| `anki-gates` | oracle parity and peer-sync, at the 04:00 rollover, via `.github/actions/hostile-hour-tz` |

```bash
sed -n '/^on:/,/^$/p' .github/workflows/ci.yml; ls .github/actions .github/workflows
```

```output
on:
  push:
    branches: [main]
  pull_request:

.github/actions:
hostile-hour-tz
setup-ffmpeg

.github/workflows:
ci.yml
deploy.yml
```

Two details change how a red run is read. `anki-gates` runs at the rollover, so if it is red while `backend` is green, suspect product code first: only it has an oracle that can tell "TT and Anki disagree about the day" from "a fixture encodes a wall-clock assumption". For `backend-hostile-tz` and `-hour` the guidance is the opposite: suspect the fixture. And CI installs lean, which is the next section.

### 14.2 Lean CI installs and the `dev` group

Every backend job runs `uv sync --no-default-groups --group dev` with `UV_NO_SYNC: "1"` at job level (four jobs; `e2e` uses a similar variant that drops the language groups), so classla, stanza, torch and transformers are absent. The job-level variable matters: a bare `uv run` inside the suite would otherwise re-sync to `[tool.uv] default-groups` mid-run, and an install-step flag alone proves nothing about the environment a test ran in. The consequence for test authors is that a test may import only what the `dev` group declares. A package that arrives transitively through a language group (`yaml` via transformers is the usual one) passes locally and fails all four backend jobs at collection with `ModuleNotFoundError`. Declare it in `dev`; never widen CI's groups.

The same constraint is why this document's own probes avoid importing modules that need torch at import time.

```bash
grep -c "UV_NO_SYNC: \"1\"" .github/workflows/ci.yml; grep "run: uv sync" .github/workflows/ci.yml | sort | uniq -c
```

```output
6
      4         run: uv sync --no-default-groups --group dev
      1         run: uv sync --no-group slovene --no-group norwegian --no-group alignment
```

To reproduce CI's environment before pushing, `.claude/rules/testing.md` gives the two-line recipe with `UV_PROJECT_ENVIRONMENT=/tmp/ci-venv`.

### 14.3 The checker scripts

Between ruff and pytest, the backend group runs a series of small AST- or file-scanning scripts in `backend/scripts/`. Each one turns an architectural claim made elsewhere in this tour into a failing build. They are listed here as `test.sh` invokes them:

```bash
grep -o 'log_step backend "[^"]*" uv run python scripts/[a-z_]*\.py' test.sh | sed 's/log_step backend //; s/ uv run python / -> /'
```

```output
"Build NST lexicon" -> scripts/build_nst_lexicon.py
"Mock boundary check" -> scripts/check_mock_boundaries.py
"Language literal check" -> scripts/check_language_literals.py
"Date today check" -> scripts/check_date_today.py
"Singular database_url check" -> scripts/check_singular_database_url.py
"Plugin import check" -> scripts/check_plugin_imports.py
"OpenAPI snapshot check" -> scripts/check_openapi_snapshot.py
"Prod env profile check" -> scripts/check_prod_env.py
"Main styling check" -> scripts/check_main_styling.py
"LLM call sites check" -> scripts/check_llm_call_sites.py
"Media filename case check" -> scripts/check_media_filename_case.py
"Content surface parity check" -> scripts/check_content_surface_parity.py
```

What each guards:

| Checker | Claim it enforces |
|---|---|
| `check_mock_boundaries.py` | tests mock only at process/network boundaries (§14.5) |
| `check_language_literals.py` | no `"sl"`/`"no"`, `Slovene`, `classla`, `*-Neural` literals in `app/**` outside allowlisted plugin modules (§3) |
| `check_date_today.py` | no `date.today()` composed into Anki-day arithmetic (§9) |
| `check_singular_database_url.py` | nothing reads the singular `settings.database_url` directly; on a multi-language install it names one fixed language, so the failure is silent. Callers go through `resolve_language_context` |
| `check_plugin_imports.py` | core never imports `app.plugins.languages.*` directly |
| `check_openapi_snapshot.py` | `api-schema.json` is fresh and every 2xx JSON route declares `response_model=` |
| `check_prod_env.py` | the offline form of the production boot guards (§15) |
| `check_main_styling.py` | every `+page.svelte`/`+layout.svelte` that renders `<main>` styles it (a review reader once shipped at full viewport width) |
| `check_llm_call_sites.py` | every product `.complete()` call passes `call_site=`, so Groq usage-ledger spend is attributed (§5) |
| `check_media_filename_case.py` | media rows match on-disk filename case (APFS vs ext4) |
| `check_content_surface_parity.py` | lessons and review sessions keep the same verbs, held as an explicit verb map rather than a router-vs-router diff |

Two design decisions shaped all of them. First, there are no grandfather ledgers. Earlier versions carried shrink-only files of known violations; they were deleted once empty, because "empty ledger" and "no additions, ever" behave identically and the ledger machinery was unexercised weight. The remaining `backend/tests/*allowlist.txt` files are plain allowlists, and additions to the mock allowlist need the user's sign-off. Second, the shared helpers live in one module so the checkers cannot drift:

```bash
grep "^def \|^class " backend/scripts/_checker_lib.py
```

```output
def _call_fn_name(call_node: ast.Call) -> str | None:
def _relative_path(filepath: Path) -> str:
def _find_inline_comment(s: str) -> int | None:
def load_allowlist(path: Path) -> list[str]:
def matches_allowlist(target: str, patterns: list[str]) -> bool:
def collect_all_hits(
```

`check_date_today.py` documents the other recurring failure mode of checkers: a tier that would have needed a ledger that never drains was scoped and deliberately not shipped. Prefer a narrow checker with zero tolerance to a broad one with a ledger.

### 14.4 Backend test infrastructure

`backend/tests/conftest.py` carries the shared fixtures. Probing its top-level functions shows the shape of the infrastructure: the day anchors, settings isolation, the language fixtures, synthetic Anki collections, the pytest options, and the sociable-sync fixtures.

```bash
grep "^def \|^async def " backend/tests/conftest.py | grep -v "^def _"
```

```output
def anki_day_anchor(today: date) -> datetime:
def anki_prev_day_anchor(today: date) -> datetime:
def pytest_runtest_setup(item):
def pytest_runtest_teardown(item, nextitem):
def language():
def language_no():
def srs_db():
def make_card_record(
def make_note_record(
def build_minimal_anki_db(
def build_norwegian_anki_db(
def fake_anki_db(tmp_path):
def fake_anki_db_modern(tmp_path):
def build_slovene_pairs_anki_db(tmp_path: Path) -> Path:
def fake_anki_db_slovene_pairs(tmp_path):
def seed_direction(
def pytest_addoption(parser: pytest.Parser) -> None:
def pytest_configure(config: pytest.Config) -> None:
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
def llm_mode(request: pytest.FixtureRequest) -> str:
def api_app_state():
async def cassette_llm(request: pytest.FixtureRequest, llm_mode: str):
def sociable_tt_collection(monkeypatch):
def fake_driver(monkeypatch):
```

The ones to know:

- **`language_no` / `language`** — Norwegian and Slovene `Language` objects. New tests use Norwegian, because the `no` plugin registers facets `sl` does not (lemma plausibility, breakdown spans, alignment, a syllabifier), so a Slovene test silently skips those paths. About 100 older modules stay on `language`; they are not retro-migrated.
- **`srs_db`** — an in-memory SRS database (`sqlite:///:memory:`), so SRS tests need no cleanup.
- **`fake_anki_db`, `fake_anki_db_modern`, `fake_anki_db_slovene_pairs`** — small on-disk Anki collections built by `build_minimal_anki_db`, `build_norwegian_anki_db` and `build_slovene_pairs_anki_db`. Tests never open a real `collection.anki2` (the safety rule in `.claude/rules/anki-safety-core.md`).
- **`seed_direction`, `make_card_record`, `make_note_record`** — row builders.
- **`api_app_state`** — populates `app.state` the way the lifespan does, for `ASGITransport` API tests.
- **`cassette_llm`** and **`llm_mode`** — the LLM replay fixtures (§14.6).
- **`sociable_tt_collection`** and **`fake_driver`** — the sociable-sync pair (§14.5).
- **Autouse isolation** — `_settings_overrides` points every path setting at `tmp_path` per test; `_autoclose_sqlite_connections` closes connections a test forgot.

The marker options are the second half of the infrastructure. Heavy or environment-dependent tests are skipped unless asked for:

```bash
sed -n '/^def pytest_addoption/,/^def pytest_configure/p' backend/tests/conftest.py | grep -o '"--[a-z-]*"'
```

```output
"--llm-mode"
"--run-oracle"
"--run-classla"
"--run-stanza"
"--run-peer-sync"
```

`--run-oracle` (drives Anki's real scheduler in a `uv run --with anki` subprocess, §9), `--run-peer-sync`, `--run-classla` and `--run-stanza` gate their `@pytest.mark` equivalents. `test.sh` passes `--run-oracle -n 6` to pytest; peer-sync is its own group. `-n 6` was measured against the other concurrent groups rather than guessed.

Helpers that are not fixtures live in `backend/tests/_helpers/`: `anki_db.py`, the `sync_server.py` session fixture, `localtz.py`, and `lemmatizer.py::StubLemmatizer` (stub the lemmatizer instead of loading stanza).

```bash
ls backend/tests/_helpers
```

```output
__init__.py
__pycache__
anki_db.py
anki_sync_create_new.py
anki_sync_pull.py
anki_sync_push.py
api_app_state.py
lemmatizer.py
llm_rate_limit_shape.py
localtz.py
protobuf.py
srs_image_shape.py
srs_item_shape.py
sync_server.py
```

#### Coverage and pragmas

`pyproject.toml` sets `fail_under = 100`, and `addopts` always includes `--cov=app`. A `# pragma: no cover` lowers the gate rather than passing it, so the policy in `.claude/rules/testing.md` § "Pragma Discipline" applies: write the test first, accept a pragma only for the `__main__` guard and for branches whose comment says why they are unreachable, and reject justifications that merely describe the test scenario ("always true in tests"). Coverage measures slightly different sets locally and in CI (the oracle tests contribute locally; CI's `backend` job omits them, which makes CI's the stricter claim).

#### Day fixtures must declare their zone

The Anki day rolls over at 04:00 local, so which timestamps share a col-day depends on the reader's zone. A fixture asserting a day fact therefore declares its zone with `tests/_helpers/localtz.py` (`local_timezone`, `timezone_with_local_hour`), pinned at the narrowest scope that fails. Over-pinning a whole module blinds the hostile jobs to the bugs they exist for. `backend-hostile-hour` samples one offset per run, so a history of green runs is not evidence a fixture is sound; the rule (and a by-hand sweep over `Etc/GMT-0..14`) is documented in `.claude/rules/testing.md`.

### 14.5 Mock boundaries and sociable tests

The most consequential rule is also the one the checker enforces: mock only at process and network boundaries (the Anki driver subprocess, the Azure and Gemini TTS HTTP APIs, Pixabay/Forvo, Groq, the macOS keychain), never `patch("app.…")` an internal function. The rule exists because of a regression class (`b0a4b8a`): two halves of a flow each tested against a fake of the other both go green while the bug lives in the gap. Seven regressions got through a 100%-coverage gate that way, which is why coverage alone is not the gate.

```bash
grep -v "^#" backend/tests/mock_allowlist.txt | grep -v "^$" | awk '{print $1}' | head -20
```

```output
app.plugins.anki_sync.sync_orchestrator.subprocess.run
app.plugins.anki_sync.sync_orchestrator._run_driver
app.plugins.anki_sync.sync_orchestrator._keychain_password
app.cards.media.pixabay.*
app.cards.media.forvo._make_client
app.api.anki.fetch_card_media
app.*.settings.*
app.plugins.anki_sync.sync._MEDIA_DIR
app.api.srs._MEDIA_DIR
app.cards.media.vocab_media._MEDIA_DIR
app.audio.cloze_tts._MEDIA_DIR
app.cards.media.normalize._apply_normalization
app.cards.media.normalize._measure_loudness
app.generation.lemma_annotation.get_lemmatizer
app.main.get_lemmatizer
```

`check_mock_boundaries.py` AST-scans `backend/tests/**` for `patch("app.…")` and `monkeypatch.setattr("app.…", …)` and fails on anything not matched by an fnmatch glob in `mock_allowlist.txt`. Its documented blind spots are `patch.object(obj, "name")` and the two-argument `monkeypatch.setattr(obj, …)`; do not use them to smuggle an internal mock past the checker.

When the checker fails on a new test, the fix is to test through the seam. The canonical pattern is `TestSociableSync` in `test_anki_sync_orchestrator.py`: the real `peer_sync` → `main` → `run_full_sync` pipeline runs against a real on-disk `SyntheticCollection`, with only `_run_driver` replaced by the `fake_driver` fixture that returns canned responses and records an op log:

```bash
sed -n '/^def fake_driver/,/^def /p' backend/tests/conftest.py | head -30
```

```output
def fake_driver(monkeypatch):
    """Replace ``_run_driver`` with canned responses so auth/sync legs complete.

    Mirrors :func:`_run_driver`'s real signature exactly
    ``(command: dict, timeout: int = 120) -> dict``.

    Yields the op log (a list of commands received) for assertion use.
    """
    import app.plugins.anki_sync.sync_orchestrator as so

    op_log: list[dict] = []

    def _fake(command: dict, timeout: int = 120) -> dict:
        op_log.append(command)
        op = command.get("op", "")
        if op == "login":
            return SOCIABLE_AUTH_RESPONSE
        if op == "sync":
            return SOCIABLE_NORMAL_SYNC
        if op == "media_pending":
            return {"pending": 0}
        return {"error": f"unknown op: {op}"}

    monkeypatch.setattr(so, "_run_driver", _fake)
    return op_log


@pytest.fixture(autouse=True)
def _clear_pixabay_search_cache():
```

Assertions are outcomes (rows in the collection file, the leg sequence in the op log, file bytes), not mock-call shapes. A sociable test earns its place through a **sabotage drill**: disable the phase it guards (say, comment out `sync_create_new` in `run_full_sync`), watch the test fail, revert, watch it pass. A test that cannot be shown to catch its target bug is decoration. `.claude/rules/anki-oracle-harness.md` extends this to the oracle parity tests.

### 14.6 The LLM cassette system

LLM tests never touch the network. `backend/app/llm/cassette.py::CassetteLLMClient` replays recorded prompt/response pairs from JSON files in `backend/tests/cassettes/`, indexed by SHA256 of the prompt. The `cassette_llm` fixture derives the file name from the test (`{ClassName}__{test_name}.json`) and picks behaviour from `--llm-mode`:

| Mode | Behaviour |
|---|---|
| `mock` (default, CI) | replay; skip the test if its cassette is missing |
| `record` | call Groq, save the result (needs `GROQ_API_KEY`) |
| `patch` | replay known prompts, record new ones |
| `live` | call Groq, save nothing |

```bash
ls backend/tests/cassettes; sed -n '/^async def cassette_llm/,/^    if llm_mode == "patch"/p' backend/tests/conftest.py | head -24
```

```output
TestPlannerLLM__test_norwegian_context_regression.json
TestPlannerLLM__test_two_turn_scenario.json
e2e.json
async def cassette_llm(request: pytest.FixtureRequest, llm_mode: str):
    """Yield a CassetteLLMClient configured for the current --llm-mode."""
    from app.llm.cassette import CassetteLLMClient

    cls_name = request.node.cls.__name__ if request.node.cls else "_noclass"
    test_name = request.node.name
    cassette_path = _CASSETTES_DIR / f"{cls_name}__{test_name}.json"

    if llm_mode == "mock":
        if not cassette_path.exists():
            pytest.skip(f"No cassette at {cassette_path} — run with --llm-mode=record first.")
        client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
        yield client
        return

    if llm_mode == "patch" and not cassette_path.exists():
```

Many suites do not need a cassette at all: a hand-written stub class with the `complete()` signature (`StubLLM` in `test_planner.py`) passes the boundary check because it is not a patch. Cassettes are kept for tests that exercise real prompt text against the real client path. `respx` intercepts HTTP at the httpx transport layer, both for the `LLMClient` retry and 429-backoff tests and for the Azure and Gemini TTS services (`test_azure_tts.py`, `test_gemini_tts.py`).

The e2e backends use the same system through `LLM_MODE=mock`, with one addition. A cassette miss is already an exception in the backend, but a fail-soft caller (the gloss pass catches it by design) swallowed it, and for a day every e2e run missed a gloss entry, shipped lessons with no glosses and went green. Each e2e backend now appends every miss to its own `LLM_CASSETTE_MISS_LOG`, and global teardown fails the run if any file has a line (`frontend/tests/cassette-misses.ts::assertNoCassetteMisses`). In e2e a miss is never a legitimate runtime condition, only a fixture gap.

### 14.7 Frontend tests and the coverage gate

Frontend unit tests are vitest under jsdom with `bun run test:coverage`, followed by `frontend/scripts/coverage-gate.ts`. The custom gate replaces vitest's built-in thresholds because V8 reports Svelte 5 compiler-injected branches (ternary literal results, template-fragment short-circuits) as uncovered even though they are not user-source branches; vitest's threshold gate would have forced the bar down to the worst file's phantom density. The script filters those and asserts 100% per file per metric, writing every drop to `coverage/dropped-branches.json` for audit. The heuristic and its limits are in `.claude/rules/frontend-coverage-gate.md`.

```bash
cd frontend && grep -o '"\(test[a-z:]*\|check[a-z:]*\|lint[a-z:]*\|fmt[a-z:]*\|gen:api\)":' package.json | tr '\n' ' '; echo
```

```output
"check": "check:watch": "gen:api": "check:api": "test": "test:coverage": "test:watch": "fmt": "fmt:check": "lint:fast": "lint:svelte": "lint": 
```

The API types the frontend consumes are generated from the committed OpenAPI snapshot (§12), and `bun run check:api` fails when `src/lib/api-types.d.ts` is stale. The fix commands are `uv run python scripts/dump_openapi.py` (backend) and `bun run gen:api` (frontend).

### 14.8 End-to-end tests

Playwright specs are under `frontend/tests/*.spec.ts`. Each Playwright worker gets its own stack: a uvicorn backend on `8001 + 2*i`, a `vite preview` of a production build on `5174 + i`, and its own SQLite databases (`tunatale-test-<i>.db`, `tunatale-test-no-<i>.db`) with its own cassette-miss log. All workers share one auth store, and `global-setup.ts` creates the E2E account through the real auth CLI, signs in through the real login page and saves the cookie for every spec. The port formula is deliberately duplicated in `playwright.config.ts`, `fixtures.ts::PORTS` and `helpers.ts::BACKEND`, with comments warning that changing one without the others sends a worker to a backend that was never started.

```bash
ls frontend/tests/*.spec.ts | xargs -n1 basename | tr '\n' ' '; echo; grep -n "WORKER_COUNT =\|retries:\|command: .*preview\|const port = \|const frontendPort" frontend/playwright.config.ts | sed 's/^[0-9]*://' | cut -c1-150
```

```output
admin-srs.spec.ts auth-login.spec.ts card-image.spec.ts cors-lockdown.spec.ts language-switch.spec.ts lesson-header-layout.spec.ts lesson-navigation.spec.ts lesson-source.spec.ts listen-preview-layout.spec.ts offline-audio.spec.ts pipeline-card.spec.ts planner-chat.spec.ts review-again-rating.spec.ts review-ahead.spec.ts review-flow.spec.ts review-grade-buttons.spec.ts review-pressure.spec.ts smoke.spec.ts tooltip-popover.spec.ts transcript-layout.spec.ts transcript-overflow.spec.ts transcript-rails.spec.ts 
export const WORKER_COUNT = Number(process.env.E2E_WORKERS ?? 2);
	const port = 8001 + 2 * i;
	const frontendPort = 5174 + i;
	const port = 5174 + i;
		command: `SVELTEKIT_OUT_DIR=.svelte-kit-e2e bun run preview -- --port ${port} --strictPort`,
	// retries: 0 EVERYWHERE, deliberately. This was `process.env.CI ? 2 : 0`,
	retries: 0,
		// Was 'on-first-retry', which with retries:0 would capture NOTHING — the
```

Several choices carry history. Database cleanup runs at module scope in the config before any server starts, behind a `TT_E2E_DBS_CLEANED` guard, because workers re-evaluate the config and an unguarded copy deleted live databases mid-run; a cleanup inside a `webServer` command raced the shared auth DB. The suite serves a real `vite build` because the service-worker and offline specs need a production bundle. Worker count is `E2E_WORKERS` (default 2); a measured sweep on a 10-core box found 4 fastest standalone and 6+ slower from oversubscription, but the default stays 2 because contention with the gate's other concurrent groups was not measured. Retries are 0 on purpose: a spec that passes only on retry is a flake chosen not to be seen. CI uploads the HTML report and traces on failure, and `test.sh` preserves failing e2e artifacts via `backend/scripts/preserve_e2e_artifacts.py`.

What belongs in Playwright is governed by `.claude/rules/test-tiers.md`. In short: core user journeys (`smoke`, `review-flow`, `planner-chat`) earn a slot by the path they walk; everything else must be a **seam** where two systems must agree, which the rule reduces to one question, "is the asserted value computed by the browser or by the app?". Layout geometry, CORS enforcement and the service worker are browser-computed and stay in e2e; `classList`, `textContent` and store values are app-computed and belong in vitest. Regression tests land at the cheapest tier that can catch the bug, and the rule's sabotage-drill criterion says when a test comes down.

### 14.9 Peer-sync tests

`test_anki_peer_sync_selfhost.py` exercises the full AnkiWeb-compatible round trip (§10) against a throwaway `anki.syncserver` that the session fixture `tests/_helpers/sync_server.py::selfhost_sync_server` starts on an ephemeral port. Under `--run-peer-sync` an unstartable server fails rather than skips, so a round-trip regression is caught before pushing. In `test.sh` it is its own third group; in CI it shares the `anki-gates` job with the oracle tests.

```bash
grep 'log_step peer_sync' test.sh | sed 's/^ *//'; grep "^def " backend/tests/_helpers/sync_server.py
```

```output
log_step peer_sync "Peer-sync round-trip" uv run pytest tests/test_anki_peer_sync_selfhost.py --run-peer-sync --no-cov
def find_free_port() -> int:
def server_cmd() -> list[str]:
def server_env(port: int, base: Path) -> dict[str, str]:
def ping(endpoint: str) -> bool:
def selfhost_sync_server(tmp_path_factory: pytest.TempPathFactory) -> Any:  # noqa: ANN401
```

### 14.10 Reading a gate result

The gate script's own failure modes are as consequential as the tests', and the instructions reflect incidents. Run it by absolute path with the gate as the last statement, redirect to a file, and treat the log as the only evidence; piping it (a hook denies this) or appending an `echo $?` replaces its exit status, and both produced green reports on runs whose log ended `=== FAILED ===`. In the log, require `=== All checks passed ===`, 100.00% backend coverage, and a ruff `N files already formatted` count no lower than the previous run's (a drop means discovery broke). `.git/tt-test-history.log` records every step with exit code, timing, load and tree id, which separates a same-tree flake from a fix. The hooks around the gate (commit gate, pipe guard, history log) are described in `.claude/rules/gate-and-ci.md` § Hooks.

```bash
sed -n '/^log_step()/,/^}/p' test.sh | grep -v "^ *#" | head -16
```

```output
log_step() {
  local group="$1" name="$2"
  shift 2
  echo "=== $name ==="
  local t0="$EPOCHREALTIME" rc elapsed load
  "$@" && rc=0 || rc=$?
  elapsed=$(awk -v a="$t0" -v b="$EPOCHREALTIME" 'BEGIN { printf "%.1f", b - a }')
  load=$(uptime | sed -E 's/.*load averages?: *//' | awk '{print $1}' | tr -d ',')
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$group" "$name" "$rc" "$elapsed" "${load:-?}" "${tt_tree_id:-?}" \
    >>"$tt_test_history"
  return "$rc"
}
```

Finally, one methodology rule from `.claude/rules/tdd.md` bears on every measurement in this chapter: when a probe disagrees with a design, run a control before reporting a finding. A clean negative (zero results, `null`, an empty list) looks the same for "absent" and "wrong query"; only a known-good control tells them apart.

## 15. Deployment & Operations

TunaTale runs in production as two containers behind Caddy on a single small GCP VM, and the same code also runs on the author's laptop as a dev server and as a separate "live" instance. This chapter explains the shape of that deployment and the reasoning behind its guard rails: what is built, how a chosen commit reaches the box and how it rolls back, how data moves between machines, and how it is backed up and watched. It is deliberately a map, not a runbook. The step-by-step procedures (provisioning, DNS, drills, exact commands) live in [`docs/deployment.md`](deployment.md) and are linked by section rather than repeated here. The settings these mechanisms read, and the boot guards that refuse a bad profile, are in §2.

### 15.1 The deployed shape

Production is one e2-micro VM (0.25 vCPU baseline, ~1 GB RAM, 2 GiB swap, a 30 GB boot disk) running Docker Compose. Admin reaches it over Tailscale; port 22 never faces the internet, and Google's IAP tunnel is the break-glass path. Provisioning, hardening and the reboot drill are in `docs/deployment.md` § "Provisioning the host".

Three compose services, all from one `docker-compose.yml`:

| Service | Image | Role |
|---|---|---|
| `init` | the API image, different entrypoint | Run-once: creates `/data/...` directories, pre-warms the `anki` package into the uv cache on the data volume, `chown`s the volume to `appuser`. |
| `api` | `ghcr.io/<owner>/tunatale-api:<sha>` | uvicorn serving `app.main:app` on :8000, `restart: unless-stopped`, gated on `init` completing. |
| `web` | `ghcr.io/<owner>/tunatale-web:<sha>` | Caddy serving the built SPA and reverse-proxying `/api/*` to `api`. |

All mutable state lives on one named volume mounted at `/data`: the per-language SQLite databases, `auth.db`, `users/`, `media/`, `output/audio/`, and everything that defaults to `tt_home()` (§2.1), because the container sets `HOME=/data`. A second pair of volumes (`caddy_data`, `caddy_config`) holds issued certificates, so a redeploy does not re-request one (Let's Encrypt allows five duplicates a week).

```bash
grep "^FROM\|^CMD\|^RUN /app\|^RUN uv sync\|^USER" Dockerfile
```

```output
FROM oven/bun:1-alpine AS frontend-build
FROM ghcr.io/astral-sh/uv:python3.14-bookworm AS api
RUN uv sync --frozen --no-dev --no-group slovene --no-group norwegian --no-group alignment
RUN /app/.venv/bin/python -m app.build_data
USER appuser
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
FROM caddy:2-alpine AS web
```

Three design decisions in these files are worth knowing:

- **One compose file for dev and the box.** A prod-only copy would drift from the dev copy silently, and the drift would surface as a production-only bug. `image:` is what the box pulls; `build:` is what a laptop uses. `TT_TAG` has no default (`${TT_TAG:?...}`, not `:-latest`), so a mistyped deploy fails closed instead of shipping whatever "latest" happens to be.
- **The API image is lean.** `uv sync --no-dev --no-group slovene --no-group norwegian --no-group alignment` leaves out PyTorch, Stanza and Classla. Prod serves lemmas from a shipped table (`LEMMATIZER_TYPE=table`, §8), and `python -m app.build_data` runs at image build time so the lemma tables' SQLite indexes and the Norwegian NST lexicon exist before the container starts. That step exists because the lexicon was once missing from every image, and its absence silently re-enabled compound over-splitting (§3); `app.build_data --check` is the "a missing artifact must stop a start" form that `switch.sh` runs before the laptop instance listens.
- **The healthcheck has a 90 s `start_period`**, measured, not guessed. On the e2-micro the app binds in about 18 s from a warm deploy but took about 70 s after a full VM reboot, when everything starts at once. A shorter window made a working box report `unhealthy`, which is the signal a deploy script or monitor acts on. The check itself is `curl -sf /api/health` (§12.8).

`web` is intentionally *not* pinned to `linux/amd64` in compose: Caddy segfaults under the Mac's QEMU amd64 emulation. The amd64 guarantee for shipped artifacts belongs to the build workflow (§15.3), not the compose file.

### 15.2 Caddy: TLS, headers and the proxy contract

`Caddyfile` takes its site address from `SITE_ADDRESS`, set in a `web.env` file on the box (not in `.env`, which `deploy.sh` rewrites on every deploy). A domain there makes Caddy obtain and renew a Let's Encrypt certificate and redirect :80 to HTTPS; unset (every laptop) it falls back to plain `:80`.

```bash
grep -o '^\s*[A-Z][A-Za-z-]* "\|handle [^ ]*\|reverse_proxy [^ ]*\|max_size [^ ]*\|try_files .*' Caddyfile
```

```output
max_size 12MB
		Strict-Transport-Security "
		Content-Security-Policy "
		X-Content-Type-Options "
		Referrer-Policy "
		Permissions-Policy "
handle /api/*
reverse_proxy api:8000
handle {
try_files {path} /index.html
```

The contract between Caddy and the backend has four parts:

1. **`/api/*` goes to `api:8000`; everything else is the static SPA** with a `try_files {path} /index.html` fallback (SvelteKit's adapter-static, §13). Same-origin serving is why production needs no cross-origin CORS entry (§2.9).
2. **Client IP.** Caddy appends the peer it actually saw to `X-Forwarded-For`. The backend reads the *rightmost* entry (`app.auth.throttle.client_ip`), and `TRUSTED_PROXY_HEADER=X-Forwarded-For` must be set or login throttling collapses every caller into one bucket (§2.6). The prod guard refuses to boot without it.
3. **Upload cap.** `request_body max_size 12MB` sits just above the API's own 10 MB image-upload limit, so a legitimate upload still reaches the API's more informative error rather than a bare proxy 413.
4. **Headers.** HSTS (one year, this host only; `includeSubDomains` and `preload` are hard to undo), `X-Content-Type-Options`, a strict `Referrer-Policy`, a `Permissions-Policy` that leaves the microphone available to this origin only, and a Content-Security-Policy whose value is the origin fence: `connect-src 'self'` stops an injection from shipping data anywhere and `frame-ancestors` stops framing. `'unsafe-inline'` scripts stay because SvelteKit's bootstrap is inline and its hash changes on every build. The single cross-origin allowance is `img-src https://cdn.pixabay.com` for the image picker's thumbnails; "nothing cross-origin" was assumed without opening the picker and shipped it broken.

### 15.3 Shipping an image and rolling back

Production runs a **tagged image a human chose**, never `git pull` of `main`. The commit gate is local and nothing on the box re-runs `./test.sh`, so an image is not evidence the code is good; CI is (§14). The pipeline therefore has two deliberately separate steps.

**Build**: `.github/workflows/deploy.yml` (workflow name `deploy-images`) runs only on manual `workflow_dispatch` or a `v*` tag push, never on a push to `main`. Shipping is a decision, and the dispatch is where it is recorded. It builds the `api` and `web` targets, tags both with the **full commit SHA** actually checked out (not `github.sha`, which differs when the dispatch names another ref), pushes to GHCR, and then asserts the pushed architecture. Three guards in it each have a failure behind them:

- A **short SHA is refused up front**: `actions/checkout` treats it as a branch name and dies three retries later with a bare "git failed with exit code 1".
- `platforms: linux/amd64` is explicit rather than inherited from the runner, so a re-dispatch from an arm runner cannot ship an image the host cannot execute.
- The architecture assertion reads the *image config* (`.Image`), handling both the single-platform object and a platform map. Earlier versions inspected the manifest, found no architecture there, and reported "unknown" for a perfectly good image.

```bash
grep "workflow_dispatch\|tags: \[\|name: .*\(short\|Resolve\|Build\|must be\)" .github/workflows/deploy.yml
```

```output
  workflow_dispatch:
    tags: ["v*"]
      - name: Refuse a short commit SHA
      - name: Resolve the commit being shipped
      - name: Build and push the API image
      - name: Build and push the web image
      - name: The pushed images must be linux/amd64
```

**Deploy**: `./deploy.sh <full-sha>` runs from the laptop. The box holds a compose file and a tag, never a checkout.

```bash
awk 'NR>1 && /^#/{print; next} NR>1{exit}' deploy.sh | head -11
```

```output
# Ship a built image to the box, or roll back to an earlier one.
#
#   ./deploy.sh <commit-sha>     deploy that SHA
#   ./deploy.sh --current        what is running right now
#   ./deploy.sh --history        what has been deployed, newest first
#
# A rollback is not a special mode: it is `./deploy.sh <older-sha>`. That is
# deliberate — a recovery path that only runs during a recovery is a path
# nobody has tested. Every deploy exercises it.
#
# The images come from .github/workflows/deploy.yml, which is manual. This
```

What the script does, in order, and what it refuses:

1. Requires a full 40-character SHA, so "what is running?" stays answerable.
2. Preflights SSH and, on failure, explains the OS Login username mismatch instead of printing `Permission denied (publickey)`.
3. Copies `docker-compose.yml`, checks that `backend/.env` already exists on the box (secrets are never shipped from the repo), writes `TT_TAG=<sha>` into `.env`, pulls, and runs `docker compose up -d --remove-orphans`.
4. Waits on **one** container id, resolved once, polling Docker's health state and printing each state change. `ps -q` can return two ids during a recreate, and a wait over two ids never equals "healthy". `unhealthy` must persist for three polls, because Docker reports it transiently while a restarting container is mid-restart.
5. Appends `<utc timestamp> <sha> from=<previous>` to `deploy-history.log` **only after health passes**, so the log records what actually ran. The "previous" tag comes from that log, not from `.env`: after a failed deploy `.env` holds the failed tag, and a rollback hint naming it would send you back to the thing that just broke.
6. Prunes superseded images, keeping the tag just deployed and the last healthy one. It never fails the deploy: a failed prune costs disk, not uptime.

**A rollback is not a special mode; it is `./deploy.sh <older-sha>`.** A recovery path that only runs during a recovery is a path nobody has tested, so every deploy exercises it. The pruning rule is a small pure function, and it is runnable against a fake image list:

```bash
bash -c "$(sed -n '/^stale_images()/,/^}/p' deploy.sh)
A=$(printf 'a%.0s' {1..40}); B=$(printf 'b%.0s' {1..40}); C=$(printf 'c%.0s' {1..40})
printf '%s\n' ghcr.io/wdhaines/tunatale-api:\$A ghcr.io/wdhaines/tunatale-api:\$B \
  ghcr.io/wdhaines/tunatale-web:\$C caddy:2-alpine ghcr.io/other/tunatale-api:\$C \
  | stale_images wdhaines \$A \$B | cut -c1-48"
```

```output
ghcr.io/wdhaines/tunatale-web:cccccccccccccccccc
```

Only `ghcr.io/<owner>/tunatale-{api,web}:<40 hex>` images ever match; Caddy's own image and other owners' images are never candidates. Left alone, old images were the box's only unbounded disk growth that was not user data (about 1 GB a week).

**Image rollback is not schema rollback.** Starting a build runs the SRS migrations (§9) against the existing volume, and the schema only moves forward, so rolling the image back after a schema-advancing deploy leaves a newer schema under older code, a worse failure than the one being escaped. Two mechanisms close the gap: `migrate` writes `{migration_backup_dir}/{stem}.pre-v{N}.db` before the first pending migration (never rotated, first snapshot of a version wins, and a failed snapshot *aborts* the migration, the opposite of the rolling backup that swallows errors), and it raises `SchemaTooNewError` when a database is ahead of the build. `backend/scripts/check_schema_compat.py` is the same refusal moved earlier, so a bad rollback declines before swapping rather than crash-looping afterwards; it reads `PRAGMA user_version` straight off the files. Which migrations are reversible, and the restore steps, are in `docs/deployment.md` § "Schema rollback".

### 15.4 The prod profile and the env template

The box's secrets and settings live in `backend/.env`, created by hand from `backend/.env.prod.example` and never shipped by `deploy.sh`. The boot guard that rejects a wrong profile is §2.8; what matters operationally is that **the template itself is tested**. `./test.sh` runs `backend/scripts/check_prod_env.py` against the committed example, so the file a deployment copies is always one that boots. The same checker accepts a path, which is how a real env file is validated before it goes to the box:

```bash
cd backend && uv run python scripts/check_prod_env.py && echo "template is a valid prod profile"
grep -v '^#' .env.prod.example | grep = | cut -d= -f1 | tr '\n' ' ' | fold -s -w 100; echo
```

```output
template is a valid prod profile
TT_ENV LLM_MODE GROQ_API_KEY AUTH_ENABLED SESSION_SECRET TRUSTED_PROXY_HEADER CORS_ORIGINS 
DATABASE_URLS AUTH_DATABASE_URL MEDIA_DIR AUDIO_DIR TARGET_LANGUAGE TZ LEMMATIZER_TYPE 
AZURE_SPEECH_KEY AZURE_SPEECH_REGION PIXABAY_API_KEY FORVO_ENABLED SYNC_ENABLED 
```

The compose file pins the values that must differ from the template's (`DATABASE_URLS`, `AUTH_DATABASE_URL`, `MEDIA_DIR`, `AUDIO_DIR`, `LEMMATIZER_TYPE`, `HOME=/data`) so they cannot be forgotten in `.env`. `tests/test_compose_profile.py::test_every_language_with_a_deck_has_a_prod_database` derives the expected `DATABASE_URLS` set from the language registry (§3), so adding a language without a prod database fails a test. `check_singular_database_url.py` backs this up from the other side: callers may not read the single-language `database_url` directly, because on a multi-language install it names one fixed language and a caller filtering by another matches nothing and reports success; they go through `app.languages.resolve_db_path`.

### 15.5 Moving data between machines

`./data-transfer.sh` moves TunaTale's state between the laptop and the box in either direction. It is a **handover, not a copy**: afterwards exactly one side syncs with AnkiWeb (the destination), because two TunaTales pushing to one AnkiWeb account from different states is how scheduling data gets overwritten. `--apply` rewrites `SYNC_ENABLED` on both sides to make that true.

```bash
awk 'NR>1 && /^#/{print; next} NR>1{exit}' data-transfer.sh | head -7; sed -n '/^SQLITE=(/,/^)/p' data-transfer.sh | sed 's/^ *"//; s/|.*//'
```

```output
# Move TunaTale's state between this Mac (dev) and the production box, either way.
#
#   ./data-transfer.sh status            compare dev and prod now, change nothing
#   ./data-transfer.sh to-prod           dry run: show the plan and the diff
#   ./data-transfer.sh to-prod --apply   dev -> prod, then hand AnkiWeb sync to prod
#   ./data-transfer.sh to-dev  --apply   prod -> dev, then hand AnkiWeb sync to dev
#
SQLITE=(
tunatale_no.db
tunatale_sl.db
tunatale_tl.db
tunatale_ceb.db
tt_collection.anki2
)
```

The safety properties are the design:

- **SQLite goes through the backup API** (`backend/scripts/data_snapshot.py`), never `cp`. The databases run in WAL mode, and a byte copy of the main file drops committed rows still in `-wal`. The helper is stdlib-only and 3.12-compatible because it also runs on the box's system `python3`.
- **The destination is saved first** under `transfer-backups/<timestamp>/`, and rsync's `--backup-dir` keeps every file it overwrites or deletes there. Undoing a transfer is copying that back.
- **Verification before the destination starts.** Row counts of every table and file counts and bytes of every directory are compared source against destination before the destination app boots, so nothing it writes on startup can blur the comparison. A mismatch exits 1 and leaves the prod api stopped.
- **Preconditions are enforced**: a final sync on the source, the dev server stopped, desktop Anki quit. The script refuses otherwise.
- **The Anki media pair moves one way only (to prod), and always together.** On the Mac `tt_collection.media` is a symlink into desktop Anki's own media folder; writing through it would rewrite the user's real library and `rsync --delete` would delete from it. The script refuses to write through any symlink. A media database listing files the folder lacks reads as deletions on the next media sync, which AnkiWeb propagates to every device.
- **Accounts and learner decks are one unit** (§2.5): `auth.db` and `users/` move together or not at all, and only when the laptop side is a real deployment (`TT_LAPTOP_ROOT`). The dev checkout's `auth.db` is a throwaway and must never replace prod's.

`./switch.sh` is the everyday form of this, for moving where you *learn* between prod and the laptop. The laptop side is a **separate live instance**, not the dev server: a git worktree at the exact commit prod runs (`~/TunaTaleLive/app`), built with prod's dependency set, on its own data and ports (API 8100, page 5273), so learning never runs code that is mid-edit and a dev-server reload never migrates live data. `to-laptop` copies data down, parks prod (`PARKED_AT`, §2.3), and starts the instance; `to-prod` reverses it. After a deploy while learning is on the laptop, `update` rebuilds the laptop instance at prod's new commit and moves no data, and `to-laptop` is refused while the laptop holds the AnkiWeb sync, because prod then holds the older copy and `to-laptop` would copy it over everything studied since. It builds only commits that know `TT_HOME` (§2.1) and refuses older ones, which would write into the dev server's `~/.tunatale`.

```bash
awk 'NR>1 && /^#/{print; next} NR>1{exit}' switch.sh | head -11
```

```output
# Switch where you LEARN between the production box and this laptop (tunatale-qyw0).
#
#   ./switch.sh status              where TunaTale is live, and the laptop instance's state
#   ./switch.sh to-laptop [--apply] prod -> laptop: copy data down, park prod, start the laptop instance
#   ./switch.sh to-prod   [--apply] laptop -> prod: stop the laptop instance, copy data up, unpark prod
#   ./switch.sh start | stop        the laptop instance alone (e.g. after a reboot)
#   ./switch.sh update              after a deploy, while learning is ON THE LAPTOP: rebuild
#                                   the laptop instance at prod's new commit; moves no data
#   ./switch.sh prepare             check out and build prod's commit now, moving no data
#                                   (the first build takes minutes; to-laptop does it anyway)
#
```

### 15.6 Backups and restore

There are three layers, with different jobs; the confusion between them is what the docs warn about most.

| Layer | Where | Protects against | Not against |
|---|---|---|---|
| Rolling daily snapshots | `rotate_db_backups` at every app start, into `db_backup_dir`, kept `db_backup_keep_days` (5) | application bugs and stray test runs (the real failure: an E2E run pointed at the live DB wiped the curricula on 2026-06-30 and 2026-07-13) | losing the machine: same disk |
| Pre-migration snapshots | `migration_backup_dir`, one per schema step, never rotated | a bad migration; a rollback past a schema change (§15.3) | nothing else is in scope |
| Off-box restic to B2 | `backend/scripts/backup_offbox.py` | losing the machine | its own passphrase being lost |

`rotate_db_backups` (`app/storage/db_backup.py`) is *earliest-wins* per day, so an afternoon wipe cannot clobber the morning's good copy, and it never raises, so a backup hiccup cannot block boot. The off-box driver is deliberately the opposite on both counts: it takes its **own fresh snapshot** at backup time (a same-day file is overwritten, otherwise it would upload this morning's database tonight and call it current), and it is **loud**, because nothing is watching it. A source that cannot be snapshotted raises, and a vanished upload source refuses to run at all, since restic treats a missing path as a warning and exits 3 having backed up a partial set.

```bash
cd backend && uv run python scripts/backup_offbox.py --help | sed -n '1p'; uv run python -c "
from scripts import backup_offbox as b
print([n for n in ('stage_db_snapshots','stage_anki_collection','stage_identity','laptop_is_live','notify_failure') if hasattr(b, n)])"
```

```output
usage: backup_offbox.py [-h] {init,check,snapshots,restore,backup} ...
['stage_db_snapshots', 'stage_anki_collection', 'stage_identity', 'laptop_is_live', 'notify_failure']
```

Design choices:

- **restic, not an rclone mirror.** A mirror propagates a corruption or a deletion to the remote on its next run, which is the failure this project already had twice. restic's repository is content-addressed and versioned.
- **Secrets come from the macOS Keychain** (`tunatale-restic` and `tunatale-b2` items), never the repo or `.env`. A missing item prints the exact `security add-generic-password` line that fixes it, because an unattended job's stderr is its only interface. The passphrase must also live in a password manager: a restic repository whose passphrase is lost is indistinguishable from no backup.
- **The Anki collection ships via `anki/safety.snapshot_collection`**, a read-only online backup that works while desktop Anki is open and skips the exclusive-lock probe (§10).
- **Identity travels with it.** `stage_identity` stages `auth.db` and `users/` so accounts and learner decks are backed up as the unit they are (§2.5).
- **Which instance is backed up follows `switch.sh`.** While `~/TunaTaleLive/live.env` says `SYNC_ENABLED=true`, `backup` ships the live instance's `data/` (every language DB, accounts, learner decks, media, audio) and refuses a source outside it; otherwise it ships the dev tree. The scheduler needs no change.
- **Scheduling is a LaunchAgent** (`com.tunatale.backup`, daily 03:30, via `backend/scripts/install_backup_agent.py`), not cron: the laptop is asleep at 03:30, cron silently skips the window and never catches up, and `StartCalendarInterval` fires when the machine next wakes. A LaunchAgent inherits nothing (`PATH`, environment), so the plist carries absolute tool paths and the bucket name, resolved at install time, and *refuses to install* if a tool is missing. Failure is announced through a durable marker file, because macOS desktop notifications do not deliver from the agent.
- **Sweep fixes.** Staged `-wal`/`-shm` sidecars are swept with their snapshot; snapshots are self-contained, and "ignore the sidecars" is the restore rule (a read-only `sqlite3` query creates them).

**The restore is the risky half, so it has a drill.** `backend/scripts/restore_drill.py` runs against any directory of `{stem}.{YYYY-MM-DD}.db` snapshots plus optional media/output trees (local snapshots or a restic-restored tree), is read-only toward its sources, and exits non-zero on any failed check. It was run on the Mac, and then on the GCP box, which has never held the Mac's Keychain: the passphrase and B2 key both came from the password manager, which is the claim every other measurement rests on. Running it on a different machine found four defects that were not findable at home, including absolute `/Users/...` paths stored in `audio_files.file_path` (which 404 on any other host) and media rows whose filename case differed from disk (macOS resolves them; ext4 does not). The second became a permanent gate, `backend/scripts/check_media_filename_case.py`. The results, timings and traps are in `docs/deployment.md` § "Backups and restore" and § "Off-box backups"; read that section before a real recovery.

### 15.7 Disk, logs and retention

The disk is a shared, finite resource, so the policy was written down (the user's decision, 2026-09-22): **nothing the app generates is deleted**. Regenerating lesson audio costs Azure characters and about 50 minutes per lesson on the e2-micro, and the disk has years of headroom at the current pace (§15.8 lists what grows). If pruning is ever needed it goes by *last use*, not age, because a lesson rendered long ago but still listened to must survive.

`backend/scripts/report_audio_retention.py` is the read-only instrument for that. It buckets lesson audio by days since the last listen or review (every listen is a `lesson_listens` row), and flags **orphaned** audio (the lesson row is gone, typically because a regenerate minted a new lesson id) and files no database references. It is stdlib-only and 3.12-compatible because the image has no `scripts/` and `uv run` inside the container is forbidden; it is piped into `docker exec -i ... python -`. Measured on prod on 2026-09-22, about 40% of lesson audio was orphaned, which makes orphans the first prune candidate: no screen can play them.

What rotates and what deliberately does not:

- Docker container logs: the `local` driver at 10 MB x 3 (set during provisioning).
- `warnings.log`: a `RotatingFileHandler` (§2.9).
- `sync.log`, `llm_usage.log`, `azure_tts_usage.log`: append-only and unrotated on purpose. About 20 MB a year combined, and the two usage ledgers are cost records that budgets read back, so rotating them would lose data.
- Docker images: pruned by `deploy.sh` (§15.3).
- `GET /api/admin/tts-cache` reports the TTS clip cache's file count and bytes. It is a readout only; there is no eviction.

**The disk alert** is an hourly systemd timer on the box that runs `backend/scripts/disk_alert.py` on the host's own `python3` (stdlib only, so it must stay 3.12-compatible: ruff targets 3.14 and rewrites `except (A, B):` into the 3.14 comma form, a `SyntaxError` on the box). It emails through Gmail's submission port with an app password read from a root-only file, never argv or the environment, and `./install-disk-alert.sh` installs it. The decision logic is a pure function, so its state machine can be shown directly (75% threshold, hourly polls, a 5-point recovery margin so a disk hovering at the line does not email every hour):

```bash
cd backend && uv run python -c "
from datetime import datetime, timedelta, UTC
from scripts.disk_alert import decide
t0, st = datetime(2026, 1, 1, tzinfo=UTC), {}
for h, pct in [(0, 60), (1, 76), (2, 77), (26, 78), (27, 72), (28, 69), (29, 69)]:
    d = decide(pct=pct, threshold=75, state=st, now=t0 + timedelta(hours=h)); st = d.state
    print(f'+{h:2}h {pct}% ->', d.kind)
"
```

```output
+ 0h 60% -> None
+ 1h 76% -> alert
+ 2h 77% -> None
+26h 78% -> remind
+27h 72% -> None
+28h 69% -> recover
+29h 69% -> None
```

A failed send exits 1 without recording state, so the next hour retries and `systemctl --failed` shows the failure.

### 15.8 What grows, and what to watch

On the measured prod volume (2026-09-22: 29 GB disk, 39% used) the growth terms are, in rough order: Anki's own media store and card media (never pruned; Anki needs them), lesson audio and the TTS cache (every render), rolling Anki and DB snapshots (rolling, bounded by `anki_backup_keep` and `db_backup_keep_days`), Docker images (bounded by the deploy prune), and the SQLite databases themselves (tens of MB). At a few lessons and a few dozen cards a week that is years of headroom; the alert exists so the assumption is checked rather than trusted.

Three operational signals complement the disk alert:

- **`GET /api/health`** (§12.8): the container healthcheck, `deploy.sh` and the uptime monitor all read its status code. 503 means a database, content store, or the audio or media directory cannot be used. It is unauthenticated, so it reveals status only.
- **`GET /api/admin/background-work`** (§2.9): whether prestage and deferred media fetches have finished. Wait for `idle` before measuring load on the box.
- **The durable files**: `warnings.log`, `sync.log` (`SYNC_SOAK` heartbeat and divergence lines, §10), and the two usage ledgers. `llm_usage.log` travels with `data-transfer.sh`, so lines dated before a transfer describe the *other* machine; date-bound any "did the box do this" query to after the transfer.

Paid-vendor pricing before a render (`backend/scripts/report_render_cost.py`), the Azure F0 tier and the ban on HD voices are in §7 and `.claude/rules/paid-vendors.md`. They are operational concerns too: the box's Azure quota is a monthly allowance that throttles rather than bills, and the usage ledger is the only meter.

### 15.9 Cutover and what was proven

Production went live on 2026-09-18 after an acceptance pass with Tailscale off (`docs/deployment.md` § "Cutover log"): login and a logged-out deep link, streaming with `Range` seeks through Caddy, service-worker caching in airplane mode, a live-LLM render from the box, card media created on prod (including Pixabay fetches from the datacenter IP), and an AnkiWeb sync after the handover. The one acknowledged gap, accepted by the user, is that the `story` generation call itself was not exercised on prod at cutover: its prompt uses the same client, key and egress as the calls that were. The prod guard (§2.8) was confirmed against the box's real env on the same day.

## 16. Working on TunaTale

This chapter is for a new contributor, human or agent: how to get the system running, how to exercise it by hand, where the written documentation lives, and how the repository's instruction files, hooks and task tracker shape day-to-day work. The rules themselves live in `AGENTS.md` and `.claude/rules/`; this chapter tells you which one to read when, and links rather than copies. For what the tests and gate do, see §14; for running TunaTale somewhere other than a laptop, §15.

### 16.1 Setup

Two toolchains: Python 3.14 with `uv` for `backend/`, and Bun for `frontend/`. `backend/` installs every dependency group by default for local work; CI deliberately installs only the `dev` group (§14.2), which is why a package that works on your machine can still fail there.

```bash
grep -E '^[A-Z_]+=' backend/.env.example | cut -c1-80
```

```output
GROQ_API_KEY=your-key-here
LLM_MODE=mock   # mock (CI-safe cassette replay) | live | record | patch
DATABASE_URL=sqlite:///./tunatale_sl.db
TARGET_LANGUAGE=sl
```

`backend/.env.example` is the copy-and-edit template, with `app/config.py` as the authoritative settings surface. The values that matter most to a first run:

- `GROQ_API_KEY` and `LLM_MODE`. `LLM_MODE=mock` replays cassettes (the CI-safe default); `live` calls Groq (§5).
- `DATABASE_URL` plus `TARGET_LANGUAGE` for a single language, or `DATABASE_URLS` (a JSON dict) for several, with the active language resolved per request from the `X-TT-Language` header (§2, §3).
- `LEMMATIZER_TYPE`: `lowercase` is the deterministic default and the test pin. `auto`, `classla` or `stanza` opt in to the per-language engines and their model downloads (§8).
- The Anki block (`ANKI_COLLECTION_PATH`, `SYNC_ENABLED`, and the AnkiWeb credentials, resolved from a setting, a password file or the macOS Keychain, §10) is optional. The Anki rules in `.claude/rules/anki-safety-core.md` apply to the real collection: it is production data, so never point tests or experiments at it.
- `AUTH_ENABLED` is off by default so dev is unchanged; Playwright runs with it on (§2).

`./start-dev.sh` starts the backend on `:8000` and the frontend on `:5173`. Its flags and TLS handling come from real use on a phone:

```bash
sed -n '/^# Frontend mode/,/^FRONTEND_MODE=/p' start-dev.sh
```

```output
# Frontend mode: "dev" (vite dev + HMR, default) or "prod" (vite build + preview).
# Prod mode is required for the offline-audio service worker to activate — HMR and
# service workers conflict, so the SW only registers against a production build.
# Use it when testing offline playback on the phone:  ./start-dev.sh --prod
#
# --certs-only refreshes the TLS certs and exits without starting anything. Use
# it after Tailscale comes up late, or to mint a cert for a specific name:
#   TS_HOST=my-mac.tailXXXX.ts.net ./start-dev.sh --certs-only
FRONTEND_MODE="dev"
```

`--prod` builds and previews the frontend instead of `vite dev`, because the offline-audio service worker only registers against a production bundle and conflicts with HMR. The script also mints TLS certificates with `mkcert` and covers the Tailscale MagicDNS name, so the app is reachable from a phone over HTTPS; `--certs-only` refreshes them without starting anything.

### 16.2 Running the checks

Day to day:

| What | Command (from repo root) |
|---|---|
| Whole gate, before any commit | `/abs/path/to/tunatale/test.sh > /tmp/gate.txt 2>&1`, then read the log |
| Backend lint and format | `cd backend && uv run ruff check app tests && uv run ruff format app tests` |
| Backend tests with coverage | `cd backend && uv run pytest` (add `--run-oracle` for Anki parity) |
| Frontend types and tests | `cd frontend && bun run check`, `bun run test:coverage` |
| E2E | `cd frontend && bun run test:e2e` |

The exact way to run and read the gate, and why (absolute path, nothing after it, never piped, never `cd` away), is in `AGENTS.md` § "Developer Commands" and `.claude/rules/gate-and-ci.md`, and §14.10 explains the incidents behind it. The development discipline is test-first: `.claude/rules/tdd.md` sets red-green-refactor, requires the new test to fail before the implementation exists, and says the red-then-green check happens in the working tree and is recorded in the commit message, because the commit gate makes a deliberately red commit impossible.

### 16.3 Trying it by hand

Automated tests cannot tell you whether a lesson sounds right, so the manual loop matters. With `./start-dev.sh` running and a Groq key (or mock mode with recorded cassettes):

1. **Plan and generate.** Open `http://localhost:5173`, state a goal in the planner chat, commit curriculum days, then generate a lesson for a day and render its audio (§1 follows this path end to end).
2. **Listen and Read.** On the lesson page, the Listen mode plays the audio and records the listen server-side, which auto-grades the recognition cards you heard; Read mode shows the colored transcript with per-word status (§8, §13).
3. **Review.** `/review` is the unified queue: due cards plus a daily-capped slice of new ones, both directions (L2 to L1 recognition and L1 to L2 production), graded Again/Hard/Good/Easy (§9).
4. **Manage cards.** `/cards` browses and edits SRS items: search, state filter, edit, suspend, reset, force state, create (§13).
5. **Sync.** The Sync button runs `run_full_sync` against TunaTale's own collection (§10). The sync path works while Anki is open, but anything that touches a real collection should follow the safety envelope first.

API tests and exploration have an easier route than the UI: FastAPI serves interactive docs at `/docs`, and §12 generates the route table. For the real-service limits that bite when exploring (Azure Speech F0 tier, no HD voices, pricing a render before running it), read `.claude/rules/paid-vendors.md` and use `backend/scripts/report_render_cost.py` first.

### 16.4 Instruction files, hooks and the commit policy

`AGENTS.md` is the single top-level instructions file. It was cut from about 39 KB to about 15 KB when domain material moved into `.claude/rules/`, and most rule files carry a `paths:` frontmatter so an agent loads a rule only when it reads a file the rule covers. A rule missing at session start is by design. Two rules load always: the Anki hard invariants and TDD.

```bash
for f in .claude/rules/*.md; do printf '%-34s ' "$(basename $f)"; awk '/^---$/{n++; next} n==1 && /^  - /{gsub(/^  - |"/,""); printf "%s ", $0} n==1 && !/^  - /&&!/^paths:/{} END{if(n<2||!p) {} }' "$f"; echo; done
```

```output
anki-oracle-harness.md             backend/tests/test_parity_*.py backend/tests/anki_oracle/** 
anki-queue-parity.md               backend/app/api/srs.py backend/app/srs/** backend/app/plugins/anki_sync/** backend/tests/test_parity_*.py backend/tests/test_api_srs*.py backend/tests/test_srs*.py backend/tests/test_fsrs*.py backend/tests/test_direction_*.py 
anki-safety-core.md                
anki-sync.md                       backend/app/plugins/anki_sync/** backend/app/api/anki.py backend/scripts/anki_archive/** backend/tests/test_anki_*.py backend/tests/test_e2e_listen_to_sync.py 
frontend-coverage-gate.md          frontend/** 
gate-and-ci.md                     test.sh .github/** .claude/hooks/** .claude/settings.json 
paid-vendors.md                    backend/app/audio/** backend/app/plugins/languages/*/__init__.py backend/scripts/report_render_cost.py backend/scripts/rebuild_lessons_from_story.py backend/scripts/render_slicing_ab.py backend/scripts/reroll_tts_clip.py backend/.env* 
tdd.md                             
test-tiers.md                      frontend/tests/** frontend/src/** frontend/playwright.config.ts backend/tests/** 
testing.md                         backend/tests/** test.sh 
```

What each is for, at a glance:

| Rule | Read it when |
|---|---|
| `anki-safety-core.md`, `tdd.md` | always loaded |
| `testing.md`, `test-tiers.md` | writing or placing a test (§14) |
| `gate-and-ci.md` | touching `test.sh`, `.github/**` or the hooks |
| `frontend-coverage-gate.md` | working in `frontend/**` |
| `paid-vendors.md` | anything that renders TTS |
| `anki-sync.md`, `anki-queue-parity.md`, `anki-oracle-harness.md` | any Anki, SRS or queue change, and **before** debugging any TT-versus-Anki divergence (§9, §10) |

The hooks in `.claude/settings.json` turn the rules into behaviour:

```bash
ls .claude/hooks/*.py | xargs -n1 basename
```

```output
close_bead_reminder.py
commit_gate.py
gate_pipe_guard.py
stage_submodule_pointer.py
```

The commit gate asks for confirmation on `git commit` unless `test.sh` passed on the exact current tree; the pipe guard refuses a piped gate; the submodule-pointer hook stages the task-tracker pointer onto commits that already carry content; a SessionStart hook lists unread agent mail and which beads are in progress; and a PostToolUse hook (`close_bead_reminder.py`) reminds you of a still-open bead after the commit that shipped it, because a closure must cite the commit hash and so cannot be part of that commit. Mechanics and history are in `.claude/rules/gate-and-ci.md` § Hooks.

Commit policy is in `AGENTS.md` § "Committing, Pushing, and Merging". The summary: committing and pushing are standing-authorized, and merging into `main` is the checkpoint. Small self-contained changes go straight to `main`. Anything touching Anki, SRS or sync, or spanning modules, goes on a branch with a PR that the user approves. Never stack one PR on another's branch (merging the parent deletes the base and closes the child). Never commit with the gate red, and never amend an audited commit.

Finally, `AGENTS.md` carries the conventions that are easy to violate without noticing, each of which has a checker (§14.3): resolve every per-language facet through the registry, never hardcode `"sl"`/`"no"`; `backend/app/**` never imports `anki`; the Anki collection is read at sync time only; there is exactly one sync sequence (`run_full_sync`); and cite code as `module.py::symbol`, never bare `file:line`.

### 16.5 The documentation set

Documentation is layered by lifetime. `README.md` is the pitch and quickstart. This walkthrough is the tour of the system as it is now. `docs/*.md` holds design references and runbooks, each with a different job; `docs/archive/` holds finished handoffs and plans, kept as history and described by its own `README.md`. Per-language or per-subsystem rationale lives next to the code in module docstrings and in the rule files, which are the freshest source for the mechanisms they cover.

```bash
for f in docs/*.md; do printf '%-34s %s\n' "$(basename $f)" "$(grep -m1 '^# ' $f | cut -c3-70)"; done
```

```output
adding-a-language.md               Adding a new language to TunaTale
anki-mirror-audit.md               Anki / FSRS Mirror Audit Workflow
anki-parity-diagnostics.md         Anki Parity — Diagnostics & Reference
anki-parity-layers.md              TT ↔ Anki Queue Parity — Layer-by-layer history
anki-recovery.md                   Anki disaster recovery
bdt.md                             BDT (Lampariello) — design influence on TunaTale
curriculum-planning.md             Curriculum Planning — Chat-based planner (design)
deployment.md                      Deployment
fluent-forever.md                  Fluent Forever — design influence on TunaTale
language-plugin-hardening.md       Workstream: enforce the language-plugin architecture (make it real, 
learning-modes.md                  Learning Modes — design reference
lesson-authoring.md                Lesson Authoring — Story-JSON round-trip (design)
lingq.md                           LingQ — design influence on TunaTale
pimsleur.md                        Pimsleur — design influence on TunaTale
prd.md                             TunaTale - Product Requirements Document
refold.md                          Refold — design influence on TunaTale
walkthrough.md                     TunaTale Codebase Walkthrough
```

Grouped by what you need them for:

- **Pedagogy ("why is it shaped like this").** `pimsleur.md` (graduated recall, backward buildup), `fluent-forever.md` (ending-blank cloze, image over translation), `lingq.md` (known/unknown tracking, the lineage of the transcript model), `refold.md` (comprehensible input, listen-first), `bdt.md` (bidirectional translation, the recognition-production pairing), `learning-modes.md` (the mode map), and `prd.md` (requirements). Each influence doc follows one shape: the claim, how TunaTale applies it, where it deliberately diverges.
- **Design references.** `curriculum-planning.md` (the chat planner), `lesson-authoring.md` (story-JSON round trip), `language-plugin-hardening.md` (why the registry and literal gate exist).
- **Anki.** `anki-parity-layers.md` (layer-by-layer parity history, the long form of §9), `anki-parity-diagnostics.md` (diagnostic snippets and the load-bearing helpers), `anki-mirror-audit.md`, and `anki-recovery.md` (disaster recovery for the primary collection).
- **Operations and extension.** `deployment.md` (runbook and recorded restore drills, §15) and `adding-a-language.md`. The latter was rewritten after Tagalog and Cebuano were wired end to end; it is the checklist to follow, and the registry is the process (§3).

When code and prose disagree, the code wins and the prose needs fixing; the tour cites symbols rather than line numbers so it can be re-verified by grep.

### 16.6 Task tracking with bd (briefly)

The backlog and its dependency ordering live in `bd` (beads), synced git-natively to a private repo and mounted as the `.beads-tasks` submodule, not in prose queue tables. `docs/briefs/` no longer exists. A few commands cover most use:

```bash
sed -n '/^bd ready/,/^bd graph/p' .claude/skills/beads/SKILL.md
```

```output
bd ready --exclude-type=epic                  # unblocked work (epics are containers, not work)
bd ready --parent <epic> --exclude-type=epic  # ...scoped to one theme
bd show <id> --json | jq -r '.[0].description'    # always --json (see traps)
bd create "title" -d "..." -p 0-4             # 0 critical .. 4 backlog
bd dep add <child> <parent>                   # child is blocked by parent
bd update <id> --claim  /  bd close <id> --reason "..."
bd graph <epic> --compact                     # terminal view of one theme
```

This repo's `bd` conventions are full of traps that return a clean negative instead of an error (wrong JSON field names, dependency-edge direction that flips between `bd dep add` and `bd create --deps`, parent/child blocking). Load the `beads` skill (`.claude/skills/beads/SKILL.md`) before any bd write, sync, mail or brief. Claim a bead when starting it, as your location (`worktree@branch`, so concurrent sessions can tell each other apart), and close what you claimed when it merges; closing a bead is not authorization to commit.

### 16.7 Delegation

Delegation is the default for mechanical work. `AGENTS.md` § "Delegation" holds the policy: hand off work whose hard part is typing rather than deciding (multi-file mechanical edits, tests against a pinned oracle, doc sweeps, renames with a mechanical rule) to a cheaper executor, via the `bp-delegate` or `swe-delegate` skills, and keep for yourself anything touching Anki, SRS or sync semantics, oracle design (deciding what would falsify a claim), the final gate, and the audit of the returned diff. If writing the brief costs more than doing the work, do the work. Executors leave work uncommitted by default; the orchestrator audits it, runs the gate and decides the merge.

When you are stuck, the rule is also in `AGENTS.md`: after three failed attempts at the same problem, stop and report what you tried rather than spinning. And before reporting that something is broken, run a control (§14.10): the same probe at `HEAD~1`, or against a record whose answer you already know.

## Appendix A. How TunaTale Got Here

The chapters above describe the system as it is. This appendix is the short version of how it got that way. It is here so that a decision that looks arbitrary can be traced to the era that produced it. The previous edition of this walkthrough was written as a running changelog, PARTs 1–31 written between March and July 2026, and it is preserved unedited at [`docs/archive/walkthrough-2026-03-to-07.md`](archive/walkthrough-2026-03-to-07.md). Read it for the step-by-step narrative of a subsystem's construction. Treat everything in it as historical: its code listings and line numbers describe the tree at the time each PART was written.

### A.1 Eras

| Era | When | What it established | Archived PARTs |
|---|---|---|---|
| Prototypes | early 2026 | Two throwaway codebases (`micro-demo-0.0` audio pipeline, `micro-demo-0.1` content engine) proved Pimsleur-style sections and LLM story generation | `docs/archive/walkthrough-prototypes.md` |
| Production rebuild | Mar–Apr 2026 | One FastAPI app, hexagonal ports, Pydantic settings, cassette-replayed LLM, FSRS-5, `ContentStore`, section builder, SvelteKit frontend | 1–11 |
| Anki integration ("Stage 3") | Apr–May 2026 | Two-direction SRS items, `safe_open`, offline-first sync against `collection.anki2`, media pipeline, queue stats from Anki's deck config | 12–14, 17 |
| Listen-first loop | Apr–May 2026 | `POST /api/srs/listen`, interactive transcript, function-word clozes, recency-led new queue | 15 |
| Queue parity | late Apr–Aug 2026 | Anki's queue rebuilt layer by layer against a differential oracle; FSRS moved to f32 bit-parity; `tt_revlog` | 16, 18–19, 26 |
| Word-learning state machine | late May–Jun 2026 | Sentence-aware lemmatizer, always-on cloze, `morphology_focus`, per-lemma mastery, recognition-before-production gates | 20, 22–25 |
| Event-sourced sync, then peer-sync | May–Jul 2026 | Stage 3b's sync modes were tried and decommissioned; the one surviving path is `run_full_sync` | 27, 29.1 |
| Restructurings | Jun–Jul 2026 | Sync and database god-modules split; language-plugin registry; Norwegian as the second language; direction-field registry | 29 |
| Plugin completion | Jul 2026 | Core imports no concrete language (a gate enforces it); server-backed listened state; budget-capped card creation | 30 |
| Syllable slicing | late Jul 2026 | Forced-alignment slicing of breakdown chunks. It was later retired from the render path in favour of lexicon IPA (§7) | 31 |
| Production and new languages | Aug–Oct 2026 | Deployment to a cloud box with accounts, backups and health checks (§2, §15). Azure and Gemini TTS replaced EdgeTTS (§7). Tagalog and Cebuano plugins (§3). Just-in-time production minting and the LLM cloze tier (§11). Review sessions (§6). Drawn picture cards (§11). Day-rule and cross-language parity fixes (§9) | not in the archive; covered in place |

### A.2 What survived from the prototypes

The prototypes' ideas outlived their code:
- the Pimsleur section format: key phrases with backward buildup, natural speed, a slowed pass and translated passes;
- hexagonal architecture with Protocol-based ports (§7.1);
- the WIDER/DEEPER content-strategy framework (§4, §6), since joined by REVIEW;
- recorded LLM responses for tests, grown into the cassette system (§5).

Almost everything else was replaced. Hardcoded language tables became plugins. A custom scheduler became FSRS in f32 bit-parity with Anki. MD5-hashed mocks became prompt-hash cassettes. One output file became per-section Opus renders. Ten endpoints became the API surface in §12.

### A.3 Where the old PART numbers went

Other documents in `docs/` cite the old `PART N` numbering. This table maps the most-cited ones to their current home.

| Old | Topic | Now |
|---|---|---|
| PART 1 | Configuration & entry point | §2 |
| PART 2 | Domain models | §3 (languages), §4 |
| PART 3 | LLM client & cassettes | §5, §14 |
| PART 4 / 4.4 | FSRS engine / per-word tracking | §9 / §8 |
| PART 5 | Content generation & storage | §6 |
| PART 6 | Audio pipeline | §7 |
| PART 7 | API layer | §12 |
| PART 8, 11 | Test suite, manual testing | §14, §16 |
| PART 9 | Full data flow | §1 |
| PART 10 | What changed from the prototypes | A.2 |
| PART 12 | Anki integration (12.1 two-direction items → §9) | §10, §11 |
| PART 13, 21 | Frontend, frontend toolchain | §13 |
| PART 14 | Settings & migrations | §2, §9 |
| PART 15 | Listen-first acquisition loop | §8 |
| PART 16, 18, 19, 26 | Queue parity, harness, `tt_revlog`, f32 FSRS | §9 |
| PART 20, 23 | Cloze pipeline | §11 |
| PART 22, 25 | Lemmatizer, word-learning state machine | §8 |
| PART 24 | `morphology_focus` generation | §6 |
| PART 27, 29.1 | Event-sourced sync, the one sync path | §10 |
| PART 29.3–29.4, 30.1 | Language registry, compound breakdown, plugin gates | §3 |
| PART 31 | Syllable slicing | §7 |

