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

# Paid vendor usage — price it before you run it

Moved verbatim from AGENTS.md (2026-09-30), which keeps the actionable summary.


**The Speech resource is F0 (free tier).** Read from the field, not inferred:
`az cognitiveservices account list --query "[].{name:name,sku:sku.name}"` →
`TunaTale / F0 / SpeechServices / eastus`, and the `AZURE_SPEECH_KEY` in
`backend/.env` matches `key2` of that resource (compared by hash — never print
the key; one was leaked to a transcript on 2026-09-12 and had to be rotated).
It is the only Speech account in the subscription.

**So standard/Multilingual Neural characters do NOT bill — they consume a
500K/month allowance, and the failure mode is a THROTTLE, not an invoice.**

⚠️ **This paragraph replaced a confident wrong claim, and how it got wrong is
the reusable part.** It read "this resource behaves like S0, where characters
bill", inferred from two real measurements: TTS sustained **49 requests in a
60-second window** where F0 documents 20/60s, and Dragon HD answered 200 where
F0 is not supposed to serve it. Both observations still stand and are still
unexplained. They were an inference from *behaviour* to *configuration*, and
`sku.name` outranks them. **Nobody had read the field.** When a claim about
configuration can be settled by reading configuration, read it.

**Azure Neural HD (Dragon) voices are still RULED OUT** (user's call,
2026-09-12), and the F0 finding does not soften it: HD is a separate billing
line at $22/1M **excluded from the F0 allowance**, so it bills from the first
character even here. Enforced by
`test_languages.py::test_no_voice_map_names_a_paid_hd_voice` (an HD id is the
only voice id containing a colon). Not a quality judgement — Andrew-HD had the
best measured WER and was the ear-test favourite — and it is also the one voice
family that cannot be regression-tested, being nondeterministic by design.

**Price a render before running it, in characters, and put the number in the
report.** Compute cache misses with the adapter's own `_cache_path` against the
real `tts_cache_dir`; that is not merely the cheapest way, it is the ONLY way:

⚠️ **There is no usable Azure-side character meter.** `SynthesizedCharacters`
appears in `az monitor metrics list-definitions` and is **not queryable** — the
error enumerates what is (`TotalCalls, SuccessfulCalls, TotalErrors,
BlockedCalls, ServerErrors, ClientErrors, SuccessRate, Ratelimit`). And the
queryable ones are not trustworthy for this: measured 2026-09-13, `TotalCalls`
returned **0 for a day in which hundreds of syntheses demonstrably happened**,
and `Ratelimit` returned no data at all. **So a `BlockedCalls` of 0 proves
nothing** — zero is also what a lagging or broken meter returns, the same
clean-negative trap `.claude/rules/tdd.md` is about. Nothing cloud-side will
catch an error in your local estimate; the local estimate is the instrument.

**Price it with the tracked instrument, not from a remembered number**
(`backend/scripts/report_render_cost.py`, shipped 2026-09-15):

```bash
cd backend
uv run python scripts/report_render_cost.py --language no --all   # incremental
uv run python scripts/report_render_cost.py --language no --all \
    --cache-dir "$(mktemp -d)"                                    # cold, from empty
```

Quote the **incremental** number for "what will this run cost" and the cold one
only for "what does this cost from empty". They have differed by ~7x, which is
the whole reason to say which one you are quoting.

⚠️ **This replaced two hardcoded figures, and how they were wrong is the
reusable part.** It read *"a cold render of the whole stored Norwegian
curriculum is ~69k characters"* beside a `263 clips (~11k chars)` incremental
figure. Both were **text counts, not billable counts** — the cold figure is
really **157,196** billable characters, because markup counts while `<speak>`
and `<voice>` do not. The gap is not rounding: `sum(len(text))` over that corpus
is 80,489, and its ~2,019 distinct requests each add ~36 characters of
`<prosody>` wrapper. In a corpus of thousands of short utterances **the markup
dominates**, so a text count understates by ~2x.

That anchor sat three paragraphs below "the billable unit is NOT `len(text)`"
and modelled the exact error this section exists to prevent. It was stale by
construction too — it said "all nine lessons" and there are now eleven. A number
measured against a living corpus rots the way a bare `file:line` citation does,
and the fix is the one this file already prescribes there: cite the thing that
regenerates the answer, not the answer.
