"""CLI backfill: synthesize TTS audio for existing cloze collocations missing media."""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.audio.cloze_tts import synthesize_cloze_audios
from app.config import settings
from app.languages import get_tts_voice, resolve_db_path
from app.srs.database import SRSDatabase
from app.srs.function_words import uncloze_text

logger = logging.getLogger(__name__)


def backfill_cloze_tts(
    *,
    db_path: str | None = None,
    language_code: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    """Synthesize missing sentence + word audio for existing cloze collocations.

    Returns {'synthesized': N, 'skipped': M, 'total': T} counts.

    With no explicit ``db_path``, the db is resolved for ``language_code``
    through the registry. It used to default to ``settings.database_url``, the
    singular setting, which names one fixed language regardless — so a backfill
    aimed at Norwegian synthesized against the Slovene db.
    """
    resolved_path = db_path or str(resolve_db_path(language_code or settings.target_language, settings))

    db = SRSDatabase(resolved_path)

    with db._get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.source_sentence, c.lemma, c.language_code
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
        sentence = uncloze_text(row["source_sentence"])
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
                    asyncio.run(
                        synthesize_cloze_audios(
                            db, collocation_id, sentence, lemma, voice=get_tts_voice(row["language_code"])
                        )
                    )
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
