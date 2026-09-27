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
- **Derived file:** `backend/app/plugins/languages/ceb/data/cebuano_lemmas.tsv.gz`
  (the only file committed here that is derived from this source).

## Licence

Wiktionary and the kaikki.org extracts are licensed under the
**Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)**
licence: <https://creativecommons.org/licenses/by-sa/4.0/>.

**Share-alike covers the DATA file, not this repository's code:** the derived
`.tsv.gz` file is data and is distributed under the same CC BY-SA 4.0 terms as
its source; the TunaTale source code that builds and consumes it is licensed
separately and is not affected.
