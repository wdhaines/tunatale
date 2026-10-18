#!/usr/bin/env python
"""Measure the dictionary-to-Handbook converter against the respelled course (tunatale-h0q3.2).

    uv run python scripts/wolff_handbook_oracle.py                 # default paths under ~/Books
    uv run python scripts/wolff_handbook_oracle.py --tsv out.tsv   # also write every compared word

Every word that both Wolff's dictionary and the owner-QA'd Lessons 1-3 spell is
converted from the dictionary's spelling by
``app.plugins.languages.ceb.handbook_spelling`` and compared with the course's.
Two scopes are reported:

* **headwords**: the course word's letters (accents and e/o ignored) match a
  dictionary headword. The cleanest same-word test, though a contracted or
  inflected course word can share letters with an unrelated headword
  (``ámong`` 'our' beside ``amung`` 'drag to ruin').
* **written forms**: any word written in the dictionary's Cebuano, examples
  included, so inflected forms (``mòádto``, ``Màáyong``) are tested too. Names
  (capitalized only) and common English words are left out.

A key with several dictionary spellings (homographs: ``tulu`` 'three',
``túlù`` 'drip') counts as "one of" when the course spelling is among their
conversions. A course word of two or more syllables with no mark at all is
reported apart: Wolff's 1966 course left stress unmarked there, or it is English.

**Inputs, none of them in the repo.** The course is "All rights reserved", so
this reads it from the owner's book build (``~/Books/wolff/build/pages-l0N``)
and commits nothing of it. The dictionary is Project Gutenberg #40074. The
English loan sounds are ``~/Books/wolff/build/lex/en_cmu.tsv`` (word, Cebuano
sound spelling, wordfreq rank, made from the CMU Pronouncing Dictionary). The
corpus counts are the committed ``cebuano_frequency.tsv.gz``, and the Spanish
and English word lists come from wordfreq.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import html
import re
import unicodedata
from pathlib import Path

from app.plugins.languages.ceb.handbook_spelling import (
    SpellingLexicon,
    build_lexicon,
    handbook_spelling,
    strip_accents,
)

BOOKS = Path.home() / "Books"
DEFAULT_DICTIONARY = BOOKS / "cebuano-refs/wolff-dictionary/wolff-dictionary.txt"
DEFAULT_PAGES = [BOOKS / f"wolff/build/pages-l0{n}" for n in (1, 2, 3)]
DEFAULT_EN_SOUNDS = BOOKS / "wolff/build/lex/en_cmu.tsv"
FREQUENCY = Path(__file__).resolve().parents[1] / "app/plugins/languages/ceb/data/cebuano_frequency.tsv.gz"

_TOKEN = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ̀-ͯ\-]*")
_HEADS = re.compile(r"((?:[^\s,]+, )*[^\s,]+)")
# Words the caller spells by sense (ug/og, kun/kon): no spelling test can judge them.
SENSE_WORDS = {"ug", "og", "kun", "kon"}


def letters_key(word: str) -> str:
    """What the two spellings of one word share: no accents, no hyphen, e/o as i/u."""
    w = strip_accents(word).lower().replace("-", "")
    return w.replace("e", "i").replace("o", "u")


def _tokens(text: str) -> list[str]:
    return [unicodedata.normalize("NFC", t) for t in _TOKEN.findall(unicodedata.normalize("NFD", text))]


def entry_paragraphs(text: str) -> list[str]:
    """The dictionary body, one string per entry paragraph."""
    body = text[text.index("=== A ===") : text.index("*** END OF THE PROJECT")]
    paras = (re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", body))
    return [p for p in paras if p and not p.startswith("===")]


def headwords(paragraph: str) -> list[str]:
    """The entry's headword(s), 'tuu, túu_1 n ...' -> ['tuu', 'túu']; affix entries are skipped."""
    out = []
    for h in _HEADS.match(paragraph).group(1).split(", "):
        h = unicodedata.normalize("NFC", h.split("_")[0].rstrip(",.;"))
        if h and not h.startswith("-") and not h.endswith("-") and _tokens(h) == [h]:
            out.append(h)
    return out


class Dictionary:
    def __init__(self, paragraphs: list[str], english: set[str]):
        self.heads: dict[str, set[str]] = collections.defaultdict(set)
        self.entries: dict[str, str] = {}
        for p in paragraphs:
            for h in headwords(p):
                self.heads[letters_key(h)].add(h)
                self.entries.setdefault(strip_accents(h).lower(), strip_accents(p).lower())
        counts = collections.Counter(t for p in paragraphs for t in _tokens(p) if t[0] != "-" and t[-1] != "-")
        lowers = {letters_key(t) for t in counts if t[0].islower()}
        self.freq = counts
        self.forms: dict[str, set[str]] = collections.defaultdict(set)
        for t in counts:
            k = letters_key(t)
            if t[0].isupper() and k not in lowers and k not in self.heads:
                continue  # a name
            if t == strip_accents(t) and "-" not in t and t.lower() in english and k not in self.heads:
                continue  # English from a translation
            self.forms[k].add(t)


def course_tokens(page_dirs: list[Path]) -> collections.Counter:
    """Every word in the Cebuano-classed elements of the respelled lesson pages."""
    sel = re.compile(r'<(td|p|i|li)[^>]*class="(ceb|cebx|conv|cue)"[^>]*>(.*?)</\1>', re.S)
    items = re.compile(r'<ul class="items">(.*?)</ul>', re.S)
    out: collections.Counter = collections.Counter()
    for d in page_dirs:
        for f in sorted(d.glob("*.html")):
            s = f.read_text(encoding="utf-8")
            for c in [m.group(3) for m in sel.finditer(s)] + items.findall(s):
                c = re.sub(r"<b>.*?</b>", " ", c)
                c = re.sub(r'<(i|span) class="(ipa|orig|edins)".*?</\1>', " ", c)
                c = html.unescape(re.sub(r"<[^>]+>", " ", c))
                out.update(t.strip("-") for t in _tokens(c) if t.strip("-"))
    return out


def _syllables(word: str) -> int:
    return len(re.findall(r"[aeiou]", strip_accents(word).lower()))  # a vowel pair is two (ma.ay)


def compare(course: collections.Counter, forms: dict[str, set[str]], lex: SpellingLexicon, freq) -> list[dict]:
    """One row per distinct course word whose letters the dictionary spells."""
    rows, seen = [], set()
    for word, n in course.most_common():
        low, k = word.lower(), letters_key(word)
        if k not in forms or low in seen or low in SENSE_WORDS or len(word) == 1:
            continue
        seen.add(low)
        conv: dict[str, list[str]] = {}
        for d in sorted(forms[k], key=lambda d: (-freq[d], d)):
            conv.setdefault(handbook_spelling(d, lex).text.lower(), []).append(d)
        if low not in conv and _syllables(word) >= 2 and word == strip_accents(word) and "-" not in word:
            status = "course-unmarked"
        elif len(conv) == 1:
            status = "agree" if low in conv else "disagree"
        else:
            status = "one-of" if low in conv else "none-of"
        rows.append({"course": word, "count": n, "status": status, "conversions": conv})
    return rows


def summarize(rows: list[dict]) -> str:
    c = collections.Counter(r["status"] for r in rows)
    single = c["agree"] + c["disagree"]
    multi = c["one-of"] + c["none-of"]
    return (
        f"single spelling: {c['agree']}/{single} agree ({100 * c['agree'] / max(single, 1):.1f}%); "
        f"several spellings: course among them {c['one-of']}/{multi}; "
        f"course leaves it unmarked: {c['course-unmarked']}"
    )


def load_lexicon(dictionary: Dictionary, en_sounds: Path) -> SpellingLexicon:
    import wordfreq

    with gzip.open(FREQUENCY, "rt", encoding="utf-8") as fh:
        freq = {w: int(n) for w, n in (line.rstrip("\n").split("\t") for line in fh if not line.startswith("#"))}
    sounds = []
    for line in en_sounds.read_text(encoding="utf-8").splitlines():
        word, template, rank = line.split("\t")
        sounds.append((word, template, int(rank)))
    return build_lexicon(
        freq=freq,
        entries=dictionary.entries,
        es_words=wordfreq.top_n_list("es", 20000),
        en_sounds=sounds,
        english_common=set(wordfreq.top_n_list("en", 10000)),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY)
    ap.add_argument("--pages", type=Path, nargs="+", default=DEFAULT_PAGES)
    ap.add_argument("--en-sounds", type=Path, default=DEFAULT_EN_SOUNDS)
    ap.add_argument("--tsv", type=Path, help="write every compared word here")
    args = ap.parse_args(argv)

    import wordfreq

    english = set(wordfreq.top_n_list("en", 30000))
    dictionary = Dictionary(entry_paragraphs(args.dictionary.read_text(encoding="utf-8")), english)
    lex = load_lexicon(dictionary, args.en_sounds)
    course = course_tokens(args.pages)
    print(f"course: {len(course)} distinct words; dictionary: {len(dictionary.entries)} headwords")
    lines = []
    for label, forms in (("headwords", dictionary.heads), ("written forms", dictionary.forms)):
        rows = compare(course, forms, lex, dictionary.freq)
        print(f"\n## {label}: {summarize(rows)}")
        for r in rows:
            conv = "; ".join(f"{c} <- {'/'.join(ds)}" for c, ds in r["conversions"].items())
            lines.append(f"{label}\t{r['status']}\t{r['course']}\t{r['count']}\t{conv}")
            if r["status"] in ("disagree", "none-of", "course-unmarked"):
                print(f"  {r['status']:15} {r['course']:14} x{r['count']:<3} {conv}")
    if args.tsv:
        args.tsv.write_text("scope\tstatus\tcourse\tcount\tconversions\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
