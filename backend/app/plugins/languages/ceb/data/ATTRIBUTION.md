# Attribution: `cebuano_lemmas.tsv.gz`

This committed file is **derived from Wiktionary**, extracted by the
[kaikki.org](https://kaikki.org) / wiktextract pipeline:

- `cebuano_lemmas.tsv.gz` rewrites Wiktionary's Cebuano entries and their
  conjugation tables into a per-surface lemma table
  (`surface, upos, lemma, is_default`), built by
  `backend/scripts/build_cebuano_lemma_table.py`. Verb forms Wiktionary does not
  tabulate are generated from its root headwords by that script's affix rules.

- **Source:** Wiktionary (Cebuano entries), via kaikki.org postprocessed
  Wiktionary extract.
- **Source extract sha256:**
  `60b7806d656ac1eaa94f1dd7911fcda15f59908889324df3323e9d565d4a92c5`
- **Fetch date:** 2026-09-25
- **Derived files:** `backend/app/plugins/languages/ceb/data/cebuano_lemmas.tsv.gz`,
  and the `dictionary_gloss` column (and the unreviewed English) of
  `next_words.tsv`, built by `backend/scripts/build_cebuano_next_words.py`.

## Licence

Wiktionary and the kaikki.org extracts are licensed under the
**Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)**
licence: <https://creativecommons.org/licenses/by-sa/4.0/>.

**Share-alike covers the DATA files, not this repository's code:** the derived
files are data and are distributed under the same CC BY-SA 4.0 terms as
their source; the TunaTale source code that builds and consumes them is licensed
separately and is not affected.

# Attribution: `cebuano_frequency.tsv.gz`

This committed file holds **word counts derived from FineWeb-2**, built by
`backend/scripts/build_cebuano_frequency.py` (tunatale-u8nz.6). It contains
lemma/count pairs only — no text from the source.

- **Source:** FineWeb-2 (Hugging Face, `HuggingFaceFW/fineweb-2`), subset
  `ceb_Latn`, shard `000_00000.parquet`; only documents from four native
  Cebuano news hosts (sunstar.com.ph, rmn.ph, rpnradio.com, pia.gov.ph) are
  counted.
- **Source shard sha256:**
  `4982dae9f61107d1f9c60725bb8434e1d0d4b990f596c8fd1e890ebef5f98a3a`
- **Fetch date:** 2026-09-26

## Licence

FineWeb-2 is released under the **Open Data Commons Attribution License
(ODC-By) v1.0**: <https://opendatacommons.org/licenses/by/1-0/>, and its use is
also subject to Common Crawl's Terms of Use: <https://commoncrawl.org/terms-of-use>.
This file is the attribution ODC-By requires.

# Attribution: `cebuano_affix_counts.tsv.gz`

This committed file holds **root/affix counts derived from FineWeb-2**, built by
`backend/scripts/build_cebuano_affix_counts.py` (tunatale-ve4p.2). It contains
root/affix/count triples only — no text from the source.

- **Source:** the same FineWeb-2 `ceb_Latn` shard and the same four native news
  hosts as `cebuano_frequency.tsv.gz` above.
- **Source shard sha256:**
  `4982dae9f61107d1f9c60725bb8434e1d0d4b990f596c8fd1e890ebef5f98a3a`
- **Fetch date:** 2026-09-26

The licence is the one stated above (ODC-By v1.0, and Common Crawl's Terms of
Use); this section is the attribution it requires for this file.

# Attribution: `wolff_lexicon.json.gz`

This committed file is **derived from John U. Wolff, *A Dictionary of Cebuano
Visayan* (Cornell University / Linguistic Society of the Philippines, 1972)**, as
transcribed by Project Gutenberg (eBook #40074). It holds every entry paragraph
verbatim, parsed into senses and sub-entries, with each form and one example per
sense respelled to the Handbook spelling. Built by
`backend/scripts/build_cebuano_wolff_lexicon.py` (tunatale-h0q3.2); the source
file's sha256 is recorded in the file's own `meta`.

- **Source:** Project Gutenberg, <https://www.gutenberg.org/ebooks/40074>
- **Licence:** public domain. The dictionary's notice places it in the public
  domain since 1982; Project Gutenberg distributes it as such.

`handbook_course_spellings.tsv` is hand-kept: four words where the owner chose
the spelling of the respelled course over the dictionary's (2026-10-18).

# Attribution: `english_relations.json.gz`

This committed file is **derived from WordNet 3.0** (Princeton University), read
through NLTK's WordNet corpus reader. It holds English word links only: a word's
dictionary form, its synonyms and kind-of relations, and the words of its first
definitions that also occur in Wolff's glosses. The card matcher uses them to
match a card's English to a close meaning in Wolff (tunatale-h0q3.2). Built by
`backend/scripts/build_english_relations.py`.

- **Source:** WordNet 3.0, <https://wordnet.princeton.edu/>
- **Licence:** the WordNet 3.0 licence, which allows use, copying, modification
  and distribution for any purpose provided the copyright notice is kept:
  "WordNet 3.0 Copyright 2006 by Princeton University. All rights reserved."
- The English word list it is restricted to is wordfreq's top 50,000 English
  words (wordfreq is already a dependency; its data is CC BY-SA 4.0).
