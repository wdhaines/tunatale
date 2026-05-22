# LingQ — design influence on TunaTale

Reference notes on LingQ (Steve Kaufmann's web app, launched 2007) and the broader [Linguist method](https://www.thelinguist.com/about/) Kaufmann developed across 20 languages. Captures only the parts that directly shape TunaTale's word-status model, transcript UI, and listen-first acquisition loop. Skip LingQ's content marketplace, social features, and statistics dashboards — they're orthogonal to TT.

## Why this doc exists

When you look at TT's `/review` transcript and the colored word spans, you are looking at a port of LingQ's text reader. When you see the `untrack` button on a tooltip ("click to untrack" — `frontend/src/lib/components/Tooltip.svelte:26`), you are looking at LingQ's "move to Known" interaction renamed. The status cycle — `unknown → new → learning → review → known` — is LingQ's status progression mapped onto TT's FSRS states. If you're wondering why TT has a text transcript at all (Pimsleur didn't), or why words have status colors (Anki doesn't), this is the source.

## The Kaufmann method, in one paragraph

Steve Kaufmann's claim, repeated across hundreds of YouTube videos and his book *The Linguist*: language acquisition is primarily a function of **hours of comprehensible input** in content that interests you, not a function of grammar drilling, classroom hours, or active speaking practice. LingQ exists to turn arbitrary text (articles, podcasts with transcripts, ebooks) into a frictionless input medium: click any unknown word to look it up, mark words as you learn them, accumulate hours.

The headline claim that drives the UX: **track everything you learn**. Every word in a LingQ lesson has a status (New, Recognized, Familiar, Learned, Known, Ignored). The status is the unit of progress. Reading 30 minutes of Spanish is measured in words-moved-from-Learning-to-Known, not minutes elapsed.

## The status cycle in TT

LingQ's status enum is a forward-only progression: New → 1 → 2 → 3 → 4 → Known (or Ignored). TT collapses this onto its FSRS states, which already encode acquisition progress through stability/difficulty:

| LingQ status | TT state (`backend/app/api/srs.py:614`) | What it means |
|---|---|---|
| Unknown (not yet in deck) | `unknown` (transcript only — not a DB state) | The word exists in the lesson but no SRS row tracks it |
| New | `new` | First encounter; SRS row exists, never graded |
| Learning (statuses 1–3) | `learning`, `relearning` | Below graduation, on learning steps |
| Known | `review` | Graduated to long intervals |
| Learned (very high status) | `known` | Untracked but remembered — see below |
| Ignored | `suspended` (admin-toggled) | Excluded from queue but kept in DB |

The transcript's `WordSpan.svelte` colors each word by `srs_state`. The CSS classes (`coll-bg-new`, `coll-bg-learning`, `coll-bg-review`, `coll-bg-known`, `coll-bg-unknown`) live in `frontend/src/lib/Transcript.svelte:252` — direct visual port of LingQ's white/blue/yellow/no-highlight scheme.

## The `untrack` action

LingQ's headline action is "mark as Known" — once a word stops needing review, you tag it Known and it stops appearing in your active vocabulary count (it still counts in your lifetime total). The word is no longer surfaced in lessons as a click-to-translate target.

TT's mirror is `SRSDatabase.untrack_collocation(row_id)` (`backend/app/srs/database.py:872`), exposed via `POST /api/srs/items/{item_id}/untrack` (`backend/app/api/srs.py:738-739`). Calling it sets `state='known'` on every direction of the collocation; queue-stats and queue-build then skip `state='known'` rows.

Critically, `known` is **terminal but reversible**: the row still exists, FSRS state is preserved, you can re-add it via `/admin/srs` if you decide it wasn't actually known. This matches LingQ's behavior — "Known" isn't deletion.

The tooltip language ("click to untrack") in `Tooltip.svelte:26` is a deliberate LingQ idiom — the user's first encounter is on the transcript, where they click a word they recognize and confirm "yes, I know this," and TT moves it out of their active load.

## The listen-first acquisition loop

LingQ's workflow: open a lesson with text + audio synced word-by-word → read while listening → click each unknown word for a translation popup → mark words as you learn them → finish, move to the next lesson. Vocabulary is acquired *during* immersion, not in a separate flashcard session.

TT's expression of this is in `walkthrough.md` PART 15 ("Listen-First Acquisition Loop, Phases B–F"). The `/listen` endpoint:

1. Receives the lesson the user just played.
2. Tokenizes the NATURAL_SPEED transcript.
3. For each word the user *already* knew (existing SRS row in LEARNING/RELEARNING/REVIEW), auto-grades a Good review — "you heard this in context and didn't ask for help."
4. For each *new* word with `card_type='cloze'` matching `is_function_word`, creates a new SRS row.

This is LingQ's loop with a key inversion: LingQ's signal is **click for help** (= "I don't know this"), TT's signal is **didn't click for help** (= "I do know this"). LingQ rewards looking words up; TT rewards making it through without help. Same input shape, opposite sign.

This is the **implicit SRS** of the PRD §5: help signals as feedback, no explicit grade button required.

## What's mined from LingQ's UX

- **Status colors on every word** — see `WordSpan.svelte` and `Transcript.svelte`.
- **Click-to-untrack** — the "I already know this" action, in TT mirrored as `untrack`.
- **Word-as-unit-of-progress** — TT's badge counts and `/admin/srs` page list collocations by status, just like LingQ's "Known words" counter.
- **Translation on demand** — the transcript's translation button (PART 15.4) is LingQ's word-popup at sentence granularity.
- **Off-transcript phrase entry** (Phase E) — adding a phrase the user heard but doesn't see on the page; analogue of LingQ's "create a new LingQ from selection."

## What TT does NOT inherit from LingQ

Listed to make scope decisions explicit:

- **The five-level status granularity (1/2/3/4 inside Learning).** LingQ tracks how confident you are within Learning. TT collapses this into FSRS's stability/difficulty, which is finer-grained but invisible. LingQ users sometimes miss the explicit slider.
- **Lifetime word counts and gamification.** LingQ heavily promotes "you know 14,832 words" as a stat. TT has no such number. PRD §4 explicitly excludes gamification.
- **The shared content library.** LingQ's central feature is its multi-language catalog of imported lessons (Mini Stories, podcasts, articles). TT generates its own content via the LLM; there's no marketplace.
- **Text-first immersion.** LingQ supports audio but text is primary — you read while listening. TT inverts: audio is primary, transcript is supplementary. This is the central divergence and the reason TT exists. See `docs/pimsleur.md` for the audio-first commitment.
- **Manual coding of unknown sentences.** LingQ users sometimes do "LingQ sentence" — explicitly track an entire phrase, not just a word. TT's collocation model (3–5 word chunks tracked as a unit) does this automatically via `app/srs/transcript.py`, but it's curriculum-driven, not user-curated.
- **The dual-track New/Recognized split for non-Latin scripts.** LingQ has separate beginner workflows for languages like Mandarin where character recognition itself is a stage. TT's Slovene scope doesn't need this.

## Where the influence ends

LingQ assumes the user supplies their own input (uploads an article, picks a YouTube transcript). TT generates the input itself. Once the input exists, the in-lesson UX converges. Once the lesson is over, TT's path goes back to a Pimsleur-style audio review loop while LingQ users go pick another article. **TT is LingQ's interface on top of a Pimsleur-style audio generator**, integrated with an Anki-style SRS backend (see `docs/fluent-forever.md`).

## Reading

- [Steve Kaufmann Language Learning Method (LingQ)](https://www.lingq.com/en/learn-languages-like-steve-kaufmann/) — the official LingQ summary.
- [The Linguist (Steve Kaufmann's site)](https://www.thelinguist.com/about/) — Kaufmann's own framing, predates LingQ.
- [The Best Way to Learn a New Language (Steve Kaufmann, Medium)](https://medium.com/the-linguist-on-language/the-best-way-to-learn-a-new-language-f1af92d756db) — his core method article.
- [How Steve Kaufmann uses comprehensible input to learn languages (Learn English Pod, 2023)](https://learnenglishpod.com/2023/10/11/how-steve-kaufmann-uses-comprehensible-input-to-learn-languages/) — accessible third-party summary.
- Steve Kaufmann's YouTube channel ([lingosteve](https://www.youtube.com/channel/UCez-2shYlHQY3LfILBuDYqQ)) is the primary source — hundreds of videos, the method is described across many of them rather than in one canonical place.

## Cross-references

- `backend/app/srs/transcript.py` — tokenization + per-word SRS state lookup that feeds the transcript.
- `backend/app/srs/database.py::untrack_collocation` — the LingQ "Known" action.
- `backend/app/api/srs.py::untrack_item` — the endpoint.
- `frontend/src/lib/WordSpan.svelte`, `Tooltip.svelte`, `Transcript.svelte` — the LingQ-style UI.
- `walkthrough.md` PART 15 — the full listen-first acquisition loop.
- `walkthrough.md` PART 4.4 — per-word SRS tracking (the data model behind the colors).
- `docs/refold.md` — the next-generation comprehensible-input community that built on Kaufmann's foundations.
- `docs/pimsleur.md` — what TT keeps from before the immersion phase.
