# BP brief — Norwegian segmenter: homograph stems, s-overlap compounds, lexicalized for-derivatives

Three fixes to `backend/app/generation/norwegian_breakdown.py`, follow-ups to
commit `8438629` (closed-class stoplist + anchor-rank scoring). Read that
commit and `docs/review-2026-07-10-followups.md` brief #1 first.

Work TDD red→green, fixes in order (1 → 2 → 3): each has its own red tests and
a green full-suite checkpoint before the next. **Goldens marked PROPOSED below
are supplied, not yours to invent or adjust** — if the implementation disagrees
with one, STOP and report the actual output; do not edit the golden to match
the code (that includes "nearby" goldens that shift as a side effect).

## Fix 1 — `hver`/`selv`/`vår` are compound-productive homographs, not pure closed-class

**Problem.** `_CLOSED_CLASS_STEMS` blocks `hver`, `selv`, `vår` entirely, so
`hverdag`, `selvtillit`, `vårsol` stay whole. But these three are productive
*compound-initial* elements (hverdag, selvmord, selvtillit, vårsol) while their
closed-class readings (determiner/pronoun/possessive) are what caused the
original over-splits. Position separates the readings.

**Change.**
1. Remove `hver`, `selv`, `vår` from `_CLOSED_CLASS_STEMS`; add them to a new
   `_COMPOUND_INITIAL_ONLY_STEMS: frozenset[str]` with a comment explaining the
   homograph rationale.
2. Thread position through the splitter: `_segment_surface(text, ranks, *,
   initial: bool = True)` — the recursive call for the remainder passes
   `initial=False`. `_is_content_stem(word, ranks, *, initial: bool)` rejects
   `_COMPOUND_INITIAL_ONLY_STEMS` members when `initial=False`. All other
   callers/semantics unchanged. (`segment_compound`'s inflection-peel path
   still starts the base at `initial=True` — the peeled article is at the
   *end*, so the first part is still word-initial.)
3. The single-stem fallthrough at the bottom of `_segment_surface`
   (`if _is_content_stem(text, ...): return [text]`) runs at the CURRENT
   position's `initial` value — a trailing bare `selv` etc. must not become a
   final free part.

**PROPOSED goldens (pin as tests):**
- `segment_compound("hverdag") == ["hver", "dag"]`
- `segment_compound("hverdagen") == ["hver", "dag", "en"]`
- `segment_compound("selvtillit") == ["selv", "tillit"]` — **verify first**
  that `tillit` clears `_MAX_STEM_RANK` in the wordlist; if it doesn't, the
  word stays whole — report that instead of forcing it.
- Regressions that must NOT change: `sommer`→whole, `morsom`→whole (`som`/`mer`
  stay absolutely stoplisted), `togstasjon`→`["tog","stasjon"]`,
  `etterforskning`→`["etter","forskning"]`,
  `etterforskningsteamet`→`["etter","forsknings","team","et"]`,
  `mannen`/`politiet`/`kjærlighet`→whole. (`forstand` changes under Fix 3
  below — do the fixes in order and adjust its test there, not here.)

**Scan (report, do not pin):** run `segment_compound` on `hverandre`,
`selvfølgelig`, `vårsol`, `våren`, `selvsagt` and paste the outputs in your
report — these are for the human by-ear pass, not test goldens.

## Fix 2 — s-overlap compounds (busstasjon = buss + stasjon)

**Problem.** Norwegian orthography reduces a triple consonant at a compound
boundary to two: `buss`+`stasjon` → written `busstasjon`;
`fjell`+`landskap` → `fjellandskap`. The splitter only tries exact-cover
splits, so these stay whole.

**Design constraint — do NOT break the surface-join invariant.** Everything
downstream (`_build_compound_sequence` partials, `slow_norwegian_word`,
cue-count arithmetic in `cues.py`) assumes compound parts join back to the
original string. Follow the existing `_spoken_syllable` precedent (geminate
syllables: raw `et|ter` joins exactly; spoken-alone form lengthens to `ett`):
**surfaces stay exact slices; the spoken form is derived at voice-alone sites.**

**Change.**
1. In `_segment_surface`, at each split index `k` where `text[k-1] == text[k]`
   and that char is a consonant (`not in _NORWEGIAN_VOWELS`), additionally try
   the overlap candidate: `first_surface = text[:k]` (e.g. `bus`),
   `rest = text[k:]` (e.g. `stasjon`), where the *stem-gate check for the first
   part runs on the doubled spoken form* `text[:k] + text[k]` (e.g. `buss` must
   be a content stem; the surface `bus` need not be). The remainder recurses as
   usual (`initial=False`).
2. Scoring: overlap candidates compete under the same anchor-rank rule
   (use the spoken form's rank for the overlap part). On a full tie
   (anchor AND part count), prefer the non-overlap split.
3. Spoken form at voice-alone sites: when a compound part is voiced alone
   (`_build_compound_sequence`'s `seq.append(part)`, and the parts list in
   `slow_norwegian_word`), an overlap-truncated part is rendered with its
   doubled final consonant (`buss`, `fjell`). Partials/rebuilds keep using raw
   surfaces so `"".join(...)` still reproduces the original spans exactly
   (`bus`+`stasjon` = `busstasjon` — never emit `bussstasjon` anywhere).
   Note `_spoken_syllable` already produces the doubled form for a piece whose
   *next* piece starts with the same consonant — check whether the morpheme
   level can reuse that mechanism (the overlap part's successor part starts
   with the shared consonant, so the same neighbor-doubling rule may fall out
   for free at the part level; if so, prefer that over threading new state).
   Whatever the mechanism, syllabification of the overlap part must operate on
   the spoken form (`buss` → one syllable), not the surface (`bus`).
4. How you represent "this part is overlap-truncated" internally is your call
   (parallel spoken list, `(surface, spoken)` tuples in a private helper, or
   the neighbor-doubling reuse above), but **the public
   `segment_compound(word) -> list[str]` signature and its surface-exact join
   semantics must not change** — every existing caller and test stays valid.

**PROPOSED goldens:**
- `segment_compound("busstasjon") == ["bus", "stasjon"]` (surface), and the
  breakdown/slow outputs voice `buss`: `slow_norwegian_word("busstasjon") ==
  "buss, stasjon"`; `build_norwegian_breakdown("busstasjon")` contains `buss`
  and `stasjon` as voiced chunks, never `bus` alone, and every
  partial/full-word step spells `busstasjon` with exactly two s's.
- `segment_compound("fjellandskap") == ["fjel", "landskap"]` with voiced
  `fjell` — **verify `fjell` and `landskap` clear the rank floor first**;
  report if not.
- Regressions: `snømann` → `["snømann"]` unchanged (its `nm` boundary is not a
  doubled consonant — assert it as the negative case), `mannen` whole,
  and the full existing golden suite untouched.

**Scan (report only):** `busstopp`, `fjelltopp` (no overlap — `llt` is not a
doubled boundary… verify), `nettopp`, `stillhet`.

## Fix 3 — lexicalized `for`-derivatives split when they shouldn't (`forstand`)

**Problem.** `forstand` (understanding) currently splits `for|stand` — a
BP-invented golden from `8438629`, now human-REJECTED: it is a lexicalized
derivative, not a transparent compound. The root cause is the same inversion
class as the original `sommer` bug, one layer up: `_is_lexicalized_whole`
compares the whole's rank against `min(part_ranks)`, and a hyper-frequent
preposition part (`for` ranks in the top ~20) always wins that min, so the
guard can never fire for ANY `for`-initial word (`forslag`, `forhold`,
`fortelle` are all lexicalized derivatives at risk of the same wrong split).
We can't stoplist `for` (it is a productive compound/prefix element) — instead,
neutralize it in the *guard's comparison*.

**Change.**
1. Add `_GUARD_EXEMPT_PREPOSITIONS: frozenset[str]` = the compound-productive
   prepositions/particles (`for`, `om`, `etter`, `over`, `under`, `inn`, `ut`,
   `opp`, `ned`, `av`, `på`, `til`, `mot`, `fra`, `ved`, `mellom`, `gjennom`),
   with a comment: these are so frequent they always win the min and neuter the
   lexicalized-whole guard, so they are EXCLUDED from its comparison — the
   whole word competes against its *content* parts only.
2. In `_is_lexicalized_whole`, compute `part_ranks` over
   `[p for p in content_parts if p not in _GUARD_EXEMPT_PREPOSITIONS]`. If that
   leaves no ranked parts, the guard does not fire (unchanged fallthrough).
3. This must NOT change `etterforskning` (→ still `["etter","forskning"]`):
   verify that `forskning` out-ranks `etterforskning` in the wordlist so the
   guard still declines to fire there. If it doesn't, STOP and report — do not
   tune the mechanism to force it.

**PROPOSED goldens:**
- `segment_compound("forstand") == ["forstand"]` — replaces `8438629`'s
  `test_segment_compound_forstand_preposition_eligible`. **Verify the rank
  data supports it** (`forstand` must out-rank `stand`); if the wordlist
  contradicts this, fall back to plan B: a human-ratified
  `_LEXICALIZED_WHOLE_OVERRIDES = frozenset({"forstand"})` checked in
  `segment_compound` before the split paths — an explicit exception list is
  acceptable ONLY for this human-supplied word, never as a general dumping
  ground (comment must say so).
- The "`for` stays stem-eligible" property still needs one pinned positive
  case: test `fortid` and `formiddag` with the new guard; pin whichever splits
  (`["for","tid"]` / `["for","middag"]`) — these are transparent (before-time /
  fore-midday) and human-accepted. If NEITHER splits, report it: `for`'s
  eligibility may then be moot, but removing it is a separate human decision —
  leave eligibility in place.
- **Scan (report only):** `forslag`, `forhold`, `fortelle`, `forsikring`,
  `omgang`, `overtid`, `utgang`, `innsjø` — paste outputs; the human ratifies
  which whole/split outcomes are right on the next pass.

## Guardrails (standing BP gaps — all mandatory)

1. Run the FULL gate from repo root: `./test.sh` — paste the tail (backend
   pass-count @100% incl. --run-oracle, frontend counts, e2e 18, ruff file
   count). A bare `uv run pytest` is not the gate.
2. No new `# pragma: no cover`, no pyproject omits, no mock of any `app.*`
   internal. The splitter is pure — test it directly.
3. Run `uv run python -m app.generation.breakdown_preview hverdag selvtillit
   busstasjon fjellandskap forstand fortid sommer morsom togstasjon
   etterforskningsteamet --no-audio` and paste the text output in your report
   (the human confirms by ear later; your job is the text table).
4. Do not touch `section_builder.py`, `cues.py`, `syllabify.py`, or anything
   outside `norwegian_breakdown.py` + its test file (+ this brief's file if you
   commit it). The `expected = 2 + len(breakdown)` cue invariant must hold —
   the existing section-builder tests will catch a violation; if they go red,
   your change is wrong, not the tests.
5. If any PROPOSED golden fails, report the divergence with the actual output —
   never adjust a golden or add a special case to force it.
