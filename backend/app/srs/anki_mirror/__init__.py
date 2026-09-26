"""Anki-mirror boundary — bit-exact ports of Anki's reference implementation.

Everything in this package exists SOLELY to reproduce Anki's behavior so TT's
live SRS matches the reference FSRS scheduler the user also grades in:

* ``_anki_rng``       — PCG32 → ChaCha12 → biased-Uniform fuzz RNG port.
* ``load_balancer``   — FSRS load-balancer (mirrors ``load_balancer.rs``).
* ``queue_engine``    — study-queue assembly (R-ascending sort, sibling bury,
                        intersperser spread, sync-time freeze).
* ``queue_stats``     — daily-cap / FSRS-param / learning-step resolution read
                        from ``anki_state_cache`` (Anki deck config).
* ``rollover``        — Anki 4 AM study-day rollover arithmetic.
* ``protobuf_wire``   — protobuf wire helpers + col-day index math.

This is the "eventually-removable" boundary from the plugin refactor: if the
"mirror Anki" strategy is ever dropped, this package and its ``test_parity_*``
goldens go together. Governed by ``.claude/rules/anki-queue-parity.md``.

These four modules have exactly ONE import path: ``app.srs.anki_mirror.<name>``.
Their former homes directly under ``app.srs`` were ``sys.modules`` aliases and
have been deleted, so a second path can never grow back unnoticed; ``rollover`` /
``protobuf_wire`` moved out of ``app.anki`` entirely (severing the
``app.srs → app.anki`` import edge) and are imported here directly.
"""
