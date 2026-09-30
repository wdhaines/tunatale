---
paths:
  - "backend/app/audio/**"
  - "backend/app/plugins/languages/*/__init__.py"
  - "backend/scripts/report_render_cost.py"
  - "backend/scripts/rebuild_lessons_from_story.py"
  - "backend/scripts/render_slicing_ab.py"
  - "backend/scripts/reroll_tts_clip.py"
  - "backend/.env*"
---

# Paid vendors — price a render before running it

AGENTS.md carries the summary; this is the detail behind it.

## The Azure Speech resource is F0

`az cognitiveservices account list --query "[].{name:name,sku:sku.name}"` returns
`TunaTale / F0 / SpeechServices / eastus`, the only Speech account in the
subscription. The `AZURE_SPEECH_KEY` in `backend/.env` is `key2` of that
resource. Compare keys by hash and never print one — a key that reaches a
transcript has to be rotated.

On F0, standard and Multilingual Neural characters draw on a 500K/month
allowance. Going over it throttles; it does not bill.

Two observed behaviours don't fit F0 and remain unexplained: TTS sustained 49
requests in a 60-second window (F0 documents 20/60s), and a Dragon HD request
returned 200. They don't change the tier. `sku.name` is the configuration and
behaviour is only indirect evidence about it — when a configuration question
can be settled by reading the configuration, read it.

## Azure Neural HD (Dragon) voices are ruled out

This is the user's decision. HD is a separate billing line ($22 per 1M
characters) outside the F0 allowance, so it bills from the first character, and
it is the one voice family that cannot be regression-tested because its output
is nondeterministic. It is not a quality judgement: Andrew-HD had the best
measured WER and won the ear test. Enforced by
`test_languages.py::test_no_voice_map_names_a_paid_hd_voice` (an HD voice id is
the only kind that contains a colon).

## Price every render in billable characters before running it

Put the number in your report, and get it from the tracked instrument rather
than a remembered figure:

```bash
cd backend
uv run python scripts/report_render_cost.py --language no --all   # incremental
uv run python scripts/report_render_cost.py --language no --all \
    --cache-dir "$(mktemp -d)"                                    # cold, from empty
```

Quote the incremental number for "what will this run cost?" and the cold number
only for "what does this cost from empty?", and say which one you are quoting —
the two have differed by about 7x.

The script decides cache hits with the adapter's own `_cache_path` against the
real `tts_cache_dir`, and it counts the billable body rather than the text. Those
differ a lot: SSML markup counts toward billing (the `<speak>` and `<voice>`
elements do not), and across thousands of short utterances the ~36-character
`<prosody>` wrapper on each request roughly doubles the total. A text-length
estimate understates by about 2x.

Don't write measured figures into docs. A number measured against the curriculum
goes stale as lessons are added; cite the command that regenerates it instead.

## There is no usable Azure-side meter

`SynthesizedCharacters` is listed by `az monitor metrics list-definitions` but is
not queryable (the error lists what is: `TotalCalls, SuccessfulCalls,
TotalErrors, BlockedCalls, ServerErrors, ClientErrors, SuccessRate, Ratelimit`).
The queryable metrics are unreliable for this: `TotalCalls` has returned 0 for a
day with hundreds of syntheses, and `Ratelimit` returned no data. A
`BlockedCalls` of 0 therefore proves nothing, and nothing cloud-side will catch a
mistake in your local estimate — the local estimate is the instrument.
