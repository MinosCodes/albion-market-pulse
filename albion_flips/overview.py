"""Overview (server mode): every way to earn silver in one ranking.

Transport flips, refining, crafting, crops, animals, gathering and fishing are put on one scale,
**silver per day**, and next to it **silver per active hour** (time you really spend at the
computer). The ranking follows the player profile: tiers you have not unlocked and, with "blue and
yellow only", trades that need red zones are left out unless the page asks for everything.

How the per-day numbers come about
----------------------------------
* Transport: the analyzer's realistic daily profit (a share of the destination's daily volume).
  Active time = one round trip on the map (``world.py``).
* Refining and crafting: profit per item × a share (``market_share``, 10 %) of what the sell city
  trades per day. Active time: half an hour for buying, crafting and selling.
* Crops and animals: the island calculator; active time half an hour a day.
* Gathering and fishing: silver per hour × hours you play per day (profile).

The trend compares each entry with its value about an hour ago; the server keeps those points in
memory for 24 hours.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from albion_flips import player, wiki, world

TREND_STEP_S = 300        # store a point at most every 5 minutes
TREND_KEEP_S = 24 * 3600
TREND_AGO_S = 55 * 60
CRAFT_ACTIVE_H = 0.5
ISLAND_ACTIVE_H = 0.5
MAX_ROWS = 150


def focus_cost(item_id: str) -> float | None:
    """Focus for one item from the game's recipe; each enchantment level doubles it (estimate)."""
    base, _, ench = item_id.partition("@")
    rec = wiki.load_wiki().get("recipes", {}).get(base.split("_LEVEL")[0])
    if not rec or not rec.get("focus"):
        return None
    return rec["focus"] * 2 ** (int(ench) if ench.isdigit() else 0) / max(1, rec.get("amount", 1))


def _travel_h(w: dict[str, Any], a: str, b: str, allow_red: bool) -> float | None:
    m = world.travel_minutes(w, a, b, allow_red)
    return None if m is None else max(0.2, 2 * m / 60)


def build_entries(prof: dict[str, Any], flips: list[dict[str, Any]], blackmarket: list[dict[str, Any]],
                  crafting: list[dict[str, Any]], focus_crafting: list[dict[str, Any]],
                  island_payload: dict[str, Any] | None, refs: dict[tuple[str, str, int], Any] | None,
                  share: float = 0.10, w: dict[str, Any] | None = None,
                  focus_of: Callable[[str], float | None] | None = None) -> list[dict[str, Any]]:
    """All candidates with ``mine`` (fits tiers and zone rule) and ``why_not`` set."""
    w = world.load_world() if w is None else w
    focus_of = focus_cost if focus_of is None else focus_of
    refs = refs or {}
    safe = prof["safe_only"]
    capital = prof.get("capital", 2_000_000)
    out: list[dict[str, Any]] = []

    # ---- transport (cities and Black Market)
    for f in list(flips) + list(blackmarket):
        if f.get("history_missing") or not f.get("est_daily_profit") or f.get("profit_per_item", 0) <= 0:
            continue
        a, b = f.get("buy_city", ""), f.get("sell_city", "")
        bm = b == "Black Market"
        trip = _travel_h(w, a, "Caerleon" if bm else b, not safe)
        why = None
        if safe and (a in player.UNSAFE_CITIES or b in player.UNSAFE_CITIES):
            trip = None  # buying in Caerleon also means getting there first
        if trip is None:
            why = player.unsafe_reason(a, b)
            trip = _travel_h(w, a, "Caerleon" if bm else b, True) or 1.0
        units = f.get("units_per_day") or 0
        price = f.get("buy_price") or 0
        per_day = f["est_daily_profit"]
        by_capital = False
        if price > 0 and units * price > capital:
            # you can only buy what your silver pays for
            units, by_capital = capital / price, True
            per_day = min(per_day, units * f["profit_per_item"])
        if per_day < 1:
            continue
        name = f.get("item_name") or f["item_id"]
        out.append({
            "id": f"flip:{f['item_id']}:{f.get('quality', 1)}:{a}:{b}", "kind": "Black Market" if bm else "Transport",
            "item_id": f["item_id"], "what": f"{name}: {a} → {b}", "tier": f.get("tier"), "enchant": f.get("enchant", 0),
            "per_day": round(per_day), "unit": round(f["profit_per_item"]), "active_h": round(trip, 2),
            "volume": f.get("avg_daily_volume"), "why_not": why, "capital_limited": by_capital,
            "steps": [f"Buy {round(units):,} × {name} in {a} for about {round(price):,} each"
                      f"{' (all your silver to invest)' if by_capital else ''}; check in game how many there are at that price.",
                      f"Ride to {b} ({round(trip * 30)} min one way)." if not bm else f"Ride to Caerleon and sell at the Black Market ({round(trip * 30)} min one way).",
                      f"Sell for {round(f.get('sell_price') or 0):,}: {round(f['profit_per_item']):,} profit each after tax, "
                      f"{round(units * f['profit_per_item']):,} for the load"
                      f"{'' if by_capital else ', about as much as the market takes a day'}."],
        })

    # ---- refining and crafting
    seen: set[str] = set()
    for c, focus in [(c, False) for c in crafting] + [(c, True) for c in focus_crafting]:
        iid = c.get("item_id", "")
        if c.get("profit_per_item", 0) <= 0:
            continue
        key = f"{iid}:{'f' if focus else ''}"
        if key in seen:
            continue
        seen.add(key)
        kind, sub = player.craft_kind(iid)
        sell = c.get("sell_city", "")
        ref = refs.get((iid, sell, 1))
        vol = float(ref.vol7) if ref is not None and getattr(ref, "vol7", None) else 0.0
        n = vol * share
        cost = c.get("effective_cost") or 0
        by_capital = cost > 0 and n * cost > capital
        if by_capital:
            n = capital / cost
        by_focus, focus_each = False, 0.0
        if focus:
            focus_each = focus_of(iid) or 0.0
            if focus_each <= 0:
                continue  # without the focus cost we cannot say how many you can make
            if n * focus_each > prof.get("focus_per_day", 10000):
                n, by_focus, by_capital = prof.get("focus_per_day", 10000) / focus_each, True, False
        if n < 1:
            continue  # nobody buys it in quantity (or it costs more than you have): not a daily income
        tier = player._tier_of(iid)
        need = (prof["refine"] if kind == "refine" else prof["craft"]).get(sub, 0)
        why = f"needs T{tier} {sub.lower() if kind == 'refine' else sub}" if tier > need else None
        if why is None and safe and (sell in player.UNSAFE_CITIES or c.get("craft_city") in player.UNSAFE_CITIES):
            why = player.unsafe_reason(sell, c.get("craft_city", ""))
        name = c.get("item_name") or iid
        where = c.get("craft_city") or "any city"
        out.append({
            "id": f"craft:{key}", "kind": "Refining" if kind == "refine" else "Crafting", "item_id": iid,
            "what": f"{name}{' with focus' if focus else ''} → {sell}", "tier": tier, "enchant": c.get("enchant", 0),
            "per_day": round(c["profit_per_item"] * n), "unit": round(c["profit_per_item"]), "active_h": CRAFT_ACTIVE_H,
            "volume": round(vol), "why_not": why, "capital_limited": by_capital, "focus_limited": by_focus,
            "steps": [f"Materials: {c.get('ingredients_desc') or 'see the recipe'} (about {round(c.get('effective_cost') or 0):,} per item after return rate).",
                      f"{'Refine' if kind == 'refine' else 'Craft'} in {where}"
                      + (f" with focus (about {round(focus_each):,} focus each)." if focus else "."),
                      f"Sell in {sell} for {round(c.get('sell_price') or 0):,}: {round(c['profit_per_item']):,} profit each, "
                      f"about {round(n):,} a day" + (" (as many as your focus per day allows)." if by_focus
                                                   else " (as many as your silver to invest pays for)." if by_capital
                                                   else f" ({round(share * 100)} % of {round(vol):,} traded).")],
        })

    # ---- island and gathering
    isl = island_payload or {}
    for r in isl.get("crops", []):
        if r.get("missing") or r["profit_day"] <= 0:
            continue
        out.append({
            "id": f"crop:{r['crop']}", "kind": "Crops", "item_id": r["crop"], "what": f"Grow {r['name']} ({r['made_day']} a day)",
            "tier": r["tier"], "per_day": r["profit_day"], "unit": r["profit_per_seed"], "active_h": ISLAND_ACTIVE_H,
            "volume": r.get("volume"),
            "why_not": f"needs T{r['locked']} {'herbs' if r['kind'] == 'herb' else 'crops'}" if r.get("locked")
            else player.unsafe_reason(r.get("seed_city", ""), r.get("sell_city", ""))
            if safe and {r.get("seed_city"), r.get("sell_city")} & player.UNSAFE_CITIES else None,
            "steps": [f"Buy seeds ({r['seed_price']:,} in {r['seed_city']}) and plant them on your island.",
                      f"Harvest after {r['grow_h']} h; {round(r['seed_back'] * 100)} % of the seeds come back.",
                      f"Sell the crops in {r['sell_city']} for {r['crop_net']:,} each after tax."],
        })
    for r in isl.get("animals", []):
        if r.get("missing") or r.get("feed_unknown") or r["profit_day"] <= 0:
            continue
        out.append({
            "id": f"animal:{r['baby']}", "kind": "Animals", "item_id": r["grown"], "what": f"Raise {r['grown_name']}",
            "tier": r["tier"], "per_day": r["profit_day"], "unit": r["profit"], "active_h": ISLAND_ACTIVE_H,
            "volume": r.get("volume"),
            "why_not": f"needs T{r['locked']} animals" if r.get("locked")
            else player.unsafe_reason(r.get("baby_city", ""), r.get("sell_city", ""))
            if safe and {r.get("baby_city"), r.get("sell_city")} & player.UNSAFE_CITIES else None,
            "steps": [f"Buy babies for {r['baby_price']:,} in {r['baby_city']} and put them on your pastures.",
                      f"Feed them ({r['food']['units'] if r.get('food') else '?'} × {r['food']['name'] if r.get('food') else 'food'}) for {r['grow_h']} h.",
                      f"Sell the grown animal in {r['sell_city']} for {r['grown_net']:,} after tax; {round(r['offspring'] * 100)} % chance of a new baby."],
        })
    hours = prof.get("hours_per_day", 3)
    for r in isl.get("gathering", []):
        per_h = r.get("per_hour_home")
        if not per_h:
            continue
        if r["type"] != "FISH" and not r.get("zones"):
            continue  # nothing near the home city
        fish = r["type"] == "FISH"
        zones = ", ".join(z["name"] for z in r.get("zones", [])[:3])
        out.append({
            "id": f"gather:{r['id']}", "kind": "Fishing" if fish else "Gathering", "item_id": r["id"],
            "what": f"{'Fish' if fish else 'Gather'} {r['name']}", "tier": r["tier"], "per_day": round(per_h * hours),
            "unit": r["home_net"], "active_h": hours, "volume": r.get("volume"),
            "why_not": f"needs T{r['locked']} {'fishing' if fish else r['type'].lower()}" if r.get("locked") else None,
            "steps": [f"{'Fish in tier ' + str(r['tier']) + ' waters' if fish else 'Gather in ' + zones}.",
                      f"At {round(r['rate']):,} an hour (your estimate) and {r['home_net']:,} each after tax that is {per_h:,} an hour.",
                      "No capital and no focus needed."],
        })

    for e in out:
        e["mine"] = e["why_not"] is None
        e["per_active_h"] = round(e["per_day"] / max(0.25, e["active_h"]))
    return out


class Overview:
    """Ranking plus a 24-hour memory of the values for the 1-hour trend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hist: dict[str, list[tuple[float, int]]] = {}

    def trend(self, entries: list[dict[str, Any]], now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            for e in entries:
                pts = self._hist.setdefault(e["id"], [])
                if not pts or now - pts[-1][0] >= TREND_STEP_S:
                    pts.append((now, e["per_day"]))
                old = [p for p in pts if now - p[0] >= TREND_AGO_S]
                e["change_1h"] = e["per_day"] - old[-1][1] if old else None
                e["since"] = pts[0][0]
            for k in [k for k, v in self._hist.items() if not v or now - v[-1][0] > TREND_KEEP_S]:
                del self._hist[k]
            for v in self._hist.values():
                while v and now - v[0][0] > TREND_KEEP_S:
                    v.pop(0)

    def build(self, prof: dict[str, Any], entries: list[dict[str, Any]], all_rows: bool = False,
              now: float | None = None) -> dict[str, Any]:
        entries = sorted(entries, key=lambda e: -e["per_day"])
        mine = [e for e in entries if e["mine"]][:MAX_ROWS]
        # remember both views, so switching between them keeps the trend
        self.trend(list({id(e): e for e in entries[:MAX_ROWS] + mine}.values()), now)
        shown = entries[:MAX_ROWS] if all_rows else mine
        hidden = sum(1 for e in entries if not e["mine"])
        movers = [e for e in shown if e.get("change_1h")]
        kinds: dict[str, int] = {}
        for e in shown:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        best_kind: dict[str, dict[str, Any]] = {}
        for e in shown:
            best_kind.setdefault(e["kind"], e)
        return {
            "rows": shown, "hidden": 0 if all_rows else hidden, "all": all_rows, "kinds": kinds,
            "best": shown[0] if shown else None,
            "best_per_kind": list(best_kind.values()),
            "up": max((e for e in movers if e["change_1h"] > 0), key=lambda e: e["change_1h"], default=None),
            "down": min((e for e in movers if e["change_1h"] < 0), key=lambda e: e["change_1h"], default=None),
        }


__all__ = ["build_entries", "Overview"]
