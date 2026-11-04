# TunaTale

*The best tuna on the net.*

An AI-powered language-learning system that generates personalized audio curricula from your goals — Pimsleur-style listening, but the stories are dynamic and the vocabulary is yours. Spaced repetition runs on two channels: implicit grades from listening (words you understood get credit without flashcards) and an explicit review queue that stays bit-for-bit consistent with your Anki deck. Eventually to be guided by TunaTale himself, an enthusiastic multilingual tuna with travel stories from everywhere.

## The pitch

Existing tools each get one thing right and one thing wrong:

- **Pimsleur** — audio-first methodology works, but the content is static. Once you outgrow "I would like to buy a sandwich," there's nowhere to go.
- **Comprehensible-input apps** (LingQ, Language Reactor) — great immersion principle, but text-first and limited to whatever content already exists.
- **Spaced repetition** (Anki) — solid retention, but isolated flashcards lack natural context and the words rarely surface in actual sentences.
- **Language podcasts** — natural input, but generic topics and no progression.

**The gap**: nothing combines AI-generated personalized content with audio-first immersion and pedagogically sound progression.

TunaTale fills it with a three-phase loop drawn from Krashen's comprehensible input research and cognitive-load theory:

1. **Explicit prep** (web) — meet the key phrases and context for the upcoming lesson, without memorization pressure.
2. **Audio immersion** (mobile / car) — listen to a generated story in the target language with the prepared vocabulary embedded naturally.
3. **Spaced reinforcement** — FSRS over words and collocations, fed both implicitly (a listen auto-grades the recognition cards you heard) and explicitly (the review queue). The next lessons build on what you know.

## What's currently built

A working personal-use system with **Slovene, Norwegian and Cebuano** wired end-to-end (Cebuano now the deepest) and a **Tagalog** plugin beside them, driving the pedagogical loop daily:

- **Curriculum + story generation** via Groq LLM, with a planner-chat UI to propose and commit curriculum days and a cassette-replay system for deterministic tests.
- **Audio pipeline** — Azure Speech for synthesis (Gemini TTS for Cebuano, which Azure has no voice for), syllable-level backward buildup for tricky words, Forvo for human pronunciations where available, ffmpeg LUFS normalization across the lesson, Opus transcoding, and a service worker that caches lesson audio for offline listening on the phone. Because Gemini's output is nondeterministic and cached forever, each fresh Cebuano take is checked before it is kept (said twice, a `t` heard as `p`, a missing glottal stop) and drawn again if it fails.
- **Listen-first acquisition loop** — `POST /listen` records the listen server-side, auto-grades the recognition cards you heard, and creates a *budget-capped* batch of new cards per listen (one Anki-day's worth, ranked key-phrases-first then by in-lesson frequency) — repeated listens gradually acquire the lesson. Function words become cloze cards with sentence audio.
- **Read mode** — LingQ-style colored transcript with per-word status and a popover per word: the lesson's own gloss, the four review ratings for a due word, start learning, Inflect (a morphology cloze for an inflected form), a second card for a second meaning, and Phrase… to make a phrase card from a phone. English can be shown idiomatically, literally (per-word gloss) or both, and a Recall mode blurs the words whose production card is due.
- **FSRS-5 scheduler** with per-direction state (RECOGNITION L2→L1 and PRODUCTION L1→L2), mirroring Anki bit-exact in f32 — RNG seed, fuzz, interval cascade, lapse-stability ceiling, the live load balancer, queue ordering, sibling burying, daily caps. Divergences found in production are documented as numbered Layers (91 so far).
- **Bidirectional Anki sync** — direct SQLite access to `collection.anki2` via a safety envelope (`safe_open` — backup, integrity check, lock probe), an event log (`tt_revlog`) whose event-sourced pull path is live, and peer-sync round-trip tests against a real `anki.syncserver`. The PRD positioned Anki as a competitor, but the real workflow turned out to be complementary: TunaTale is the audio-first front-end for an Anki deck the author was already studying, with grades flowing in both directions and FSRS staying consistent across both apps.
- **Language plugins** — each language is a self-contained plugin (registration, preprocessor, lemmatizer, syllabifier, audio breakdown, vocab notetype) under `app/plugins/languages/`; the core never hardcodes a language, and a checker enforces it.
- **Cebuano, in depth** — the deepest plugin, built on John U. Wolff's public-domain *Dictionary of Cebuano Visayan* (1972) and his *Beginning Cebuano* course:
  - every vocab card carries the dictionary in tiers under its word: the Handbook spelling with stress and glottal stops marked, the meaning, the verb's affixes as a forms table, numbered senses with examples, and the entry verbatim;
  - an affix decoder implements Wolff's sound-change rules in both directions, so an affixed word in a lesson is glossed by its parts (`mo- will + ádto there · go`) in the reader and on the card;
  - short forms are first class: `dili, di` is one card, while the pronoun clitics `ko`/`ako` are two cards that name each other;
  - every story lesson ends on an **affix drill**, a Pimsleur-style section built from hand-checked tables with no LLM call, that models one aspect pair (`molakaw` / `milakaw`, "will walk" / "walked") and then asks for the forms from English. A pace gate picks the day's pattern: the first one in the teaching order the learner has not yet passed, judged by drill days and first-review pass rates on its contrast cards. The patterns run from `mo-`/`mi-` to object focus (`gi-`/`-on`), which is drilled as whole sentences with the narrator stressing the focus;
  - pronouns, deictics (`kini`/`kiri`/`kana`/`kadto`), numbers and clock times get drawn SVG pictures instead of photos.
- **Card media** — pictures from LanGeek's per-sense picture dictionary first, then Pixabay, with a guard that no two cards share a photograph; drawn SVGs where the relation is the meaning (numbers, spatial words, pronouns, calendar).
- **SvelteKit frontend** — unified `/review` queue, lesson pages with Listen/Read modes, paged review sessions, planner chat, `/cards` browser (search, suspend, image management), single Sync button; usable from a phone over Tailscale.
- **Accounts** — an owner plus learner accounts, each learner with their own per-language databases; no self-serve signup, but the admin can mint a single-use invite link from the CLI.

Still ahead from the PRD:

- Target-language audio control phrases ("Más despacio").
- A native mobile / car experience (today it's the browser at `:5173` plus the offline-audio service worker).
- The TunaTale mascot in the prep phase, telling travel stories about the new vocabulary.

## Quickstart

```bash
# Backend (Python 3.14, uv)
cd backend
uv sync --all-groups
cp .env.example .env      # set GROQ_API_KEY for live generation; LLM_MODE=mock is CI-safe

# Frontend (Bun)
cd ../frontend
bun install

# Run both dev servers (backend :8000, frontend :5173)
cd ..
./start-dev.sh
```

Open <https://localhost:5173> (`start-dev.sh` serves HTTPS via mkcert; see `frontend/README.md` for the plain-HTTP standalone mode).

## Testing

```bash
./test.sh                              # full gate: ruff + checkers + pytest + svelte-check + vitest + playwright
cd backend && uv run pytest            # backend only (~19,000 tests, 100% coverage required)
cd frontend && bun run test:coverage   # frontend only (100% per-file via a custom Svelte 5 phantom-filter coverage gate)
```

CI runs six parallel jobs: backend (lint + the checker scripts + pytest), two hostile-clock backend runs (a far-off timezone and an hour near Anki's 04:00 rollover), frontend (svelte-check + vitest), e2e (Playwright), and anki-gates (the oracle parity suite at the rollover and before it, then peer-sync). `./test.sh` is a strict subset of CI. The Anki oracle harness (`--run-oracle`) spawns Anki's actual scheduler in a subprocess via `uv run --with anki python` — production code never imports Anki.

## Deployment (Docker Compose)

A production-ready Docker Compose stack is included. It builds a multi-stage image (Bun for the frontend, uv + Python 3.14 for the API, Caddy for serving) and runs two services behind a single port.

```bash
# 1. Create your .env (copy .env.example, set GROQ_API_KEY and other values)
cd backend
cp .env.example .env
# edit .env with your keys

# 2. Build and start the stack
cd ..
docker compose build
docker compose up -d

# 3. Verify
curl -s localhost/api/health   # should return {"status":"ok",...}
```

The stack uses `HOME=/data` to relocate all `~/.tunatale/*` paths (logs, backups, alignment cache, sync log) onto a named volume. The `media_dir` and `audio_dir` are also volume-backed. An init service pre-creates the required directories so the health check passes on first boot.

Caddy serves the static SPA at `/` and reverse-proxies `/api` to the backend — no CORS configuration needed (same-origin).

See `Dockerfile`, `docker-compose.yml`, and `Caddyfile` in the repo root.

## Stack

- **Backend** — FastAPI on Python 3.14, `uv` for dependencies, SQLite for SRS + content storage. Per-language plugin registry so adding an L2 doesn't touch the core.
- **Frontend** — SvelteKit + TypeScript (Svelte 5), Vite, Vitest with a custom phantom-filter coverage gate at 100% per-file. Lint via Oxlint (fast Rust) + ESLint with `eslint-plugin-svelte` (thorough). Format via Oxfmt. Playwright for E2E.
- **Audio** — Azure Speech and Gemini TTS, ffmpeg, Opus delivery, Forvo + LanGeek + Pixabay for media enrichment with deterministic fallbacks.
- **SRS** — FSRS-5 in f32 bit-parity with Anki (pinned by a differential oracle against `fsrs-rs`), per-direction state, live load-balancer mirror, `tt_revlog` event log with the event-sourced pull path live (walkthrough §9.9 and §10.5).
- **LLM** — Groq for content generation with a VCR-style cassette system so tests are deterministic and offline.

## Documentation

- **[docs/prd.md](docs/prd.md)** — original product-requirements doc with the full pitch: market gap, user journeys, three-phase pedagogical cycle, competitive positioning, the tuna.
- **[docs/walkthrough.md](docs/walkthrough.md)** — full system tour, ordered by subsystem (a one-lesson journey, then 15 subsystem chapters and a history appendix), executable via [Showboat](https://github.com/simonw/showboat) so every code block is re-runnable (`uvx showboat verify docs/walkthrough.md`). Start here to understand any specific subsystem; the earlier chronological edition is archived in `docs/archive/`.
- **[docs/learning-modes.md](docs/learning-modes.md)** — the canonical design for the study modes (Review / Listen / Read) and their build order.
- **Design influences** — what TT inherits from each, and where it diverges, with code references:
  - **[docs/pimsleur.md](docs/pimsleur.md)** — the four-section lesson format, anticipation pause, syllable-level backward buildup.
  - **[docs/fluent-forever.md](docs/fluent-forever.md)** — the Slovene Vocabulary notetype, picture+audio production cards, cloze for function words.
  - **[docs/lingq.md](docs/lingq.md)** — the colored-transcript UI, word-status cycle, click-to-untrack, implicit-grade-on-listen.
  - **[docs/refold.md](docs/refold.md)** — 1T sentence clozes, recognition-before-production direction split.
  - **[docs/bdt.md](docs/bdt.md)** — Luca Lampariello's Bi-Directional Translation method; reception side overlaps with TT today, production side (L1→L2 written reconstruction) is a future mode.
- **[docs/anki-parity-layers.md](docs/anki-parity-layers.md)** — 91 layers of TT ↔ Anki scheduler parity work, each one a divergence found in production, the mechanism, and the fix. Load-bearing reference for the sync code, with [docs/anki-parity-diagnostics.md](docs/anki-parity-diagnostics.md) holding the runnable diagnostic snippets.
- **[docs/archive/stage-3b-empirical-measurement.md](docs/archive/stage-3b-empirical-measurement.md)** — the measurement campaign that gated the event-sourced sync cutover (since shipped).
- **[docs/adding-a-language.md](docs/adding-a-language.md)** — the touch-points to wire a new L2, with the measured costs Tagalog and Cebuano hit, with [docs/language-plugin-hardening.md](docs/language-plugin-hardening.md) covering the no-hardcoded-language enforcement.
- **[docs/anki-recovery.md](docs/anki-recovery.md)** — disaster-recovery procedure if TT ever corrupts `collection.anki2`. Read before you need it.
- **`AGENTS.md` + `.claude/rules/`** — developer workflow and cross-model project rules: Anki safety invariants, USN sync protocol, queue-parity playbook + pre-Layer checklist, oracle harness workflow, testing strategy, TDD discipline.

## Repo layout

```
backend/
  app/
    main.py            FastAPI app + lifespan
    config.py          Pydantic settings (env-driven)
    languages.py       Per-language plugin registry (LanguageConfig/LanguageContext)
    api/               FastAPI route modules
    audio/             TTS, preprocessing, assembly, cloze TTS, Opus transcode
    cards/             Vocab-card notetypes + add-time media pipeline
    common/            Cross-cutting helpers (guid generation)
    generation/        Curriculum + story generators
    llm/               Groq client + cassette system
    media/             In-app media import (Anki media → TT cache)
    models/            Pure domain models
    plugins/
      anki_sync/       Optional Anki integration: safety envelope, sync engine, import
      languages/       Self-contained language plugins (sl, no, tl, ceb)
    srs/               FSRS-5 scheduler, Anki-mirror queue engine, database mixins
    storage/           ContentStore SQLite repository
  tests/
    anki_oracle/       Subprocess parity-test harness
    cassettes/         Recorded LLM responses
    test_*.py          ~19,000 tests, 100% coverage

frontend/
  src/
    routes/            SvelteKit pages: home, lesson, review sessions, /review, /cards, planner, settings, login, invite
    lib/               api client, components, stores, playback, service worker
  scripts/
    coverage-gate.ts   Custom Svelte 5 phantom-filter coverage gate
  tests/               Playwright E2E

tests/                 Shared prompts + test data (not a test package)
docs/                  Walkthrough + designs + parity history
.claude/rules/         Cross-model project rules
```

## Status

Personal project with two learners in production: the author, whose TunaTale decks are mirrored to an Anki deck per language, and a second learner studying Cebuano in TunaTale alone, with no Anki behind it. Not packaged for distribution. If you're an efficiency-focused language learner who'd find value here, the [walkthrough](docs/walkthrough.md) is enough to understand the system and the code is published openly — but there's no installer and no support contract.

## License

No license file yet. Open an issue if you want to use any of this for anything.
