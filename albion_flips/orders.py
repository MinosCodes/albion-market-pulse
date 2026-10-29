"""Order tracker: your own buy and sell orders, stock and profit (server mode).

Albion has no API for a player's own orders and this project never reads the game, so orders are
entered by hand in the dashboard. The server then does the bookkeeping:

* Fees: a buy or sell *order* costs the setup fee (2.5 %) when it is placed, a sale also pays the
  market tax (4 % with premium, 8 % without). Instant trades pay no setup fee.
* Market check: after every sweep and every live upload, each open order is compared with the
  current order book of its city. A buy order is *outbid* when somebody offers more, a sell order is
  *undercut* when somebody sells for less. This is reported once (dashboard and in-game overlay)
  and again only after your order was back on top.
* Stock and profit per item: average purchase cost of everything bought, realised profit of what
  was sold, and what the rest is worth today.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from albion_flips.insights import ITEM_ID_RE, MAX_PRICE
from albion_flips.web import get_human_name

KINDS = ("buy_order", "sell_order", "buy_instant", "sell_instant")
MAX_ORDERS = 500
MAX_QTY = 1_000_000


def is_buy(kind: str) -> bool:
    return kind.startswith("buy")


def is_order(kind: str) -> bool:
    return kind.endswith("_order")


def unit_value(kind: str, price: float, tax: float, setup: float) -> float:
    """Silver per unit after fees: what a buy costs, or what a sale brings in."""
    if is_buy(kind):
        return price * (1 + setup) if is_order(kind) else price
    return price * (1 - tax - setup) if is_order(kind) else price * (1 - tax)


class OrderBook:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.orders: list[dict[str, Any]] = []
        self._load()

    # ---- persistence
    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.orders = [o for o in data.get("orders", []) if isinstance(o, dict) and o.get("kind") in KINDS][:MAX_ORDERS]
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"orders": self.orders}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # ---- validation
    @staticmethod
    def _int(body: dict[str, Any], key: str, default: int | None = None) -> int:
        v = body.get(key, default)
        try:
            return int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number") from None

    @classmethod
    def validate(cls, body: dict[str, Any], cities: Iterable[str]) -> dict[str, Any]:
        item_id = str(body.get("item_id", "")).strip().upper()
        if not ITEM_ID_RE.match(item_id):
            raise ValueError("invalid item id")
        kind = str(body.get("kind", ""))
        if kind not in KINDS:
            raise ValueError("unknown order kind")
        city = str(body.get("city", ""))
        if city not in set(cities):
            raise ValueError("unknown city")
        price, qty = cls._int(body, "price"), cls._int(body, "qty")
        quality = cls._int(body, "quality", 1)
        filled = cls._int(body, "filled", 0 if is_order(kind) else qty)
        if not 1 <= price <= MAX_PRICE:
            raise ValueError("price out of range")
        if not 1 <= qty <= MAX_QTY:
            raise ValueError("quantity out of range")
        if not 0 <= filled <= qty:
            raise ValueError("filled must be between 0 and the quantity")
        if not 1 <= quality <= 5:
            raise ValueError("quality must be 1-5")
        return {"item_id": item_id, "quality": quality, "city": city, "kind": kind, "price": price, "qty": qty,
                "filled": filled, "note": str(body.get("note", "") or "")[:80]}

    # ---- changes
    def add(self, order: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if len(self.orders) >= MAX_ORDERS:
                raise ValueError(f"at most {MAX_ORDERS} orders, delete old ones first")
            now = time.time()
            done = not is_order(order["kind"]) or order["filled"] >= order["qty"]
            o = dict(order, id=secrets.token_hex(6), item_name=get_human_name(order["item_id"]), created=now,
                     updated=now, closed=now if done else None, on_top=True)
            self.orders.append(o)
            self._save()
            return o

    def update(self, order_id: str, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            o = next((x for x in self.orders if x.get("id") == order_id), None)
            if o is None:
                raise ValueError("unknown order")
            if "price" in body:
                price = self._int(body, "price")
                if not 1 <= price <= MAX_PRICE:
                    raise ValueError("price out of range")
                if price != o["price"]:
                    if o["filled"] and not o.get("closed") and o["filled"] < o["qty"]:
                        # The filled part was traded at the old price: keep it as its own finished entry.
                        if len(self.orders) >= MAX_ORDERS:
                            raise ValueError(f"at most {MAX_ORDERS} orders, delete old ones first")
                        done = dict(o, id=secrets.token_hex(6), qty=o["filled"], closed=time.time(), updated=time.time())
                        done.pop("market", None)
                        self.orders.append(done)
                        o["qty"] -= o["filled"]
                        o["filled"] = 0
                    o["price"], o["on_top"] = price, True  # relisted: watch again
            if "qty" in body:
                qty = self._int(body, "qty")
                if not max(1, o["filled"]) <= qty <= MAX_QTY:
                    raise ValueError("quantity out of range")
                o["qty"] = qty
            if "filled" in body:
                filled = self._int(body, "filled")
                if not 0 <= filled <= o["qty"]:
                    raise ValueError("filled must be between 0 and the quantity")
                o["filled"] = filled
            if "note" in body:
                o["note"] = str(body.get("note") or "")[:80]
            if "closed" in body:
                o["closed"] = time.time() if body.get("closed") else None
            elif is_order(o["kind"]) and o["filled"] >= o["qty"] and not o.get("closed"):
                o["closed"] = time.time()  # completely filled
            o["updated"] = time.time()
            self._save()
            return dict(o)

    def remove(self, order_id: str) -> bool:
        with self._lock:
            n = len(self.orders)
            self.orders = [o for o in self.orders if o.get("id") != order_id]
            if len(self.orders) != n:
                self._save()
                return True
            return False

    # ---- market check
    def check(self, quotes: Iterable[tuple[str, str, int, int, int]], now: float | None = None) -> list[dict[str, Any]]:
        """Open orders against fresh quotes (item, city, quality, sell_min, buy_max).

        Returns 'outbid' events for orders that just lost the top spot.
        """
        now = time.time() if now is None else now
        with self._lock:
            open_orders = [o for o in self.orders if not o.get("closed") and is_order(o["kind"])]
            if not open_orders:
                return []
            book = {(i, c, q): (s, b) for i, c, q, s, b in quotes}
            events, changed = [], False
            for o in open_orders:
                quote = book.get((o["item_id"], o["city"], o["quality"]))
                if quote is None:
                    continue
                sell_min, buy_max = quote
                if o["kind"] == "buy_order":
                    if not buy_max:
                        continue
                    best, top = buy_max, buy_max <= o["price"]
                else:
                    if not sell_min:
                        continue
                    best, top = sell_min, sell_min >= o["price"]
                if top != o.get("on_top", True):
                    o["on_top"], changed = top, True
                    if not top:
                        events.append({"type": "alert", "kind": "outbid", "order_id": o["id"], "order_kind": o["kind"],
                                       "item_id": o["item_id"], "item_name": o.get("item_name") or get_human_name(o["item_id"]),
                                       "quality": o["quality"], "city": o["city"], "price": best, "target": o["price"],
                                       "note": o.get("note", ""), "time": now})
                o["market"], o["market_at"] = best, now
            if changed:
                self._save()
            return events

    # ---- view
    def view(self, quotes: Iterable[tuple[str, str, int, int, int]], tax: float, setup: float) -> dict[str, Any]:
        """Orders with their market status, stock per item and the totals for the dashboard."""
        quotes = list(quotes)
        book = {(i, c, q): (s, b) for i, c, q, s, b in quotes}
        best_exit: dict[tuple[str, int], tuple[float, str]] = {}
        for i, c, q, s, b in quotes:
            if b:
                net = b * (1 - tax)
                if net > best_exit.get((i, q), (0, ""))[0]:
                    best_exit[(i, q)] = (net, c)

        with self._lock:
            orders = [dict(o) for o in self.orders]
        per_item: dict[tuple[str, int], dict[str, float]] = {}
        spent = earned = 0.0
        for o in sorted(orders, key=lambda x: x["created"]):
            key = (o["item_id"], o["quality"])
            st = per_item.setdefault(key, {"bought": 0, "cost": 0.0, "sold": 0, "revenue": 0.0, "cost_of_sold": 0.0})
            value = unit_value(o["kind"], o["price"], tax, setup) * o["filled"]
            if is_buy(o["kind"]):
                st["bought"] += o["filled"]
                st["cost"] += value
                spent += value
            else:
                avg = st["cost"] / st["bought"] if st["bought"] else 0.0
                st["sold"] += o["filled"]
                st["revenue"] += value
                st["cost_of_sold"] += avg * o["filled"]
                earned += value
            if not o.get("closed") and is_order(o["kind"]):
                s, b = book.get((o["item_id"], o["city"], o["quality"]), (0, 0))
                market = b if is_buy(o["kind"]) else s
                o["market_now"] = market or None
                if not market:
                    o["status"] = "no_data"
                elif is_buy(o["kind"]):
                    o["status"] = "outbid" if market > o["price"] else "top"
                else:
                    o["status"] = "undercut" if market < o["price"] else "top"
            o["value"] = round(value)
            o["open_value"] = round(unit_value(o["kind"], o["price"], tax, setup) * (o["qty"] - o["filled"])) if not o.get("closed") else 0

        stock, stock_value, realised = [], 0.0, 0.0
        for (item_id, quality), st in per_item.items():
            realised += st["revenue"] - st["cost_of_sold"]
            n = st["bought"] - st["sold"]
            if n <= 0:
                continue
            avg = st["cost"] / st["bought"] if st["bought"] else 0.0
            exit_net, exit_city = best_exit.get((item_id, quality), (0.0, ""))
            stock_value += exit_net * n
            stock.append({"item_id": item_id, "item_name": get_human_name(item_id), "quality": quality, "qty": n,
                          "avg_cost": round(avg), "value_now": round(exit_net * n), "best_city": exit_city,
                          "unit_now": round(exit_net), "profit_now": round((exit_net - avg) * n) if exit_net else None})
        stock.sort(key=lambda s: -(s["value_now"] or 0))
        orders.sort(key=lambda o: (bool(o.get("closed")), -(o.get("closed") or o["created"])))
        open_orders = [o for o in orders if not o.get("closed")]
        return {
            "orders": orders,
            "stock": stock,
            "totals": {
                "spent": round(spent), "earned": round(earned), "realised": round(realised),
                "stock_value": round(stock_value), "total": round(earned + stock_value - spent),
                "open": len(open_orders),
                "outbid": sum(1 for o in open_orders if o.get("status") in ("outbid", "undercut")),
                "tied_up": sum(o["open_value"] for o in open_orders if is_buy(o["kind"])),
            },
            "tax_rate": tax,
            "setup_fee": setup,
        }
