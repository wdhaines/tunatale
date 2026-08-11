"""One-shot repair of the 13 defective TT-minted Norwegian rows (tunatale-s7f.5).

Repairs data the creation-path fixes (4265b32, 2f0e06c, 69bd1b5, 56d29d7) now
prevent but do not retroactively correct:

  A. 11 nouns missing their gender article  -> set_article (display-only)
  B. row 3015 `å lyve` glossed "is lying"   -> "lie"
  C. row 3022 `trø` (should be `trøtt`)     -> rename

**Every write is TT-local.** Nothing here opens collection.anki2. B and C stamp
``dirty_fields``, so the ordinary sync pushes them to Anki on its next run —
C keeps the same ``anki_note_id`` (1785381650319), so the card and its 6 reps /
1 lapse survive the rename. That is why this is a rename and not a grave +
recreate: the review history is preserved by decision (user, 2026-08-11).

Run ``--dry-run`` first; it prints every intended change and writes nothing.

    uv run python -m scripts.anki_archive.backfill_card_quality_no --dry-run
    uv run python -m scripts.anki_archive.backfill_card_quality_no --apply
"""

from __future__ import annotations

import argparse
import json
import sys

from app.srs.database import SRSDatabase

# Gender is re-derived from the lemma cache at runtime and CHECKED against this
# table; a mismatch aborts rather than writing. The table is the oracle, so a
# silent divergence (e.g. a stanza model-version bump moving a gender) must stop
# the run instead of quietly repairing 11 rows to different values.
# Fem -> "ei/en" matches the source deck's own convention (313 rows).
ARTICLE_TARGETS: tuple[tuple[int, str, str, str], ...] = (
    (2993, "etterforsker", "Masc", "en"),
    (2994, "jakke", "Masc", "en"),
    (2995, "avhør", "Neut", "et"),
    (2996, "etterforskningsteam", "Neut", "et"),
    (3000, "morder", "Masc", "en"),
    (3001, "eik", "Fem", "ei/en"),
    (3002, "drikk", "Masc", "en"),
    (3003, "kopp", "Masc", "en"),
    (3014, "avhørsrom", "Neut", "et"),
    (3016, "snømann", "Masc", "en"),
    (3021, "nabolag", "Neut", "et"),
)

_GENDER_ARTICLE = {"Masc": "en", "Fem": "ei/en", "Neut": "et"}

# (row id, new text, new translation). update_collocation_fields recomputes the
# guid from the text and stamps dirty_fields, so both reach Anki via sync.
TEXT_TARGETS: tuple[tuple[int, str, str], ...] = (
    (3015, "å lyve", "lie"),  # B: was "is lying" — a finite form on an infinitive front
    (3022, "trøtt", "tired"),  # C: was `trø`, an unrelated verb ("to tread")
)


def _cached_gender(db: SRSDatabase, lemma: str, sentence: str) -> str | None:
    """The cached stanza gender for *lemma* in *sentence*, or None if absent."""
    with db._get_conn() as conn:  # noqa: SLF001 — one-shot repair script
        rows = conn.execute(
            "SELECT analyses_json FROM lemma_analysis_cache WHERE sentence = ? AND language_code = 'no'",
            (sentence,),
        ).fetchall()
    for row in rows:
        for tok in json.loads(row["analyses_json"]):
            if tok["lemma"].casefold() == lemma.casefold() and tok["upos"] == "NOUN":
                return tok["gender"]
    return None


def _check_articles(db: SRSDatabase) -> list[str]:
    """Re-derive each gender from the cache; return a list of disagreements."""
    problems = []
    for row_id, lemma, want_gender, want_article in ARTICLE_TARGETS:
        found = db.get_collocation_by_id(row_id)
        if found is None:
            problems.append(f"row {row_id} ({lemma}): no longer exists")
            continue
        _, item, _ = found
        gender = _cached_gender(db, lemma, item.syntactic_unit.source_sentence)
        if gender is None:
            problems.append(f"row {row_id} ({lemma}): no cached NOUN analysis")
        elif gender != want_gender:
            problems.append(f"row {row_id} ({lemma}): cache says {gender!r}, table says {want_gender!r}")
        elif _GENDER_ARTICLE[gender] != want_article:
            problems.append(
                f"row {row_id} ({lemma}): {gender!r} maps to {_GENDER_ARTICLE[gender]!r}, not {want_article!r}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="print every change, write nothing")
    group.add_argument("--apply", action="store_true", help="perform the writes")
    parser.add_argument("--db", default="tunatale_no.db", help="path to the Norwegian SRS DB")
    args = parser.parse_args(argv)

    db = SRSDatabase(f"sqlite:///{args.db}")

    print("=== control: re-deriving gender from lemma_analysis_cache ===")
    problems = _check_articles(db)
    if problems:
        print("ABORT — the cache disagrees with the pinned table:")
        for p in problems:
            print(f"  {p}")
        print("\nThe table is the oracle. Investigate before writing anything.")
        return 1
    print(f"  all {len(ARTICLE_TARGETS)} genders confirmed against the cache\n")

    print("=== A. gender articles ===")
    for row_id, lemma, gender, article in ARTICLE_TARGETS:
        _, item, _ = db.get_collocation_by_id(row_id)
        current = item.syntactic_unit.article
        print(f"  {row_id:<5}{lemma:<22}{gender:<6}{current!r:<6} -> {article!r}")
        if args.apply:
            db.set_article(row_id, article)

    print("\n=== B/C. text + translation ===")
    for row_id, text, translation in TEXT_TARGETS:
        found = db.get_collocation_by_id(row_id)
        if found is None:
            print(f"  {row_id}: MISSING — skipped")
            continue
        _, item, _ = found
        unit = item.syntactic_unit
        print(f"  {row_id:<5}text        {unit.text!r} -> {text!r}")
        print(f"  {row_id:<5}translation {unit.translation!r} -> {translation!r}")
        if args.apply:
            db.update_collocation_fields(row_id, text=text, translation=translation)

    if args.apply:
        print("\nApplied. B and C are stamped dirty — run a sync to push them to Anki.")
    else:
        print("\nDry run — nothing written.")
    return 0


if __name__ == "__main__":  # pragma: no cover — one-shot CLI
    sys.exit(main())
