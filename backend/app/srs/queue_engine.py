"""Back-compat alias — canonical home is ``app.srs.anki_mirror.queue_engine``.

The module was relocated into the ``app.srs.anki_mirror`` boundary (plugin
refactor, Stage 1). This shim aliases the old dotted path to the real module
object so existing imports and ``monkeypatch.setattr("app.srs.queue_engine.…")``
targets resolve to the same object.
"""

import sys

from app.srs.anki_mirror import queue_engine as _real

sys.modules[__name__] = _real
