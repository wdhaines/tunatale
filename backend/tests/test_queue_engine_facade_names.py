"""Guard: the queue engine's private helpers live in exactly one module.

These six names used to be identity re-exported by ``app.api.srs`` so HTTP-layer
and test call sites could reach through the API module. The re-exports are gone
(they were dead — ``app.api.srs`` itself calls only ``assemble_review_queue``),
so the single import path is ``app.srs.anki_mirror.queue_engine``. This pins that
name set there: a rename or a re-split of the queue engine is a breaking change
for every caller, and silently re-adding a forwarding alias in the API layer is
the drift the split exists to prevent.
"""

from app.srs.anki_mirror import queue_engine

_MOVED_NAMES = [
    "_fnv1a_64_i64",
    "_merge_by_retrievability_ascending",
    "_merge_directions",
    "_spread_mix",
    "_compute_live_main",
    "build_and_freeze_main_queue",
]


def test_queue_engine_owns_the_queue_helpers() -> None:
    for name in _MOVED_NAMES:
        assert callable(getattr(queue_engine, name, None)), name


def test_api_srs_does_not_forward_queue_engine_names() -> None:
    """The API layer must not become a second home for queue logic."""
    from app.api import srs as api_srs

    for name in _MOVED_NAMES:
        assert not hasattr(api_srs, name), name
