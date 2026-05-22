# Refold — design influence on TunaTale

Reference notes on the [Refold method](https://refold.la/roadmap) (Ethan Hellier + Matt vs Japan, formalized 2020 from the earlier Mass Immersion Approach community). Captures only the parts that directly shape TunaTale's recognition-before-production stance, cloze design, and listen-first acquisition loop. Skip Refold's stage 0 alphabet drills, stage 4 active output guides, and the discord community discussions — they're useful as user resources but don't shape TT's code.

## Why this doc exists

Phase F (function-word cloze cards) is Refold doctrine landing in TT. The "1T sentence" — a sentence where exactly one target word is unknown and everything else is familiar — is Refold's signature card design and the exact shape `make_cloze_text` in `backend/app/srs/function_words.py:55` produces. The per-direction split (Recognition L2→L1 vs Production L1→L2) with `PRODUCTION` deliberately lagged is Refold's "don't output early" rule encoded in the data model. If you're wondering why TT generates passive listening lessons before any production drilling, or why the cloze cards have a single masked word in a real sentence, this is the source.

## The Refold roadmap, in one paragraph

Refold organizes acquisition into stages 0 through 4:

- **Stage 0** — pick a language, set up tools. No actual learning.
- **Stage 1** — build a foundation. Learn the writing/phonetic system, do a pre-made frequency deck of ~1500 most-common words via SRS, start doing passive immersion ("free-flow immersion" — watching content with subtitles in the target language).
- **Stage 2** — build comprehension. Switch to active immersion (consciously analyzing the content), start **sentence mining**: when you encounter a sentence you almost understand, add it to Anki as a cloze card. Refold calls these "1T sentences" — one Target word, everything else known.
- **Stage 3** — build output. Read native content, optional speaking practice.
- **Stage 4** — refine output. Active production, accent work.

The headline rule is **comprehension before production**. You spend hundreds of hours acquiring through input before you try to speak. The 1T sentence is the unit of acquisition.

## The 1T sentence and TT's cloze flow

Refold's [Basic Sentence Mining guide](https://refold.la/roadmap/stage-2/a/basic-sentence-mining/) describes the 1T rule: when mining a sentence, the target word should be the **only unknown** in that sentence. Sentences with 2+ unknowns are too high a cognitive load; sentences with 0 unknowns waste a card. Anki then drills you with the sentence as front, target word revealed on back, in cloze format (`{{c1::word}}`).

TT's Phase F implementation in `backend/app/srs/function_words.py`:

```python
def make_cloze_text(surface: str, source_sentence: str) -> str:
    # wraps the target's surface form with {{c1::...}} inside source_sentence
```

The constraint that makes it 1T: TT only creates a cloze when the target is a **function word** from the curated Slovene list (`SLOVENE_FUNCTION_WORDS`). The surrounding content words are by construction the ones the user is already studying (they were the curriculum's target collocations). So a sentence like "*Grem v trgovino z mamo*" with `v` as the target reads as a 1T sentence by design — `grem`, `trgovino`, `mamo` are vocabulary the user is in.

**Where TT scopes down from Refold**: Refold sentence-mines any unknown word from immersion. TT only mines function words because content words go through the picture+audio Fluent Forever flow (see `docs/fluent-forever.md`). The data model (`card_type` enum) supports the full Refold extension — just expand the predicate from `is_function_word` to "any word the user hasn't seen."

**Where TT diverges further**: Refold expects you to mine sentences from native content you chose. TT mines from LLM-generated stories the system chose for you. PRD §5 makes this trade explicit — personalization in exchange for surrendering content choice to the AI. For function words (high-frequency, omnipresent) this barely matters; for content-word mining it would matter more, which is one reason that extension hasn't shipped.

## Recognition before production

Refold's strongest position: **do not attempt to produce the language until you can comprehend it**. The reasoning: production with insufficient input ossifies errors into your mental model; comprehension trains your ear; output emerges naturally when comprehension is solid. Stages 1–2 are pure input; output starts in stage 3+ after hundreds of hours of immersion.

TT's expression of this is structural, in the SRS data model (`walkthrough.md` PART 12.1):

```
DirectionState (per direction, independently scheduled):
  - RECOGNITION (L2 → L1) — "what does this word mean?"
  - PRODUCTION  (L1 → L2) — "what's the word for this?"
```

Both directions go through FSRS independently. A card can be at REVIEW for recognition (stable, high R) while still at LEARNING or even NEW for production. The frontend `DrillCard` renders the production card front as `{{Image}}` alone (Wyner via Refold via TT) — there's no L1 word handed to the user; they have to recall the L2 from semantic content.

In practice this lets recognition lead production by weeks or months — which is the Refold pacing. The PRD doesn't enforce this; the data model just makes it natural.

## Free-flow vs. active immersion

Refold stage 1 ("free-flow immersion") is passive watching with no analysis. Stage 2 ("active immersion") is consciously breaking apart what you understood vs. didn't. The hand-off happens when your comprehension is high enough that effortful analysis is worth the cognitive cost.

**TT's expression**: the listen-first acquisition loop has both modes wired in.

- **Free-flow** = lesson playback. The user listens to KEY_PHRASES → NATURAL_SPEED → SLOW_SPEED → TRANSLATED. No interaction required. The full lesson MP3 supports this; the section MP3s let the user re-listen to one section without scrubbing.
- **Active** = the `/review` page with transcript. Words colored by SRS status, click-to-translate, click-to-untrack. The user explicitly engages with what they did and didn't catch.

The same content powers both modes. The user toggles between them (no algorithmic gating — PRD §7.4 explicitly says "User-controlled mode switching based on context and preference"). This matches Refold's stance that mode choice is the user's, not the tool's.

## The implicit-grade move (where TT goes beyond Refold)

Refold's sentence mining still requires explicit Anki review: you grade each card Again/Hard/Good/Easy. This is the standard SRS interface.

TT's `/listen` auto-grade (PART 15.2) is the divergence: when the user finishes a lesson, every collocation in the LEARNING/RELEARNING/REVIEW pool that appeared in the NATURAL_SPEED transcript gets an auto-Good grade — implicit positive feedback because the user heard it in context and didn't tap "translate this phrase." Help-seeking behavior (translate, slowdown, untrack) is the negative-feedback channel.

This is the PRD §7.3 "Implicit Spaced Repetition System" applied. Refold doesn't have an equivalent — they assume you'll do your daily Anki reviews explicitly. TT's bet is that **using the words in audio comprehension** is a stronger acquisition signal than **clicking Good on a flashcard**.

Both directions still grade through FSRS the same way (`schedule()` takes a rating and returns the new state). The novelty is the *source* of the rating, not the algorithm.

## What TT does NOT inherit from Refold

- **Stage 0 phonetic-alphabet drilling.** Refold front-loads IPA / writing-system mastery. TT relies on the user's separate phonics deck (Slovene Vocabulary's `IPA` field + the user's existing Basic-notetype phonics cards). See `docs/fluent-forever.md` "Sound system first."
- **The "1500-word frequency deck" prescription for Stage 1.** TT is curriculum-driven, not frequency-driven. A user without a foundation deck imports their own seed via `import_seed`.
- **Output-free stages.** Refold says no output for hundreds of hours. TT's production-direction cards drill output from day 1 — which is a deliberate divergence. The Slovene user wanted both directions trained in parallel; PRD §7.4 leaves the input/output split to user preference rather than enforcing Refold's gating.
- **Native-content immersion as the input source.** This is the central divergence (and the reason TT exists). Refold says go watch Korean dramas with target-language subtitles. TT says we'll generate stories using your existing vocabulary. The personalization-for-authenticity trade is explicit in PRD §13.
- **Monolingual transition (Stage 2C).** Refold encourages switching from bilingual to monolingual dictionaries at mid-intermediate. TT's translation field is always L1.
- **The Refold Discord / community feedback loop.** Out of scope for a personal tool.

## Where the influence ends

Refold is a community-built roadmap; TT is one person's tool. They overlap most heavily on the **acquisition-first, output-deferred, sentence-as-unit** principles that Refold codified from the earlier Mass Immersion Approach (which itself drew on Krashen's comprehensible input hypothesis). TT takes those principles, hands the input-curation problem to an LLM, and turns the explicit-Anki-grade interface into an implicit-help-signal one.

The clearest litmus: if a Refold user looked at TT's `/review` page, they'd recognize the colored transcript and the cloze cards immediately. If they looked at the `/listen` auto-grade behavior, they'd say "wait, where's the rating button?" — and that's where TT goes its own way.

## Reading

- [Refold Roadmap (entry point)](https://refold.la/roadmap) — the canonical method overview, free to read.
- [Sentence Mining (Refold Library)](https://refold.la/roadmap/library/sentence-mining) — the 1T sentence rule and its rationale.
- [Basic Sentence Mining (Stage 2A)](https://refold.la/roadmap/stage-2/a/basic-sentence-mining/) — the specific card design.
- [Learning Words with Anki (Refold Library)](https://refold.la/roadmap/library/learning-words-with-anki) — the SRS-side workflow.
- [Stage 2: Build Comprehension](https://refold.la/roadmap/stage-2/overview/) — the active-immersion phase, where the cloze flow happens.
- [Refold/Mass Immersion Approach: Spanish 4-6 Month Update](https://deusexvita.medium.com/refold-mass-immersion-approach-spanish-4-6ish-month-update-ee266aa6f1e9) — a user account showing the method in motion.

Underlying research:

- Krashen's Input Hypothesis (the "i+1" formula) — Refold's pedagogical foundation. PRD §6 cites this directly.

## Cross-references

- `backend/app/srs/function_words.py` — `is_function_word`, `make_cloze_text`, the Slovene curated list.
- `backend/app/anki/sync.py::create_cloze_note` — pushes cloze rows to Anki's built-in Cloze notetype.
- `backend/app/models/srs_item.py` — `SyntacticUnit.card_type` ('vocab' | 'cloze'), `DirectionState` per direction.
- `backend/app/api/srs.py::listen` — the implicit-grade loop (auto-Good on heard, no signal on missed).
- `walkthrough.md` PART 12.1 — two-direction SRS items.
- `walkthrough.md` PART 15.5 — Phase F function-word clozes.
- `walkthrough.md` PART 20 — current cloze pipeline (TTS, sentence translation, Anki round-trip).
- `docs/fluent-forever.md` — the picture+audio model for content words (the half of the card system Refold doesn't cover).
- `docs/lingq.md` — sibling comprehensible-input lineage (Refold built on the same Krashen foundations as LingQ but from a Japanese-immersion subculture rather than Kaufmann's polyglot direction).
