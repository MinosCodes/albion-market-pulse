"""Price history, item search and price alerts (server mode).

These need the SQLite history of ``storage.MarketStore``, so they only exist in
``albion_flips.server``. The desktop version is unchanged.

Price alerts
------------
A rule says "tell me when <item> sells below <price>" (``sell_below``) or "when somebody
buys above <price>" (``buy_above``), optionally for one city. Rules are checked after
every AODP sweep and after every upload of the live client. A rule fires once, then waits
until the condition is no longer true before it can fire again, so a price that stays low
does not ring every few minutes. Fired alerts go to the dashboard (browser notification)
and to the in-game overlay.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from albion_flips.storage import DAY_S, MarketStore
from albion_flips.web import ITEM_NAMES, FlipDataStore, get_human_name

ITEM_ID_RE = re.compile(r"^[A-Z0-9_@]{2,80}$")
ALERT_KINDS = ("sell_below", "buy_above")
MAX_ALERTS = 100
MAX_EVENTS = 50
MAX_PRICE = 2_000_000_000
FRESH_SECONDS = 6 * 3600
HISTORY_DAYS = (7, 30, 120)


# ---------------------------------------------------------------- item search
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9@. ]+", " ", s.lower()).strip()


def search_items(query: str, item_ids: Iterable[str], limit: int = 20) -> list[dict[str, Any]]:
    """Finds items by name or id. 'bag 4' finds T4 Bag; '6.1 bow' finds T6 bows with enchant 1.

    Ranking: name starts with the query, then whole-word matches, then substring matches,
    lower tiers and lower enchantments first.
    """
    words = _norm(query).split()
    if not words:
        return []
    hits: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
    for iid in item_ids:
        name = get_human_name(iid)
        tier = int(iid[1]) if len(iid) > 1 and iid[0] == "T" and iid[1].isdigit() else 0
        ench = int(iid.rsplit("@", 1)[1]) if "@" in iid and iid.rsplit("@", 1)[1].isdigit() else 0
        hay = f"{_norm(name)} {_norm(iid.replace('_', ' '))} t{tier} {tier}.{ench}"
        if not all(w in hay for w in words):
            continue
        nn = _norm(name)
        rank = 0 if nn.startswith(words[0]) else 1 if f" {words[0]}" in f" {nn}" else 2
        hits.append(((rank, tier, ench, name), {"id": iid, "name": name, "tier": tier, "enchant": ench}))
    hits.sort(key=lambda h: h[0])
    return [h[1] for h in hits[:limit]]


def known_items(store: FlipDataStore, configured: Iterable[str] = ()) -> list[str]:
    ids = set(configured)
    with store._lock:
        ids.update(p.item_id for p in store.last_prices)
    return sorted(ids)


# ---------------------------------------------------------------- history
def history_payload(market: MarketStore, store: FlipDataStore, item_id: str, quality: int,
                    days: int = 30, now: float | None = None) -> dict[str, Any]:
    """Everything the price-history tab draws for one item, per city."""
    now = time.time() if now is None else now
    days = days if days in HISTORY_DAYS else 30
    today = int(now) // DAY_S
    daily = market.daily_series(item_id, quality, today - days)
    samples = market.sample_series(item_id, quality, int(now) - min(days, 7) * DAY_S)
    latest = market.latest_for(item_id, quality)
    refs = store.reference or {}
    cfg = store.last_config
    tax = cfg.active_tax_rate if cfg else 0.04

    cities: dict[str, dict[str, Any]] = {}
    for city in sorted(set(daily) | set(samples) | set(latest)):
        pts = daily.get(city, [])
        sell, sell_ts, buy, buy_ts = latest.get(city, (0, None, 0, None))
        sell = sell if sell_ts and now - sell_ts < FRESH_SECONDS else 0
        buy = buy if buy_ts and now - buy_ts < FRESH_SECONDS else 0
        ref = refs.get((item_id, city, quality))
        avg7 = float(ref.avg7) if ref is not None and ref.avg7 else None
        # traded per day over the shown period, from full days only
        full = [p for p in pts if p[0] < today]
        cities[city] = {
            "daily": [[p[0] * DAY_S, round(p[1]), p[2]] for p in pts],
            "orders": [[s[0], s[1], s[2]] for s in samples.get(city, [])],
            "sell_min": sell,
            "buy_max": buy,
            "updated": max(sell_ts or 0, buy_ts or 0) or None,
            "avg7": round(avg7) if avg7 else None,
            "vs_avg_pct": round((sell / avg7 - 1) * 100) if sell and avg7 else None,
            "per_day": round(sum(p[2] for p in full) / len(full), 1) if full else 0,  # on days with trades
            "net_instant": round(buy * (1 - tax)) if buy else 0,
        }
    return {
        "item_id": item_id,
        "item_name": get_human_name(item_id),
        "quality": quality,
        "days": days,
        "cities": cities,
        "tax_rate": tax,
        "time": now,
    }


# ---------------------------------------------------------------- alerts
class AlertBook:
    """Price alert rules and the last fired alerts, saved as JSON next to the database."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.rules: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._load()

    # ---- persistence
    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.rules = [r for r in data.get("rules", []) if isinstance(r, dict) and r.get("kind") in ALERT_KINDS][:MAX_ALERTS]
            self.events = list(data.get("events", []))[:MAX_EVENTS]
            self._seq = int(data.get("seq", 0))
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": self.rules, "events": self.events, "seq": self._seq}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # ---- rules
    @staticmethod
    def validate(body: dict[str, Any], cities: Iterable[str]) -> dict[str, Any]:
        item_id = str(body.get("item_id", "")).strip().upper()
        if not ITEM_ID_RE.match(item_id):
            raise ValueError("invalid item id")
        kind = str(body.get("kind", ""))
        if kind not in ALERT_KINDS:
            raise ValueError("kind must be sell_below or buy_above")
        city = str(body.get("city", "") or "")
        if city and city not in set(cities):
            raise ValueError("unknown city")
        try:
            price = int(body.get("price", 0))
            quality = int(body.get("quality", 1))
        except (TypeError, ValueError):
            raise ValueError("price and quality must be numbers") from None
        if not 1 <= price <= MAX_PRICE:
            raise ValueError("price out of range")
        if not 1 <= quality <= 5:
            raise ValueError("quality must be 1-5")
        note = str(body.get("note", "") or "")[:80]
        return {"item_id": item_id, "quality": quality, "city": city, "kind": kind, "price": price, "note": note}

    def add(self, rule: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if len(self.rules) >= MAX_ALERTS:
                raise ValueError(f"at most {MAX_ALERTS} alerts")
            r = dict(rule, id=secrets.token_hex(6), item_name=get_human_name(rule["item_id"]),
                     created=time.time(), armed=True, last_fired=None, fired_count=0)
            self.rules.append(r)
            self._save()
            return r

    def remove(self, rule_id: str) -> bool:
        with self._lock:
            n = len(self.rules)
            self.rules = [r for r in self.rules if r.get("id") != rule_id]
            if len(self.rules) != n:
                self._save()
                return True
            return False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"rules": [dict(r) for r in self.rules], "events": [dict(e) for e in self.events], "seq": self._seq}

    # ---- checking
    def check(self, quotes: Iterable[tuple[str, str, int, int, int]], now: float | None = None) -> list[dict[str, Any]]:
        """quotes: (item_id, city, quality, sell_min, buy_max) with fresh prices only (0 = none).

        Returns the alerts that fired now.
        """
        now = time.time() if now is None else now
        with self._lock:
            if not self.rules:
                return []
            wanted = {(r["item_id"], r["quality"]) for r in self.rules}
            by_key: dict[tuple[str, int], list[tuple[str, int, int]]] = {}
            for item_id, city, quality, sell, buy in quotes:
                if (item_id, quality) in wanted:
                    by_key.setdefault((item_id, quality), []).append((city, sell, buy))
            fired: list[dict[str, Any]] = []
            changed = False
            for r in self.rules:
                rows = by_key.get((r["item_id"], r["quality"]))
                if rows is None:
                    continue  # nothing new about this item: keep the state
                matches = []
                for city, sell, buy in rows:
                    if r["city"] and city != r["city"]:
                        continue
                    if r["kind"] == "sell_below" and sell and sell <= r["price"]:
                        matches.append((sell, city))
                    elif r["kind"] == "buy_above" and buy and buy >= r["price"]:
                        matches.append((-buy, city))
                if not matches:
                    seen_city = not r["city"] or any(c == r["city"] for c, _, _ in rows)
                    if seen_city and not r.get("armed", True):
                        r["armed"] = True  # price recovered: the next dip may fire again
                        changed = True
                    continue
                if not r.get("armed", True):
                    continue
                best, city = min(matches)
                price = abs(best)
                self._seq += 1
                ev = {"type": "alert", "seq": self._seq, "rule_id": r["id"], "item_id": r["item_id"],
                      "item_name": r.get("item_name") or get_human_name(r["item_id"]), "quality": r["quality"],
                      "kind": r["kind"], "target": r["price"], "price": price, "city": city,
                      "note": r.get("note", ""), "time": now}
                r["armed"], r["last_fired"] = False, now
                r["fired_count"] = int(r.get("fired_count", 0)) + 1
                self.events = [ev] + self.events[: MAX_EVENTS - 1]
                fired.append(ev)
                changed = True
            if changed:
                self._save()
            return fired


def store_quotes(store: FlipDataStore, item_keys: set[tuple[str, int]] | None = None,
                 now: float | None = None) -> list[tuple[str, str, int, int, int]]:
    """Fresh (item, city, quality, sell_min, buy_max) from the dashboard's current prices."""
    from albion_flips.analyzer import parse_aodp_datetime

    now = time.time() if now is None else now

    def fresh(price: int, date: str) -> int:
        if not price:
            return 0
        dt = parse_aodp_datetime(date) if date else None
        return price if dt is not None and now - dt.timestamp() < FRESH_SECONDS else 0

    with store._lock:
        prices = list(store.last_prices)
    out = []
    for p in prices:
        if item_keys is not None and (p.item_id, p.quality) not in item_keys:
            continue
        out.append((p.item_id, p.city, p.quality, fresh(p.sell_price_min, p.sell_price_min_date), fresh(p.buy_price_max, p.buy_price_max_date)))
    return out


def quotes_from_orders(aggregated: dict[tuple[str, str, int], dict[str, int]]) -> list[tuple[str, str, int, int, int]]:
    """Live-client upload (just seen in game) -> quotes."""
    return [(k[0], k[1], k[2], int(v.get("min_sell", 0) or 0), int(v.get("max_buy", 0) or 0)) for k, v in aggregated.items()]


__all__ = ["AlertBook", "history_payload", "search_items", "known_items", "store_quotes", "quotes_from_orders", "ITEM_NAMES"]
