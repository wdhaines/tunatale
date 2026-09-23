# Attribution: `tagalog_lemmas.tsv.gz`, `tagalog_pronunciations.tsv.gz`

These committed files are **derived from Wiktionary**, extracted by the
[kaikki.org](https://kaikki.org) / wiktextract pipeline:

- `tagalog_lemmas.tsv.gz` rewrites Wiktionary's word entries into a per-surface
  lemma table (`surface, upos, lemma, is_default`), built by
  `backend/scripts/build_kaikki_lemma_table.py`.
- `tagalog_pronunciations.tsv.gz` keeps Wiktionary's narrow IPA readings per
  word and part of speech (`word, upos, rank, ipa, is_default`), built by
  `backend/scripts/build_kaikki_pronunciation_table.py`.

- **Source:** Wiktionary (Tagalog entries), via kaikki.org postprocessed
  Wiktionary extract.
- **Source extract sha256:**
  `7c8c903fffcba63d10dd60b3fcfa9eebc4dbbc8b5f92c00ada84cf95e5b91786`
- **Fetch date:** 2026-09-22
- **Derived files:** `backend/app/plugins/languages/tl/data/tagalog_lemmas.tsv.gz`
  and `backend/app/plugins/languages/tl/data/tagalog_pronunciations.tsv.gz`
  (the only files committed here that are derived from this source).

## Licence

Wiktionary and the kaikki.org extracts are licensed under the
**Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)**
licence: <https://creativecommons.org/licenses/by-sa/4.0/>.

**Share-alike covers the DATA files, not this repository's code:** the derived
`.tsv.gz` files are data and are distributed under the same
CC BY-SA 4.0 terms as its source; the TunaTale source code that builds and
consumes it is licensed separately and is not affected.