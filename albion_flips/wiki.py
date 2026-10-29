"""Item wiki (server mode): everything about one item on one page.

Static facts (weight, slot, item power, recipe, nutrition) come from ``data/wiki.json``, extracted
from the official game files. Live facts come from the server: current prices and 7-day averages
per city, what the materials cost right now, which recipes use the item, where raw resources grow
(``world.py``) and how seeds and animals do on an island (``data/island.json``).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from albion_flips import island, world
from albion_flips.insights import FRESH_SECONDS
from albion_flips.robust import check_buy_price
from albion_flips.web import FlipDataStore, get_human_name

WIKI_FILE = Path(__file__).resolve().parent.parent / "data" / "wiki.json"
RAW_OF = {"METALBAR": "ORE", "CLOTH": "FIBER", "PLANKS": "WOOD", "STONEBLOCK": "ROCK", "LEATHER": "HIDE"}
REFINED_OF = {v: k for k, v in RAW_OF.items()}

_DATA: dict[str, Any] | None = None
_USED: dict[str, list[str]] | None = None


def load_wiki(path: Path | str | None = None) -> dict[str, Any]:
    global _DATA, _USED
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if _DATA is None:
        _DATA = json.loads(WIKI_FILE.read_text(encoding="utf-8")) if WIKI_FILE.is_file() else {"items": {}, "recipes": {}}
        _USED = used_in_index(_DATA)
    return _DATA


def used_in_index(data: dict[str, Any]) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for out, r in data.get("recipes", {}).items():
        for mat, _ in r["materials"]:
            used.setdefault(mat, []).append(out)
    return used


def _split(item_id: str) -> tuple[str, int]:
    """T4_2H_BOW@1 -> (T4_2H_BOW, 1); T4_PLANKS_LEVEL1@1 -> (T4_PLANKS, 1)."""
    base, _, ench = item_id.partition("@")
    return base.split("_LEVEL")[0], int(ench) if ench.isdigit() else 0


def _tier(item_id: str) -> int:
    return int(item_id[1]) if len(item_id) > 1 and item_id[0] == "T" and item_id[1].isdigit() else 0


def _resource_of(base: str) -> tuple[str, int] | None:
    """('FIBER', 4) for T4_FIBER, also for refined T4_CLOTH -> its raw resource."""
    tier = _tier(base)
    kind = base.split("_", 1)[1] if "_" in base else ""
    if kind in REFINED_OF:
        return kind, tier
    if kind in RAW_OF:
        return RAW_OF[kind], tier
    return None


def item_page(item_id: str, quality: int, store: FlipDataStore, market: Any, cities: list[str],
              data: dict[str, Any] | None = None, used: dict[str, list[str]] | None = None,
              home: str = "Thetford", now: float | None = None) -> dict[str, Any]:
    data = load_wiki() if data is None else data
    used = (_USED or used_in_index(data)) if used is None else used
    now = time.time() if now is None else now
    base, ench = _split(item_id)
    info = data["items"].get(base, {})
    cfg = store.last_config
    tax = cfg.active_tax_rate if cfg else 0.04
    refs = store.reference or {}

    # ---- prices per city
    prices = []
    latest = market.latest_for(item_id, quality) if market is not None else {}
    for city in cities:
        sell, sell_ts, buy, buy_ts = latest.get(city, (0, None, 0, None))
        sell = sell if sell_ts and now - sell_ts < FRESH_SECONDS else 0
        buy = buy if buy_ts and now - buy_ts < FRESH_SECONDS else 0
        ref = refs.get((item_id, city, quality))
        if not (sell or buy or ref):
            continue
        prices.append({"city": city, "sell_min": sell, "buy_max": buy,
                       "avg7": round(ref.avg7) if ref is not None and ref.avg7 else None,
                       "per_day": round(ref.vol7, 1) if ref is not None and ref.vol7 else 0,
                       "net_instant": round(buy * (1 - tax)) if buy else 0,
                       "updated": max(sell_ts or 0, buy_ts or 0) or None})
    best_sale = max((p for p in prices if p["net_instant"]), key=lambda p: p["net_instant"], default=None)

    # ---- recipe with current material prices (cheapest checked buy in any city)
    def cheapest(mat: str) -> tuple[float, str] | None:
        lat = market.latest_for(mat, 1) if market is not None else {}
        opts = []
        for city, (sell, sell_ts, buy, buy_ts) in lat.items():
            if city == "Black Market" or not sell or not sell_ts or now - sell_ts >= FRESH_SECONDS:
                continue
            fresh_buy = buy if buy_ts and now - buy_ts < FRESH_SECONDS else 0
            opts.append((check_buy_price(sell, refs.get((mat, city, 1)), fresh_buy, 0 if fresh_buy else None).price, city))
        return min(opts) if opts else None

    recipe = None
    rec = data.get("recipes", {}).get(base)
    if rec:
        mats, total, complete = [], 0.0, True
        for mat, n in rec["materials"]:
            # an enchanted item needs enchanted refined materials (AODP id T4_PLANKS_LEVEL1@1)
            is_refined = "_" in mat and mat.split("_", 1)[1] in RAW_OF
            mid = f"{mat}_LEVEL{ench}@{ench}" if ench and is_refined else mat
            c = cheapest(mid)
            if c is None:
                complete = False
            else:
                total += c[0] * n
            mats.append({"id": mid, "name": get_human_name(mid), "count": n,
                         "price": round(c[0]) if c else None, "city": c[1] if c else None})
        per_item = total / max(1, rec["amount"]) if complete else None
        recipe = {"materials": mats, "amount": rec["amount"], "focus": rec["focus"],
                  "cost": round(per_item) if per_item is not None else None,
                  "profit_instant": round(best_sale["net_instant"] - per_item) if best_sale and per_item is not None else None,
                  "note": "Material cost without return rate; enchanted items need enchanted materials." if ench else
                          "Material cost without return rate: with a city bonus or focus you get part of it back."}

    # ---- used in
    used_list = [{"id": o, "name": get_human_name(o), "tier": _tier(o)} for o in sorted(used.get(base, []), key=lambda o: (_tier(o), o))]

    # ---- where it comes from
    found: dict[str, Any] = {}
    res = _resource_of(base)
    if res and info.get("cat") in ("raw", None) and base.split("_", 1)[1] in RAW_OF.values():
        w = world.load_world()
        found["zones"] = world.resource_zones(w, res[0], res[1])[:12]
        found["refine_into"] = f"T{res[1]}_{REFINED_OF[res[0]]}"
        found["refine_into_name"] = get_human_name(found["refine_into"])
        found["refine_city"] = data.get("refining_bonus", {}).get(REFINED_OF[res[0]])
    elif res:
        found["refine_city"] = data.get("refining_bonus", {}).get(base.split("_", 1)[1])
    isl = island.load_data()
    for p in isl.get("plants", []):
        if base in (p["seed"], p["crop"]):
            found["farm"] = {"seed": p["seed"], "crop": p["crop"], "crop_name": get_human_name(p["crop"]), "grow_h": round(p["grow_s"] / 3600, 1),
                             "seed_chance": p["seed_chance"], "seed_chance_watered": round(p["seed_chance"] + p["water_bonus"], 4),
                             "nutrition": p.get("nutrition")}
    for a in isl.get("animals", []):
        if base in (a["baby"], a["grown"]):
            found["animal"] = {"baby": a["baby"], "grown": a["grown"], "grow_h": round(a["grow_s"] / 3600, 1),
                               "offspring": a["offspring"], "offspring_focus": round(a["offspring"] + a["focus_bonus"], 4),
                               "food": a["food"], "favorite": a.get("favorite"),
                               "favorite_name": get_human_name(a["favorite"]) if a.get("favorite") else None}
    if base in isl.get("fish", []):
        found["fish"] = {"tier": _tier(base)}

    return {
        "item_id": item_id, "base_id": base, "enchant": ench, "quality": quality,
        "name": get_human_name(item_id) if get_human_name(item_id) != item_id.replace("_", " ").title() else info.get("name", item_id),
        "name_de": info.get("name_de"), "tier": _tier(base), "category": info.get("cat"),
        "weight": info.get("weight"), "slot": info.get("slot"), "power": info.get("power"),
        "nutrition": info.get("nutrition"), "known": bool(info),
        "prices": prices, "best_sale": best_sale, "tax_rate": tax,
        "recipe": recipe, "used_in": used_list[:40], "used_in_count": len(used_list), "found": found,
        "home": home,
    }


__all__ = ["load_wiki", "item_page", "used_in_index"]
