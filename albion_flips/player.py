"""Player profile (server mode): what your character has unlocked, and what that lets you do now.

Albion has no API for a character's Destiny Board, so the player enters the unlocked tier per
skill once. The profile is stored on the server (``profile.json`` in the data dir), so the
dashboard shows the same on every device; only a logged-in visitor can change it.

The profile is used to
* pre-fill the island tab (home city, seeds per day from farms and herb gardens, animals from
  pastures) and mark crops, animals and resources you cannot use yet, and
* build "right now for you": the best refining, crafting, island, gathering and transport options
  that fit your tiers and, by default, avoid red and black zones.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

GATHER = ("ORE", "FIBER", "WOOD", "ROCK", "HIDE", "FISH")
REFINE = ("METALBAR", "CLOTH", "PLANKS", "STONEBLOCK", "LEATHER")
CRAFT = ("gear", "saddler", "cook", "alchemist")
ISLAND = ("crops", "herbs", "animals")
RAW_OF = {"METALBAR": "ORE", "CLOTH": "FIBER", "PLANKS": "WOOD", "STONEBLOCK": "ROCK", "LEATHER": "HIDE"}
UNSAFE_CITIES = {"Caerleon", "Black Market"}  # reaching them means red zones
MAX_TIER = 8
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{0,30}$")

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "",
    "home": "Thetford",
    "premium": True,
    "safe_only": True,
    "focus_per_day": 10000,
    "island": {"farms": 2, "herb_gardens": 0, "pastures": 1},
    "gather": {k: 4 for k in GATHER},
    "refine": {k: 3 for k in REFINE},
    "craft": {"gear": 4, "saddler": 3, "cook": 3, "alchemist": 3},
    "farming": {"crops": 4, "herbs": 3, "animals": 3},
}


def _tier_of(item_id: str) -> int:
    return int(item_id[1]) if len(item_id) > 1 and item_id[0] == "T" and item_id[1].isdigit() else 0


def _int(v: Any, lo: int, hi: int, name: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not lo <= n <= hi:
        raise ValueError(f"{name} must be {lo}-{hi}")
    return n


def validate(body: dict[str, Any], cities: Iterable[str]) -> dict[str, Any]:
    """Complete, checked profile from a (possibly partial) request body."""
    markets = [c for c in cities if c != "Black Market"]
    p = json.loads(json.dumps(DEFAULT_PROFILE))
    name = str(body.get("name", "") or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("name: letters, digits, space, - and _ only, at most 30")
    p["name"] = name
    home = str(body.get("home", p["home"]))
    if home not in markets:
        raise ValueError("unknown home city")
    p["home"] = home
    p["premium"] = bool(body.get("premium", True))
    p["safe_only"] = bool(body.get("safe_only", True))
    p["focus_per_day"] = _int(body.get("focus_per_day", p["focus_per_day"]), 0, 100000, "focus per day")
    for group, keys, hi in (("island", ("farms", "herb_gardens", "pastures"), 30), ("gather", GATHER, MAX_TIER),
                            ("refine", REFINE, MAX_TIER), ("craft", CRAFT, MAX_TIER), ("farming", ISLAND, MAX_TIER)):
        src = body.get(group)
        src = {} if src is None else src
        if not isinstance(src, dict):
            raise ValueError(f"{group} must be an object")
        for k in keys:
            p[group][k] = _int(src.get(k, p[group][k]), 0, hi, f"{group}.{k}")
    return p


class ProfileStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.profile: dict[str, Any] = json.loads(json.dumps(DEFAULT_PROFILE))
        self.saved = False
        self.updated: float | None = None
        if self.path and self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.profile = validate(data.get("profile", {}), [data.get("profile", {}).get("home", "Thetford")])
                self.saved, self.updated = True, data.get("updated")
            except (OSError, ValueError, TypeError):
                pass

    def get(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.profile))

    def set(self, profile: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.profile, self.saved, self.updated = profile, True, time.time()
            if self.path:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"profile": profile, "updated": self.updated}, indent=1), encoding="utf-8")
                tmp.replace(self.path)
            return json.loads(json.dumps(profile))


# ---------------------------------------------------------------- what can I do
def island_query(p: dict[str, Any]) -> dict[str, list[str]]:
    """Island tab settings that follow from the profile."""
    isl = p["island"]
    return {"home": [p["home"]], "seeds": [str(9 * (isl["farms"] + isl["herb_gardens"]))],
            "animals": [str(9 * isl["pastures"])], "yield": ["9" if p["premium"] else "4.5"]}


def island_locks(p: dict[str, Any], payload: dict[str, Any]) -> None:
    """Marks island rows you have not unlocked yet (``locked`` = tier you need)."""
    fm = p["farming"]
    for r in payload.get("crops", []):
        need = fm["herbs"] if r.get("kind") == "herb" else fm["crops"]
        r["locked"] = r["tier"] if r["tier"] > need else None
    for r in payload.get("animals", []):
        r["locked"] = r["tier"] if r["tier"] > fm["animals"] else None
    for r in payload.get("gathering", []):
        r["locked"] = r["tier"] if r["tier"] > p["gather"].get(r["type"], 0) else None


def craft_kind(item_id: str) -> tuple[str, str]:
    """('refine', 'PLANKS') / ('craft', 'gear'|'saddler'|'cook'|'alchemist')."""
    base = item_id.split("@")[0]
    for r in REFINE:
        if base.endswith("_" + r):
            return "refine", r
    if "_MOUNT_" in base or base.endswith("_MOUNT"):
        return "craft", "saddler"
    if "_MEAL_" in base:
        return "craft", "cook"
    if "_POTION_" in base:
        return "craft", "alchemist"
    return "craft", "gear"


def _can_craft(p: dict[str, Any], item_id: str) -> bool:
    kind, sub = craft_kind(item_id)
    tier = _tier_of(item_id)
    return tier <= (p["refine"] if kind == "refine" else p["craft"]).get(sub, 0)


def _safe(p: dict[str, Any], *cities: str) -> bool:
    return not p["safe_only"] or not any(c in UNSAFE_CITIES for c in cities if c)


def suggestions(p: dict[str, Any], crafting: list[dict[str, Any]], focus_crafting: list[dict[str, Any]],
                flips: list[dict[str, Any]], island_payload: dict[str, Any] | None, limit: int = 5) -> dict[str, Any]:
    """The best options that fit the profile, per kind of work."""
    def craft_rows(rows: list[dict[str, Any]], focus: bool) -> list[dict[str, Any]]:
        out = []
        for c in rows:
            iid = c.get("item_id", "")
            if c.get("profit_per_item", 0) <= 0 or not _can_craft(p, iid):
                continue
            if not _safe(p, c.get("craft_city", ""), c.get("sell_city", "")):
                continue
            kind, sub = craft_kind(iid)
            out.append({"kind": kind, "sub": sub, "item_id": iid, "name": c.get("item_name") or iid,
                        "tier": _tier_of(iid), "enchant": c.get("enchant", 0), "focus": focus,
                        "city": c.get("craft_city"), "sell_city": c.get("sell_city"),
                        "profit": round(c["profit_per_item"]), "margin": c.get("margin_pct"),
                        "detail": c.get("ingredients_desc", "")})
        return out

    made = craft_rows(crafting, False) + craft_rows(focus_crafting, True)
    made.sort(key=lambda r: -r["profit"])
    seen: set[str] = set()
    made = [r for r in made if not (r["item_id"] in seen or seen.add(r["item_id"]))]  # best variant per item
    refine = [r for r in made if r["kind"] == "refine"][:limit]
    craft = [r for r in made if r["kind"] == "craft"][:limit]

    tr = []
    for f in flips:
        if f.get("history_missing") or not f.get("est_daily_profit"):
            continue
        if not _safe(p, f.get("buy_city", ""), f.get("sell_city", "")):
            continue
        tr.append({"item_id": f["item_id"], "name": f.get("item_name") or f["item_id"], "tier": f.get("tier"),
                   "enchant": f.get("enchant", 0), "quality": f.get("quality", 1),
                   "buy_city": f["buy_city"], "sell_city": f["sell_city"], "buy_price": f.get("buy_price"),
                   "profit": round(f.get("profit_per_item", 0)), "per_day": round(f["est_daily_profit"])})
    tr.sort(key=lambda r: -r["per_day"])

    crops = animals = gathering = []
    if island_payload:
        crops = sorted((r for r in island_payload.get("crops", []) if not r.get("missing") and not r.get("locked")
                        and _safe(p, r.get("sell_city", ""), r.get("seed_city", "")) and r["profit_day"] > 0),
                       key=lambda r: -r["profit_day"])[:limit]
        animals = sorted((r for r in island_payload.get("animals", []) if not r.get("missing") and not r.get("locked")
                          and not r.get("feed_unknown") and _safe(p, r.get("sell_city", ""), r.get("baby_city", ""))
                          and r["profit_day"] > 0), key=lambda r: -r["profit_day"])[:limit]
        gathering = sorted((r for r in island_payload.get("gathering", []) if not r.get("locked") and r.get("per_hour_home")
                            and (r["type"] == "FISH" or r.get("zones"))), key=lambda r: -r["per_hour_home"])[:limit]
    return {"refine": refine, "craft": craft, "transport": tr[:limit], "crops": crops, "animals": animals,
            "gathering": gathering}


__all__ = ["DEFAULT_PROFILE", "ProfileStore", "validate", "island_query", "island_locks", "suggestions", "craft_kind"]
