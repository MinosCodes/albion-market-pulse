"""Builds data/recipes.json from the official ao-bin-dumps game data.

Recipes, enchanting (upgrade) costs, item weights, item values and city crafting
bonuses all come straight from the game files instead of being hand-typed, so
they stay correct after game patches. Re-run after a patch:

    python tools/build_recipes.py              # downloads from GitHub
    python tools/build_recipes.py --items items.json --modifiers craftingmodifiers.json

Source: https://github.com/ao-data/ao-bin-dumps (items.json, craftingmodifiers.json)
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

DUMPS = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/"
ROOT = Path(__file__).resolve().parent.parent

# craftingmodifiers.json cluster id -> AODP market location name
CLUSTER_TO_CITY = {
    "0000": "Thetford",
    "1000": "Lymhurst",
    "2000": "Bridgewatch",
    "3004": "Martlock",
    "4000": "Fort Sterling",
    "3003": "Caerleon",
    "5000": "Brecilien",
}

# Output categories we never treat as craft targets (no market or not player-craftable)
SKIP_SHOP_CATEGORIES = {"artefacts", "token", "other", "furniture", "luxurygoods", "trophies", "skillbooks", "cityresources"}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _load_json(path: str | None, name: str) -> Any:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    req = Request(DUMPS + name, headers={"User-Agent": "albion-market-pulse"})
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resource_market_id(unique_name: str) -> str:
    """Enchanted resources are their own items (T5_PLANKS_LEVEL1) but trade as T5_PLANKS_LEVEL1@1."""
    m = re.search(r"_LEVEL(\d)$", unique_name)
    return f"{unique_name}@{m.group(1)}" if m else unique_name


def _market_id(unique_name: str, enchant: int) -> str:
    """Converts a game resource reference to the AODP market item id."""
    if enchant <= 0:
        return unique_name
    return f"{unique_name}@{enchant}"


def _pick_recipe(reqs: Any) -> dict[str, Any] | None:
    """Picks the standard recipe: the first alternative without faction tokens or favor
    tokens (those are optional substitutes). Falls back to the first alternative when
    every option needs them, e.g. faction capes always need a heart."""
    alts = [r for r in _as_list(reqs) if isinstance(r, dict) and r.get("craftresource")]
    if not alts:
        return None

    def uses_substitute(alt: dict[str, Any]) -> bool:
        names = [c.get("@uniquename", "") for c in _as_list(alt.get("craftresource"))]
        return any("_TOKEN_FAVOR" in n or n.startswith("T1_FACTION_") for n in names)

    return next((a for a in alts if not uses_substitute(a)), alts[0])


def _parse_recipe(req: dict[str, Any]) -> tuple[list[list[Any]], int, int, int] | None:
    resources: list[list[Any]] = []
    for c in _as_list(req.get("craftresource")):
        name = c.get("@uniquename", "")
        if not name or name.startswith("UNIQUE_"):
            return None
        enchant = int(c.get("@enchantmentlevel", 0) or 0)
        count = int(c.get("@count", 1))
        returnable = 0 if c.get("@maxreturnamount") == "0" else 1
        resources.append([_market_id(name, enchant), count, returnable])
    if not resources:
        return None
    amount = int(req.get("@amountcrafted", 1) or 1)
    silver = int(float(req.get("@silver", 0) or 0))
    focus = int(float(req.get("@craftingfocus", 0) or 0))
    return resources, amount, silver, focus


def build(items_doc: Any, modifiers_doc: Any) -> dict[str, Any]:
    raw = items_doc["items"]
    index: dict[str, dict[str, Any]] = {}
    for key, entries in raw.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "@uniquename" in entry:
                entry["_kind"] = key
                index[entry["@uniquename"]] = entry

    items: dict[str, dict[str, Any]] = {}
    recipes: list[dict[str, Any]] = []
    upgrades: dict[str, dict[str, list[Any]]] = {}

    def item_value(name: str, seen: set[str] | None = None) -> float:
        """Game item value: explicit @itemvalue, else the sum of its recipe ingredients."""
        base = name.split("@")[0]
        entry = index.get(base)
        if entry is None:
            return 0.0
        if entry.get("@itemvalue"):
            return float(entry["@itemvalue"])
        seen = seen or set()
        if base in seen:
            return 0.0
        seen.add(base)
        recipe = _pick_recipe(entry.get("craftingrequirements"))
        if not recipe:
            return 0.0
        total = 0.0
        for c in _as_list(recipe.get("craftresource")):
            total += int(c.get("@count", 1)) * item_value(c.get("@uniquename", ""), seen)
        return total

    def enchanted_value(market_id: str) -> float:
        """Enchanted resources (T4_CLOTH_LEVEL1) carry their own value; enchanted gear
        references (T6_ARMOR_PLATE_SET1@1) are worth 2^level times the base item."""
        base, _, level = market_id.partition("@")
        if "_LEVEL" in base or not level:
            return item_value(base)
        return item_value(base) * (2 ** int(level))

    for name, entry in index.items():
        weight = float(entry.get("@weight", 0) or 0)
        category = entry.get("@craftingcategory") or ""
        items[name] = {"w": weight, "cat": category}

        if entry.get("@shopcategory") in SKIP_SHOP_CATEGORIES:
            continue

        recipe = _pick_recipe(entry.get("craftingrequirements"))
        if recipe:
            parsed = _parse_recipe(recipe)
            if parsed:
                res, amount, silver, focus = parsed
                value = sum(cnt * item_value(rid) for rid, cnt, _ in res)
                recipes.append({
                    "out": resource_market_id(name), "cat": category, "n": amount, "res": res,
                    "silver": silver, "focus": focus, "value": round(value, 2),
                })

        for ench in _as_list((entry.get("enchantments") or {}).get("enchantment")):
            level = int(ench.get("@enchantmentlevel", 0))
            if level <= 0:
                continue
            e_recipe = _pick_recipe(ench.get("craftingrequirements"))
            if e_recipe:
                parsed = _parse_recipe(e_recipe)
                if parsed:
                    res, amount, silver, focus = parsed
                    value = sum(cnt * enchanted_value(rid) for rid, cnt, _ in res)
                    recipes.append({
                        "out": f"{name}@{level}", "cat": category, "n": amount, "res": res,
                        "silver": silver, "focus": focus, "value": round(value, 2),
                    })
            up = (ench.get("upgraderequirements") or {}).get("upgraderesource")
            if isinstance(up, dict) and up.get("@uniquename"):
                upgrades.setdefault(name, {})[str(level)] = [up["@uniquename"], int(up.get("@count", 0))]

    city_mods: dict[str, dict[str, float]] = {}
    base_bonus: dict[str, float] = {}
    for loc in _as_list(modifiers_doc["craftingmodifiers"].get("craftinglocation")):
        city = CLUSTER_TO_CITY.get(loc.get("@clusterid", ""))
        if not city:
            continue
        base_bonus[city] = float((loc.get("craftingbonus") or {}).get("@value", 0.18))
        city_mods[city] = {m["@name"]: float(m["@value"]) for m in _as_list(loc.get("craftingmodifier"))}

    return {
        "source": "ao-data/ao-bin-dumps items.json + craftingmodifiers.json",
        "base_bonus": base_bonus,
        "city_modifiers": city_mods,
        "items": items,
        "recipes": recipes,
        "upgrades": upgrades,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--items", help="Local ao-bin-dumps items.json (default: download)")
    parser.add_argument("--modifiers", help="Local ao-bin-dumps craftingmodifiers.json (default: download)")
    parser.add_argument("--out", default=str(ROOT / "data" / "recipes.json"))
    args = parser.parse_args()

    data = build(_load_json(args.items, "items.json"), _load_json(args.modifiers, "craftingmodifiers.json"))
    Path(args.out).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(data['recipes'])} recipes, {len(data['upgrades'])} upgrade paths, "
          f"{len(data['items'])} items to {args.out}")


if __name__ == "__main__":
    main()
