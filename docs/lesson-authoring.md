# Lesson Authoring — Story-JSON round-trip (design)

Status: **implemented** (2026-07-01) — `app/storage/lesson_io.py` + `GET /api/story/{id}/source` /
`POST /api/story/import`; the build step is the module-level `build_lesson_from_story`
(`app/generation/story.py`). One deliberate deviation from decision #4's mechanism: the exact
Story JSON is persisted inside `generation_metadata["story"]` (stashed by the build step itself)
rather than a new `lessons.story_json` column — no schema migration, and *both* generated and
imported lessons carry their exact source, so export is byte-exact for anything built after
2026-07-01; reconstruction remains the fallback for older rows. Validation additionally requires
`lines[].translation` (build_translated_section hard-accesses it), and import responses carry
`warnings` for speakers missing from the voice map.

## Motivation

Two forces converge:

1. The free Groq models we can use (gpt-oss-120b, llama-4-scout, qwen) all have
   real quality gaps — most importantly **scrambled speaker logic** (the clerk asks
   the price, the customer answers it) and occasional EN/NO mixing. gpt-oss-120b is
   the best of them but not perfect.
2. We want Claude Code (or any model in the editor) to be able to **edit a generated
   day, or author one from scratch**, without a Groq call.

If lessons live in an **editable file format**, both problems shrink: the LLM
produces a *first draft*, and a human or Claude polishes the file by hand. Model
choice stops being load-bearing.

## Core principle: Story JSON is source, the Lesson blob is a build artifact

There are already two representations of a lesson in the codebase:

| Representation | Where it lives today | Shape | Role |
|---|---|---|---|
| **Story JSON** | *thrown away* after generation | compact dialogue | the thing you'd hand-write |
| **Lesson blob** | `lessons.data_json` in `tunatale_<lang>.db` (`Lesson.to_json()`) | expanded: 4 sections, per-phrase `voice_id`, tokenized glosses | playable artifact, consumed by audio + review + sync |

The Lesson blob is *derived* from Story JSON by `StoryGenerator._parse_response()`
(`backend/app/generation/story.py`), which:
- expands each scene into the NATURAL_SPEED / SLOW_SPEED / TRANSLATED sections,
- resolves each line's `speaker` → a TTS `voice_id` via `language.tts_voice_map`,
- runs the **lemmatizer** (classla) over every L2 line to build the word→lemma map
  used for word-state coloring and glosses.

So the design is: **treat Story JSON as source code and the Lesson blob as a
compiled artifact.** Author/edit the source; rebuild the artifact.

## The Story JSON schema (authoring contract)

This is exactly what `_parse_response` consumes and what `build_*_section`
(`section_builder.py`) reads — verified against the code, not invented:

```json
{
  "title": "Buying a Train Ticket and Finding the Platform",
  "key_phrases": [
    { "phrase": "Hvor er billettautomaten?", "translation": "Where is the ticket machine?" }
  ],
  "scenes": [
    {
      "label": "At the Ticket Counter",
      "lines": [
        { "speaker": "female-1", "text": "Jeg vil ha en billett til Bergen, takk.", "translation": "I would like a ticket to Bergen, please." },
        { "speaker": "male-1",   "text": "Enveis eller tur-retur?", "translation": "One-way or round-trip?" }
      ]
    }
  ],
  "dialogue_glosses": [
    { "word": "billettautomaten", "translation": "the ticket machine" }
  ],
  "morphology_focus": []
}
```

Field notes (all load-bearing):
- **`scenes[].label`** — English scene header, spoken by the narrator voice. Required
  by `build_natural_speed_section` (`scene["label"]`, hard key access).
- **`lines[].speaker`** — role string (`female-1`, `male-1`, `female-2`, …). Mapped to
  a voice by `_resolve_voice`; unknown speakers fall back to the narrator voice. This
  is the field to get *right* for coherent two-speaker flow — the free models'
  weak spot.
- **`lines[].text`** — the L2 (Norwegian/Slovene) line.
- **`lines[].translation`** — English; becomes `generation_metadata.sentence_translations[text]`
  (the Anki Back Extra / interlinear source).
- **`dialogue_glosses[].word`** — an L2 surface (conjugated form ok); `.translation` its
  gloss. Surface + lemma both keyed (lemma via the sentence-aware lemmatizer).
- **`morphology_focus`** — optional list, passed through to metadata.

## File format & location

**Recommendation: JSON**, 1:1 with the schema above (no transform, no new
dependency, identical to the LLM's own output). Claude edits JSON fine; the escaping
cost is low because lines are short.

Proposed layout (one file per curriculum-day, language-scoped):

```
lessons/
  no/
    at-the-train-station/day-01.json
  sl/
    <curriculum-slug>/day-01.json
```

A sidecar or a top-of-file block carries the binding metadata the DB row needs:
`curriculum_id`, `day`, and (on export) the source `lesson_id`. Options:
- **(a)** wrap: `{ "curriculum_id": "...", "day": 1, "story": { ...schema... } }`
- **(b)** keep the file pure Story JSON and pass `curriculum_id`/`day` as import args.

Lean toward **(a)** — self-describing files are easier for Claude to pick up cold and
re-import without remembering CLI args. (Decision for the user — see Open questions.)

## Export path (lesson → file)

`export_lesson(lesson_id) -> Story-JSON file`

Story JSON isn't persisted today, so export **reconstructs** it from the stored
Lesson (fully recoverable — checked against the model):
- `title` ← `Lesson.title`
- `key_phrases` ← `Lesson.key_phrases` (`KeyPhraseInfo.phrase/translation`)
- `scenes` ← walk the NATURAL_SPEED section: each `role="narrator"` English phrase
  starts a new scene (`label`); following L2 phrases become `lines` with
  `speaker=phrase.role`, `text=phrase.text`,
  `translation=generation_metadata.sentence_translations[text]`
- `dialogue_glosses` ← `generation_metadata.token_glosses`
- `morphology_focus` ← `generation_metadata.morphology_focus`

(The first NATURAL_SPEED phrase is the section title "Natural Speed" — skip it.)

## Import path (file → rebuilt Lesson)

`import_lesson(file) -> saves a new Lesson`

1. Read the file, split metadata (`curriculum_id`, `day`) from the `story` payload.
2. Feed the story payload straight into `StoryGenerator._parse_response(story, language)`
   — **the exact same build step generation uses**, so authored and generated
   lessons are byte-identical in shape (this is the whole point: one build path).
3. `store.save_lesson(new_lesson_id, curriculum_id, day, lesson)`.

Because import reuses `_parse_response`, it inherits the **lemmatizer requirement**
(classla). Two consequences:
- Import must run in a process where the lemmatizer is available (same as the backend).
- Word-state coloring is **regenerated from the edited text** — edit a line and its
  coloring/glosses update on import, for free. No stale coloring.

Downstream (audio, `/review`, Anki sync) already consumes `save_lesson` output, so an
imported day flows through unchanged. New collocations still enter Anki only via the
existing `/listen` → `sync_create_new` contract (`.claude/rules/anki-sync.md`), which
this feature doesn't touch.

## Workflows this unlocks

- **Fix a bad generation.** Generate on gpt-oss → export → I edit the `speaker`
  fields / rewrite an incoherent line → import. No re-roll, no lost good lines.
- **Author a day from scratch.** I write `day-03.json` by hand against the schema →
  import → playable lesson with correct coloring. Zero Groq calls.
- **Version lessons.** Story-JSON files are diffable and can be committed; the DB blob
  is a build artifact you can regenerate.

## Validation & round-trip fidelity

- **Schema validation on import** — reject missing `scenes[].label`, `lines[].speaker/text`,
  `key_phrases[].phrase/translation` with a clear error (today `_parse_response` does
  hard `[]`-key access and would `KeyError`; import should validate first and message
  well).
- **Round-trip test** — `export(import(f)) == f` (modulo key ordering) for a known file,
  and `import(export(lesson))` produces a Lesson equal to the original for a
  generated lesson. This pins reconstruction fidelity.
- **Speaker coverage** — warn if a `speaker` isn't in `language.tts_voice_map` (it will
  silently fall back to the narrator voice = mono-voice audio).

## Surface: API (+ trivial CLI wrapper)

Two endpoints under the existing `/api/story` router (language resolved from the
`X-TT-Language` header, like every other content route):

- `GET /api/story/{lesson_id}/source` → the self-describing Story-JSON file body
  (reconstructed from the stored Lesson, or the persisted raw source when present).
- `POST /api/story/import` → body is a self-describing file; rebuilds the Lesson via
  `_parse_response` and `save_lesson`; returns the new `lesson_id`.

### Story prompt export (manual mode)

`GET /api/story/prompt?curriculum_id={id}&day={day}&strategy={WIDER|DEEPER}` returns
`{system_prompt, user_prompt}` — the exact prompts the Groq story generator would send.
No LLM call, no persistence. Used by the manual copy/paste flow: the user copies the
prompts into Claude chat and pastes the reply back via the `raw` import variant.

### Raw paste import

`POST /api/story/import` accepts an optional `raw` field (string) instead of `story`
(dict). Exactly one of `story` or `raw` must be provided (Pydantic model validator
→ 422 otherwise). When `raw` is set, the text is parsed through `parse_json_object()`
(`app/generation/json_parsing.py`) which strips think-blocks, fences, and surrounding
prose — the single hygiene path for pasted text. The parsed dict then flows through the
existing `import_lesson` path unchanged.

**Lemmatizer-on-request-path is already the norm** — `POST /api/story/generate` runs
`_parse_response` (and thus classla) on the request path today, with a background
`_prewarm_lesson` task. Import mirrors that exactly, so it adds no new architectural
concern. The import/export logic lives in a plain module (`app/storage/lesson_io.py`)
that the routes are thin wrappers over, so a `python -m app.storage.lesson_io`
CLI is a trivial add if the editing loop ever wants it offline.

## Settled decisions (2026-06-30)

| # | Decision | Choice |
|---|----------|--------|
| 1 | File wrapper | **Self-describing** `{curriculum_id, day, story}` |
| 2 | Format | **JSON**, pretty-printed (2-space indent, `ensure_ascii=false`) |
| 3 | Surface | **API** (`GET …/source`, `POST …/import`); logic in `lesson_io.py`, CLI is a trivial later add |
| 4 | Source of truth | **Both** — reconstruct-from-Lesson now; persist raw Story JSON at generation time as a fast-follow so new lessons have an exact source, reconstruction is the fallback for legacy |
| 5 | Location | Repo **`lessons/<lang>/<curriculum>/day-NN.json`**, export-on-demand (generation still writes the DB; you export only days you choose to curate) |

## Testing plan (TDD, when we build)

- Unit: reconstruct Story JSON from a hand-built `Lesson` (export); rebuild `Lesson`
  from a Story-JSON dict (import) — assert sections/metadata match.
- Round-trip: `import(export(lesson))` equality on a generated fixture.
- Validation: each required-field omission raises a clear error, not `KeyError`.
- Integration: imported day appears in `/review-queue` / audio like a generated one.
- No new mock boundaries; the lemmatizer is real (uses the existing test lemmatizer
  path). Follows `.claude/rules/tdd.md` red-green-refactor.
