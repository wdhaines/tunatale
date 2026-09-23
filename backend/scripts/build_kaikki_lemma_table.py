#!/usr/bin/env python3
"""Build the Tagalog lemma table from the kaikki.org Wiktionary extract.

Run from ``backend/``::

    uv run python scripts/build_kaikki_lemma_table.py
    uv run python scripts/build_kaikki_lemma_table.py --source <jsonl>

Why: Tagalog has no Stanza/classla model in this repo, but Wiktionary already
publishes the conjugation tables (kaikki.org, wiktextract), keyed by ROOT — the
measured design is ``.beads-tasks/briefs/design-philippine-morphology-2026-09.md``.
The rows go straight into the torch-free table engine
(``app.srs.lemma_table``): ``surface, upos, lemma, is_default``, one default row
per surface. Verb lemmas are ROOTS (``kumain`` → ``kain``); what a verb card
FRONTS is derived at mint time from ``app.plugins.languages.tl.verb_headword``
(the actor-focus infinitive).

Rules, in order (see the w4m7.6 brief for rationale):

1. kaikki ``pos`` → UPOS; every other pos is dropped.
2. Non-verb headwords: the entry's own ``word`` → itself, under its UPOS.
3. Verb roots: from the entry's etymology templates — the af-family templates
   or the ``ety`` template with ``args["2"] == ":af"``, after stripping
   Wiktionary's inline ``<id:…>``/``<t:…>`` modifiers. ``prefix`` gives the
   affix bare, so its root is its LAST arg; ``infix``/``suffix`` its FIRST; the
   rest hyphen-mark every affix, so the root is the longest arg that is a word
   (an affix typed without its hyphen, ``i`` beside ``halili``, loses; the
   later arg wins a tie). A prefix/infix/suffix template with one arg names no root.
   No root → the entry is skipped (the untabled 27% is out of scope).
4. Verb forms: the entry's own ``word`` plus every string in its ``forms``
   (kaikki's ``error-unrecognized-form`` tag lies: ``nakain`` is a word) → the
   root. Only Latin-script words survive: table metadata (Baybayin renderings,
   affix markers, template names, the ``actor`` column header) is dropped.
5. Shorter-root rule: when one surface has several candidate roots and one is
   itself an affixed form of another (``ibigay`` ⊃ ``bigay``), keep the shorter —
   implemented as a PROPERTY (drop A when another candidate B is a proper
   substring of A), not as a list scan.
6. ``is_default``: a surface that is itself a non-verb headword keeps that
   reading (UPOS tie-break) — except an INTERJECTION reading, which never does:
   an ``intj`` entry like ``makita`` ("let's see; we'll see") is a fixed verbal
   phrase, so a surface that is both an interjection and a verb form defaults to
   its VERB reading instead (the golden's ``makita`` → ``kita``). Otherwise the
   VERB reading, choosing the root with the most forms, then alphabetical.
   Everything else stays a non-default row.
7. The ``-ng`` linker, emitted at BUILD time so the lemmatizer stays untouched:
   for a non-verb headword ``h``, ``h+"g"`` when ``h`` ends in ``n``, ``h+"ng"``
   when ``h`` ends in a vowel. Precedence: (1) a candidate that is already a
   surface gets no linker row; (2) the ``n``+``g`` source wins over the
   vowel+``ng`` one (``naming`` → ``namin``, never ``nami``).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = _BACKEND / "scripts/local/kaikki/Tagalog.jsonl"
OUTPUT = _BACKEND / "app/plugins/languages/tl/data/tagalog_lemmas.tsv.gz"

# kaikki's lowercase POS → the UPOS tag the row carries. Anything else is dropped.
POS_TO_UPOS = {
    "noun": "NOUN",
    "verb": "VERB",
    "adj": "ADJ",
    "adv": "ADV",
    "pron": "PRON",
    "det": "DET",
    "num": "NUM",
    "prep": "ADP",
    "conj": "CCONJ",
    "intj": "INTJ",
    "particle": "PART",
    "name": "PROPN",
}

# Etymology templates whose args name a root directly (rule 3).
AFFIX_TEMPLATES = frozenset({"af", "affix", "prefix", "infix", "suffix", "circumfix", "confix"})
# These give the affix BARE, so its position is the only signal (measured on the
# extract: ``{{prefix|tl|in|antok}}``, ``{{infix|tl|bili|um}}``,
# ``{{suffix|tl|dala|hin}}``). The rest mark every affix with a hyphen. Reading
# prefix/infix/suffix with the hyphen rule keyed 722 verb rows on a bare affix
# (``aandar`` → ``um``, ``pahingi`` → ``pa``).
ROOT_IS_LAST = frozenset({"prefix"})
ROOT_IS_FIRST = frozenset({"infix", "suffix"})
# Wiktionary's inline modifiers on a template arg: ``basa<id:wet>``,
# ``baon<t:bury>``. Unstripped, they keyed 6,167 verb rows (7%) on strings like
# ``baon<t:bury>`` and let ``ma-<id:stative prefix>`` pass as a root.
_INLINE_MODIFIER = re.compile(r"<[^<>]*>")

# A surface must be a Latin-script word (internal hyphens and apostrophes
# allowed). kaikki's form lists also carry table METADATA as form strings: the
# Baybayin-script rendering, affix markers (``mag-``, ``-um-``, ``-``), the
# template name (``tl-infl-mag``, tagged ``inflection-template``), the
# ``no-table-tags`` sentinel (tagged ``table-tags``), and the conjugation
# table's focus-column header (``actor``, 780 verbs), none of which is a word.
_WORD = re.compile(r"[a-z\u00c0-\u024f]+(?:['\u2019-][a-z\u00c0-\u024f]+)*")
_METADATA_TAGS = frozenset({"table-tags", "inflection-template", "class"})
_TABLE_LABELS = frozenset({"actor", "object", "locative", "benefactive", "instrument", "directional", "causative"})

VOWELS = frozenset("aeiou")

# Deterministic tie-break among several non-verb readings of one surface — the
# same order the stanza builder uses for its bare-word default.
UPOS_ORDER = (
    "NOUN",
    "VERB",
    "ADJ",
    "ADV",
    "PRON",
    "DET",
    "ADP",
    "AUX",
    "NUM",
    "PROPN",
    "CCONJ",
    "SCONJ",
    "INTJ",
    "PART",
)

# ── the golden table (w4m7.5 spike; same literals as tests/test_tagalog_lemma_table.py) ──

GOLDEN_VERBS_BY_ROOT = {
    "kain": ["kumain", "kumakain", "kakain", "kinain", "kinakain", "kakainin", "kainin"],
    "bili": ["bumili", "bumibili", "bibili", "binili", "bilhin"],
    "punta": ["pumunta", "pupunta", "pumupunta", "nagpunta"],
    "sabi": ["sinabi", "sabihin", "sinasabi", "magsabi", "nagsabi"],
    "hanap": ["hinahanap", "hanapin", "hinanap", "naghanap"],
    "trabaho": ["nagtrabaho", "nagtatrabaho", "magtatrabaho"],
    "uwi": ["umuwi", "umuuwi", "uuwi"],
    "inom": ["uminom", "iinom", "ininom"],
    "tulog": ["natulog", "natutulog", "matutulog", "matulog"],
    "kita": ["nakita", "nakikita", "makikita", "makita"],
    "intindi": ["naintindihan", "nakakaintindi", "intindihin"],
    "bigay": ["ibinigay", "ibibigay", "binigay"],
    "sulat": ["sumulat", "sinulat", "isusulat"],
    "laro": ["naglalaro", "maglaro"],
}
GOLDEN_VERBS = {surface: root for root, surfaces in GOLDEN_VERBS_BY_ROOT.items() for surface in surfaces}

GOLDEN_SELF_HEADWORDS = (
    "ako",
    "siya",
    "bahay",
    "kaibigan",
    "bukas",
    "wala",
    "maganda",
    "marami",
    "mga",
    "ang",
)

GOLDEN_LINKER = {
    "akong": "ako",
    "maraming": "marami",
    "dalawang": "dalawa",
    "magandang": "maganda",
    "mong": "mo",
    "walang": "wala",
    "naming": "namin",
    "noong": "noon",
    "kaunting": "kaunti",
}

GOLDEN_TOTAL = len(GOLDEN_VERBS) + len(GOLDEN_SELF_HEADWORDS) + len(GOLDEN_LINKER)  # 72

Readings = dict[str, list[tuple[str, str]]]
Rows = set[tuple[str, str, str, int]]


# ── rules 1–4: extract rows from parsed entries ────────────────────────────────


def _positionals(args: dict) -> list[str]:
    """Positional template args (``"1"``, ``"2"``, ...) in numeric order."""
    keys = sorted((k for k in args if k.isdigit()), key=int)
    return [args[k] for k in keys if isinstance(args[k], str)]


def root_candidates(templates: list | None) -> set[str]:
    """Rule 3: the root(s) one verb entry's etymology templates name."""
    roots: set[str] = set()
    for template in templates or []:
        name = template.get("name")
        args = template.get("args") or {}
        if name in AFFIX_TEMPLATES:
            start = 1  # from positional arg "2" onward
        elif name == "ety" and args.get("2") == ":af":
            start = 2  # from positional arg "3" onward
        else:
            continue
        parts = [_INLINE_MODIFIER.sub("", v).strip() for v in _positionals(args)[start:]]
        if name in ROOT_IS_LAST or name in ROOT_IS_FIRST:
            # One arg is the affix alone (``{{prefix|tl|mag}}``, its root in a
            # separate ``compound`` template): it names no root.
            if len(parts) < 2:
                continue
            positional = parts[-1:] if name in ROOT_IS_LAST else parts[:1]
        else:
            positional = parts
        # Longest word arg, the LAST on a tie. Normally exactly one arg is
        # unmarked; this covers entries whose affix was typed without its hyphen
        # (``{{ety|tl|:af|i|halili}}`` → ``halili``), and a hyphenless affix is a
        # prefix, so it precedes its root (``mang``/``amoy`` → ``amoy``).
        words = [value for value in positional if _is_word(value)]
        if words:
            roots.add(max(reversed(words), key=len))
    return roots


def _is_word(value: str) -> bool:
    """A Latin-script word: no affix hyphen at either end, no markup, no other script."""
    return bool(_WORD.fullmatch(value.lower()))


def _is_word_form(form: dict) -> bool:
    """Rule 4's filter on one ``forms`` entry: a real word, not table metadata."""
    text = form.get("form") or ""
    return (
        _is_word(text) and text.lower() not in _TABLE_LABELS and not _METADATA_TAGS.intersection(form.get("tags") or ())
    )


def _entry_surfaces(entry: dict) -> set[str]:
    """Rule 4: the entry's own word plus its form strings, space/empty dropped."""
    word = entry.get("word") or ""
    forms = {f["form"] for f in entry.get("forms") or [] if _is_word_form(f)}
    return forms | ({word} if _is_word(word) else set())


def extract(entries: Iterable[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]], Counter]:
    """Rules 1–4: (verb surface → candidate roots, non-verb headword → UPOSes, per-pos counts)."""
    verb_surfaces: dict[str, set[str]] = defaultdict(set)
    nonverb: dict[str, set[str]] = defaultdict(set)
    counts: Counter = Counter()
    for entry in entries:
        pos = entry.get("pos") or "?"
        counts[pos] += 1
        upos = POS_TO_UPOS.get(pos)
        if upos is None:
            continue
        if pos == "verb":
            roots = root_candidates(entry.get("etymology_templates"))
            if not roots:
                continue
            for surface in _entry_surfaces(entry):
                verb_surfaces[surface] |= roots
        else:
            word = entry.get("word") or ""
            if word and " " not in word:
                nonverb[word] |= {upos}
    return dict(verb_surfaces), dict(nonverb), counts


# ── rule 5: the shorter-root property ──────────────────────────────────────────


def apply_shorter_root(verb_surfaces: dict[str, set[str]]) -> dict[str, set[str]]:
    """Drop candidate A when another candidate B is a proper substring of A.

    ``ibigay`` is itself i- + ``bigay``; every surface that collected both as a
    candidate (through the two entries' forms) keeps the shorter one.
    """
    return {
        surface: {r for r in roots if not any(other != r and other in r for other in roots)}
        for surface, roots in verb_surfaces.items()
    }


# ── rule 6: exactly one default row per surface ────────────────────────────────


def assign_defaults(
    verb_surfaces: dict[str, set[str]], nonverb: dict[str, set[str]], linkers: set[tuple[str, str, str]]
) -> Rows:
    """Materialise every reading as ``rows`` with rule 6's ``is_default``."""
    root_form_count: Counter = Counter()
    for roots in verb_surfaces.values():
        root_form_count.update(roots)

    readings: Readings = {}
    for surface, roots in verb_surfaces.items():
        readings.setdefault(surface, []).extend(("VERB", root) for root in roots)
    for surface, uposes in nonverb.items():
        readings.setdefault(surface, []).extend((upos, surface) for upos in uposes)
    for surface, upos, lemma in linkers:
        readings.setdefault(surface, []).append((upos, lemma))

    rows: Rows = set()
    for surface, surface_readings in readings.items():
        default = _pick_default(surface, surface_readings, nonverb, root_form_count)
        rows.update((surface, upos, lemma, int((upos, lemma) == default)) for upos, lemma in surface_readings)
    return rows


def _pick_default(
    surface: str, readings: list[tuple[str, str]], nonverb: dict[str, set[str]], root_form_count: Counter
) -> tuple[str, str]:
    if surface in nonverb:
        # Rule 6a: the surface is itself a non-verb headword → that reading,
        # tie-broken by UPOS_ORDER when several non-verb readings exist. An INTJ
        # reading is excluded: an interjection (makita "let's see; we'll see",
        # penge "gimme") is a quoted verbal phrase, not an independent headword,
        # so where a surface also has a VERB reading, the verb wins — the golden
        # demands `makita` → `kita`. An INTJ-only surface still keeps its row.
        headword = [r for r in readings if r[0] in nonverb[surface] and r[0] != "INTJ"]
        if headword:
            return min(headword, key=lambda r: UPOS_ORDER.index(r[0]))
    verb = [r for r in readings if r[0] == "VERB"]
    if verb:
        # Rule 6b: the root with the most forms, then alphabetical.
        return min(verb, key=lambda r: (-root_form_count[r[1]], r[1]))
    # A linker surface (rule 7): its single reading is the default by construction.
    return readings[0]


# ── rule 7: the -ng linker ─────────────────────────────────────────────────────


def linker_rows(nonverb: dict[str, set[str]], base_surfaces: set[str]) -> set[tuple[str, str, str]]:
    """Linker candidate rows for non-verb headwords, precedence (1) then (2).

    ``base_surfaces`` are the table's surfaces before linkers: a candidate that
    is already one (``ang``, ``nang``, ``ng``) gets no row. When two headwords
    generate the same surface, the ``n``+``g`` source wins over the vowel+``ng``
    one (``naming`` → ``namin``, never ``nami``).
    """
    g_source: dict[str, tuple[str, str]] = {}
    ng_source: dict[str, tuple[str, str]] = {}
    for headword, uposes in sorted(nonverb.items()):
        upos = min(uposes, key=UPOS_ORDER.index)  # the headword's default UPOS
        if headword.endswith("n"):
            g_source.setdefault(headword + "g", (headword, upos))
        elif headword[-1] in VOWELS:
            ng_source.setdefault(headword + "ng", (headword, upos))
    rows = {(cand, upos, headword) for cand, (headword, upos) in g_source.items() if cand not in base_surfaces}
    rows |= {
        (cand, upos, headword)
        for cand, (headword, upos) in ng_source.items()
        if cand not in base_surfaces and cand not in g_source
    }
    return rows


# ── the golden report ──────────────────────────────────────────────────────────


def read_golden_matches(rows: Rows) -> tuple[dict[str, str], int]:
    """Golden checks the row set passes, keyed by surface (denominator is fixed at 72)."""
    default_lemma = {surface: lemma for surface, _upos, lemma, is_default in rows if is_default}
    matches: dict[str, str] = {}
    for surface, root in GOLDEN_VERBS.items():
        if default_lemma.get(surface) == root:
            matches[surface] = root
    for word in GOLDEN_SELF_HEADWORDS:
        if default_lemma.get(word) == word:
            matches[word] = word
    for surface, base in GOLDEN_LINKER.items():
        if default_lemma.get(surface) == base:
            matches[surface] = base
    return matches, GOLDEN_TOTAL


def _golden_count(verb_surfaces: dict[str, set[str]], nonverb: dict[str, set[str]], with_linker: bool) -> int:
    linkers = linker_rows(nonverb, set(verb_surfaces) | set(nonverb)) if with_linker else set()
    rows = assign_defaults(verb_surfaces, nonverb, linkers)
    return len(read_golden_matches(rows)[0])


# ── the build ──────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_from_path(source: Path, output: Path) -> dict:
    """Build the table from *source* JSONL into *output* ``.tsv.gz``, print the report."""
    sha_hex = _sha256(source)
    with open(source, encoding="utf-8") as fh:
        verb_surfaces, nonverb, counts = extract(json.loads(line) for line in fh if line.strip())

    golden_raw = _golden_count(verb_surfaces, nonverb, with_linker=False)
    pruned = apply_shorter_root(verb_surfaces)
    golden_pruned = _golden_count(pruned, nonverb, with_linker=False)

    linkers = linker_rows(nonverb, set(pruned) | set(nonverb))
    rows = assign_defaults(pruned, nonverb, linkers)
    golden_final = len(read_golden_matches(rows)[0])

    # Emit: surfaces sorted, readings in UPOS order then lemma (deterministic).
    out = io.StringIO()
    out.write(f"#source_version=kaikki-{sha_hex[:12]}\n")
    by_surface: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for surface, upos, lemma, is_default in rows:
        by_surface[surface].append((upos, lemma, is_default))
    for surface in sorted(by_surface):
        for upos, lemma, is_default in sorted(by_surface[surface], key=lambda r: (UPOS_ORDER.index(r[0]), r[1])):
            out.write(f"{surface}\t{upos}\t{lemma}\t{is_default}\n")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(out.getvalue().encode("utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(buf.getvalue())

    multi_lemma = sum(1 for readings in by_surface.values() if len({r[1] for r in readings}) > 1)
    stats = {
        "sha256": sha_hex,
        "golden": (golden_final, GOLDEN_TOTAL),
        "rows": len(rows),
        "surfaces": len(by_surface),
        "multi_lemma": multi_lemma,
        "size": output.stat().st_size,
        "pos_counts": dict(counts),
    }
    print(f"source: {source} ({sha_hex[:12]})")
    print(f"per-pos entry counts: {dict(sorted(counts.items()))}")
    print(
        f"golden: {GOLDEN_TOTAL} checks — before rules 5/7: {golden_raw}, after rule 5: {golden_pruned}, after rule 7: {golden_final}"
    )
    print(f"total rows: {stats['rows']}; distinct surfaces: {stats['surfaces']}; surfaces with >1 lemma: {multi_lemma}")
    print(f"wrote {output} ({stats['size']} bytes)")
    return stats


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="kaikki Tagalog JSONL extract")
    args = parser.parse_args(argv)
    if not args.source.exists():
        parser.error(f"source extract not found: {args.source}")
    build_from_path(args.source, OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
