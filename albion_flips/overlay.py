"""In-game overlay feed (server side).

The Windows client shows a small always-on-top window over Albion (like Overwolf apps do, but
without injecting anything into the game). Its content comes from here:

* ``summary``: the best verified flips right now, plus server and live-client status.
* ``events``: a Server-Sent-Events stream. Whenever the live client uploads market orders
  (you opened a market window in game), the overlay gets an *item card* for the item you are
  looking at: 7-day average, whether the visible price is cheap or expensive, where it sells
  best after tax, and matching flips.

Nothing here reads the game: the data is the same market data the Albion Data Client already
uploads to AODP.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any

from albion_flips.web import FlipDataStore, get_human_name

MAX_SUBSCRIBERS = 8
QUEUE_SIZE = 50
CHEAP_BELOW = 0.85      # visible price below 85 % of the 7-day average
EXPENSIVE_ABOVE = 1.15  # visible price above 115 % of the 7-day average
FRESH_SECONDS = 6 * 3600


class OverlayHub:
    """Fan-out of overlay events to connected overlay windows (bounded, never blocks ingest)."""

    def __init__(self, max_subscribers: int = MAX_SUBSCRIBERS) -> None:
        self.max_subscribers = max_subscribers
        self._subs: list[queue.Queue[dict[str, Any]]] = []
        self._lock = threading.Lock()
        self.published = 0

    def subscribe(self) -> queue.Queue[dict[str, Any]] | None:
        with self._lock:
            if len(self._subs) >= self.max_subscribers:
                return None
            q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=QUEUE_SIZE)
            self._subs.append(q)
            return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        self.published += 1
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:  # a stalled overlay must not slow down price ingest
                pass


def _ref_avg7(store: FlipDataStore, item_id: str, city: str, quality: int) -> float | None:
    refs = store.reference or {}
    ref = refs.get((item_id, city, quality))
    avg = getattr(ref, "avg7", None) if ref is not None else None
    return float(avg) if avg else None


def _age_seconds(date_str: str, now: float) -> float | None:
    from albion_flips.analyzer import parse_aodp_datetime

    dt = parse_aodp_datetime(date_str) if date_str else None
    return max(0.0, now - dt.timestamp()) if dt else None


def verdict(price: int, avg7: float | None) -> str:
    if not price or not avg7:
        return "unknown"
    ratio = price / avg7
    if ratio < CHEAP_BELOW:
        return "cheap"
    if ratio > EXPENSIVE_ABOVE:
        return "expensive"
    return "fair"


def item_card(store: FlipDataStore, item_id: str, quality: int, seen_city: str = "",
              seen_sell: int = 0, seen_buy: int = 0, now: float | None = None) -> dict[str, Any]:
    """Everything the overlay shows for one item (all prices in silver)."""
    now = time.time() if now is None else now
    cfg = store.last_config
    tax = cfg.active_tax_rate if cfg else 0.04
    setup = cfg.setup_fee_rate if cfg else 0.025

    with store._lock:
        prices = [p for p in store.last_prices if p.item_id == item_id and p.quality == quality]
        flips = [f for f in store.flips if f.get("item_id") == item_id and f.get("quality") == quality]

    cities = []
    for p in prices:
        if p.city == "Black Market" and not p.buy_price_max:
            continue
        sell_age = _age_seconds(p.sell_price_min_date, now)
        buy_age = _age_seconds(p.buy_price_max_date, now)
        sell_min = p.sell_price_min if p.sell_price_min and (sell_age is None or sell_age < FRESH_SECONDS) else 0
        buy_max = p.buy_price_max if p.buy_price_max and (buy_age is None or buy_age < FRESH_SECONDS) else 0
        avg7 = _ref_avg7(store, item_id, p.city, quality)
        if not sell_min and not buy_max:
            continue
        # Sell-order price is capped at 110 % of the 7-day average, like the flip engine does,
        # so a lonely overpriced listing does not make a city look attractive.
        order_price = min(sell_min, int(avg7 * 1.10)) if sell_min and avg7 else sell_min
        cities.append({
            "city": p.city,
            "sell_min": sell_min,
            "buy_max": buy_max,
            "avg7": round(avg7) if avg7 else None,
            "net_instant": round(buy_max * (1 - tax)) if buy_max else 0,
            "net_order": round(order_price * (1 - tax - setup)) if order_price else 0,
            "age_min": round(min(a for a in (sell_age, buy_age) if a is not None) / 60) if (sell_age is not None or buy_age is not None) else None,
        })

    best_instant = max(cities, key=lambda c: c["net_instant"], default=None)
    best_order = max(cities, key=lambda c: c["net_order"], default=None)
    seen_avg = _ref_avg7(store, item_id, seen_city, quality) if seen_city else None
    seen_price = seen_sell or seen_buy
    flips.sort(key=lambda f: ("no_history" in (f.get("price_note") or ""), -float(f.get("est_daily_profit") or 0)))

    return {
        "type": "item",
        "item_id": item_id,
        "item_name": get_human_name(item_id),
        "quality": quality,
        "seen": {
            "city": seen_city,
            "sell_min": seen_sell,
            "buy_max": seen_buy,
            "avg7": round(seen_avg) if seen_avg else None,
            "verdict": verdict(seen_sell, seen_avg) if seen_sell else verdict(seen_buy, seen_avg),
            "vs_avg_pct": round((seen_price / seen_avg - 1) * 100) if seen_price and seen_avg else None,
        },
        "best_instant": {"city": best_instant["city"], "net": best_instant["net_instant"]} if best_instant and best_instant["net_instant"] else None,
        "best_order": {"city": best_order["city"], "net": best_order["net_order"]} if best_order and best_order["net_order"] else None,
        "cities": sorted(cities, key=lambda c: -max(c["net_instant"], c["net_order"])),
        "flips": [_flip_row(f) for f in flips[:3]],
        "tax_rate": tax,
        "time": now,
    }


def _flip_row(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": f.get("item_id"),
        "item_name": f.get("item_name") or get_human_name(str(f.get("item_id"))),
        "tier": f.get("tier"),
        "enchant": f.get("enchant"),
        "quality": f.get("quality"),
        "buy_city": f.get("buy_city"),
        "sell_city": f.get("sell_city"),
        "buy_price": f.get("buy_price"),
        "sell_price": f.get("sell_price"),
        "profit_per_item": round(float(f.get("profit_per_item") or 0)),
        "est_daily_profit": round(float(f.get("est_daily_profit") or 0)),
        "margin_pct": round(float(f.get("margin_pct") or 0), 1),
        "price_note": f.get("price_note") or "",
        "age_min": f.get("data_age_minutes"),
    }


def summary(store: FlipDataStore, limit: int = 5) -> dict[str, Any]:
    """Top flips for the overlay: only verified ones (with 7-day history), best daily profit first."""
    with store._lock:
        flips = list(store.flips)
        last_refresh = store.last_refresh
        live_ts = store.sniffer_last_timestamp
        live_city = store.sniffer_last_city
    verified = [f for f in flips if not f.get("history_missing") and "no_history" not in (f.get("price_note") or "")]
    verified.sort(key=lambda f: -float(f.get("est_daily_profit") or 0))
    return {
        "type": "summary",
        "last_refresh": last_refresh,
        "flips_total": len(flips),
        "flips_verified": len(verified),
        "top": [_flip_row(f) for f in verified[:limit]],
        "live_client_seconds_ago": round(time.time() - live_ts) if live_ts else None,
        "live_city": live_city,
        "time": time.time(),
    }


def card_from_orders(store: FlipDataStore, aggregated: dict[tuple[str, str, int], dict[str, int]]) -> dict[str, Any] | None:
    """Picks the item you are most likely looking at: the one with the most orders in this upload.

    Opening an item in the market sends its full order book (many orders for one item),
    while scrolling the list sends a few orders for many items.
    """
    if not aggregated:
        return None
    (item_id, city, quality), data = max(aggregated.items(), key=lambda kv: kv[1].get("n", 0))
    card = item_card(store, item_id, quality, city, data.get("min_sell", 0), data.get("max_buy", 0))
    card["orders"] = data.get("n", 0)
    card["items_in_upload"] = len(aggregated)
    return card
