# Pimsleur — design influence on TunaTale

Reference notes on the Pimsleur method (developed by Dr. Paul Pimsleur in the 1960s, now sold as 30-minute audio courses). Captures only the parts that directly shape TunaTale's audio pipeline, lesson structure, and pedagogical pacing. Skip the marketing copy and the cultural-tips fillers — they're not what TT inherits.

## Why this doc exists

TT's lesson model is the Pimsleur format. Open `backend/app/models/lesson.py` and you'll find a `SectionType` enum with `KEY_PHRASES`, `NATURAL_SPEED`, `SLOW_SPEED`, `TRANSLATED` — the four-section shape of a Pimsleur lesson, with Slovene-specific tweaks. The `section_builder` module's docstring says "Pimsleur-style syllable-level backward buildup sequence." If you're wondering why the lesson structure looks the way it does, this is the source.

The first TunaTale prototype (`micro-demo-0.0`, Tagalog) was built explicitly to reproduce a Pimsleur-style lesson with multi-voice TTS. Everything after that traces back to that initial Pimsleur-shaped artifact, plus refinements.

## The four core principles (per Pimsleur's own documentation)

Pimsleur's lessons rest on four principles. Three of them are load-bearing in TT:

### 1. Anticipation — prompt for recall before reveal

The user is asked to produce a phrase (in their head or aloud) *before* the recording supplies it. This is active recall, not passive listening.

**TT's expression**: the TRANSLATED section. Each L2 phrase is followed by a narrator-voice English translation. In playback order: L2 → pause → L1. The pause is the anticipation window. `section_builder.py::build_translated_section` constructs this; the pause length is configured via `NaturalPauseCalculator` (`backend/app/audio/pause_calculator.py`) which targets ~500 ms (natural / translated) plus proportional pauses for KEY_PHRASES L2 and 600 ms for SLOW_SPEED.

### 2. Graduated Interval Recall — the original spacing schedule

Pimsleur discovered (1967) that re-presenting a word at exponentially increasing intervals (5 sec → 25 sec → 2 min → 10 min → 1 hr → 5 hr → 1 day → 5 days → 25 days → 4 months → 2 years) moved it from short-term to long-term memory faster than any other tested schedule. This is the historical ancestor of modern SRS.

**TT's expression**: not directly. TT uses **FSRS-5** (§9 of `walkthrough.md`), the modern descendant. Pimsleur's specific intervals don't appear anywhere in `app/srs/fsrs.py` — FSRS computes intervals from stability/difficulty/retrievability, not a fixed schedule. The lineage is real but the algorithm is two generations newer.

### 3. Core Vocabulary — high-frequency words first

Pimsleur drills 500 high-frequency words because those words carry the majority of any conversation (a finding that converges with the [Zipf-distribution argument in Fluent Forever's "First 625 Words"](fluent-forever.md#the-625-word-foundation-chapter-6)).

**TT's expression**: not directly. TT is curriculum-driven (story generation chooses target collocations per day from the user's deck), not frequency-driven. If a user starts without a foundation, they can seed via `import_seed` from any frequency list — see Fluent Forever doc on this seam.

### 4. Organic Learning — listening + speaking first, reading later

Pimsleur lessons are audio-only by design. Reading and writing come after the audio foundation.

**TT's expression**: the `/review` queue is audio-first by default (recognition direction plays L2 audio first, asks the user to recall L1). The transcript is text-second — you see the words only after the audio plays. §8.6 of `walkthrough.md` covers the transcript component.

## The lesson format

Pimsleur lessons are ~30 minutes structured as: introduction → new vocabulary in a dialogue → drilling each new phrase with anticipation → integrating the new phrases into varied sentences → a wrap-up review. TT expresses this as named sections, the `SectionType` enum in `backend/app/models/lesson.py` (walkthrough §4.3, §6.7):

| Section | What | Pimsleur analogue |
|---|---|---|
| `KEY_PHRASES` | Each target phrase: L2, its English, then the backward buildup | Pre-teaching the new vocabulary |
| `NATURAL_SPEED` | The story dialogue at normal conversational pace | The new dialogue, presented whole |
| `SLOW_SPEED` ("Enunciated") | The same lines with a pause after every word, not a slower rate | Hearing where the words part |
| `TRANSLATED` / `EN_TRANSLATED` ("English After" / "English Before") | Every L2 line with its English, in either order | The integration / check-comprehension phase |
| `SLOW_TRANSLATED` / `SLOW_EN_TRANSLATED` | The enunciated line with its English, in either order | The same, enunciated |
| `AFFIX_DRILL` (Cebuano) | One affix pair modelled on a few roots, then each form asked for from its English, with a pause sized for the answer | Anticipation drilling: "How do you say …?", pause, answer |

`backend/app/generation/section_builder.py` builds the story sections from the `Story` produced by the LLM. The affix drill is built from hand-checked tables with no model call (`app/generation/affix_drill.py`, walkthrough §6.12), and it is the closest thing in TT to Pimsleur's anticipation drill: the learner hears the English, says the form into the pause, and hears it said. Each section is rendered to its own audio file, which is what the player's section pills and hands-free sequence select between.

## Backward buildup pronunciation

When a Pimsleur lesson introduces a new word with three or more syllables, the narrator builds it backward: final syllable alone → final two syllables → all three. This trains pronunciation in the order the mouth has to produce it (the ending shape, then back-chained to the start).

**TT's expression**: `backend/app/generation/syllabify.py` performs onset-maximization syllabification through each language's own phonotactics, then `section_builder.py::build_word_breakdown_spans` generates the syllable-level sequence for KEY_PHRASES (walkthrough §6.7). For a three-syllable Slovene word like `do-bro-doš-li`, the buildup is `dôš-li → bro-doš-li → do-bro-doš-li`. The narrator-voice scaffolding ("Listen and repeat") wraps it.

This is one of the parts of the Pimsleur method that's mechanically reproducible and TT reproduces it, for every language: a language without its own syllabifier falls back to a generic one, which can cut wrongly (walkthrough §3.4). Two refinements came from listening to whole lessons. A word the learner already knows well is not built up at all, in key phrases or drill (`app/generation/known_buildups.py`), and within one affix drill a root is built up once, after which its forms add only their affix (`mo, molakaw`).

## Audio-first all the way down

Pimsleur's strongest claim is that audio-first acquisition transfers to spoken fluency better than text-first study. The PRD (`docs/prd.md` §5–6) extends this with the three-phase loop:

1. **Explicit prep** (web, before listening) — see the target collocations.
2. **Audio immersion** (mobile / car) — the Pimsleur-style lesson.
3. **Spaced reinforcement** (SRS) — implicit-feedback grading via help signals.

TT's audio pipeline (§7 of `walkthrough.md`): Azure/Gemini TTS, per-voice gain, then one streamed Opus encode per section. It exists because the audio is the artifact. Everything else (transcript, translation, SRS) is scaffolding around the audio.

## The micro-demo lineage

`micro-demo-0.0/` (Tagalog) is the proof-of-concept audio engine. Its README leads with "Multi-Voice Support: Different Filipino and English voices for natural dialogue" and "Structured Lessons: Automatic section detection (Key Phrases, Natural Speed, Slow Speed, Translated)." That's the Pimsleur format, on tape. The production rebuild kept the format and generalized the language layer.

`walkthrough.md` Appendix A.2, "What survived from the prototypes", explicitly calls out:

- Pimsleur 4-section format (KEY_PHRASES, NATURAL_SPEED, SLOW_SPEED, TRANSLATED)
- EdgeTTS rate limiting (200 ms delay between requests)
- Hexagonal architecture / Protocol-based ports

The Pimsleur format is in the "do not break this" set.

## What TT does NOT inherit from Pimsleur

- **30-minute fixed length.** TT lessons scale to content (KEY_PHRASES count + dialogue length). A short lesson might be 8 minutes; a dense one 25. The PRD §7.1 explicitly says "scale content to available learning time rather than fixed lesson durations."
- **Static content.** Pimsleur lessons are pre-recorded once. TT generates per-day stories tailored to the user's deck via the LLM (`backend/app/generation/story.py`).
- **The original interval schedule.** TT uses FSRS-5, not Pimsleur's 1967 intervals.
- **Pure listening (no transcript).** Pimsleur's audio courses ship with workbooks but the courses themselves are audio-only. TT shows a transcript with word-status coloring on the `/review` page — the user can see the words. This is a LingQ-style addition; see `docs/lingq.md`.
- **No SRS integration with the user's existing vocabulary.** Pimsleur is closed: their words, their lessons. TT integrates with the user's Anki deck and surfaces words the user is actively studying. This is the central design move that distinguishes TT.

## Reading

- [Pimsleur Language Programs (Wikipedia)](https://en.wikipedia.org/wiki/Pimsleur_Language_Programs) — overview, history, methodology.
- [Why Graduated Interval Recall Is the Key to Mastering a New Language](https://www.pimsleur.com/blog/why-graduated-interval-recall-is-the-key-to-mastering-a-new-language) — Pimsleur's own writeup of the spacing schedule.
- [Memory and Language Learning: How Pimsleur Helps You Retain What You Learn](https://www.pimsleur.com/blog/memory-and-language-learning-how-pimsleur-helps-you-retain-what-you-learn) — connecting graduated-interval recall to the four core principles.
- [The Pimsleur Language Method (Art of Memory)](https://artofmemory.com/blog/the-pimsleur-language-method/) — third-party summary of the four principles.

## Cross-references

- `backend/app/models/lesson.py` — `SectionType` enum.
- `backend/app/generation/section_builder.py` — builds the four-section structure.
- `backend/app/generation/syllabify.py` — Slovene syllabifier driving backward buildup.
- `backend/app/audio/pause_calculator.py` — pause timing for the anticipation phase.
- `walkthrough.md` §4.3 (Lesson Structure) and §7 (Audio Pipeline).
- `walkthrough.md` Appendix A.2 — what survived from the prototypes.
- `docs/fluent-forever.md` — companion influence (memory principles, card design).
- `docs/lingq.md` — what TT adds *on top of* the Pimsleur audio (word-status transcript).
- `docs/refold.md` — what TT does instead of Pimsleur's static-content limitation.
