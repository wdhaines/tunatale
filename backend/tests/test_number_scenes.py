"""Drawn pictures for the Spanish-derived number words (tunatale-w4m7.10).

A native cardinal is drawn as a heap of dots (``app.cards.number_image``), but
the Spanish-derived numbers of Tagalog and Cebuano are used in two *other*
contexts, and a heap is the wrong picture for both: ``alas singko`` is five
o'clock, and a price is the coins and bills that make it up. So they get an
analog clock at that hour, and the money that sums to the amount.

The same argument as the counting picture makes these testable at all. A
fetched photo of a clock cannot be checked for telling the right time — that is
a vision problem with no oracle — but a rendered one is true by construction, so
a test reads the hour hand's angle off the line's own coordinates and compares
it to a table measured before this was written. Same for the money: the claim
"this picture is worth exactly *n*" is a sum over ``data-value`` attributes.

Two oracle tables below (the denomination breakdown and the hand angles) were
measured by the orchestrator, not derived here. A mismatch is a finding about
the brief, not a thing to reconcile by editing the test.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import pytest

from app.cards.number_scenes import (
    MAX_CLOCK_HOUR,
    break_into_denominations,
    render_clock_svg,
    render_money_svg,
)

# The Philippine set, as a literal: tests may name a currency even though the
# renderer may not. These are the denomininations the oracle table was measured
# against, and every value 1..1000 is representable in them.
SYMBOL = "₱"
COINS = (1, 5, 10, 20)
BILLS = (50, 100, 200, 500, 1000)
ALL = (*COINS, *BILLS)

_SVG = "{http://www.w3.org/2000/svg}"


# ── Parsing helpers ────────────────────────────────────────────────────────
# The markup contract is the spec here, so the tests read it the way a renderer
# would: parse the document, find elements by class, and take the numbers off
# the geometry. A regex over the source would pass on malformed XML.


def _root(svg: bytes) -> ET.Element:
    return ET.fromstring(svg.decode("utf-8"))


def _classes(el: ET.Element) -> list[str]:
    return (el.get("class") or "").split()


def _by_class(svg: bytes, name: str) -> list[ET.Element]:
    return [el for el in _root(svg).iter() if name in _classes(el)]


def _lines(svg: bytes, cls: str) -> list[ET.Element]:
    return [el for el in _by_class(svg, cls) if el.tag == f"{_SVG}line"]


def _angle_deg(el: ET.Element) -> float:
    """The line's bearing, degrees clockwise from straight up.

    SVG's y axis grows DOWNWARD, so the sign on the cosine is what tells 3
    o'clock from 9 o'clock. Getting it wrong mirrors the dial and still passes
    any test that only checks 12 and 6 — which is why the table covers 3 and 9.
    """
    dx = float(el.get("x2", "0")) - float(el.get("x1", "0"))
    dy = float(el.get("y2", "0")) - float(el.get("y1", "0"))
    return math.degrees(math.atan2(dx, -dy)) % 360


def _length(el: ET.Element) -> float:
    dx = float(el.get("x2", "0")) - float(el.get("x1", "0"))
    dy = float(el.get("y2", "0")) - float(el.get("y1", "0"))
    return math.hypot(dx, dy)


def _size(svg: bytes) -> tuple[int, int]:
    el = _root(svg)
    return int(el.get("width", "0")), int(el.get("height", "0"))


def _pieces(svg: bytes) -> list[ET.Element]:
    return [el for el in _root(svg).iter() if el.get("data-value") is not None]


def _values(svg: bytes) -> list[int]:
    return [int(el.get("data-value", "0")) for el in _pieces(svg)]


class TestBreakIntoDenominations:
    """Greedy, largest first. The table is the orchestrator's, measured."""

    @pytest.mark.parametrize(
        ("amount", "expected", "coins", "bills"),
        [
            (13, [10, 1, 1, 1], 4, 0),
            (15, [10, 5], 2, 0),
            (16, [10, 5, 1], 3, 0),
            (19, [10, 5, 1, 1, 1, 1], 6, 0),
            (20, [20], 1, 0),
            (30, [20, 10], 2, 0),
            (40, [20, 20], 2, 0),
            (50, [50], 0, 1),
            (70, [50, 20], 1, 1),
            (90, [50, 20, 20], 2, 1),
            (100, [100], 0, 1),
            (1000, [1000], 0, 1),
        ],
    )
    def test_the_measured_breakdown(self, amount, expected, coins, bills) -> None:
        """Pieces descending, and the coin/bill split the picture draws from."""
        pieces = break_into_denominations(amount, ALL)
        assert pieces == expected
        assert len([p for p in pieces if p in COINS]) == coins
        assert len([p for p in pieces if p in BILLS]) == bills

    @pytest.mark.parametrize("amount", range(1, 1001))
    def test_the_pieces_always_sum_to_the_amount(self, amount) -> None:
        assert sum(break_into_denominations(amount, ALL)) == amount

    def test_a_value_in_both_sets_is_available_either_way(self) -> None:
        """₱20 exists as a coin and as a bill; the breakdown is about value."""
        assert break_into_denominations(20, ALL) == [20]
        assert break_into_denominations(20, (20,)) == [20]

    def test_the_result_is_in_descending_order(self) -> None:
        for amount in (13, 19, 999, 1000):
            pieces = break_into_denominations(amount, ALL)
            assert pieces == sorted(pieces, reverse=True)

    @pytest.mark.parametrize("amount", [0, -1, -100])
    def test_refuses_an_amount_below_one(self, amount) -> None:
        """An empty picture is indistinguishable from a render that failed."""
        with pytest.raises(ValueError, match="renderable"):
            break_into_denominations(amount, ALL)

    def test_refuses_an_amount_the_denominations_cannot_make(self) -> None:
        """No coins at all for the remainder is a hole, not a rounding."""
        with pytest.raises(ValueError, match="renderable"):
            break_into_denominations(3, (5, 10))

    def test_refuses_a_denomination_set_with_nothing_usable_in_it(self) -> None:
        """An empty set can make nothing, and says so rather than drawing nothing."""
        with pytest.raises(ValueError, match="renderable"):
            break_into_denominations(5, ())

    @pytest.mark.parametrize("denominations", [(0, 5), (5, -1), (5, 0, -20)])
    def test_ignores_a_denomination_it_could_not_use(self, denominations) -> None:
        """A zero would divide by zero and a negative would never terminate.

        Both are junk in a hand-edited numbers.json. Dropping them keeps a typo
        from taking the whole picture down, and the amount still has to be
        makeable from what is left.
        """
        assert break_into_denominations(5, denominations) == [5]
        with pytest.raises(ValueError, match="renderable"):
            break_into_denominations(3, (0, 5))


class TestClock:
    """An analog face at the hour, with nothing on the dial that gives it away."""

    def test_the_range_is_twelve_hours(self) -> None:
        assert MAX_CLOCK_HOUR == 12

    @pytest.mark.parametrize("hour", range(1, MAX_CLOCK_HOUR + 1))
    def test_draws_a_face_with_exactly_twelve_ticks(self, hour) -> None:
        assert len(_by_class(render_clock_svg(hour), "face")) == 1
        assert len(_lines(render_clock_svg(hour), "tick")) == MAX_CLOCK_HOUR

    @pytest.mark.parametrize("hour", range(1, MAX_CLOCK_HOUR + 1))
    def test_marks_the_four_cardinal_hours_apart(self, hour) -> None:
        svg = render_clock_svg(hour)
        majors = _lines(svg, "major")
        assert len(majors) == 4
        assert sorted(round(_angle_deg(el)) for el in majors) == [0, 90, 180, 270]

    def test_the_cardinal_ticks_are_visibly_longer(self) -> None:
        """Otherwise a major tick is a claim the drawing does not make."""
        svg = render_clock_svg(5)
        majors = [_length(el) for el in _lines(svg, "major")]
        minors = [_length(el) for el in _lines(svg, "tick") if "major" not in _classes(el)]
        assert min(majors) > max(minors)

    @pytest.mark.parametrize("hour", range(1, MAX_CLOCK_HOUR + 1))
    def test_carries_no_numeral_that_would_answer_the_question(self, hour) -> None:
        """A "5" on the dial would make the card answerable by reading it."""
        svg = render_clock_svg(hour)
        assert b"<text" not in svg
        assert _pieces(svg) == []

    @pytest.mark.parametrize(("hour", "degrees"), [(1, 30), (3, 90), (6, 180), (9, 270), (12, 0)])
    def test_the_hour_hand_points_at_the_hour(self, hour, degrees) -> None:
        """The measured table. 3 and 9 are here to catch a mirrored dial."""
        hand = _lines(render_clock_svg(hour), "hour-hand")[0]
        assert _angle_deg(hand) == pytest.approx(degrees, abs=0.5)

    @pytest.mark.parametrize("hour", range(1, MAX_CLOCK_HOUR + 1))
    def test_both_hands_are_pinned_to_the_centre_of_the_face(self, hour) -> None:
        svg = render_clock_svg(hour)
        face = _by_class(svg, "face")[0]
        for hand in _lines(svg, "hour-hand") + _lines(svg, "minute-hand"):
            assert (hand.get("x1"), hand.get("y1")) == (face.get("cx"), face.get("cy"))

    @pytest.mark.parametrize("hour", range(1, MAX_CLOCK_HOUR + 1))
    def test_the_minute_hand_reads_twelve_and_outranks_the_hour_hand(self, hour) -> None:
        """:00 — which is also why the two hands are told apart by length."""
        svg = render_clock_svg(hour)
        minute = _lines(svg, "minute-hand")[0]
        hour_hand = _lines(svg, "hour-hand")[0]
        assert _angle_deg(minute) == pytest.approx(0.0, abs=0.5)
        assert _length(minute) > _length(hour_hand)

    def test_is_well_formed_xml(self) -> None:
        assert _root(render_clock_svg(7)).tag == f"{_SVG}svg"

    def test_paints_its_own_background(self) -> None:
        """An <img> cannot inherit currentColor; night mode would erase it."""
        svg = render_clock_svg(7).decode("utf-8")
        first_rect = svg[svg.index("<rect") :]
        first_rect = first_rect[: first_rect.index(">")]
        assert 'fill="#' in first_rect
        assert "viewBox" in svg

    @pytest.mark.parametrize("hour", range(1, MAX_CLOCK_HOUR + 1))
    def test_stays_close_enough_to_square_to_survive_a_card(self, hour) -> None:
        w, h = _size(render_clock_svg(hour))
        assert w / h <= 3.0, f"{hour} renders {w}x{h}, too wide to read when scaled down"

    def test_is_deterministic(self) -> None:
        """The filename is the content hash; a jittered render re-stages forever."""
        assert render_clock_svg(11) == render_clock_svg(11)

    def test_every_hour_renders_different_bytes(self) -> None:
        """Identical bytes would point two cards at one picture."""
        rendered = {render_clock_svg(h) for h in range(1, MAX_CLOCK_HOUR + 1)}
        assert len(rendered) == MAX_CLOCK_HOUR

    @pytest.mark.parametrize("hour", [0, 13, -1, 24])
    def test_refuses_an_hour_off_the_dial(self, hour) -> None:
        """There is no 13 o'clock; drawing one would invent a fact."""
        with pytest.raises(ValueError, match="renderable"):
            render_clock_svg(hour)


class TestMoney:
    """The coins and bills that make the amount, summing to it by construction."""

    @pytest.mark.parametrize(
        ("amount", "expected", "coins", "bills"),
        [
            (13, [10, 1, 1, 1], 4, 0),
            (15, [10, 5], 2, 0),
            (16, [10, 5, 1], 3, 0),
            (19, [10, 5, 1, 1, 1, 1], 6, 0),
            (20, [20], 1, 0),
            (30, [20, 10], 2, 0),
            (40, [20, 20], 2, 0),
            (50, [50], 0, 1),
            (70, [50, 20], 1, 1),
            (90, [50, 20, 20], 2, 1),
            (100, [100], 0, 1),
            (1000, [1000], 0, 1),
        ],
    )
    def test_the_measured_breakdown_is_drawn(self, amount, expected, coins, bills) -> None:
        svg = render_money_svg(amount, symbol=SYMBOL, coins=COINS, bills=BILLS)
        assert _values(svg) == expected
        assert len(_by_class(svg, "coin")) == coins
        assert len(_by_class(svg, "bill")) == bills

    def test_the_pieces_are_worth_exactly_the_amount(self) -> None:
        """The oracle for the whole range: a sum, over every denomination."""
        for amount in range(1, 1001):
            svg = render_money_svg(amount, symbol=SYMBOL, coins=COINS, bills=BILLS)
            assert sum(_values(svg)) == amount, f"{amount} does not add up"

    def test_every_piece_is_one_the_breakdown_asked_for(self) -> None:
        """The picture shows the same multiset the arithmetic produced."""
        for amount in range(1, 1001):
            svg = render_money_svg(amount, symbol=SYMBOL, coins=COINS, bills=BILLS)
            assert len(_values(svg)) == len(break_into_denominations(amount, ALL))

    @pytest.mark.parametrize("amount", range(1, 1001))
    def test_stays_close_enough_to_square_to_survive_a_card(self, amount) -> None:
        """Eleven pieces in one line would be a strip, not a countable picture."""
        w, h = _size(render_money_svg(amount, symbol=SYMBOL, coins=COINS, bills=BILLS))
        assert w / h <= 3.0, f"{amount} renders {w}x{h}, too wide to read when scaled down"

    def test_labels_every_piece_with_the_symbol_and_its_own_value(self) -> None:
        """₱50 on a ₱50 bill — the label, not the shape, is what names the money."""
        svg = render_money_svg(70, symbol=SYMBOL, coins=COINS, bills=BILLS)
        for piece in _pieces(svg):
            labels = [el for el in piece if el.tag == f"{_SVG}text"]
            assert len(labels) == 1
            assert labels[0].text == f"{SYMBOL}{piece.get('data-value')}"

    @pytest.mark.parametrize("amount", [13, 85, 1000])
    def test_every_label_is_written_on_its_piece_in_a_contrasting_colour(self, amount) -> None:
        """The label is the only thing naming the money, so it must be SEEN.

        The first cut drew each coin's label in white BELOW the coin — on the
        near-white background, where it vanished; the text was present in the
        markup and every structural test passed. So assert the two properties
        that make it legible: it sits inside its own shape, and its fill differs
        from that shape's fill.
        """
        svg = render_money_svg(amount, symbol=SYMBOL, coins=COINS, bills=BILLS)
        for piece in _pieces(svg):
            label = next(el for el in piece if el.tag == f"{_SVG}text")
            shape = next(el for el in piece if el.tag != f"{_SVG}text")
            tx, ty = float(label.get("x")), float(label.get("y"))
            if shape.tag == f"{_SVG}circle":
                cx, cy, r = (float(shape.get(k)) for k in ("cx", "cy", "r"))
                assert math.hypot(tx - cx, ty - cy) < r
            else:
                x, y, w, h = (float(shape.get(k)) for k in ("x", "y", "width", "height"))
                assert x < tx < x + w and y < ty < y + h
            assert label.get("fill") != shape.get("fill")

    def test_a_coin_is_a_circle_and_a_bill_is_a_rounded_rect(self) -> None:
        """85 is one bill and three coins — 50 + 20 + 10 + 5, both shapes at once."""
        svg = render_money_svg(85, symbol=SYMBOL, coins=COINS, bills=BILLS)
        assert _values(svg) == [50, 20, 10, 5]

        def shape_tags(cls: str) -> list[str]:
            return [el.tag for el in _by_class(svg, cls) for el in el if el.tag != f"{_SVG}text"]

        assert shape_tags("coin") == [f"{_SVG}circle"] * 3
        assert shape_tags("bill") == [f"{_SVG}rect"] * 1
        assert _by_class(svg, "bill")[0][0].get("rx") is not None

    def test_a_value_that_is_both_is_drawn_as_the_coin(self) -> None:
        """₱20 is a coin and a bill; the coin is what is current.

        A synthetic set, because the Philippine one has no overlap and so cannot
        reach this branch — the rule is about the renderer, not about peso.
        """
        svg = render_money_svg(20, symbol="X", coins=(1, 20), bills=(20, 50))
        assert _values(svg) == [20]
        assert len(_by_class(svg, "coin")) == 1
        assert _by_class(svg, "bill") == []

    def test_a_bill_is_drawn_for_a_value_only_bills_carry(self) -> None:
        svg = render_money_svg(70, symbol="X", coins=(1, 20), bills=(20, 50))
        assert _values(svg) == [50, 20]
        assert [el.get("data-value") for el in _by_class(svg, "bill")] == ["50"]
        assert [el.get("data-value") for el in _by_class(svg, "coin")] == ["20"]

    def test_a_symbol_needing_escapes_still_produces_valid_xml(self) -> None:
        """The symbol is data from numbers.json, not a trusted constant."""
        svg = render_money_svg(20, symbol="<&", coins=COINS, bills=BILLS)
        assert [el for el in _pieces(svg) for el in el if el.tag == f"{_SVG}text"][0].text == "<&20"

    def test_lays_the_pieces_out_in_reading_order(self) -> None:
        """Descending value, left to right, then down — so the order is a sentence."""
        svg = render_money_svg(19, symbol=SYMBOL, coins=COINS, bills=BILLS)
        rows: dict[int, list[int]] = {}
        for piece in _pieces(svg):
            shape = next(el for el in piece if el.tag != f"{_SVG}text")
            x = int(shape.get("cx", shape.get("x", "0")))
            y = int(shape.get("cy", shape.get("y", "0")))
            rows.setdefault(y, []).append(x)
        ys = sorted(rows)
        assert len(set(rows)) == 2, "six pieces should wrap onto two rows"
        for row in ys:
            assert rows[row] == sorted(rows[row])
        assert [y for y in ys] == ys

    def test_is_well_formed_xml(self) -> None:
        svg = render_money_svg(13, symbol=SYMBOL, coins=COINS, bills=BILLS)
        assert _root(svg).tag == f"{_SVG}svg"

    def test_paints_its_own_background(self) -> None:
        svg = render_money_svg(13, symbol=SYMBOL, coins=COINS, bills=BILLS).decode("utf-8")
        first_rect = svg[svg.index("<rect") :]
        first_rect = first_rect[: first_rect.index(">")]
        assert 'fill="#' in first_rect
        assert "viewBox" in svg

    def test_is_deterministic(self) -> None:
        """The filename is the content hash; a jittered render re-stages forever."""
        one = render_money_svg(45, symbol=SYMBOL, coins=COINS, bills=BILLS)
        assert one == render_money_svg(45, symbol=SYMBOL, coins=COINS, bills=BILLS)

    def test_two_amounts_never_render_the_same_bytes(self) -> None:
        """Identical bytes would point two cards at one picture."""
        rendered = {render_money_svg(a, symbol=SYMBOL, coins=COINS, bills=BILLS) for a in range(1, 1001)}
        assert len(rendered) == 1000

    def test_the_symbol_is_part_of_the_picture(self) -> None:
        """A peso amount drawn with no peso on it is a number, not an amount."""
        with_peso = render_money_svg(20, symbol=SYMBOL, coins=COINS, bills=BILLS)
        without = render_money_svg(20, symbol="", coins=COINS, bills=BILLS)
        assert with_peso != without
