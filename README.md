# TunaTale

*The best tuna on the net.*

An AI-powered language-learning system that generates personalized audio curricula from your goals — Pimsleur-style listening, but the stories are dynamic, the vocabulary is yours, and the spaced repetition runs implicitly off your help-seeking behavior instead of explicit flashcard reviews. Guided by TunaTale himself, an enthusiastic multilingual tuna with travel stories from everywhere.

## The pitch

Existing tools each get one thing right and one thing wrong:

- **Pimsleur** — audio-first methodology works, but the content is static. Once you outgrow "I would like to buy a sandwich," there's nowhere to go.
- **Comprehensible-input apps** (LingQ, Language Reactor) — great immersion principle, but text-first and limited to whatever content already exists.
- **Spaced repetition** (Anki) — solid retention, but isolated flashcards lack natural context and the words rarely surface in actual sentences.
- **Language podcasts** — natural input, but generic topics and no progression.

**The gap**: nothing combines AI-generated personalized content with audio-first immersion and pedagogically sound progression.

TunaTale fills it with a three-phase loop drawn from Krashen's comprehensible input research and cognitive-load theory:

1. **Explicit prep** (web) — meet the key collocations and context for the upcoming lesson, without memorization pressure.
2. **Audio immersion** (mobile / car) — listen to a generated story in the target language with the prepared vocabulary embedded naturally.
3. **Spaced reinforcement** — implicit FSRS over **collocations** (3-5 word chunks), not individual words. Asking for a translation or slowdown counts as a "failed review"; no help needed counts as a successful one. The next story surfaces what you missed.

## What's currently built

This is a working personal-use system for **Slovene**, driving the PRD's pedagogical loop end-to-end:

- **Curriculum + story generation** via Groq LLM with cassette replay for deterministic tests.
- **Audio pipeline** — EdgeTTS for synthesis, syllable-level backward buildup for tricky words, Forvo for human pronunciations where available, ffmpeg LUFS normalization across the lesson, pydub for assembly.
- **Listen-first acquisition loop** — `/listen` endpoint that auto-grades the words you heard, creates Anki-style cloze cards for function words, and shows the L1 sentence translation on reveal.
- **FSRS-5 scheduler** with per-direction state (RECOGNITION L2→L1 and PRODUCTION L1→L2), mirroring Anki's algorithm bit-exact (RNG seed, fuzz, interval cascade, lapse-stability ceiling, graduation short-term, …).
- **Bidirectional Anki sync** — direct SQLite access to `collection.anki2` via a safety envelope (`safe_open` — backup, integrity check, lock probe). The PRD positioned Anki as a competitor, but the real workflow turned out to be complementary: TunaTale is the audio-first front-end for an Anki deck the author was already studying, with grades flowing in both directions and FSRS staying consistent across both apps.
- **SvelteKit frontend** with a unified `/review` queue, transcript with translation, single Sync button, and `/admin/srs` console.

The PRD targets are still ahead:

- Tagalog to exercise the pedagogical loop in a third language (Norwegian is wired end-to-end as of 2026-07 — recognition-only deck, Stanza lemmatizer, compound-aware word breakdown — Slovene remains the most complete).
- Target-language audio control phrases ("Más despacio").
- Mobile / car-optimized native experience (today it's browser-based at `:5173`).
- The TunaTale mascot in the prep phase, telling travel stories about the new collocations.

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

Open <https://localhost:5173> (the dev server is HTTPS-only via mkcert — see `start-dev.sh`).

## Testing

```bash
./test.sh                              # full suite: ruff + pytest + svelte-check + vitest + playwright + oracle parity
cd backend && uv run pytest            # backend only (100% coverage required)
cd frontend && bun run test:coverage   # frontend only (100% per-file via a custom Svelte 5 phantom-filter coverage gate)
```

CI runs four parallel jobs: backend (lint + mock-boundary check + pytest), frontend (svelte-check + vitest), oracle-parity, and peer-sync. The Anki oracle harness (`--run-oracle`) spawns Anki's actual scheduler in a subprocess via `uv run --with anki python` — production code never imports Anki.

## Stack

- **Backend** — FastAPI on Python 3.14, `uv` for dependencies, SQLite for SRS storage. Pluggable language preprocessors so adding a new L2 doesn't require touching the core.
- **Frontend** — SvelteKit + TypeScript, Vite 8, Vitest 4 with a custom Svelte-5 phantom-filter coverage gate that hits 100% per-file. Lint via Oxlint (fast Rust) + ESLint with `eslint-plugin-svelte` (thorough). Format via Oxfmt. Playwright for E2E.
- **Audio** — EdgeTTS, ffmpeg, pydub, Forvo + Pixabay for media enrichment with deterministic fallbacks.
- **SRS** — FSRS-5, per-direction state, daily unbury sweep, an emerging `tt_revlog` event log alongside today's field-merge sync (full story in `docs/walkthrough.md` PART 19).
- **LLM** — Groq for content generation with a VCR-style cassette system so tests are deterministic and offline.

## Documentation

- **[docs/prd.md](docs/prd.md)** — original product-requirements doc with the full pitch: market gap, user journeys, three-phase pedagogical cycle, competitive positioning, the tuna.
- **[docs/walkthrough.md](docs/walkthrough.md)** — full system tour (~6300 lines, executable via [Showboat](https://github.com/jbenet/showboat) so every code block is re-runnable). Start here to understand any specific subsystem.
- **Design influences** — what TT inherits from each, and where it diverges, with code references:
  - **[docs/pimsleur.md](docs/pimsleur.md)** — the four-section lesson format, anticipation pause, syllable-level backward buildup.
  - **[docs/fluent-forever.md](docs/fluent-forever.md)** — the Slovene Vocabulary notetype, picture+audio production cards, cloze for function words.
  - **[docs/lingq.md](docs/lingq.md)** — the colored-transcript UI, word-status cycle, click-to-untrack, implicit-grade-on-listen.
  - **[docs/refold.md](docs/refold.md)** — 1T sentence clozes, recognition-before-production direction split.
  - **[docs/bdt.md](docs/bdt.md)** — Luca Lampariello's Bi-Directional Translation method; reception side overlaps with TT today, production side (L1→L2 written reconstruction) is a candidate Phase G.
- **[docs/anki-parity-layers.md](docs/anki-parity-layers.md)** — 80 layers of TT ↔ Anki scheduler parity work, each one a divergence found in production, the mechanism, and the fix. Load-bearing reference for the sync code.
- **[docs/stage-3b-empirical-measurement.md](docs/stage-3b-empirical-measurement.md)** — procedure for the measurement that gates the next big architectural move (replacing field-merge sync with event-replay).
- **[docs/adding-a-language.md](docs/adding-a-language.md)** — the touch-points to wire a new L2 (Norwegian, wired 2026-06/07, is the worked example; Tagalog has scaffolding from the original prototype).
- **[docs/anki-recovery.md](docs/anki-recovery.md)** — disaster-recovery procedure if TT ever corrupts `collection.anki2`. Read before you need it.
- **`.claude/rules/`** — project rules cross-model: USN sync protocol, queue-parity playbook + pre-Layer checklist, oracle harness workflow, testing strategy, TDD discipline.

## Repo layout

```
backend/
  app/
    main.py            FastAPI app + lifespan
    config.py          Pydantic settings (env-driven)
    languages.py       Per-language plugin registry (LanguageContext)
    anki/              Direct SQLite I/O against collection.anki2 + sync engine
    api/               FastAPI route modules
    audio/             TTS, preprocessing, assembly, cloze TTS
    common/            Cross-cutting helpers (guid generation)
    generation/        Curriculum + story generators, syllabifier, compound breakdown
    llm/               Groq client + cassette system
    media/             In-app media import (Anki media → TT cache)
    models/            Pure domain models
    srs/               FSRS-5 scheduler, queue engine/stats, database mixins
    storage/           ContentStore SQLite repository
  tests/
    anki_oracle/       Subprocess parity-test harness
    cassettes/         Recorded LLM responses
    test_*.py          ~3600 tests, 100% coverage

frontend/
  src/
    routes/            SvelteKit pages incl. /review and /admin/srs
    lib/               Components (DrillCard, Transcript, LessonPlayer, …)
  scripts/
    coverage-gate.ts   Custom Svelte 5 phantom-filter coverage gate
  tests/               Vitest + Playwright

docs/                  Walkthrough + parity history + plans
.claude/rules/         Cross-model project rules
```

## Status

Personal project with one production user (the author, learning Slovene and Norwegian). Anki integration is one deck per language. Not packaged for distribution. If you're an efficiency-focused language learner who'd find value here, the [walkthrough](docs/walkthrough.md) is enough to understand the system and the code is published openly — but there's no installer and no support contract.

## License

No license file yet. Open an issue if you want to use any of this for anything.
