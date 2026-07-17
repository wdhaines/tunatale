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

A working personal-use system with **Slovene and Norwegian** wired end-to-end (Slovene most completely), driving the pedagogical loop daily:

- **Curriculum + story generation** via Groq LLM, with a planner-chat UI to propose and commit curriculum days and a cassette-replay system for deterministic tests.
- **Audio pipeline** — EdgeTTS for synthesis, syllable-level backward buildup for tricky words, Forvo for human pronunciations where available, ffmpeg LUFS normalization across the lesson, Opus transcoding, and a service worker that caches lesson audio for offline listening on the phone.
- **Listen-first acquisition loop** — `POST /listen` records the listen server-side, auto-grades the recognition cards you heard, and creates a *budget-capped* batch of new cards per listen (one Anki-day's worth, ranked key-phrases-first then by in-lesson frequency) — repeated listens gradually acquire the lesson. Function words become cloze cards with sentence audio.
- **Read mode** — LingQ-style colored transcript with per-word status, tap-to-introduce/untrack, interlinear translations, and morphology clozes for inflected forms.
- **FSRS-5 scheduler** with per-direction state (RECOGNITION L2→L1 and PRODUCTION L1→L2), mirroring Anki bit-exact in f32 — RNG seed, fuzz, interval cascade, lapse-stability ceiling, the live load balancer, queue ordering, sibling burying, daily caps. Divergences found in production are documented as numbered Layers (80 so far).
- **Bidirectional Anki sync** — direct SQLite access to `collection.anki2` via a safety envelope (`safe_open` — backup, integrity check, lock probe), an event log (`tt_revlog`) whose event-sourced pull path is live, and peer-sync round-trip tests against a real `anki.syncserver`. The PRD positioned Anki as a competitor, but the real workflow turned out to be complementary: TunaTale is the audio-first front-end for an Anki deck the author was already studying, with grades flowing in both directions and FSRS staying consistent across both apps.
- **Language plugins** — each language is a self-contained plugin (registration, preprocessor, lemmatizer, syllabifier, audio breakdown, vocab notetype) under `app/plugins/languages/`; the core never hardcodes a language, and a checker enforces it.
- **SvelteKit frontend** — unified `/review` queue, lesson pages with Listen/Read modes, planner chat, `/cards` browser (search, suspend, image management), single Sync button; usable from a phone over Tailscale.

Still ahead from the PRD:

- Tagalog, to exercise the loop in a third language (scaffolding exists from the original prototype; `docs/adding-a-language.md` has the recipe, with Norwegian as the worked example).
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
cd backend && uv run pytest            # backend only (~4000 tests, 100% coverage required)
cd frontend && bun run test:coverage   # frontend only (100% per-file via a custom Svelte 5 phantom-filter coverage gate)
```

CI runs four parallel jobs: backend (lint + mock-boundary and language-literal checkers + pytest), frontend (svelte-check + vitest), oracle-parity, and peer-sync. The Anki oracle harness (`--run-oracle`) spawns Anki's actual scheduler in a subprocess via `uv run --with anki python` — production code never imports Anki.

## Stack

- **Backend** — FastAPI on Python 3.14, `uv` for dependencies, SQLite for SRS + content storage. Per-language plugin registry so adding an L2 doesn't touch the core.
- **Frontend** — SvelteKit + TypeScript (Svelte 5), Vite, Vitest with a custom phantom-filter coverage gate at 100% per-file. Lint via Oxlint (fast Rust) + ESLint with `eslint-plugin-svelte` (thorough). Format via Oxfmt. Playwright for E2E.
- **Audio** — EdgeTTS, ffmpeg, pydub, Opus delivery, Forvo + Pixabay for media enrichment with deterministic fallbacks.
- **SRS** — FSRS-5 in f32 bit-parity with Anki (pinned by a differential oracle against `fsrs-rs`), per-direction state, live load-balancer mirror, `tt_revlog` event log with the event-sourced pull path live (walkthrough PARTs 19 and 27).
- **LLM** — Groq for content generation with a VCR-style cassette system so tests are deterministic and offline.

## Documentation

- **[docs/prd.md](docs/prd.md)** — original product-requirements doc with the full pitch: market gap, user journeys, three-phase pedagogical cycle, competitive positioning, the tuna.
- **[docs/walkthrough.md](docs/walkthrough.md)** — full system tour (29 parts, ~7700 lines, executable via [Showboat](https://github.com/jbenet/showboat) so every code block is re-runnable). Start here to understand any specific subsystem.
- **[docs/learning-modes.md](docs/learning-modes.md)** — the canonical design for the study modes (Review / Listen / Read) and their build order.
- **Design influences** — what TT inherits from each, and where it diverges, with code references:
  - **[docs/pimsleur.md](docs/pimsleur.md)** — the four-section lesson format, anticipation pause, syllable-level backward buildup.
  - **[docs/fluent-forever.md](docs/fluent-forever.md)** — the Slovene Vocabulary notetype, picture+audio production cards, cloze for function words.
  - **[docs/lingq.md](docs/lingq.md)** — the colored-transcript UI, word-status cycle, click-to-untrack, implicit-grade-on-listen.
  - **[docs/refold.md](docs/refold.md)** — 1T sentence clozes, recognition-before-production direction split.
  - **[docs/bdt.md](docs/bdt.md)** — Luca Lampariello's Bi-Directional Translation method; reception side overlaps with TT today, production side (L1→L2 written reconstruction) is a future mode.
- **[docs/anki-parity-layers.md](docs/anki-parity-layers.md)** — 80 layers of TT ↔ Anki scheduler parity work, each one a divergence found in production, the mechanism, and the fix. Load-bearing reference for the sync code, with [docs/anki-parity-diagnostics.md](docs/anki-parity-diagnostics.md) holding the runnable diagnostic snippets.
- **[docs/archive/stage-3b-empirical-measurement.md](docs/archive/stage-3b-empirical-measurement.md)** — the measurement campaign that gated the event-sourced sync cutover (since shipped).
- **[docs/adding-a-language.md](docs/adding-a-language.md)** — the touch-points to wire a new L2 (Norwegian, wired 2026-06/07, is the worked example), with [docs/language-plugin-hardening.md](docs/language-plugin-hardening.md) covering the no-hardcoded-language enforcement.
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
      languages/       Self-contained language plugins (sl, no, …)
    srs/               FSRS-5 scheduler, Anki-mirror queue engine, database mixins
    storage/           ContentStore SQLite repository
  tests/
    anki_oracle/       Subprocess parity-test harness
    cassettes/         Recorded LLM responses
    test_*.py          ~4000 tests, 100% coverage

frontend/
  src/
    routes/            SvelteKit pages: home, lesson, /review, /cards, planner, settings
    lib/               api client, components, stores, playback, service worker
  scripts/
    coverage-gate.ts   Custom Svelte 5 phantom-filter coverage gate
  tests/               Playwright E2E

tests/                 Shared prompts + test data (not a test package)
docs/                  Walkthrough + designs + parity history
.claude/rules/         Cross-model project rules
```

## Status

Personal project with one production user (the author, learning Slovene and Norwegian). Anki integration is one deck per language. Not packaged for distribution. If you're an efficiency-focused language learner who'd find value here, the [walkthrough](docs/walkthrough.md) is enough to understand the system and the code is published openly — but there's no installer and no support contract.

## License

No license file yet. Open an issue if you want to use any of this for anything.
