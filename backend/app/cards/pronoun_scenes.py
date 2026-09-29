"""Pronoun pictures: a conversation, the people in it, and the ones it is about.

**A photo search cannot serve this word class at all.** Asked for "she" it
returns a portrait of one person, and the thing the word says is not that there
is a person — it is that the referent is somebody the speaker is *talking to*,
and not the speaker. That is a relation between people, and a photograph has no
way to carry it in a form anyone can check. So these concepts are **drawn**,
which is the whole case rather than a preference: in a render the relation is
in the coordinates, so "the other is filled and stands outside the outline" is
a number a test reads off the SVG and compares with a table — where in a
photograph it is a judgement about pixels that nobody can check, and so is not a
test at all. Same argument as ``app.cards.number_scenes`` and
``app.cards.spatial_scenes``; the pictures look like diagrams, and that cost was
accepted for those two as well.

**One visual convention for all fourteen concepts**, so a learner meets the same
cast every time and only who is filled changes: a dashed rounded **outline** is
the conversation, the **speaker** stands at its left with a speech bubble whose
tail points at their head, the **listeners** stand inside the outline with the
speaker, and everybody the word points at is **filled** while everybody else is
**pale**. Nothing is labelled: a printed "she" would make the card answerable by
reading it, which is the one thing a picture on a vocabulary card must not do.

**The minimal cast is a decision with a reason.** A speaker and at least one
listener are always drawn — there has to be an inside for "not them" to be
outside of, and a scene of one standing figure says nothing at all. Beyond that
only the referents are drawn, so the eye is left with nothing to confuse.

**He and she carry a gender mark, and third-one does not.** A small ♂ or ♀
circle above the head, in the accent colour, is the only difference between the
two — and ``third_one`` (Tagalog and Cebuano ``siya``) has none, because no
language says which it is and a mark would teach a fact the word does not
carry. ``it`` is a **lit cat** outside the outline instead of a person, so the
one non-human referent is not drawn as a person.

**The palette is shared** — background, filled reference and pale fill imported
from the counting pictures, the outline and the accent from the spatial ones, so
all of them read as one deck — plus one new colour, :data:`_REF_STROKE`, the
outline of a *filled* figure. Contrast is a property here and not a style note:
the pale fill is 1.10:1 on the background, which is why every pale figure also
carries an outline, and the tests recompute the WCAG ratio of every colour pair
each render actually uses and hold it to the figures measured before this was
written.

**``we_two`` and ``we_incl`` render identically, on purpose.** No language has
both, and a contrast the drawing cannot show is not a distinction worth drawing.
They are asserted byte-identical so that splitting them later is a decision
somebody makes on purpose rather than an accident that survives.

**A possessive is not a second cast; it is this scene with a bag added to every
lit referent** (tunatale-uy38, the user 2026-09-28). Tagalog ``siya`` ("he/she")
and ``niya`` ("his/her") are the same conversation with the same filled figures,
so if the possessive drew its own layout the two words would be told apart by
anything but the bags — and a learner could not say which difference they were
being asked to see. One layout and one addition is therefore the whole design:
the render is its nominative counterpart plus :func:`_bag` per lit referent, and
the tests assert that as a *difference* (strip the bags, get the other render
back byte for byte) rather than trusting the code path. The one referent with no
hands is the cat, whose bag therefore stands **on the ground** to the right of
it, clear of the animal — the single case where the group also has to move to
stay centred, and the single exception the byte-for-byte test allows.

**A Norwegian possessive then says more than its nominative does**
(tunatale-l1ba): ``min`` is "my", ``mi`` "my" of a feminine thing, ``mitt`` of a
neuter one and ``mine`` of a plural. Four words on one picture family, and the
only thing that tells them apart is a mark drawn **inside** the bag, in the bag's
own stroke colour rather than :data:`_ACCENT` — a referent's natural gender is
already drawn in the accent above a head, and the same glyph in both places would
read as one fact about one person. The marks are ♂, ♀ and ⚲, and ♀ and ⚲ are the
pair that shapes the design: same ring, same vertical stem, one crossbar between
them. A plural is not a gender, so it is a **second bag** behind the first, and
it carries no mark at all.

Nothing here is per-language. The concept ids are the *picture's* key, and a
language's own words are mapped onto them by the caller — which is where the
language lives, and what keeps this module clear of one.
"""

from __future__ import annotations

from typing import NamedTuple

from app.cards.number_image import _BG, _DOT_FILL, _ROD_FILL
from app.cards.spatial_scenes import _ACCENT, _BOX_STROKE

#: The concepts this module knows how to draw, and the only ones: an id outside
#: this list is refused rather than approximated, because a picture that puts the
#: referent inside the conversation when the card asked for "he" is worse than no
#: picture, because it is confidently wrong.
#:
#: The fourteen possessives follow their nominatives, in the same order, and are
#: the SAME SCENE plus a bag per lit referent — see :func:`render_pronoun_svg`.
PRONOUN_CONCEPTS: tuple[str, ...] = (
    "i",
    "you_one",
    "he",
    "she",
    "third_one",
    "it",
    "we_two",
    "we_many",
    "we_incl",
    "we_excl",
    "you_two",
    "you_many",
    "they_two",
    "they_many",
    "i_poss",
    "you_one_poss",
    "he_poss",
    "she_poss",
    "third_one_poss",
    "it_poss",
    "we_two_poss",
    "we_many_poss",
    "we_incl_poss",
    "we_excl_poss",
    "you_two_poss",
    "you_many_poss",
    "they_two_poss",
    "they_many_poss",
)

#: The suffix that turns a concept into its possessive. One suffix, one rule: the
#: scene is looked up under the nominative name, so there is exactly one layout
#: in this module and "his" cannot drift away from "he".
_POSSESSIVE = "_poss"

#: The outline of a *filled* figure. The only colour new in this module for the
#: nominative cast: the pale fill's own outline would be invisible against it, so
#: a lit figure needs a dark one to read as a solid shape rather than a silhouette.
_REF_STROKE = "#1f406f"

#: The bag a lit referent carries in a possessive scene. The user rejected the
#: first mockup's orange: it was 1.40:1 against the filled body it hangs on, and
#: a bag nobody can see is not a picture of possession. These are 4.57:1 on the
#: body, 7.60:1 on the background and 5.58:1 on the bag's own fill — all
#: comfortably past the 3.0 the tests hold every colour pair in these pictures
#: to, and recomputed from the markup rather than asserted here.
BAG_FILL = "#ffd23f"
BAG_STROKE = "#6b4a00"

# ── One frame for every concept ─────────────────────────────────────────────
# The same 260x220 the spatial pictures use, so a deck mixes them without the
# cards changing size. The ground is a touch higher than theirs because the
# figures are taller than a box: a person stands on it, and the outline the
# conversation occupies is a dashed rounded rect whose bottom sits below it, so
# the cast is inside the outline on the ground rather than resting on its edge.

_W = 260
_H = 220
_G = 198

#: A person: a head of this radius, a body twice as wide, and three units of
#: air between them — the gap is what reads as a neck rather than a blob.
_HEAD_R = 12
_BODY_HALF = 12
_BODY_TOP = _G - 64
_HEAD_CY = _BODY_TOP - _HEAD_R - 3

#: The speaker is pinned to the left of the cast, the listener beside them is a
#: clear step away (close enough to read as one conversation, far enough that
#: two heads do not touch), and the outline takes the speaker's bubble width at
#: its left and one step past the last listener at its right.
_SPEAKER_X = 24
_STEP = 30
_FIRST_LISTENER_DX = 38
_PANEL_PAD = 12

#: How far the first referent stands off the outline. Further for the cat,
#: whose body and tail are wider than a person's.
_GAP = 26
_GAP_CAT = 34

#: The conversation outline, from this far above the bubble to this far below
#: the ground, so the dashed line never passes through a figure.
_PANEL_TOP = 24
_PANEL_BELOW = 10

#: The speech bubble, and the gap left between the tail's point and the head it
#: points at.
_BUBBLE_W = 66
_BUBBLE_H = 38
_BUBBLE_TOP = 34
_BUBBLE_DY = _HEAD_CY - _HEAD_R - (_BUBBLE_TOP + _BUBBLE_H) - 6
_BUBBLE_R = 14

#: The gender mark: a ring this far above the head, and the reach of the two
#: strokes that tell ♀ from ♂.
_MARK_DY = _HEAD_R + 16
_MARK_R = 6
_MARK_CROSS = 8
_MARK_ARM = 4
_MARK_ARROW = 6
_MARK_DIAGONAL = _MARK_R * 0.7071

#: A bag: this wide and tall, rounded at this corner, with a handle arc of this
#: radius standing this far above it. It is as wide as a body and a shade wider
#: than a head, which is what makes "the lit referent is carrying this" read
#: without a label. The handle's RISE and its arc's RADIUS are different
#: numbers on purpose: a wide, low tote arc, because a narrow shackle read as a
#: PADLOCK once a mark sat in the bag like a keyhole (2026-09-28). Bag, handle
#: and mark were all grown about 1.45x on 2026-09-29, the user's pick after the
#: marks proved too small to tell apart at card size; the numbers are that
#: approved render rounded to whole or half units, not derived — except the
#: WIDTH, held to 28 so that people standing 30 apart keep a gap between their
#: bags (34 made the three-person scenes' bags run together).
_BAG_W = 28
_BAG_H = 26
_BAG_RX = 4
_BAG_HANDLE_R = 12
_BAG_HANDLE_RY = 7
_BAG_HANDLE_DY = 3

#: How high a CARRIED bag hangs: its bottom this far above the ground, which puts
#: it across the referent's chest rather than at their feet.
_BAG_CARRY_DY = 36

#: The cat's bag, which has no hands to hold it: this far to the right of the
#: animal (far enough that the bigger bag clears the cat's tail), standing on the ground rather than hanging (its top is the ground
#: minus one bag height), and — being wider than the cat — the reason the
#: ``it_poss`` group is centred wider than the ``it`` one.
_CAT_BAG_DX = 50

# ── The gender of the thing in the bag (tunatale-l1ba) ──────────────────────
# The gender belongs to the OWNED thing, not to the person carrying the bag, so
# it is drawn inside the bag and in BAG_STROKE: the accent already means "this
# referent is a man/a woman" above a head, and reusing it here would say the same
# thing twice about one person rather than once about a house.
#
# Every number below is transcribed from the approved mockup rather than
# derived, exactly as the bag's own handle is (see _BAG_HANDLE_R): these are
# drawing decisions, and a renderer that computed a "tidier" mark would pass
# every count in the suite while drawing a different glyph.

#: What ``owned`` may be: the three genders, and the plural. Public because it
#: is part of the renderer's contract — the caller that reads it out of a data
#: file needs the same list this module draws from.
OWNED_KINDS: tuple[str, ...] = ("m", "f", "n", "pl")

#: A mark is a ring of this radius with strokes this thin, small enough to sit in
#: a 28x26 bag with air around it and thick enough to read at card size.
_OWNED_R = 5
_OWNED_STROKE_W = "2.2"

#: ♂ is the only mark that is not centred: its ring sits low and left, because
#: the arrow goes up and to the RIGHT out of it and needs the room. ♀ and ⚲
#: share a ring, the stem, and the stem's length — the crossbar is the whole of
#: the difference between them.
_OWNED_M_DX = -3
_OWNED_M_DY = 16
_OWNED_DY = 10
_OWNED_STEM_END = 13
_OWNED_ARM_DX = 3.5
_OWNED_ARM_DY = 9.5
#: The ♂ arrow: out of the ring's up-right edge, up and right, and an arrowhead.
_OWNED_M_ARROW_DX = 3.5
_OWNED_M_ARROW_DY = -3.5
_OWNED_M_REACH = 9
_OWNED_M_HEAD = 4

#: A plural owned thing is a second bag, drawn BEHIND the first (so it is
#: emitted first) and up and to the right of it — which is also why a plural
#: widens the cast and the group has to be centred on the pair.
_OWNED_PL_DX = 9
_OWNED_PL_DY = -7


def _n(value: float) -> str:
    """Format a coordinate, trimmed so the markup carries no ``48.000000``."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _person(x: float, lit: bool, marker: str | None = None) -> str:
    """One figure: a rounded body on the ground, a head above it, and a mark.

    **Document order is the spec.** The body comes first so the head is painted
    over it, and the mark last so it is painted over the head — the same
    convention ``spatial_scenes`` uses, and the reason a test can read "which
    circle belongs to which figure" off the markup without asking the builder.
    """
    fill, stroke = (_DOT_FILL, _REF_STROKE) if lit else (_ROD_FILL, _BOX_STROKE)
    body = (
        f"M{_n(x - _BODY_HALF)} {_G} V{_n(_BODY_TOP + _BODY_HALF)} "
        f"a{_BODY_HALF} {_BODY_HALF} 0 0 1 {2 * _BODY_HALF} 0 V{_G} Z"
    )
    parts = [
        f'<path d="{body}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>',
        f'<circle cx="{_n(x)}" cy="{_HEAD_CY}" r="{_HEAD_R}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>',
    ]
    if marker in ("sym_m", "sym_f"):
        cx, cy = x, _HEAD_CY - _MARK_DY
        parts.append(
            f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_MARK_R}" fill="none" stroke="{_ACCENT}" stroke-width="2.5"/>'
        )
        if marker == "sym_f":
            # A vertical stroke down off the ring, crossed near its foot: the
            # direction is what tells it from ♂, and the test reads it.
            parts.append(
                f'<path d="M{_n(cx)} {_n(cy + _MARK_R)} v{_MARK_CROSS} '
                f'M{_n(cx - _MARK_ARM)} {_n(cy + _MARK_R + _MARK_CROSS / 2)} h{2 * _MARK_ARM}" '
                f'stroke="{_ACCENT}" stroke-width="2.5" fill="none"/>'
            )
        else:
            # A stroke up and to the right off the ring, with an arrowhead.
            parts.append(
                f'<path d="M{_n(cx + _MARK_DIAGONAL)} {_n(cy - _MARK_DIAGONAL)} '
                f"l{_MARK_ARROW} -{_MARK_ARROW} "
                f"M{_n(cx + _MARK_DIAGONAL + 1)} {_n(cy - _MARK_DIAGONAL - _MARK_ARROW)} "
                f'h{_MARK_ARROW / 2} v{_MARK_ARROW / 2}" '
                f'stroke="{_ACCENT}" stroke-width="2.5" fill="none" stroke-linecap="round"/>'
            )
    return "".join(parts)


def _cat(x: float) -> str:
    """The one non-person referent: a lit cat, sitting, facing the conversation.

    Its head is the *same radius as a person's*, which is deliberate — a cat
    drawn at its own scale would be a detail to be got right, and the only
    property that has to read is that it is not a person. It is told apart by
    having no arc-bodied path under its head, and the test proves it is by
    pasting one in front of it and watching the count change.
    """
    fill, stroke = _DOT_FILL, _REF_STROKE
    hx, hy = x - 9, _G - 50
    return (
        # The tail, curling up behind it.
        f'<path d="M{_n(x + 13)} {_G - 5} q20 -2 16 -30" fill="none" stroke="{stroke}" '
        f'stroke-width="5" stroke-linecap="round"/>'
        # The body, sitting.
        f'<path d="M{_n(x - 17)} {_G} q-2 -34 12 -40 q20 -4 22 20 q2 14 -2 20 Z" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2" stroke-linejoin="round"/>'
        # The ears: the two closepaths, which is what a test keys on.
        f'<path d="M{_n(hx - 11)} {_n(hy - 2)} l1 -15 l9 8 Z M{_n(hx + 11)} {_n(hy - 2)} '
        f'l-1 -15 l-9 8 Z" fill="{fill}" stroke="{stroke}" stroke-width="2" stroke-linejoin="round"/>'
        f'<circle cx="{_n(hx)}" cy="{_n(hy)}" r="{_HEAD_R}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    )


def _bubble(x: float) -> str:
    """The speech bubble, with its tail pointing down at the speaker's head.

    The tail's tip is what identifies the speaker in the tests — proximity to
    it, rather than being leftmost, which is a different fact and one a scene
    can get wrong on its own.
    """
    x0, y0, w, h = x - 18, _BUBBLE_TOP, _BUBBLE_W, _BUBBLE_H
    tail = f"M{_n(x - 4)} {_n(y0 + h)} l2 {_n(_BUBBLE_DY)} l8 -{_n(_BUBBLE_DY)}"
    dots = "".join(
        f'<circle cx="{_n(x0 + 19 + i * 14)}" cy="{_n(y0 + h / 2)}" r="3" fill="{_BOX_STROKE}"/>' for i in range(3)
    )
    return (
        f'<path d="{tail}" fill="#ffffff" stroke="{_BOX_STROKE}" stroke-width="2" stroke-linejoin="round"/>'
        f'<rect x="{_n(x0)}" y="{_n(y0)}" width="{w}" height="{h}" rx="{_BUBBLE_R}" '
        f'fill="#ffffff" stroke="{_BOX_STROKE}" stroke-width="2"/>'
        # The notch, painted over the tail's edges where they meet the bubble.
        f'<rect x="{_n(x - 3)}" y="{_n(y0 + h - 2)}" width="12" height="4" fill="#ffffff"/>'
        f"{dots}"
    )


def _bag_parts(x: float, y: float) -> str:
    """One bag's two elements: the handle above it, the body over the handle."""
    r, ry, dy = _BAG_HANDLE_R, _BAG_HANDLE_RY, _BAG_HANDLE_DY
    return (
        f'<path d="M{_n(x - r)} {_n(y)} v-{dy} a{r} {ry} 0 0 1 {2 * r} 0 v{dy}" fill="none" '
        f'stroke="{BAG_STROKE}" stroke-width="2.4"/>'
        f'<rect x="{_n(x - _BAG_W / 2)}" y="{_n(y)}" width="{_BAG_W}" height="{_BAG_H}" '
        f'rx="{_BAG_RX}" fill="{BAG_FILL}" stroke="{BAG_STROKE}" stroke-width="2"/>'
    )


def _owned_mark(x: float, y: float, owned: str) -> str:
    """The gender of the thing in the bag at ``(x, y)``, as a mark inside it.

    **The direction is the whole of it.** ♂ is a stroke up and to the right with
    an arrowhead; ♀ is a stroke DOWN with a crossbar; ⚲ is the same stroke down
    with no crossbar. So ♀ and ⚲ differ by one horizontal segment and nothing
    else, which is why the tests read the direction and the crossbar off the path
    rather than trusting anything recorded here.

    Drawn in :data:`BAG_STROKE` rather than the accent — see the note on the
    constants above — and emitted AFTER the bag body, since the body is opaque
    and a mark painted under it is a mark nobody can see.
    """
    if owned == "m":
        cx, cy = x + _OWNED_M_DX, y + _OWNED_M_DY
        d = (
            f"M{_n(cx + _OWNED_M_ARROW_DX)} {_n(cy + _OWNED_M_ARROW_DY)} "
            f"L{_n(cx + _OWNED_M_REACH)} {_n(cy + _OWNED_M_ARROW_DY - (_OWNED_M_REACH - _OWNED_M_ARROW_DX))} "
            f"h-{_OWNED_M_HEAD} m{_OWNED_M_HEAD} 0 v{_OWNED_M_HEAD}"
        )
    else:
        cx, cy = x, y + _OWNED_DY
        d = f"M{_n(cx)} {_n(cy + _OWNED_R)} V{_n(cy + _OWNED_STEM_END)}"
        if owned == "f":
            d += f" M{_n(cx - _OWNED_ARM_DX)} {_n(cy + _OWNED_ARM_DY)} H{_n(cx + _OWNED_ARM_DX)}"
    stroke = f' fill="none" stroke="{BAG_STROKE}" stroke-width="{_OWNED_STROKE_W}"'
    return f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_OWNED_R}"{stroke}/><path d="{d}"{stroke}/>'


def _bag(x: float, y: float, owned: str | None = None) -> str:
    """A bag hanging at ``(x, y)`` — the one thing a possessive render adds.

    The handle goes down first and the body over it, the same document-order
    convention the rest of this module uses, and the caller appends these after
    every figure: a bag is always painted ON TOP of the person carrying it, so
    it reads as held rather than as a box behind them.

    A plural owned thing is a second bag, emitted BEFORE the first so that it is
    painted behind it, and it carries no mark. The three genders carry one mark
    each, emitted last so the opaque body cannot hide it.
    """
    parts = [_bag_parts(x + _OWNED_PL_DX, y + _OWNED_PL_DY)] if owned == "pl" else []
    parts.append(_bag_parts(x, y))
    if owned in ("m", "f", "n"):
        parts.append(_owned_mark(x, y, owned))
    return "".join(parts)


class _Scene(NamedTuple):
    """One concept, as who is in it and who is the referent.

    ``lit`` names the roles that are FILLED — "S", "L0".."L2", "O0".."O2" —
    which is also how the cast is counted, so a concept is one short literal
    rather than a layout. ``markers`` puts a gender mark on one of them.
    """

    lit: frozenset[str]
    markers: dict[str, str]
    it_cat: bool = False


_SCENES: dict[str, _Scene] = {
    "i": _Scene(frozenset({"S"}), {}),
    "you_one": _Scene(frozenset({"L0"}), {}),
    "he": _Scene(frozenset({"O0"}), {"O0": "sym_m"}),
    "she": _Scene(frozenset({"O0"}), {"O0": "sym_f"}),
    "third_one": _Scene(frozenset({"O0"}), {}),
    "it": _Scene(frozenset({"O0"}), {}, it_cat=True),
    "we_two": _Scene(frozenset({"S", "L0"}), {}),
    "we_many": _Scene(frozenset({"S", "L0", "O0"}), {}),
    # Identical to we_two on purpose: no language has both, and a distinction
    # the drawing cannot show is not one worth drawing. Asserted byte-identical
    # in the tests so that splitting them is a decision, not an accident.
    "we_incl": _Scene(frozenset({"S", "L0"}), {}),
    "we_excl": _Scene(frozenset({"S", "O0"}), {}),
    "you_two": _Scene(frozenset({"L0", "L1"}), {}),
    "you_many": _Scene(frozenset({"L0", "L1", "L2"}), {}),
    "they_two": _Scene(frozenset({"O0", "O1"}), {}),
    "they_many": _Scene(frozenset({"O0", "O1", "O2"}), {}),
}


def render_pronoun_svg(concept: str, owned: str | None = None) -> bytes:
    """Draw the pronoun *concept* names, as SVG bytes.

    The relation is carried by who stands inside the dashed outline, who stands
    outside it, and which of them are filled — so the claim is checkable: the
    tests read every number below off the emitted markup, and a referent that is
    quietly in the wrong place is a thing a unit test fails rather than a thing a
    learner is misled by.

    A possessive (``<concept>_poss``) is the SAME scene with a bag added to every
    lit referent. The cast is looked up under the nominative name rather than
    being laid out twice, so the two renders cannot drift apart: strip the bags
    and this one is the other one byte for byte, and the tests assert exactly
    that by stripping rather than by comparing the two calls. The ``it_poss``
    exception is the cat's bag, which has no hands to hold it — it stands on the
    ground beside the animal, and being wider than the cat it widens the group so
    the cast stays centred.

    *owned* says what is IN the bags — ``"m"``, ``"f"``, ``"n"`` or ``"pl"``, or
    ``None`` for no mark at all — which is how one picture family serves four
    Norwegian words. It is valid only on a possessive, because a nominative scene
    has no bag to put a mark in, and a plural widens the cast by a second bag.
    See :func:`_owned_mark`.

    Raises ``ValueError`` for a concept this module cannot draw, and for an
    *owned* it cannot draw, rather than emitting a near-miss: a picture showing
    the referent inside the conversation when the card asked for "he" is worse
    than no picture, because it is confidently wrong — and so is a bag with no
    mark in it where the card asked for a neuter thing.
    """
    if concept not in PRONOUN_CONCEPTS:
        raise ValueError(
            f"{concept!r} is not a renderable pronoun concept; expected one of {', '.join(PRONOUN_CONCEPTS)}"
        )
    possessive = concept.endswith(_POSSESSIVE)
    if owned is not None and not (possessive and owned in OWNED_KINDS):
        raise ValueError(
            f"owned={owned!r} cannot be drawn into {concept!r}; expected None, or one of "
            f"{', '.join(OWNED_KINDS)} on a possessive concept"
        )
    scene = _SCENES[concept[: -len(_POSSESSIVE)] if possessive else concept]
    # A listener is always drawn even when none is the referent: "we, and not
    # them" needs an inside to be outside of.
    listeners = max(1, sum(1 for role in scene.lit if role.startswith("L")))
    others = sum(1 for role in scene.lit if role.startswith("O"))
    inside_x = [_SPEAKER_X + _FIRST_LISTENER_DX + _STEP * i for i in range(listeners)]
    panel_right = inside_x[-1] + _BODY_HALF + _PANEL_PAD
    outside_x = [panel_right + (_GAP_CAT if scene.it_cat else _GAP) + _STEP * i for i in range(others)]
    # The drawn group is centred in the frame, so a card scaled to its width
    # does not put the cast off to one side. Widths are the figures' own reach:
    # a cat's tail is the widest thing in any of these scenes.
    right = outside_x[-1] + (_GAP_CAT if scene.it_cat else _BODY_HALF + 2) if outside_x else panel_right
    # Every lit referent's own x — which is where its bag goes, since a bag is
    # carried and therefore rides inside the reach the cast is already centred on.
    lit_x = ([_SPEAKER_X] if "S" in scene.lit else []) + [x for i, x in enumerate(inside_x) if f"L{i}" in scene.lit]
    lit_x += [x for x in outside_x if not scene.it_cat]
    if possessive and scene.it_cat:
        # The one reach a bag adds: the cat's is wider than the cat is, so the
        # group is centred on the pair of them rather than on the cat alone. A
        # plural's back bag reaches a step further still, and the group has to
        # know about it or the one scene that could not be a bare addition would
        # become the one scene that sits off to one side.
        right = outside_x[-1] + _CAT_BAG_DX + _BAG_W / 2 + (_OWNED_PL_DX if owned == "pl" else 0) + 2
    dx = (_W - right) / 2

    parts = [
        # The conversation: a dashed outline, so it reads as a boundary rather
        # than as a box somebody is standing in.
        f'<rect x="0" y="{_PANEL_TOP}" width="{_n(panel_right)}" '
        f'height="{_n(_G - _PANEL_TOP + _PANEL_BELOW)}" rx="16" fill="none" '
        f'stroke="{_BOX_STROKE}" stroke-width="1.5" stroke-dasharray="6 5"/>',
        _bubble(_SPEAKER_X),
        _person(_SPEAKER_X, "S" in scene.lit, scene.markers.get("S")),
    ]
    parts += [_person(x, f"L{i}" in scene.lit, scene.markers.get(f"L{i}")) for i, x in enumerate(inside_x)]
    for i, x in enumerate(outside_x):
        parts.append(_cat(x) if scene.it_cat else _person(x, True, scene.markers.get(f"O{i}")))
    if possessive:
        # Last, so every bag is painted over every figure: one hanging at chest
        # height in each lit referent's hands, or — for the cat, which has no
        # hands — one standing on the ground to its right.
        if scene.it_cat:
            parts.append(_bag(outside_x[-1] + _CAT_BAG_DX, _G - _BAG_H, owned))
        else:
            parts += [_bag(x, _G - _BAG_CARRY_DY, owned) for x in lit_x]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
        f'<rect x="0" y="0" width="{_W}" height="{_H}" fill="{_BG}"/>'
        f'<line x1="8" y1="{_G}" x2="{_W - 8}" y2="{_G}" stroke="{_BOX_STROKE}" stroke-width="1.5" '
        f'opacity="0.5"/>'
        f'<g transform="translate({_n(dx)} 0)">{"".join(parts)}</g></svg>'
    )
    return svg.encode("utf-8")
