"""CLI backfill: synthesize TTS audio for existing cloze collocations missing media."""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.audio.cloze_tts import synthesize_cloze_audios
from app.config import settings
from app.srs.database import SRSDatabase

logger = logging.getLogger(__name__)


def backfill_cloze_tts(
    *,
    db_path: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    """Synthesize missing sentence + word audio for existing cloze collocations.

    Returns {'synthesized': N, 'skipped': M, 'total': T} counts.
    """
    resolved_path = db_path or settings.database_url

    db = SRSDatabase(resolved_path)

    with db._get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.source_sentence, c.lemma
            FROM collocations c
            WHERE c.card_type = 'cloze'
              AND c.source_sentence IS NOT NULL
              AND c.source_sentence != ''
            ORDER BY c.id
            """,
        ).fetchall()
    total = len(rows)
    if limit is not None:
        rows = rows[:limit]

    synthesized = 0
    skipped = 0

    for i, row in enumerate(rows, 1):
        collocation_id = row["id"]
        sentence = row["source_sentence"]
        lemma = row["lemma"]

        if not sentence or not lemma:
            skipped += 1
            continue

        sent_filename = db.get_sentence_audio_filename(collocation_id)
        word_filename = db.get_audio_filename(collocation_id)

        if sent_filename and word_filename:
            skipped += 1
        else:
            print(f"[{i}/{total}] {lemma} — {'dry-run' if dry_run else 'synthesizing'}", flush=True)
            if not dry_run:
                try:
                    asyncio.run(synthesize_cloze_audios(db, collocation_id, sentence, lemma))
                    synthesized += 1
                except Exception:
                    logger.warning("Failed to synthesize for collocation %d (%s)", collocation_id, lemma)
                    skipped += 1

    print(
        f"[DONE] synthesized={synthesized} skipped={skipped} total={total}" + (" (dry-run)" if dry_run else ""),
        flush=True,
    )
    return {"synthesized": synthesized, "skipped": skipped, "total": total}


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Backfill TTS audio for cloze collocations missing sentence/word audio",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without doing it")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of rows to process")
    args = parser.parse_args()
    backfill_cloze_tts(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":  # pragma: no cover
    _cli()
