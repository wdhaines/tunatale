"""Lemma lookup + analysis/image-query cache mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 4).
Lemma-keyed collocation lookups plus the lemma_analysis_cache and
image_query_cache memoization tables.
"""

from app.models.srs_item import SRSItem


class DbLemmaCacheMixin:
    """Lemma lookups + analysis/image-query caches. Mixed into SRSDatabase."""

    def get_collocation_by_lemma(self, lemma: str) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE lemma = ? LIMIT 1", (lemma,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_by_lemma_with_id(self, lemma: str) -> tuple[int, SRSItem] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE lemma = ? LIMIT 1", (lemma,)).fetchone()
            if row is None:
                return None
            return (row["id"], self._row_to_item(conn, row))

    def get_variant_candidates_with_items(self, language_code: str, separator: str) -> list[tuple[int, str, SRSItem]]:
        """Hydrated (id, text, item) for *language_code* rows whose front contains *separator*.

        Candidates for the reader's spelling-variant index: a front like Norwegian
        ``mot, imot`` lists alternate spellings of one word. Scans and hydrates in
        ONE query — the old scan-then-refetch-by-id shape left a window where the
        row could vanish between queries, forcing a dead "row vanished" branch on
        the caller. The caller confirms each row is a genuine variant list (all
        single-word parts) via ``languages.card_surface_variants`` — this method
        only narrows the scan.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE language_code = ? AND text LIKE ?",
                (language_code, f"%{separator}%"),
            ).fetchall()
            return [(row["id"], row["text"], self._row_to_item(conn, row)) for row in rows]

    def get_inflection_clozes_for_lemma(self, lemma: str) -> list[tuple[int, SRSItem]]:
        """All morphology (inflection) clozes for a lemma, hydrated with directions.

        Inflection clozes are card_type='cloze' with a disambig_key like 'morph:%'
        (set by the /listen morphology path and POST /inflection-clozes). This
        deliberately EXCLUDES the lemma's plain function-word base cloze, which
        has disambig_key NULL/empty.
        Returns (collocation_id, SRSItem) per row; empty list if none.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE lemma = ? AND card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
                (lemma,),
            ).fetchall()
            return [(row["id"], self._row_to_item(conn, row)) for row in rows]

    def get_collocations_with_lemma_key(
        self,
        language_code: str,
        min_word_count: int = 2,
    ) -> list[tuple[int, str, str | None]]:
        """Return (id, text, lemma_key) for collocations of at least min_word_count words.

        lemma_key is the space-joined lemma tuple for multi-word span matching
        (NULL until first computed). Read by transcript._build_collocation_index,
        which lazily fills any NULL via set_lemma_key.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, text, lemma_key FROM collocations WHERE language_code = ? AND word_count >= ?",
                (language_code, min_word_count),
            ).fetchall()
        return [(row["id"], row["text"], row["lemma_key"]) for row in rows]

    def set_lemma_key(self, row_id: int, lemma_key: str) -> None:
        """Persist the precomputed lemma_key for a collocation (span-match cache)."""
        with self._get_conn() as conn:
            conn.execute("UPDATE collocations SET lemma_key = ? WHERE id = ?", (lemma_key, row_id))
            self._commit(conn)

    def get_sentence_analysis(self, sentence: str, language_code: str, model_version: str) -> str | None:
        """Return cached analyses_json for a sentence, or None on miss."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT analyses_json FROM lemma_analysis_cache WHERE sentence = ? AND language_code = ? AND model_version = ?",
                (sentence, language_code, model_version),
            ).fetchone()
        return row["analyses_json"] if row else None

    def set_sentence_analysis(self, sentence: str, language_code: str, model_version: str, analyses_json: str) -> None:
        """Upsert a sentence analysis into the persistent cache."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO lemma_analysis_cache (sentence, language_code, model_version, analyses_json, updated_at)"
                " VALUES (?, ?, ?, ?, datetime('now'))",
                (sentence, language_code, model_version, analyses_json),
            )
            self._commit(conn)

    def get_image_query(self, word: str, english: str, model_version: str) -> str | None:
        """Return the cached image-search query for a card, or None on miss.

        An empty-string result is a *hit*, not a miss: it is the sentinel for
        "this word is abstract, don't fetch an image". Callers must check
        ``is not None`` rather than truthiness.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT query FROM image_query_cache WHERE word = ? AND english = ? AND model_version = ?",
                (word, english, model_version),
            ).fetchone()
        return row["query"] if row else None

    def set_image_query(self, word: str, english: str, model_version: str, query: str) -> None:
        """Upsert an image-search query (possibly the empty-string skip sentinel)."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO image_query_cache (word, english, model_version, query, updated_at)"
                " VALUES (?, ?, ?, ?, datetime('now'))",
                (word, english, model_version, query),
            )
            self._commit(conn)
