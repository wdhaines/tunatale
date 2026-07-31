"""Shared LLM rate-limit shape literals for openapi ledger batch 6f.

Literal key-sets pinned against the UNFILTERED output of ``llm.py::
_status_payload``. Nested snapshots are whole-or-None (built as full dict
literals or left None), so a plain ``response_model=`` (no exclude_unset) is
safe. Serves GET /api/llm/rate-limit and POST /api/llm/rate-limit/probe.
"""

RATE_LIMIT_STATUS_KEYS = {
    "provider",
    "model",
    "llm_mode",
    "snapshot",
    "last_429",
    "tokens_used_24h",
    "tokens_per_day_limit",
}

RATE_LIMIT_SNAPSHOT_KEYS = {
    "age_s",
    "requests_limit",
    "requests_remaining",
    "requests_reset_in_s",
    "tokens_limit",
    "tokens_remaining",
    "tokens_reset_in_s",
}

RATE_LIMIT_LAST_429_KEYS = {
    "ago_s",
    "retry_in_s",
}
