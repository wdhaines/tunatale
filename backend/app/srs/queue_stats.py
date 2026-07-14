"""Back-compat alias — canonical home is ``app.srs.anki_mirror.queue_stats``.

The module was relocated into the ``app.srs.anki_mirror`` boundary (plugin
refactor, Stage 1). This shim aliases the old dotted path to the real module
object so existing imports and ``monkeypatch.setattr("app.srs.queue_stats.…")``
targets resolve to the same object (this is why the mock-boundary grandfather
entries keyed on ``app.srs.queue_stats.…`` stay valid unchanged).
"""

import sys

from app.srs.anki_mirror import queue_stats as _real

sys.modules[__name__] = _real
