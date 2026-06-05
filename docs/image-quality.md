# Card image quality

How TunaTale picks the image for a new Anki vocab card, and where to take it next.

## The pipeline today

At sync time, `sync_create_new` calls the `_media_fn` closure (`app/api/anki.py`)
per new vocab card. For images the path is:

```
generate_image_query(word, english, source_sentence, grammar)  →  query (str | "" | None)
        │                                                              │
        │  app/anki/media/query_llm.py                                 ▼
        └─ cache: image_query_cache (database.py)        fetch_card_media(..., image_query=…)
                                                                       │
                                                                       ▼
                                                         fetch_pixabay_image(english, query=…)
                                                         app/anki/media/pixabay.py
```

### Layer A — LLM sense-aware queries (`query_llm.py`)

The legacy `build_query` is a ~400-entry hand-curated map keyed on the English
gloss; everything outside it falls back to the raw gloss. New cards (the
lemma-pipeline ones) are exactly the words *not* in the map, and ambiguous glosses
("court", "ring", "bill") pull whatever sense Pixabay's tags happen to favour.

`generate_image_query` asks the project LLM (Groq via `app.state.llm`) for a
concrete, depictable, sense-disambiguated query, feeding the context the card
already carries (**example sentence + grammar**). Three outcomes:

| Result | Meaning |
| --- | --- |
| non-empty `str` | sent to Pixabay verbatim |
| `""` | **skip the image** — abstract/function word, no photo can depict it |
| `None` | LLM unavailable/failed → fall back to legacy `build_query` (never blocks card creation) |

Results are cached per-word in `image_query_cache` (mirrors the lemma-analysis
cache): **one LLM call per new word, never per render.**

**Operational notes**
- Active only when the backend runs with a **live-capable LLM mode** (not `mock`).
  In `mock`/CI it falls back to legacy queries, which keeps tests hermetic.
- Changing the prompt? **Bump `IMAGE_QUERY_MODEL_VERSION`** in `query_llm.py` — it's
  part of the cache key, so old cached queries auto-invalidate.

### Layer B — relevance-first ranking (`pixabay.py`)

The old `score_hit` weighted engagement (likes/views) ~2:1 over query relevance,
so a viral-but-irrelevant stock photo beat the on-target one. Now each overlapping
query tag is worth `10`; engagement is squashed into `[0, 1)` so it can only break
ties, never dominate. `editors_choice` adds a small tiebreak bonus. `per_page` was
widened 20→50 so the ranker sees more candidates per (single) API call.

## Potential next steps

Ranked by expected quality-per-effort. All stay within "free + fast".

### C — multi-source + POS routing (the biggest remaining win)
Route by what the word *is*, instead of forcing every word through Pixabay:
- **Concrete nouns / named things** → try **Wikimedia/Wikipedia lead image** and
  **Openverse** (CC aggregator, no API key) in addition to Pixabay; these are often
  more on-target than Pixabay's stock corpus for literal objects.
- **The `""` (NONE) cases** → instead of *no* image, render a clean **icon/emoji**
  (OpenMoji / Twemoji / Noto Emoji — local, instant, free). A crisp icon for
  "therefore" / a pronoun is clearer than any photo.
- Feed classla **`upos`** (already available from the lemmatizer) into the router so
  the noun/verb/function-word split is structural, not just LLM-inferred.

This needs a small `image_source` abstraction in front of `fetch_pixabay_image`
(or a fallback chain inside `fetch_card_media`). Bigger change — it touches the
media-source layer — but it's where the abstract-word tail finally gets solved.

### D — free generative (for un-photographable words)
For words no stock library depicts well: **Pollinations.ai** (no-key text→image
URL) as a fallback, or **local SDXL-Turbo via MLX** on Apple Silicon (~1–2 s/image,
fully offline). Highest "wow", but adds an external dep / consistency / latency
tradeoff — hold unless A+B+C still disappoint.

### Smaller follow-ups
- **Backfill existing cards.** A and B only affect *newly created* cards. A one-shot
  migration could re-run `generate_image_query` for cards whose current image is a
  known-bad legacy-fallback pick, and re-fetch. (Mirror `link_tt_images.py`'s shape;
  honour the Anki sync envelope.)
- **Spot-check tooling.** A tiny CLI that dumps `(word, english) → generated query`
  for the deck would make it easy to eyeball query quality and catch prompt
  regressions before a sync.
- **Dedup quality.** `used_image_urls` already prevents the *same* URL twice; consider
  perceptual-hash dedup if near-duplicate stock photos become a problem.
