"""Collocation span matching for transcript enrichment.

Scans a list of lemmas and annotates positions that match known multi-word
SRS collocations. Uses longest-match-first scanning; no overlaps.
"""

from __future__ import annotations


def match_spans(
    lemmas: list[str],
    collocation_index: dict[tuple[str, ...], int],
    max_span_len: int = 5,
) -> list[tuple[int | None, bool]]:
    """Annotate each token position with (span_id, is_start).

    Args:
        lemmas: Lemmatized token sequence for a single dialogue line.
        collocation_index: Maps lemma-tuple → SRS item DB id.
            Only tuples of length >= 2 are meaningful for collocation matching.
        max_span_len: Maximum collocation length to try (default 5).

    Returns:
        A list of (span_id, is_start) pairs, one per input lemma.
        span_id is the SRS item id if the token belongs to a collocation, else None.
        is_start is True only for the first token of a matched span.
    """
    n = len(lemmas)
    result: list[tuple[int | None, bool]] = [(None, False)] * n

    i = 0
    while i < n:
        matched = False
        # Try longest span first (greedy)
        for span_len in range(min(max_span_len, n - i), 1, -1):
            key = tuple(lemmas[i : i + span_len])
            if key in collocation_index:
                coll_id = collocation_index[key]
                for j in range(span_len):
                    result[i + j] = (coll_id, j == 0)
                i += span_len
                matched = True
                break
        if not matched:
            i += 1

    return result
