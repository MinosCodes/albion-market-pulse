"""Robust price handling for real-world AODP data.

Why this module exists
----------------------
AODP only knows the *cheapest* sell order and the *highest* buy order that some
player happened to see in-game. Those two numbers are often misleading:

* A single cheap sell order (a typo, a bait listing, or an order that was bought
  out hours ago) makes an item look like a huge flip. In live data from
  2026-10-29, 39 of the top 50 city flips ranked by daily profit rested on such a
  price; the top "1 million silver/day" route was worth about 90k in reality.
* A single expensive sell order in the destination city makes the sale look
  better than the market will pay for more than a handful of units.
* Profit per day was estimated as profit x the whole daily volume of the city,
  which assumes you buy out the entire market every day.

The functions below correct for this using the item's own 7-day average
(computed from AODP daily history, see ``storage.py``). They are pure and have
no I/O so they are easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass

# Default thresholds. They are also exposed in config.json (see config.py).
BAIT_FLOOR = 0.85        # a buy price below 85 % of the 7-day average is not trusted
BAIT_REPLACE = 0.95      # ...and replaced by 95 % of the average (what you will realistically pay)
SELL_CAP = 1.10          # a sell price is counted at most 10 % above the 7-day average
MARKET_SHARE = 0.10      # you can realistically sell about 10 % of what a city trades per day
INVERTED_WINDOW_S = 3600  # buy > sell within the same hour means the two prices cannot both be real


@dataclass(slots=True, frozen=True)
class Reference:
    """Market reference for one item in one city and quality."""

    avg7: float | None = None   # volume-weighted average price over the last 7 full days
    avg30: float | None = None  # same over 30 days
    vol7: float | None = None   # average units traded per day over the last 7 full days


@dataclass(slots=True, frozen=True)
class CheckedPrice:
    price: float
    adjusted: bool = False  # True when the live price was not trusted and replaced
    reason: str = ""        # "bait", "inverted_book", "sell_cap" or ""


def check_buy_price(
    live_sell_min: float,
    ref: Reference | None,
    live_buy_max: float = 0,
    seconds_between: float | None = None,
    floor: float = BAIT_FLOOR,
    replace: float = BAIT_REPLACE,
) -> CheckedPrice:
    """Price you will actually pay when buying instantly (cheapest sell order).

    * Inverted book: the highest buy order is above the cheapest sell order and
      both were seen within the same hour. In a live market those two orders
      would have matched, so at least one of them is stale.
    * Bait: the cheapest sell order is far below the 7-day average.

    In both cases we assume you pay ``replace`` x avg7 (never less than the live
    price). Without a 7-day average there is nothing to compare against, so the
    live price is kept unless the book is inverted.
    """
    inverted = (
        live_buy_max > 0
        and live_sell_min > 0
        and live_buy_max > live_sell_min
        and seconds_between is not None
        and abs(seconds_between) < INVERTED_WINDOW_S
    )
    avg7 = ref.avg7 if ref else None
    if avg7 and avg7 > 0:
        if live_sell_min < avg7 * floor:
            return CheckedPrice(max(live_sell_min, avg7 * replace), True, "bait")
        if inverted:
            return CheckedPrice(max(live_sell_min, avg7 * replace), True, "inverted_book")
        return CheckedPrice(live_sell_min)
    if inverted:
        # No reference at all and the book contradicts itself: use the buy order,
        # the higher and therefore more conservative of the two numbers.
        return CheckedPrice(live_buy_max, True, "inverted_book")
    return CheckedPrice(live_sell_min)


def check_sell_price(live_price: float, ref: Reference | None, cap: float = SELL_CAP) -> CheckedPrice:
    """Price you can realistically list at when selling more than a few units."""
    avg7 = ref.avg7 if ref else None
    if avg7 and avg7 > 0 and live_price > avg7 * cap:
        return CheckedPrice(avg7 * cap, True, "sell_cap")
    return CheckedPrice(live_price)


def units_per_day(
    dest_daily_volume: float | None,
    market_share: float = MARKET_SHARE,
    capital: float | None = None,
    unit_cost: float | None = None,
    units_per_trip: int | None = None,
    trips_per_day: int | None = None,
) -> float:
    """How many units of one flip you can realistically move per day.

    The minimum of: your share of the destination market, what your capital
    buys, and what fits on your mount in the trips you make per day. Any
    limit given as ``None`` is ignored.
    """
    if dest_daily_volume is None or dest_daily_volume <= 0:
        return 0.0
    limits = [dest_daily_volume * market_share]
    if capital is not None and unit_cost and unit_cost > 0:
        limits.append(capital / unit_cost)
    if units_per_trip is not None and trips_per_day is not None:
        limits.append(float(units_per_trip * max(1, trips_per_day)))
    return max(0.0, min(limits))
