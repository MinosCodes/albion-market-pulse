"""Realistic prices: live AODP quotes checked against what the item really sold for.

AODP only knows the cheapest sell order and the highest buy order some player last
saw in-game. Taken at face value they make the tool far too optimistic:

* The cheapest sell order in the destination is only an *asking* price. Nobody has to
  buy at it. In live data from 2026-11-01 the tool's top picks assumed sell prices
  14-67 % above the 7-day average of real sales; several "top picks" lose silver at
  the real price.
* A single very cheap sell order (bait, typo, or bought out hours ago) makes buying
  look cheaper than it is.
* Many rare items have no sales history at all; their listings (e.g. 75 million for
  an Avalonian crossbow nobody buys) floated to the top of the profit list.

``PriceCheck`` corrects this with the item's own volume-weighted 7-day average from
AODP daily history. When the city itself has no history, the median 7-day average of
the same item and quality in the other cities is used with a wider margin. Without
any history the price is kept but marked ``no_history`` so the dashboard can show it
as unverified.

Buy orders you sell into instantly are real money and are not capped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

# Defaults; all of them can be changed in config.json (see config.py).
BAIT_FLOOR = 0.85        # a buy price below 85 % of the 7-day average is not trusted...
BAIT_REPLACE = 0.95      # ...you will realistically pay 95 % of the average
SELL_CAP = 1.10          # a listing counts at most 10 % above the city's 7-day average
SELL_CAP_CROSS = 1.25    # ...or 25 % above the median of other cities when the city has no history
INVERTED_WINDOW_S = 3600  # buy order above sell order within one hour: one of them is stale

# Reasons shown in the dashboard
BAIT = "bait"
INVERTED = "inverted_book"
CAPPED = "sell_cap"
CAPPED_CROSS = "sell_cap_cross"
NO_HISTORY = "no_history"


@dataclass(slots=True, frozen=True)
class Reference:
    """What one item sold for in one city and quality."""

    avg7: float | None = None  # volume-weighted average price over the last 7 full days
    vol7: float = 0.0          # average units sold per day over those 7 days
    cross: bool = False        # True: median of other cities, not this city's own history


@dataclass(slots=True, frozen=True)
class Checked:
    price: int
    live: int              # the live quote before the check
    reason: str = ""       # BAIT, INVERTED, CAPPED, CAPPED_CROSS, NO_HISTORY or ""
    avg7: float | None = None

    @property
    def adjusted(self) -> bool:
        return self.price != self.live

    @property
    def verified(self) -> bool:
        return self.reason != NO_HISTORY

    def as_dict(self) -> dict[str, Any]:
        return {"live": self.live, "reason": self.reason,
                "avg7": round(self.avg7) if self.avg7 else None}


def _day(ts: str) -> date | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date()


def references_from_history(history: Iterable[Any] | Mapping[Any, Any], now: datetime) -> dict[tuple[str, str, int], Reference]:
    """(item, city, quality) -> 7-day reference from AODP daily history (time-scale=24).

    Only full past days count; today's partial day would bias the average.
    """
    records = history.values() if isinstance(history, Mapping) else history
    today = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()
    out: dict[tuple[str, str, int], Reference] = {}
    for rec in records:
        total = count = 0.0
        for pt in rec.data:
            d = _day(pt.timestamp)
            if d is None or pt.item_count <= 0 or pt.avg_price <= 0:
                continue
            if 1 <= (today - d).days <= 7:
                total += pt.avg_price * pt.item_count
                count += pt.item_count
        if count:
            out[(rec.item_id, rec.location, rec.quality)] = Reference(avg7=total / count, vol7=count / 7.0)
    return out


def _median(vals: list[float]) -> float:
    vals = sorted(vals)
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


class PriceCheck:
    """Checks live quotes against 7-day references. One instance per market refresh."""

    def __init__(self, references: Mapping[tuple[str, str, int], Reference] | None = None, *,
                 enabled: bool = True, bait_floor: float = BAIT_FLOOR, bait_replace: float = BAIT_REPLACE,
                 sell_cap: float = SELL_CAP, sell_cap_cross: float = SELL_CAP_CROSS) -> None:
        self.refs = dict(references or {})
        self.enabled = enabled
        self.bait_floor, self.bait_replace = bait_floor, bait_replace
        self.sell_cap, self.sell_cap_cross = sell_cap, sell_cap_cross
        per_item: dict[tuple[str, int], list[float]] = {}
        for (item, city, q), r in self.refs.items():
            if r.avg7 and city != "Black Market":
                per_item.setdefault((item, q), []).append(r.avg7)
        self._cross = {k: Reference(avg7=_median(v), cross=True) for k, v in per_item.items()}

    @classmethod
    def from_history(cls, history: Iterable[Any] | Mapping[Any, Any] | None, config: Any, now: datetime) -> PriceCheck:
        refs = references_from_history(history, now) if history else {}
        return cls(refs, enabled=getattr(config, "robust_prices", True),
                   bait_floor=getattr(config, "bait_floor", BAIT_FLOOR),
                   bait_replace=getattr(config, "bait_replace", BAIT_REPLACE),
                   sell_cap=getattr(config, "sell_cap", SELL_CAP),
                   sell_cap_cross=getattr(config, "sell_cap_cross", SELL_CAP_CROSS))

    def reference(self, item_id: str, city: str, quality: int = 1) -> Reference | None:
        """This city's own 7-day reference, else the median of the other cities."""
        own = self.refs.get((item_id, city, quality))
        if own and own.avg7:
            return own
        return self._cross.get((item_id, quality))

    def buy(self, item_id: str, city: str, quality: int, live_sell_min: int,
            live_buy_max: int = 0, seconds_between: float | None = None) -> Checked:
        """Price you will really pay when buying from the cheapest sell order.

        Only this city's own history counts: an item that is cheap here and dear
        elsewhere is exactly the flip we are looking for, not bait.
        """
        live = int(live_sell_min)
        if not self.enabled:
            return Checked(live, live)
        ref = self.refs.get((item_id, city, quality))
        avg7 = ref.avg7 if ref else None
        inverted = (live_buy_max > live > 0 and seconds_between is not None
                    and abs(seconds_between) < INVERTED_WINDOW_S)
        if avg7:
            if live < avg7 * self.bait_floor:
                return Checked(max(live, int(round(avg7 * self.bait_replace))), live, BAIT, avg7)
            if inverted:
                return Checked(max(live, int(round(avg7 * self.bait_replace))), live, INVERTED, avg7)
            return Checked(live, live, "", avg7)
        if inverted:
            # No history and the order book contradicts itself: take the higher number.
            return Checked(int(live_buy_max), live, INVERTED)
        # Nothing to compare with. The listing itself can be bought, so it stays and
        # does not make the deal unverified; the sale side decides that.
        return Checked(live, live)

    def sell_order(self, item_id: str, city: str, quality: int, live_price: int) -> Checked:
        """Price you can realistically list at: the asking price, at most a bit above real sales."""
        live = int(live_price)
        if not self.enabled:
            return Checked(live, live)
        ref = self.reference(item_id, city, quality)
        if not ref or not ref.avg7:
            return Checked(live, live, NO_HISTORY)
        cap = ref.avg7 * (self.sell_cap_cross if ref.cross else self.sell_cap)
        if live > cap:
            return Checked(int(round(cap)), live, CAPPED_CROSS if ref.cross else CAPPED, ref.avg7)
        return Checked(live, live, "", ref.avg7)

    def instant_sell(self, item_id: str, city: str, quality: int, live_bid: int) -> Checked:
        """Selling into a buy order pays the bid, so it is never capped (the order is the proof)."""
        live = int(live_bid)
        ref = self.reference(item_id, city, quality) if self.enabled else None
        return Checked(live, live, "", ref.avg7 if ref else None)


def seconds_between(a: str, b: str) -> float | None:
    """Seconds between two AODP timestamps (None when either is missing)."""
    def parse(ts: str) -> datetime | None:
        if not ts or ts.startswith("0001-01-01"):
            return None
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    da, db = parse(a), parse(b)
    return None if da is None or db is None else (da - db).total_seconds()
