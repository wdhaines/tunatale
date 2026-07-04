"""Guard: app.api.srs re-exports the queue engine (god-module split).

If a name here stops being an identity re-export, someone re-implemented
queue logic in the API layer — the exact drift the split exists to prevent.
"""

from app.api import srs as api_srs
from app.srs import queue_engine

_MOVED_NAMES = [
    "_fnv1a_64_i64",
    "_merge_by_retrievability_ascending",
    "_merge_directions",
    "_spread_mix",
    "_compute_live_main",
    "build_and_freeze_main_queue",
]


def test_queue_engine_names_are_reexports() -> None:
    for name in _MOVED_NAMES:
        assert getattr(api_srs, name) is getattr(queue_engine, name), name
