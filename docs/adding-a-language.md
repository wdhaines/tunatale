# Adding a new language to TunaTale

*Rewritten 2026-07-11. The original version of this doc was a plan for wiring Norwegian; Norwegian has since been wired end-to-end (2026-06/07) and the architecture moved to a central registry, so this is now a description of the actual process with Norwegian as the worked example. Tagalog still has scaffolding from `micro-demo-0.0` plus an in-tree style guide.*

## The registry is the process

Adding a language means creating **a plugin package under `backend/app/plugins/languages/<code>/`** plus a per-language dependency group. The plugin package is the single wiring point: its `__init__.py` calls `register()` with a `LanguageConfig` that bundles all per-language facets. There is no other switchboard: `app/main.py` resolves everything through the registry accessors (`get_language` / `get_preprocessor` / `get_deck_name` / `get_tts_voice` / `get_lemmatizer_type` / `get_syllabifier` / `get_vocab_notetype` / `uses_compound_word_breakdown` / `get_variant_separator` / `get_morphology_profile` / `get_style_notes` / `get_function_words_path`), and the sync/render paths bundle it all via `resolve_language_context(code, settings) → LanguageContext`.

This is **enforced**: `backend/scripts/check_language_literals.py` (runs in `./test.sh` and the CI backend job) fails on any hardcoded language literal (`"sl"`/`"no"`, `Slovene`/`Norwegian`, `classla`/`stanza`, `*-Neural` voices) in `backend/app/**` outside the allowlisted plugin modules (`backend/tests/language_literals_allowlist.txt`). If wiring your language requires an `if code == "xx"` anywhere in core code, the gate will catch it — add a registry facet instead. Rationale and remaining seams: `docs/language-plugin-hardening.md`.

## Required touch-points

| # | Touch-point | File | Notes |
|---|---|---|---|
| 1 | `Language` factory | `backend/app/models/language.py` | `@classmethod` returning code, names, script, `tts_voice_map`. Norwegian: `Language.norwegian()`, code `"no"` |
| 2 | TTS voice map | inside the factory | `narrator` is always English (reads titles + L1); `female-1/2`, `male-1/2` want distinct voices; duplicate if EdgeTTS only offers one per gender |
| 3 | Plugin package | `backend/app/plugins/languages/<code>/__init__.py` | Calls `register("xx", LanguageConfig(...))`. Imports the preprocessor from `preprocessor.py` in the same package. Passes `style_notes` and `function_words_path` from `data/` subdir. |
| 4 | Preprocessor | `backend/app/plugins/languages/<code>/preprocessor.py` | One-method protocol (`app/audio/preprocessing/base.py`); pass-through is a fine start, add EdgeTTS-quirk replacements as you find them by ear |
| 5 | Style guide | `backend/app/plugins/languages/<code>/data/style.md` | Copy the shape of `sl`/`no` style files: family + confusable-language guards, orthography, LLM failure modes, good/bad examples |
| 6 | Function words | `backend/app/plugins/languages/<code>/data/function_words.json` | POS-first policy: `pos` (UPOS tags), `include` (curated surfaces), `exclude`, `clozes_only_verbs`. `build_function_word_list.py` can bootstrap a corpus-derived list |
| 7 | Per-language dep group | `backend/pyproject.toml` `[dependency-groups]` | Add `<code> = ["<engine>==<version>"]` and append to `[tool.uv] default-groups`. CI opts out with `--no-group <code>`. |
| 8 | Runtime config | `.env` | Multi-language mode: add the code to `settings.database_urls` (per-language TT db); requests select the language via the `X-TT-Language` header. Single-language fallback: `TARGET_LANGUAGE` + `DATABASE_URL` + `ANKI_DECK_NAME` |

Audition voices with:

```bash
uv run python -c "import edge_tts, asyncio, json; \
  print(json.dumps(asyncio.run(edge_tts.list_voices()), indent=1))" | grep -B2 -A2 '<code>-'
```

## Optional facets (all registry fields — see `LanguageConfig`)

- **`syllabifier`** — an onset-maximization profile name resolved to a `syllabify_<lang>_word` function in `backend/app/generation/syllabify.py`. Unlocks Pimsleur syllable-level backward buildup in KEY_PHRASES; languages without one fall back to Slovene onset rules.
- **`compound_word_breakdown`** — `True` routes the Pimsleur word breakdown through compound/morpheme-aware segmentation instead of per-syllable buildup. Norwegian is the only user today (`backend/app/generation/norwegian_breakdown.py`: frequency-ranked compound splitting, s-joint/geminate handling, closed-class stem stoplist — design in `docs/archive/bp-brief-segmenter-homographs-overlap.md`). A new compounding language would add its own breakdown module and dispatch via this flag in `section_builder.py`.
- **`lemmatizer_type`** — morphological engine per language (`classla` for Slovene, `stanza` for Norwegian, `lowercase` default). A property of the *language*, not the process: multi-language mode runs both in one process. The heavy engines live in per-language default dependency-groups (`slovene`, `norwegian`), so a plain `uv sync` installs and keeps both; CI drops them with `--no-group slovene --no-group norwegian` to stay PyTorch-free.
- **`vocab_notetype`** — the TT-managed Anki notetype cards are minted into, defined in `backend/app/anki/vocab_notetype.py` (`SLOVENE_VOCAB`, `NORWEGIAN_VOCAB` are the references). Adding a notetype bumps `col.scm` — read `.claude/rules/anki-sync.md` first.
- **`variant_separator`** — for languages whose card fronts list alternate spellings of one word (Norwegian `mot, imot`); makes `card_surface_variants` split them.
- **`morphology_profile`** — story-prompt drill block (`"slavic"` = case/dual tagging for Slovene).

## What Norwegian actually needed (the empirical answer)

Beyond the table above, wiring Norwegian for real surfaced:

- **Recognition-only deck**: the imported Norwegian deck has no production cards; the direction model handles this **structurally** (directions are whatever rows exist — never assume a sibling exists; see memory/commits around `directions[Direction.…]` guards). Don't add a per-language "directions" registry field — absence of rows is the source of truth.
- **Stanza over classla**: classla silently no-ops on Norwegian; `lemmatizer_type="stanza"` with a protobuf≥5.29 override on Python 3.14.
- **Compound breakdown quality is human-gated**: the segmenter's stoplist and golden splits are verified by ear (`uv run python -m app.generation.breakdown_preview <words>`); an agent can build machinery but not the linguistic oracle.

If you find a touch-point this doc doesn't name, that's a doc bug — update this file and, if core code needed an `if code == …`, treat it as a missing registry facet.

## The Tagalog lineage

`micro-demo-0.0` is the Tagalog audio prototype. Its ~1000-line TTS preprocessor is the long pole for reviving Tagalog. Touch-points 1, 2, 7, 8 are quick; porting the preprocessor quirks and writing the style guide + function-word config are the work.

## Cross-references

- `docs/language-plugin-hardening.md` — why the registry + literal-gate exist, remaining seams.
- `docs/pimsleur.md` — what the syllabifier / breakdown buy pedagogically.
- `docs/refold.md`, `docs/fluent-forever.md` — what the function-word list enables (cloze cards).
- `walkthrough.md` PART 2 (Language model), PART 6 (audio pipeline / preprocessor seam).
