# Learning Modes — design reference

How a TunaTale learner actually uses the app, broken into distinct **modes** (postures), with the design decisions made for each. This is the durable "what we decided and why" doc that grounds the lesson-page work; the actionable build plan for the first target (Read v1) is tracked separately (see *Status & build order* at the bottom).

This refines PRD §7.4 ("Learning Modes") with concrete decisions reached in design discussion. Where a decision diverges from or sharpens an influence doc, that's noted inline.

## Why this doc exists

The lesson page (`/c/[curriculumId]/l/[lessonId]`) was trying to be several things at once with no clear framing. Naming the modes — and which mode owns which interaction — is the precondition for cleaning it up. The PRD's three-phase loop (**Explicit prep → Audio immersion → Spaced reinforcement**) maps onto the concrete learner postures below.

## The mode map

In the user's stated frequency order:

| # | Mode | Posture / context | Lineage | Today |
|---|------|-------------------|---------|-------|
| 1 | **Review** (daily anchor) | Clear the SRS queue every day. Audio-first drills. | Pimsleur GIR → FSRS; Fluent Forever card design | ✅ `/review` route |
| 2 | **Listen** (immersion) | Hands-free / car. Play the 4-section lesson straight through. Glanceable. Passive (Refold *free-flow*). Cards created implicitly. | Pimsleur audio-first + Refold free-flow + LingQ listen-first + implicit grade | ⚠️ exists, crude (AudioPlayer + Mark-as-Listened) |
| 3 | **Read** (study / cleanup) | Dense transcript. Active immersion. Fine-grained add/remove/grade of SRS by tapping words/phrases. BDT-flavored L2↔L1. | LingQ word-status transcript + Refold active immersion/1T + BDT reception | ⚠️ exists, **first build target** |
| – | **Generate / Manage** | Setup: create curriculum, regenerate a day. Not a learning mode. | — | ✅ (lesson page Regenerate + `/` library) |
| – | **Produce / BDT write-back** | Output practice; translate-back; spaced re-encounter. | BDT steps 5–6, PRD §7.4 output mode | ❌ future (`bdt.md` "Phase G") |

## Mode 1 — Review (daily anchor)

The habit: pop in (phone-first), clear the review queue. This is the SRS, and it lives at `/review`, not on the lesson page. Decisions:

- **Recognition splits into two buckets: listening-recognition and reading-recognition.** You can recognize a word by eye without being able to by ear, so the SRS should schedule those **independently** (separate FSRS schedules, like recognition vs. production today). Gold standard = recognize by ear with no visual support; if you can only read it, you earn reading-recognition credit, *not* listening credit. *(Data-model change — a modality axis on `DirectionState`; grows the Anki-parity surface. Deferred, scoped when Review is built.)*
- **Hands-free mode (à la Glossika).** Filters the queue to what's doable with no touch (listening-recognition; listen-and-repeat). **Mobile: hands-free is the default**, switchable to a fuller mode. **Desktop: freely interleave** listening + reading, the way recognition/production interleave today.
- **Grading with no hands.** The ideal grade signal is a **target-language control phrase** (PRD §8.1). Interim acceptable fallback = Glossika-style *show-then-repeat* with no explicit grade (treat as Good). **The implicit-grade problem is shared with the Listen bulk-add** (below) — solve both with one mechanism.
- **Production is not hands-free yet.** Eventually: production via a target-language audio prompt (Pimsleur-style). For now the hands-free filter is listening-recognition only.
- **Homepage:** if reviews are due, "Review (N due)" is the top suggested next action. (`QueueStatsWidget` already exists.)

## Mode 2 — Listen (immersion)

Prototyped in `micro-demo-0.0/` + `micro-demo-0.1/`. Straight-through playback of the four Pimsleur sections (KEY_PHRASES listen-and-repeat → NATURAL_SPEED → SLOW_SPEED → TRANSLATED), then a single **"I listened"** action (click or voice command) that triggers a **bulk review + creation** pass. Decisions:

- **Whole-lesson, not per-section.** One "done"; the system doesn't care how you got the exposure. You hit Listen when ready.
- **"I listened" credits the listening-recognition bucket.** Words you heard that are already tracked get an auto-Good on *listening-recognition* (not reading, not production). Same implicit-grade mechanism as hands-free Review.
- **Review-vs-create split.** Anything already in SRS → reviewed (auto-Good). Anything not in SRS → a **candidate for creation**.
- **Creation is throttled into a staged "build."** Never create 50+ cards from one listen. Over multiple listens you gradually acquire (most of) the lesson's vocab. Throttle reuses the existing new-card cap (`daily_new_cap`, queue-parity rule 12) + the word-learning **introduction gates** (`word-learning-state-machine.md`, FSRS Layer 65). *Which* candidates get created should be pedagogically ranked — CEFR level vs. grammar topic, and word frequency — not FIFO. *(Frequency tooling partially exists: `build_function_word_list.py`, `import_seed`. CEFR/grammar-topic gating is likely new.)*
- **Correction flow** — auto-Good is too generous (you don't catch everything by ear), so listening ends with a non-hands-free **"check your work"** step (or it's the gateway into Read if you skipped it). **Decision: the correction flow *is* the Review flow, scoped to this lesson** — a *lesson-scoped review session* that reuses the existing drill UI + FSRS grading. Grades there overwrite the provisional auto-Good with real signal; skipping leaves the optimistic Good in place. Requires a new lesson-scoped entry/filter into the review flow, and it reshapes the lesson page's post-listen action from "Mark as Listened" → "Check your work / review this lesson's N words."

## Mode 2.5 — Subtitles / supplements (Listen scaffolding; future)

A Language-Reactor-style **fallback ladder** over the audio, most→least challenging: sound only → sound + fully-blurred subtitle → sound + *some words* blurred → sound + full L2 subtitle → sound + L1 translation. Decisions:

- **Blur is a test, not a crutch — blur the words you KNOW, keep unknown words visible.** Blurring strips reading support off known words to check you can still get them *by ear with less context*; the unknown words stay legible because they're the legit scaffolding to parse the line. (Built on the existing LingQ word-status colors, inverted intent: blur = known, show = unknown.)
- This makes the rung a clean **listening-recognition** signal: a blurred known word you still catch = success; miss = demote. Feeds the buckets model and the Listen correction flow — i.e. the "scaffolding-as-grade" idea, landed here rather than in Review.
- **Technical prerequisite for synced reveal:** a render-time **timing manifest**. `backend/app/audio/renderer.py` already measures each phrase's real duration to compute pauses, so per-phrase start/end offsets are derivable — they're just not emitted. Static rungs (pick one for the session) need no timing data; karaoke-style synced reveal does.
- **Open shaping:** on-demand-per-line reveal vs. whole-session rung; whether the rung consumed feeds a per-line implicit grade in v1 or is a pure comprehension aid. **Most feature-heavy + fuzziest mode; gated on the timing manifest. Future.**

## Mode 3 — Read (study / cleanup) — FIRST BUILD TARGET

Where you make **fine-grained decisions about what you understand**, per word and per phrase. The most similar mode to what exists today: the LingQ-style transcript with word-status colors and per-item SRS actions. Decisions:

- **Read v1 = reception + fine-grained SRS curation only. Production exercises are deferred.**
- **BDT is the intended approach for intensive reading.** The full BDT loop the user wants (eventually): see L2 only → **type** an L1 translation → **compare** to a reference → **write the L2 back** with the original hidden → self-check the match. *That whole typed loop is **production** → deferred to Read phase 2.* What lands in **v1 is the interlinear *display*** (per-*line* L1 under each L2 line, cover-one-side friendly, **no typing**), **toggleable**. Note: the redesign had replaced the old per-line translation toggle with a per-*word* **Gloss** toggle; the interlinear is a *new, distinct* per-line toggle (`showInterlinear` → `transcriptScenes.ts` `translatedText` → `.line-interlinear`), complementary to Gloss.
- **Keep the full current per-item action set** (add base card, grade Good, ignore lemma, untrack, mark known, un-mark known, reset, create inflection cloze, drag-to-create phrase, add off-transcript phrase). Cut/refine *as the user tests*, not up front. The actions-placement work is about making them **discoverable and organized** (today they're hover/focus-gated in `Tooltip.svelte` — a mobile discoverability problem).

### Read phase 2 (deferred)
The typed BDT loop (type L1 → compare → write-back L2 → match), blur/cloze for production, typing-for-production. These are the production layers of Read.

## Mode — Generate / Manage

Setup posture, infrequent: create a curriculum (`/` library), regenerate a day (lesson page Regenerate button). Not a learning mode; called out only because the Regenerate action lives on the lesson page and competes for attention with the learning actions.

## Mode — Produce / BDT write-back (future)

PRD §7.4 output mode + BDT steps 5–6. The passage-level production loop. Unbuilt; see `docs/bdt.md` "Phase G" scope hooks.

## Status & build order

1. **Read v1** — first build target. Pure frontend (no Anki/sync/backend). Makes the lesson page's Read mode coherent: header/hierarchy fix, Listen↔Read mode distinction, organized + discoverable actions, toggleable interlinear display. Detailed implementation plan handed to the implementer; advised + audited per the Big-Pickle delegation pattern.
2. **Listen** rebuild — the "I listened" bulk pass + staged creation + the lesson-scoped correction-review handoff. Backend work (creation ranking, lesson-scoped review filter, implicit-grade mechanism).
3. **Review buckets** — split recognition into listening/reading schedules; hands-free mode; control-phrase grading. Data-model + Anki-parity work.
4. **Subtitles** — timing manifest first, then the blur-known ladder.
5. **Read phase 2 / Produce** — the typed BDT loop and production exercises.

## Cross-references

- `docs/prd.md` §6 (three-phase loop), §7.3 (implicit SRS), §7.4 (learning modes), §8.1 (control phrases).
- `docs/pimsleur.md` — the 4-section audio that Listen plays.
- `docs/lingq.md` — the word-status transcript that Read is built on.
- `docs/refold.md` — free-flow (Listen) vs. active (Read) immersion; recognition-before-production.
- `docs/bdt.md` — the interlinear/translate-back method behind Read's intensive-reading direction; "Phase G" for the deferred production loop.
- `docs/fluent-forever.md` — Review card design (image-only production front).
- `.claude/rules/anki-queue-parity.md` — rule 12 (`daily_new_cap`), the buckets work's parity surface.
- `word-learning-state-machine.md` (memory + plan) — the introduction gates the Listen creation throttle reuses.
