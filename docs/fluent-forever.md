# Fluent Forever — design influence on TunaTale

Reference notes from Gabriel Wyner's *Fluent Forever* (Harmony, 2014). Captures only the parts that directly inform TunaTale's data model, card design, and acquisition loop. Skip the book's chapters on classroom courses, language-specific resources, and the appendices — they're useful as reading material but don't shape TT's code.

## Why this doc exists

TT's Slovene Vocabulary notetype, the listen-first acquisition loop (Phases A–E), and Phase F's function-word cloze spike all trace back to specific prescriptions in this book. When a question comes up like "should the production card show an image or the translation?" or "why don't we generate a separate pronunciation card?" — this is the source. Where TT diverges from Wyner, that's a deliberate scope decision, also documented below.

## The three card types per word (Chapter 4)

Wyner prescribes **up to three cards per word**, training different aspects of comprehension:

| Card | Front | Back | Trains |
|---|---|---|---|
| 1 — Comprehension | L2 word (with audio) | Picture + IPA + grammar + L1 translation | "What does this word mean?" |
| 2 — Production | Picture only | L2 word + audio + grammar + L1 | "What's the word for this image?" |
| 3 — Spelling | IPA + audio | L2 word spelled out | "How do you spell this word?" |

Three usage tiers:
- **Intensive Track** (CJK + Arabic): all three cards.
- **Normal Track** (most languages, including Slovene): cards 1 and 2 only. Skip spelling.
- **Refresher Track** (intermediate speakers): card 1 only.

### How TT's notetype maps

`backend/app/anki/notetype.py:103-107` defines the Slovene Vocabulary notetype with **two templates**:
- `Recognition` (ord=0): front = `{{Audio}} {{Slovene}}`, back = `{{Image}} {{English}} {{Grammar}} {{Note}}` → Wyner's Card Type 1.
- `Production` (ord=1): front = `{{Image}}` *only*, back = `{{Audio}} {{Slovene}} {{English}} {{Grammar}} {{Note}}` → Wyner's Card Type 2.

That's the Normal Track. We've **deliberately skipped Card Type 3** (spelling). Slovene's spelling is phonetic enough that the user is unlikely to need spell-from-pronunciation drilling. Adding it later would mean a third template, which bumps `col.scm` and forces a full AnkiWeb upload per `.claude/rules/anki-sync.md`.

### Why production card front is the image alone

This is the part that bit us when sidro had no image (see commit conversation history): the entire production card front is `{{Image}}`. **No image means a blank front.** Wyner's argument: an L2 word linked to a mental image acquires faster than one linked via L1 translation. The image is not decoration; it's the prompt.

Empty `Image` fields on imported Anki notes break the production-card half of every word. That's the load-bearing reason for the proposed (not-yet-built) backfill-media tool.

## Essential facts vs. bonus points per card

For each card, Wyner distinguishes **essential** (must remember) from **bonus** (extra connections that strengthen memory):

| Card | Essential | Bonus points |
|---|---|---|
| 1 | Picture, Pronunciation, Gender | Personal Connection, Other Words |
| 2 | Pronunciation, Gender | Spelling, Personal Connection, Other Words |
| 3 | Spelling | Gender, Personal Connection, Other Words |

TT currently surfaces: picture (`{{Image}}`), pronunciation (`{{Audio}}`), gender (`{{Grammar}}` — when populated), and the source sentence (`{{Note}}` — populated for some auto-adds). Personal Connection and Other Words are out of scope — they require user-curated content TT can't generate.

## Cloze cards for grammar and function words (Chapter 5)

The translation-card model breaks down for words that don't have clean L1 equivalents — function words (prepositions, pronouns, conjunctions, particles), inflected forms, and grammatical constructions. Wyner's prescription: **cloze deletion in a real sentence**.

His canonical example (the `dernier` card, p. 99):

> **Front**: *Le \_\_\_ dictateur argentin condamné à la prison à perpétuité.* [image of an Argentine dictator]
> **Back**: **dernier** ("last")

The cloze front gives:
- A real-context sentence (passive vocabulary boost for surrounding words too).
- An image that anchors the situation.
- The target word blanked — forces recall of the *form*, not just the meaning.

This is exactly the pattern Phase F implements (commit pending). When `/listen` sees a Slovene function word from the curated list (in `backend/app/srs/function_words.py`), it creates a `card_type='cloze'` collocation carrying the NATURAL_SPEED phrase as `source_sentence`. `sync_create_new` routes cloze items through `create_cloze_note` (`sync.py`), targeting Anki's built-in Cloze notetype with `{{c1::word}}` markup. No image required (the sentence context is the prompt).

**Wyner's grammar-pattern variant**: same cloze mechanism, but applied to content-word inflections (e.g., a Slovene noun in the genitive case). Phase F's scope is function words only for now; the data model (`card_type`, `source_sentence`) already supports the grammar-pattern extension without further migrations.

### Why not use Anki Cloze for everything?

Wyner uses cloze cards as a *complement* to the three vocab cards, not a replacement. Content words still get picture+audio cards because images anchor meaning better than sentence context for concrete concepts ("apple" → picture of an apple is faster than a sentence with "apple" blanked). Function words and grammar patterns lack image anchors, which is why cloze is their natural form.

## The 625-word foundation (Chapter 6)

Wyner argues for starting with a **frequency-curated foundational list** (his "First 625 Words" in Appendix 5) before specializing. Coverage estimates from the book:

| Words known | % of spoken comprehension | % of reading |
|---|---|---|
| 1,000 | ~85% | ~75% |
| 2,000 | ~90% | ~80% |
| 5,500 | ~95% | ~90% |
| 12,500 | ~99% | ~95% |

After the foundation, **specialize** via thematic vocabulary books (Wyner recommends Barron's) for topics the learner actually needs.

**Implication for TT**: TT's auto-add path is curriculum-driven, not frequency-driven. A user with a comprehensive imported Anki backlog of common words will find `/listen`'s auto-add produces few new rows because the GUID conflict path silently merges with existing cards (see Phase F gotcha in `enchanted-floating-crescent.md`). This is fine — the user already has the foundation; auto-add catches edge-case vocabulary from each lesson.

If a user starts *without* a foundation deck, Wyner's argument is to import a 625-list first. TT can support this via `import_seed` + a curated word list.

## Sound system first (Chapter 3)

Wyner front-loads **phonetics** before grammar or vocabulary:

1. **Train your ears**: minimal-pair tests (Wyner sells per-language pronunciation trainers).
2. **Train your mouth**: produce sounds accurately.
3. **Train your eyes**: recognize spelling patterns.

The user has a separate set of Slovene phonics cards in their Anki deck (on the "Basic" notetype, with IPA + audio — files like `voda`, `okno`, `vesel`, `beseda`, etc.) that implement exactly this. Those are *not* TunaTale-generated. TT's role is to leave them alone — the `d306311` fix (`migrate_v15_to_v16`) specifically prevents TT from inventing phantom production directions for single-template Basic-notetype cards.

## Memory principles (Chapter 2)

Five principles from Wyner's "Five Principles to End Forgetting." TT's FSRS implementation handles most of them mechanically, but the principles are worth knowing:

1. **Make memories more memorable** — multisensory associations (image + audio + word). This is why both Image and Audio fields are essential.
2. **Maximize laziness** — minimize total review effort by leveraging spacing.
3. **Don't review, recall** — active retrieval > passive re-reading. (FSRS enforces this via the SRS interface.)
4. **Wait, wait! Don't tell me!** — give yourself time to struggle before revealing the answer. (Anki's reveal-on-click serves this.)
5. **Rewrite the past** — every recall act rewrites the memory; spacing strengthens the rewrite.

The listen-first acquisition loop (Phase A: auto-add on listen) is consistent with #1: encountering a word in real audio context creates a richer initial memory than seeing it on a flashcard cold. The cloze cards (Phase F) double down — the surrounding sentence is part of the encoding.

## Source/input strategies (Chapter 6)

Wyner's recommendations for passive input once the foundation is in place:

- **Reading**: pick books you'd read anyway (Harry Potter translations, detective novels, etc.). One book ≈ 300–500 new words via context inference, without a dictionary.
- **TV (not film, not comedy)**: TV series get easier after episode 2 once you know the characters; films don't have that ramp; comedy depends on wordplay that's often impenetrable to learners.
- **Subtitles**: target-language subtitles okay, English subtitles defeat the purpose (you're reading not listening).
- **Audiobook + book combo**: ideal first-novel approach.

These are orthogonal to TT's design — TT generates listening curricula, but doesn't recommend books/TV. Worth knowing for user advice.

## What TT does NOT inherit from Fluent Forever

Listed to make scope decisions explicit:

- **Personal-connection cards** — Wyner suggests writing your own memorable associations on each card. Out of scope; requires per-user UI.
- **Monolingual dictionary integration** — Wyner switches from bilingual to monolingual at intermediate. TT's translation field is always L1.
- **Pronunciation trainer** — Wyner's commercial product. TT relies on Forvo audio + the user's existing phonics deck.
- **Multisearch web tool** — Wyner's one-click research workflow. TT auto-populates translations via the LLM, images via Pixabay, audio via Forvo, all server-side.
- **Self-directed writing** — Wyner recommends writing example sentences and getting them corrected. Adjacent to TT's transcript-add-phrase flow but no native-speaker correction loop.
- **Card Type 3 (spelling)** — see "How TT's notetype maps" above. Deliberately skipped for Slovene.

## Reading list

If you actually need to consult the book:

- **Chapter 2** (Upload): memory principles.
- **Chapter 3** (Sound Play): phonetics-first argument; not directly used by TT but useful for understanding the user's separate phonics deck.
- **Chapter 4** (Word Play): the three card types, special cases (multiple definitions, synonyms, category words, easily-confounded images). **Most directly applicable to TT's Slovene Vocabulary notetype.**
- **Chapter 5** (Sentence Play): cloze cards for grammar and function words. **Direct source for Phase F.**
- **Chapter 6** (The Language Game): vocabulary strategy, input strategies.
- **Toolbox / The Galleries**: card design templates Wyner uses himself; reference if redesigning the notetype.

The PDF used during Phase F planning lived at `/tmp/fluent_forever.pdf` (not committed). If you need to re-download:

```
https://ia600608.us.archive.org/26/items/FluentForeverHowToLearnAnyLanguageFastAndNeverForgetIt/Fluent%20Forever%20_%20How%20to%20Learn%20Any%20Language%20Fast%20and%20Never%20Forget%20It.pdf
```

## Cross-references

- `enchanted-floating-crescent.md` — the listen-first acquisition loop plan (Phases A–F).
- `backend/app/anki/notetype.py` — the Slovene Vocabulary notetype.
- `backend/app/srs/function_words.py` — Phase F's curated function-word list.
- `backend/app/anki/sync.py` — `create_note` and `create_cloze_note`, the two card-creation paths.
- `docs/anki-parity-layers.md` — Layer 24 (Phase C: recency-prioritized new bucket) for how new cards reach the user; Layer 29 (Phase F: cloze branching) once committed.
