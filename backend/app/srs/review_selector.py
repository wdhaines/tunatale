"""Select the review vocabulary a generated lesson should try to reinforce.

TunaTale knows precisely which words are decaying and when, and until this
existed it spent none of it on deciding what the next lesson SAYS. This is the
signal side of that (tunatale-fgeq); the prompt side is separate.

WHY A SIBLING OF ``build_learner_snapshot`` AND NOT A PARAMETER ON IT
(decided in tunatale-fgeq.1, do not re-litigate): that function's docstring
states a hard purity contract — "output is a pure function of DB contents" —
and its only app caller is the curriculum planner. A due-aware sample is by
definition a function of the clock, so adding one there would falsify the
contract AND change the planner's prompt text, invalidating planner cassettes
for a feature the planner never asked for.

The contract HERE is purity in (DB contents, ``now``). Tests pin ``now``; only
production lets it default. That is what keeps a cassette key stable.

THIS IS NOT A QUEUE AND MAKES NO ANKI-PARITY CLAIM. It reads the queue's due
pool and reuses the queue's retrievability, but its tie-break is content-based
(text, then row id) rather than Anki's ``fnvhash(card_id, mod)`` — determinism
for a prompt string is the goal, not reproducing Anki's ordering. It writes
nothing: selecting a word to APPEAR in a lesson must never touch SRS state.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.srs_item import Direction
from app.srs.anki_mirror.queue_stats import resolve_col_crt, resolve_fsrs_params
from app.srs.anki_mirror.rollover import anki_today
from app.srs.database import SRSDatabase
from app.srs.fsrs import compute_retrievability

# Sized against the prompt budget, not against pedagogy alone. Groq's free tier
# reserves prompt + max_completion against 8000 tokens/request, and the story
# system prompt is already ~2800 with a 4096 completion cap — leaving roughly a
# thousand tokens for the whole user prompt, which also carries the CEFR block,
# the story guidance and the new collocations. Twelve short entries is ~60
# tokens. It is also about as many extra words as a 2-3 scene story can absorb
# without becoming a word list.
DEFAULT_REVIEW_LIMIT = 12


def select_review_collocations(
    db: SRSDatabase,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_REVIEW_LIMIT,
    horizon_days: int = 0,
    direction: Direction = Direction.RECOGNITION,
) -> list[str]:
    """Return up to *limit* texts the learner is closest to forgetting.

    Ordered by ascending retrievability: the word most likely to have decayed
    comes first.

    ⚠️ RETRIEVABILITY, NOT DUE DATE, AND THE DIFFERENCE IS NOT COSMETIC. FSRS
    regulates R toward ``desired_retention``, so R is above it for a card not
    yet due, at it on the due day, and falls below as the card goes overdue —
    which makes R-ascending overdue-first by construction and a separate
    due-window filter redundant. What R adds over ``due_at`` is the rate of
    that fall: a card with two days of stability that is two days overdue is
    far more likely to be gone than one with six hundred days of stability
    forty days overdue, and a due date cannot see that.

    The candidate pool is exactly the review queue's due pool — same states,
    same ``due_at <= as_of`` bound (``get_due_items``). So the words the story
    reinforces are the words the queue is already asking about, which is the
    point; the sample is not a second opinion about what is due.

    *horizon_days* widens that bound into the future. It defaults to 0, today's
    behaviour, and was added for tunatale-6r44.

    ⚠️ IT IS A NO-OP UNLESS THE DUE POOL IS SMALLER THAN *limit*, and the
    rationale this docstring used to give ("a learner who hears a story a week
    after generating it") did not survive measurement. The ranking above is
    R-ASCENDING and truncated at *limit*; FSRS holds not-yet-due cards ABOVE
    ``desired_retention`` and overdue ones below, so widening ``as_of`` only
    appends cards that sort LAST. They reach the sample only when fewer than
    *limit* are already due. Measured 2026-09-03 on the real NO deck: 78 due
    against a limit of 12, so every horizon from 0 to +29 returned the same
    twelve words. Note this makes a large overdue backlog — the case tunatale-6r44
    was filed for — precisely the case where the parameter can do nothing, since a
    backlog is what guarantees the pool exceeds the limit. Do not wire a cadence
    into this expecting an effect without re-measuring that ratio first.

    NO TOPICAL FILTER, DELIBERATELY (user, 2026-09-02): "adding diversity
    through the additional vocab and letting the LLM use its best judgement for
    how/if to incorporate is kinda the point". A large overdue backlog feeding
    the generator old, off-theme words is the intended behaviour. Licensing the
    model to skip what does not fit is the PROMPT's job, not this function's.
    """
    today = anki_today(now)
    as_of = today + timedelta(days=horizon_days)
    params, _source = resolve_fsrs_params(db)
    col_crt = resolve_col_crt(db)

    ranked: list[tuple[float, str, int]] = []
    for row_id, item, _language_code in db.get_due_items(as_of, direction):
        ranked.append(
            (
                compute_retrievability(
                    item.directions[direction],
                    today,
                    now=now,
                    desired_retention=params.desired_retention,
                    decay=-params.decay,
                    col_crt=col_crt,
                ),
                item.syntactic_unit.text,
                row_id,
            )
        )
    # Text before row id: two rows with identical FSRS state must not have their
    # order decided by insertion sequence, or the sample stops being a function
    # of DB contents.
    ranked.sort()

    selected: list[str] = []
    seen: set[str] = set()
    for _retrievability, text, _row_id in ranked:
        if len(selected) >= limit:
            break
        # Homographs are separate rows sharing one `text` (that is what
        # `disambig_key` is for). The model only ever sees the text, so offering
        # it twice is noise.
        if text in seen:
            continue
        seen.add(text)
        selected.append(text)
    return selected
