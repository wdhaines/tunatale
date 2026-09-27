# Adding a new language to TunaTale

*Rewritten 2026-09-27, after Tagalog (`tl`, 2026-09-22..24) and Cebuano (`ceb`, 2026-09-25..27) were wired end-to-end. The previous version described Norwegian as the worked example, a `Language` factory in `models/language.py`, edge-tts voice auditions and a `micro-demo-0.0` Tagalog lineage; none of that is current. Four languages now take the same path, so this describes that path, with the real commits as evidence.*

## The registry is the process

A language is **a plugin package under `backend/app/plugins/languages/<code>/`**. Its `__init__.py` calls `register("<code>", LanguageConfig(...))`, and that call is the only switchboard. Core code resolves every per-language facet through the accessors in `app/languages.py`, and the sync/render paths bundle them all via `resolve_language_context(code, settings) → LanguageContext`.

Two gates keep it that way:

- `backend/scripts/check_language_literals.py` (in `./test.sh` and CI) fails on any language literal in `backend/app/**` outside the plugin packages: codes, English and native names, engine names, and voice ids (`<locale>-<Name>Neural` or `<locale>-<Name>Gemini`). Adding a language means adding its names to `_NAME_SUBSTRINGS` there, native name included. Cebuano added `bisaya` and `binisaya` (`f27fc14`).
- `backend/tests/test_plugin_isolation.py`: **a plugin may not import another plugin**. When two related languages share logic, it goes in core. `app/audio/spelled_ipa.py` is the worked example (see below).

If wiring a language seems to need `if code == "xx"` in core, add a registry facet instead. Rationale: `docs/language-plugin-hardening.md`.

**Codes:** ISO 639-1 where one exists (`sl`, `no`, `tl`), else 639-3 (`ceb`). The code is the `X-TT-Language` value, the `DATABASE_URLS` key and the plugin directory name. A request naming an unconfigured code is refused, never silently served the default language (`5b609de`).

## The skeleton: what the first commit touches

Measured from the two skeleton commits, `bc4fb4e` (Tagalog, 8 files) and `f27fc14` (Cebuano, 13 files: the same set plus the deploy wiring below):

| Touch-point | File | Notes |
|---|---|---|
| Plugin registration | `app/plugins/languages/<code>/__init__.py` | `register()` with a `Language(...)` built inline: code, English name, native name, script, `tts_locale`, `tts_voice_map`, `tts_voice_gain_db`. |
| Preprocessor | `app/plugins/languages/<code>/preprocessor.py` | A 12-line pass-through is the right start for both `tl` and `ceb`. Add TTS-quirk replacements only when you hear one. |
| Vocab notetype | `app/cards/vocab_notetype.py` | One `VocabNotetype(name="<Lang> Vocabulary", l2_field="<Lang>", l2_css_class="<lang>")` line. Adding a notetype bumps `col.scm`, so read `.claude/rules/anki-sync.md` first. |
| Literal gate | `scripts/check_language_literals.py` + its test | Names and native names; widen `_VOICE_RE` only for a new voice vendor. |
| Registry tests | `tests/test_languages.py`, `tests/test_plugin_isolation.py` | Registry-wide voice/gain invariants apply automatically; add the isolation case. |

**Deploy wiring**, where a language gets its own database everywhere the others have one (`5870aba` for Tagalog, part of `f27fc14` for Cebuano):

- `docker-compose.yml`: the `DATABASE_URLS` JSON
- `data-transfer.sh`: the DB file list, the tar glob and the `chown` list
- `switch.sh`: the `for code in …` loop
- `backend/.env.example`, `backend/.env.prod.example`
- `tests/test_compose_profile.py`

The schema is created and migrated on first open (`app/srs/migrations.py`), so a new DB file needs no manual step.

**No dependency group.** Neither `tl` nor `ceb` added one, because both lemmatize from a table (below) and need no NLP engine at runtime. Only `sl` (classla) and `no` (stanza) carry heavy groups, and CI drops those.

## Facets beyond the skeleton

All are `LanguageConfig` fields. Who uses what today:

| Facet | sl | no | tl | ceb | Notes |
|---|---|---|---|---|---|
| `style_notes` (`data/style.md`) | ✓ | ✓ | ✓ | ✓ | Its first job is keeping the confusable language OUT: Tagalog out of Cebuano, Nynorsk, Danish and Swedish out of Bokmål. |
| `function_words_path` | ✓ | ✓ | ✓ | ✓ | POS-first policy; function words become clozes, not picture cards. |
| `numbers_path` | ✓ | ✓ | ✓ | ✓ | |
| `syllabifier_fn` | ✓ | ✓ | ✓ | ✓ | Drives the Pimsleur backward buildup *and* the phoneme planner's spans, so both move together. Without one, `app.generation.syllabify.syllabify_word` cut Cebuano `pan|ga|lan`; its own cuts `pa|nga|lan` (`deb104d`). |
| `lemmatizer_type` + `lemma_table_path` | classla | stanza/table | table | table | A gzipped `surface, upos, lemma, is_default` table. `tl` and `ceb` are built from kaikki.org Wiktionary extracts (below). |
| `wordfreq_lang` / `frequency_table_path` | | | `fil` | own table | wordfreq has no `ceb`, so Cebuano uses root-keyed counts from FineWeb-2 news (`scripts/build_cebuano_frequency.py`). |
| `phoneme_planner_factory` | | ✓ | ✓ | ✓ | IPA for the key-phrase breakdown. `no`: NST lexicon. `tl`: Wiktionary pronunciations. `ceb`: read off the spelling. |
| `ipa_read_in_voice_locale` | | | ✓ | | The voice ignores `<phoneme>`, so IPA must reach it unwrapped. |
| `ipa_for_drill_phrases` | | | | ✓ | Per-word IPA on multi-word drill steps. Deliberately not a default, because on Azure it would wrap every word in `<phoneme>`. |
| `notetype_profiles` | | | ✓ | ✓ | **Required for any language with no `l2_scorer`**, which covers both Philippine languages: no letters tell them apart from English. TT's own vocab notes must be read by field name, or the first sync after minting fails with "No L2 scorer" (`1e93783`, tunatale-2qqr). An imported deck needs its own profile too (Tagalog's Pimsleur deck: `recognition_ord=1`, `disambig="Front"`). |
| `mint_deck_name` | | | ✓ | ✓ | TT mints into a `::TunaTale` subdeck. Create the subdeck in Anki first. |
| `a1_morphology` | ✓ | ✓ | | ✓ | Cebuano: an affixed verb form becomes an inflection cloze on its root card. A language without a bundle gets no morphology, not Slovene's (`0f34ec3`). |
| `verb_headword_fn`, `story_text_normalizer` | | | ✓ | | Tagalog keys verbs on the root; the normalizer joins the LLM's `mag‑kape` affix hyphens. |
| `planner_example` | ✓ | ✓ | ✓ | ⚠️ | See the gotchas below. |
| Norwegian-only | | ✓ | | | `lexicon_factory`, `breakdown_spans_fn` (compound breakdown), `variant_separator`, gender facets, `alignment`. |

## Voices and cost

- **The voice id picks the vendor.** `app/audio/tts_router.py` dispatches on the suffix: `…Neural` goes to Azure and `…Gemini` to Gemini-TTS through Cloud TTS (`app/audio/gemini_tts.py`, `cf84720`). Gemini ids mirror Azure's shape (`ceb-PH-KoreGemini`) on purpose, so the registry's locale-slicing invariants hold unchanged. Cebuano is on Gemini because Azure has no `ceb-PH` voice.
- **Never an Azure Dragon HD voice**: `test_no_voice_map_names_a_paid_hd_voice`, and the pricing section of `CLAUDE.md`.
- **When the native locale has only one voice per gender** (Azure `fil-PH`: Blessica and Angelo), the second woman and man are Multilingual voices under a `<lang>` wrapper.
- **Pick the cast by measurement, in this order:** STT word error rate on ~10 A1 sentences, then median F0 distance (Praat) against a ≥13.7 Hz minimum gap between same-gender voices. Measure on *this* language's text: the Norwegian pitch map did not transfer (Dustin measured 160.0 Hz on Norwegian and 136.5 on Tagalog). Gains come from integrated loudness (ffmpeg `ebur128`) to −20 LUFS. The narrator's gain has to appear in every plugin's table.
- **Price the first render before running it:** `uv run python scripts/report_render_cost.py --language <code> --all`. Quote the incremental figure. The script prices Gemini in audio tokens (`9d60400`).

## Philippine-family pieces: what Cebuano reused from Tagalog

Tagalog was the pathfinder for Cebuano. The comparison below answers whether that paid off. **It did, measured by time:** Tagalog went from skeleton (`bc4fb4e`, 09-22) to a lesson the user had listened to in about three days. Cebuano went from skeleton (`f27fc14`, 09-25) to its first lesson the same day.

**Reused, with no Cebuano-specific work:**
- The notetype-profile-by-field-name fix, the `::TunaTale` mint subdeck and per-language mint deck (`13d0e6b`), and "a deck includes its subdecks" (`8d8a160`). All were built for Tagalog in core.
- The Slovene-fallback purge Tagalog forced (`1bb2332`, `0f34ec3`, `5b609de`): a third language was what exposed each hidden "default to Slovene".
- The deploy-wiring shape, identical file for file.
- The table lemmatizer path, and the measurement methods for cast and gain.

**Reused, with adaptation:**
- **Lemma table:** `scripts/build_cebuano_lemma_table.py` imports its core from `scripts/build_kaikki_lemma_table.py` (Tagalog's), adding Cebuano's affix reduction and hand-curated closed-class readings (`7b22233`).
- **Spelled IPA:** the Latin letter table is duplicated in `ceb/phoneme_plan.py` (plugins cannot import each other). The shared logic (glottal stop, digraphs, lone-syllable stress) moved into core as `app.audio.spelled_ipa.SpelledPhonemePlanner`.
- **Starter cards from cognates:** `scripts/seed_cognate_cards.py` seeded Cebuano review cards from the Tagalog deck's cognates, gated on an equivalent English gloss (`8a1c330`, `ce95e3b`).
- **Style guide:** same shape. Cebuano's guards against Tagalog, its closest and most training-data-dominant relative.

**New for Cebuano:**
- The Gemini-TTS adapter and voice router (no Azure voice exists).
- A frequency table (wordfreq has no `ceb`), a Fluent Forever base list (`data/base625.tsv`, `scripts/seed_base_list.py`) and a reviewed next-words queue.
- Its own syllabifier and A1 inflection clozes.

**Expect a fifth Philippine language to reuse** everything in the first two groups. Budget for the third group only where the new language lacks a vendor voice or wordfreq coverage.

## Gotchas each language hit

- **`planner_example` for a code that sorts first.** `get_planner_example` shows each language the example of the lowest-sorting *other* language that has one. `ceb` sorts before every other code, so giving Cebuano an example would replace the one every other language sees, and Tagalog would be shown Cebuano. Cebuano deliberately has none.
- **A voice that ignores IPA.** Both `fil-PH` voices produce byte-identical audio for "salamat" under two different IPA strings. Hence `ipa_read_in_voice_locale` and a separate `key-phrases` voice for the breakdown (Seraphina, chosen by ear).
- **Nondeterministic voices** (Gemini, some Multilingual voices): a sound-merge check cannot compare single-render hashes, and a single bad take in the TTS cache is permanent (tunatale-u8nz.18).
- **The LLM's hyphens.** It writes affixed forms with U+2011 (`mag‑kape`), hence `story_text_normalizer`.
- **Homographs** need a sense in their card identity once a language has many short function words (tunatale-umbu, tunatale-u8nz.22).

If you find a touch-point this doc doesn't name, that's a doc bug. Update this file, and if core code needed an `if code == …`, treat it as a missing registry facet.

## Cross-references

- `docs/language-plugin-hardening.md`: why the registry and the literal gate exist.
- `docs/pimsleur.md`: what the syllabifier and breakdown buy pedagogically.
- `docs/deployment.md` § "Moving data between dev and prod": `data-transfer.sh` and `switch.sh`.
- Each plugin's `data/ATTRIBUTION.md` (`tl`, `ceb`): licences for the Wiktionary and FineWeb-2 data.
