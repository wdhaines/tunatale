# BDT (Lampariello) — design influence on TunaTale

Reference notes on Luca Lampariello's Bi-Directional Translation (BDT) method, a beginner-targeted language-learning system built around translating short texts back and forth between L2 and L1. Unlike the other four influence docs in this folder, **BDT is partially aspirational for TT** — about half of its six-step loop has direct code analogues; the rest is a candidate direction the rest of this doc maps out.

The full BDT course is behind a paywall ([sales page](https://www.lucalampariello.com/master-language-learner/), 11 modules / 23 sub-modules). This doc reflects only what's available publicly. The owner of this repo has the full course and is the source of record for course-specific details.

## Why this doc exists

The PRD (`docs/prd.md`) lists "comprehensible input" and "adaptive pacing" as core principles, and the existing influence docs cover the input side (LingQ, Refold, Pimsleur) and the SRS side (Fluent Forever). BDT is the **production side** that TT has been mostly silent on. The other docs explain why TT shows you a transcript and grades implicit listening; BDT is the lens for what TT could add when a learner is ready to actively produce the language. This doc names the shape of that influence so future scope decisions on production-direction features have a clear pedagogical reference rather than getting reinvented.

It's also a hedge against the worst kind of doc rot: if you wait until something is built to write the influence doc, you forget what influenced you. Naming the influence early lets the implementation be checked against the source.

## The method, in one paragraph

You take a short text in your target language with native audio. You analyze it intensively (listen + read until the shape is clear). You annotate its phonetics (pitch contours, stress, intonation). You review it through varied modalities (read with audio, listen alone, listen with L1 translation, read aloud). Then you translate it into your L1 in writing. Then — *days* later, the L2 mostly faded — you orally translate your own L1 version back to L2 without looking at the original. Then you write that L2 reconstruction down. You compare your reconstruction to the original; the gaps are your learning material. Materials are short and you go deep; the [public critique](https://forum.lingq.com/t/luca-lampariello%E2%80%99s-method-of-bi-directional-translation/32568) of BDT is precisely that it's depth-per-passage, not throughput.

The "bi-directional" is the back-half: translate L2→L1, then L1→L2, then compare. The gap between the two L1→L2 attempts (yours and the original) is where attention concentrates.

## The six steps (publicly documented)

The clearest public summary is on the LingQ forum:

| # | Step | What you do | Direction |
|---|---|---|---|
| 1 | Intensive analysis | Listen + read short text with audio. Understand the shape. | — |
| 2 | Phonetic analysis | Annotate text with pitch contours, stress patterns, intonation. | — |
| 3 | Smart review | Cycle through modalities: read+listen → listen alone → listen+L1 → read aloud. | — |
| 4 | Direct translation | Translate the L2 text into L1, in writing. | L2 → L1 |
| 5 | Reverse translation (oral) | Without looking at the L2 original, orally translate your L1 version back to L2. | L1 → L2 |
| 6 | Reverse translation (written) | Write that L2 reconstruction down. | L1 → L2 |

The spacing between (4) and (5) is the critical move: it has to be long enough that you can no longer recall the original L2 from short-term memory. Lampariello's specifics on the spacing window are course content; the public sources don't pin it.

## What TT does that overlaps

| BDT step | TT equivalent today | Shipped? |
|---|---|---|
| 1. Intensive analysis | Lesson playback + transcript with word-status coloring (PART 15.3). Same shape: audio-anchored text. | ✅ |
| 2. Phonetic analysis | IPA field on the Slovene Vocabulary notetype + Forvo audio per word. **No prosody annotation, no pitch contours.** TT punts on prosody to the user's separate phonics deck. | ⚠️ Partial |
| 3. Smart review (varied modalities) | KEY_PHRASES → NATURAL_SPEED → SLOW_SPEED → TRANSLATED (PART 6, also `docs/pimsleur.md`). The varied-modality *spirit* is there; the *cycle order* differs. | ⚠️ Different shape |
| 4. Direct translation L2 → L1 | Transcript translation button + the TRANSLATED section (every L2 line followed by narrator L1). User reads / hears the translation; doesn't *produce* it. **Read mode v1 adds a toggleable *interlinear* L2↔L1 display (cover-one-side, no typing) as the reading-side seed of the BDT loop — see `docs/learning-modes.md`.** | ⚠️ Reception only (interlinear display landing in Read v1) |
| 5. Reverse translation L1 → L2 (oral) | Production-direction SRS cards (PRODUCTION L1→L2): the front is `{{Image}}` only, the back is L2 + audio. User mentally produces L2 from a semantic prompt. **But it's per-word, not per-passage, and there's no oral-output check.** | ⚠️ Per-word, not passage |
| 6. Reverse translation L1 → L2 (written) | **Not implemented.** No write-your-L2 flow exists. | ❌ |

So TT covers BDT's reception half (steps 1–4) at lesson granularity and BDT's production half (5) at flashcard granularity. The passage-level production loop (5 + 6, together) is unbuilt.

## The depth-vs-throughput trade BDT makes

The forum's "snail's pace" critique is the load-bearing constraint: BDT trades input volume for depth-per-passage. One short text gets six exhaustive passes. A typical 30-minute LingQ session might cover 1,000 words of input; a BDT pass over the same time budget might cover 100.

TT today sits on the LingQ / Pimsleur side of this trade: every day, a new lesson, ~300 L2 words across KEY_PHRASES + dialogue, lots of breadth. There's no current path where a learner says "I want to *really know* this one paragraph cold."

This is where BDT influence could go in TT — a "BDT mode" wrapped around an existing lesson:

- **A "deep" toggle on a lesson** that exposes steps 4, 5, 6 as explicit exercises rather than passive playback.
- **A scheduled re-encounter** where the same passage resurfaces N days later (where N is long enough that recall is genuinely empty) prompting the L1→L2 reconstruction.
- **An L1→L2 diff** showing where the user's written reconstruction matches and diverges from the original L2.

None of this exists today. It would be a feature, not a refactor.

## Where BDT influence could land — concrete scope hooks

Naming these so they can be referenced from a plan or a feature flag if/when scope opens:

- **Phase G: BDT writing exercise.** New endpoint `/api/srs/translate-back?lesson_id=…` that returns a lesson's NATURAL_SPEED L1 translation as the prompt; user submits an L2 reconstruction; backend diffs against the original L2 and surfaces token-level deltas. Data model already supports this — `Lesson.sections[NATURAL_SPEED]` and `Story.l1_translation` are populated; we just don't expose them as an exercise.
- **Spaced re-encounter scheduler.** A new SRS event class for *passages*, not just collocations. Today's `tt_revlog` (PART 19) is per-direction; a passage-revlog would be a different table or a `review_kind=5` (passage-review) extension.
- **Pitch / prosody annotation on key phrases.** Step 2 of BDT. The Slovene Vocabulary notetype has an `IPA` field but no pitch-contour field; adding one would be a notetype schema change (`col.scm` bump, full Anki upload — see `.claude/rules/anki-sync.md`).
- **Per-passage "depth" toggle in the UI.** Decoupled from BDT specifically — would also serve the "I want to drill *this lesson* harder than the curriculum thinks I need to" case.

If any of these gets built, the BDT entry in this doc should move from the "What TT does NOT inherit" table to the "What TT does inherit" one.

## What TT does NOT inherit from BDT

These are out of scope today, ordered by likelihood of eventually landing:

- **Step 6 — written L1→L2 reconstruction.** Plausible future feature (Phase G above). Mid-likelihood.
- **Step 5's oral output check.** Speech recognition is hard; the PRD §8.1 limits speech to recognizing target-language *control phrases* ("Más despacio") rather than open production. Low-likelihood without a different SR approach.
- **Step 2's pitch contours / prosody annotation.** Possible if pronunciation accuracy becomes a focus. Low-likelihood given Slovene's relatively transparent prosody for English speakers.
- **The "short text you chose yourself" framing.** TT picks the text via story generation. Importing a user-supplied passage for BDT-style treatment is technically possible (it's just a `Curriculum.lessons[i].story.natural_speed_l2`) but the LLM-generated-content path is the central design.
- **Beginner-only positioning.** Lampariello's BDT is sold for absolute / false beginners. TT is more flexible — the existing Slovene user is well past A2 — so BDT-shaped features would need to work at higher levels than the source method targets.

## Reading

Public sources only (the paid course is the source of record for course-specific mechanics):

- [BDT Sales Page (Luca Lampariello)](https://www.lucalampariello.com/master-language-learner/) — marketing-pitch view: claims, audience, course structure.
- [BDT — Bidirectional Translation Course (Luca Lampariello)](https://www.lucalampariello.com/bdt-37/) — course landing page.
- [Luca Lampariello's method of Bi-Directional Translation (LingQ forum thread)](https://forum.lingq.com/t/luca-lampariello%E2%80%99s-method-of-bi-directional-translation/32568) — **most concrete public account of the six steps.** Also contains the "snail's pace" critique on depth-vs-throughput.
- [Lampariello with some details on how he found his BDT method (language-learners forum)](https://forum.language-learners.org/viewtopic.php?t=18384) — Luca's own account of the method's origin.
- [The first 3 months of Dutch study using Luca Lampariello's BDT method (YouTube)](https://www.youtube.com/watch?v=a28-s0aWCFs) — user account of running the method in practice.
- [Mastering Language Learning: The Bi-Directional Translation Method (YouTube summary, transcript via gist.ly)](https://gist.ly/youtube-summarizer/mastering-language-learning-the-bi-directional-translation-method) — useful only as overview; light on specifics.

If the user (this repo's owner) extracts concrete prescriptions from the paid course that change TT's direction, update this doc with the specific scope hooks they unlock — but cite only paraphrase, not the course's verbatim content.

## Cross-references

- `docs/learning-modes.md` — where BDT lands in the mode map: the **interlinear display** is in Read v1; the **typed translate-back loop** (type L1 → compare → write-back L2 → match) is deferred to Read phase 2 / Produce mode ("Phase G" above).
- `docs/prd.md` §7.3 (implicit SRS) — adjacent territory. BDT's reverse-translation could supply *explicit* production signal the way `/listen` supplies implicit reception signal.
- `docs/pimsleur.md` — the four-section lesson is the existing varied-modality scaffold; BDT's step 3 would overlap heavily.
- `docs/lingq.md` — sibling input-side influence. LingQ is high-throughput / low-depth; BDT is the opposite. TT today is closer to LingQ.
- `docs/refold.md` — Refold also defers output; BDT brings output forward, on passages the user has already absorbed.
- `docs/fluent-forever.md` — Card Type 2 (production card with image-only front) is the closest existing analogue to BDT's L1→L2 direction, at flashcard granularity.
- `walkthrough.md` PART 12.1 (two-direction SRS items) — the data model that would support a BDT-style passage exercise.
- `walkthrough.md` PART 15.4 — the transcript translation button (the reception side of step 4).
