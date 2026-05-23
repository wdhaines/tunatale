# Adding a new language to TunaTale

Slovene is the language wired end-to-end today. Tagalog has scaffolding from `micro-demo-0.0` plus an in-tree language-style guide. Norwegian is the next planned full target. This doc names the five places you touch to wire a new L2 and the order in which to do them.

## The five touch-points

| Touch-point | File / location | Required? | Effort |
|---|---|---|---|
| 1. `Language` factory method | `backend/app/models/language.py` | Yes | 5 min |
| 2. EdgeTTS voice map | `Language.tts_voice_map` | Yes | 10 min (audition voices) |
| 3. `TextPreprocessor` implementation | `backend/app/audio/preprocessing/<code>.py` | Yes (can be pass-through) | 5 min – several days |
| 4. LLM language-style guide | `backend/app/generation/language_styles/<code>_style.md` | Yes | 1–2 hours of writing |
| 5. Wire it into `app/main.py` | `main.py:48-58` (lifespan) | Yes | 2 min |

Three optional touch-points unlock more sophisticated features:

| Optional | File / location | What it unlocks |
|---|---|---|
| Syllabifier | `backend/app/generation/syllabify.py` | Pimsleur-style syllable-level backward buildup in KEY_PHRASES (see [docs/pimsleur.md](pimsleur.md)) |
| Function-word list | `backend/app/srs/function_words.py` | Phase F cloze cards for grammar-bearing words (see [docs/refold.md](refold.md), [docs/fluent-forever.md](fluent-forever.md)) |
| Anki notetype | `backend/app/anki/notetype.py` | Bidirectional sync against a language-specific notetype. The Slovene Vocabulary notetype is the reference; new languages either reuse it or define their own |

## Walkthrough — the required five

### 1. Add a `Language` factory method

`backend/app/models/language.py` already has `slovene()` and `english()` as `@classmethod` factories. Add a third:

```python
@classmethod
def norwegian(cls) -> Language:
    return cls(
        code="nb",                       # ISO 639-1; nb=Bokmål, nn=Nynorsk
        name="Norwegian",
        native_name="norsk bokmål",
        script="latin",
        tts_voice_map={
            "narrator":  "en-US-GuyNeural",      # always English — narrator reads titles + L1
            "female-1":  "nb-NO-PernilleNeural",
            "female-2":  "nb-NO-IselinNeural",
            "male-1":    "nb-NO-FinnNeural",
            "male-2":    "nb-NO-FinnNeural",
            "female":    "nb-NO-PernilleNeural", # legacy keys for older curricula
            "male":      "nb-NO-FinnNeural",
        },
    )
```

The `narrator` role is always L1 (English in this codebase) — it speaks section titles and L1 translations. The numbered `female-1/2`, `male-1/2` roles get distinct voices so the multi-speaker dialogue feels real; if EdgeTTS doesn't give you two distinct voices per gender for your target language, duplicate the one you have (as Slovene does — both `female-1` and `female-2` are `PetraNeural`).

Audition voices via:

```bash
uv run python -c "import edge_tts; import asyncio; \
    asyncio.run(edge_tts.list_voices())" | grep -E '(nb|no)-NO'
```

### 2. Implement the `TextPreprocessor` protocol

The protocol is one method (`backend/app/audio/preprocessing/base.py:10-14`):

```python
@runtime_checkable
class TextPreprocessor(Protocol):
    def preprocess(self, text: str, section_type: SectionType) -> str: ...
```

For Norwegian (mostly phonemic spelling, EdgeTTS handles it cleanly), the same pass-through implementation Slovene uses is fine to start. Create `backend/app/audio/preprocessing/norwegian.py`:

```python
"""Norwegian-specific text preprocessing for TTS synthesis."""

from __future__ import annotations
from app.models.lesson import SectionType


class NorwegianPreprocessor:
    """Norwegian text preprocessor (pass-through; reserved for future transforms)."""

    def preprocess(self, text: str, section_type: SectionType) -> str:
        return text
```

When you discover TTS pronunciation bugs in the wild (e.g. EdgeTTS reads `kj` as English `ky` instead of Norwegian `ç`), this is where you'd add the workaround — often a `text.replace("kj", "<phoneme>")` for the affected section types. The `micro-demo-0.0` Tagalog preprocessor had several hundred lines of these rules; expect Norwegian to need a handful as you find them.

### 3. Write the LLM language-style guide

`backend/app/generation/language_styles/sl_style.md` is the reference. Copy it to `nb_style.md` and rewrite each section. The headings to keep (from `sl_style.md`):

- Language family + closely-confused languages (LLMs frequently produce Croatian when asked for Slovene; Norwegian Bokmål gets confused with Danish and Swedish — guard against this aggressively).
- Spelling / orthography conventions specific to the target language.
- Common LLM failure modes for this language and how to instruct around them.
- A short "good vs. bad" example block — copy the shape of the Slovene one.

Without this file, `app/generation/story.py`'s prompt assembly falls back to language-agnostic instructions and content quality degrades sharply. The Slovene style guide is ~120 lines; budget similar.

### 4. Wire it into `app/main.py`

`backend/app/main.py:48-58` currently hardcodes Slovene:

```python
language = Language.slovene()
...
preprocessor=SlovenePreprocessor(),
```

For Norwegian, this becomes a switch on settings or env. The simplest move (you can refactor to plugin discovery later):

```python
language_code = settings.target_language  # add to Settings; default "sl"
language, preprocessor = {
    "sl": (Language.slovene(), SlovenePreprocessor()),
    "nb": (Language.norwegian(), NorwegianPreprocessor()),
}[language_code]
```

Then `cp ../.env.example .env && echo "TARGET_LANGUAGE=nb" >> .env` to switch. Watch out for the Anki side — `settings.anki_deck_name` defaults to `"0. Slovene"`; you'll want a Norwegian deck name too.

### 5. Smoke test

```bash
cd backend
uv run pytest tests/test_main.py -k norwegian      # if you wrote a test
uv run uvicorn app.main:app --reload               # then hit /api/curriculum/generate
```

A working curriculum + first lesson generated + audio rendered is the bar. If the audio sounds wrong, fix the preprocessor (touch-point 3). If the content sounds inauthentic, fix the style guide (touch-point 4).

## Walkthrough — the optional three

### Syllabifier (unlocks KEY_PHRASES backward buildup)

The Slovene syllabifier (`backend/app/generation/syllabify.py`) does onset-maximization based on Slovene phonotactics. Norwegian needs its own — the syllabification rules differ (Norwegian has consonant clusters Slovene doesn't, and vice versa). Without a syllabifier, KEY_PHRASES falls back to whole-word audio without buildup, which still works but loses the Pimsleur pronunciation-training move.

Recommended approach: write `syllabify_norwegian_word(word) -> list[str]`, then patch `section_builder.py:54, 67` to dispatch on `Language.code`. Make `syllabify` a per-language registry rather than a Slovene-only call.

### Function-word list (unlocks Phase F cloze cards)

`backend/app/srs/function_words.py:14` defines `SLOVENE_FUNCTION_WORDS` as a `frozenset[str]` of ~150 lemmas. For Norwegian, add `NORWEGIAN_FUNCTION_WORDS` and extend `is_function_word(lemma, language_code)` to dispatch on language code (it already takes the code as a parameter).

`backend/app/srs/build_function_word_list.py` is a CLI generator that produces a list from corpus data. It currently emits the Slovene list; generalize the script to take a language code if you want a corpus-derived starting point. Otherwise hand-curate from a frequency list of Norwegian closed-class words (~200 candidates, manually filtered down to the high-frequency ones — see Phase F's commit history for the curation methodology).

### Anki notetype

The Slovene Vocabulary notetype (`backend/app/anki/notetype.py:103-107`) has language in its name and field labels. You have three options:

- **Reuse it** as-is and stuff Norwegian content into the "Slovene" field. Works for testing but mislabels the data. Cheap.
- **Add a parallel "Norwegian Vocabulary" notetype** with the same field shape and a different `mid`. Cleanest. Bumps `col.scm` (full Anki upload required — see `.claude/rules/anki-sync.md`).
- **Rename the existing notetype** to "Vocabulary" with language-agnostic field labels. Lowest long-term debt, highest one-shot migration cost.

Sync logic doesn't care which notetype you pick as long as the field shape matches and the extractor (`backend/app/anki/sqlite_reader.py`) can locate the L2 and L1 fields.

## The Tagalog lineage

`micro-demo-0.0` is the Tagalog audio prototype. The production rebuild generalized everything language-specific *out* of the core path:

- `Language` enum (`micro-demo-0.0/tunatale/core/models/language.py`) was hardcoded with TAGALOG / ENGLISH / SPANISH. Production replaced it with the `@classmethod` factory pattern above.
- The Tagalog TTS preprocessor (`micro-demo-0.0/tunatale/core/utils/tts_preprocessor.py`, ~1000 lines) became the `TextPreprocessor` protocol with a stub Slovene implementation. Adding Tagalog back means porting that preprocessor's accumulated EdgeTTS-quirk workarounds into a `TagalogPreprocessor` class.
- Tagalog still has its style guide in `backend/app/generation/language_styles/tl_style.md` — the LLM-content side never lost the Tagalog plumbing.

So if you ever come back to Tagalog: touch-points 1, 2, 5 are quick (~30 min). Touch-point 3 is the long pole — porting the micro-demo preprocessor. Touch-point 4 is already done.

## Order of operations for Norwegian

A reasonable sequence that lets you ship value at each step:

1. **Day 1**: touch-points 1, 2, 3 (pass-through), 5. Generate a curriculum without a style guide, render a lesson, listen. Catch obvious EdgeTTS pronunciation bugs in step 3 as you find them.
2. **Day 2**: touch-point 4 (style guide). Regenerate; content quality should jump.
3. **Week 1+**: optional touch-points as needs surface — syllabifier when KEY_PHRASES sounds flat, function-word list when you want Phase F cloze coverage, notetype when you wire Anki sync.

If you find a sixth touch-point this doc didn't name, that's a bug — update this doc and the language-plugin protocol.

## Cross-references

- `docs/pimsleur.md` — what the syllabifier and KEY_PHRASES section get you.
- `docs/refold.md`, `docs/fluent-forever.md` — what the function-word list enables (cloze cards).
- `docs/lingq.md` — the word-status transcript UI is language-agnostic; it just needs an SRS DB with rows in your L2.
- `walkthrough.md` PART 2.1 — `Language` and `tts_voice_map` in the domain model.
- `walkthrough.md` PART 6 — full audio pipeline including the preprocessor seam.
- `walkthrough.md` PART 10 — the prototype→production migration table; "Language support" row names the data-driven factory pattern.
