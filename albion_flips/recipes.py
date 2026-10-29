"""Game recipe data generated from ao-bin-dumps by tools/build_recipes.py.

Holds every crafting / refining recipe, Artifact enchanting (upgrade) costs, item
weights, and the per-city crafting bonuses used for resource return rates.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from albion_flips.paths import data_file

logger = logging.getLogger(__name__)

# Bonus that focus adds to the production bonus (game constant, 59%).
FOCUS_BONUS = 0.59
# Cities that have a crafting station bonus table in craftingmodifiers.json.
DEFAULT_BASE_BONUS = 0.18

REFINING_LABELS = {
    "wood": "Wood Refining",
    "ore": "Ore Refining",
    "fiber": "Fiber Refining",
    "hide": "Hide Refining",
    "rock": "Stone Refining",
}


@dataclass(frozen=True, slots=True)
class Recipe:
    output: str
    category: str
    amount: int
    resources: tuple[tuple[str, int, bool], ...]  # (item_id, count, returnable)
    silver: int
    focus: int
    item_value: float


@dataclass(frozen=True, slots=True)
class RecipeBook:
    recipes: tuple[Recipe, ...]
    upgrades: dict[str, dict[int, tuple[str, int]]]
    weights: dict[str, float]
    base_bonus: dict[str, float]
    city_modifiers: dict[str, dict[str, float]]

    def ingredient_ids(self) -> set[str]:
        return {rid for r in self.recipes for rid, _, _ in r.resources}

    def output_ids(self) -> set[str]:
        return {r.output for r in self.recipes}


def return_rate(bonus: float) -> float:
    """Resource return rate for a total production bonus (e.g. 0.18 -> 15.25%)."""
    return 1.0 - 1.0 / (1.0 + bonus)


def best_craft_city(book: RecipeBook, category: str, cities: list[str] | None = None) -> tuple[str, float]:
    """Returns (city, total_bonus) of the station with the highest bonus for a category."""
    best_city, best_bonus = "Any Royal City", DEFAULT_BASE_BONUS
    for city, base in book.base_bonus.items():
        if cities and city not in cities:
            continue
        bonus = base + book.city_modifiers.get(city, {}).get(category, 0.0)
        if bonus > best_bonus:
            best_city, best_bonus = city, bonus
    return best_city, best_bonus


def city_return_rates(book: RecipeBook, category: str, focus: bool = False) -> dict[str, float]:
    """Resource return rate for a recipe category at every crafting city."""
    extra = FOCUS_BONUS if focus else 0.0
    return {
        city: round(return_rate(base + book.city_modifiers.get(city, {}).get(category, 0.0) + extra), 4)
        for city, base in book.base_bonus.items()
    }


_RAW_RESOURCE = re.compile(r"^T\d_(WOOD|ORE|HIDE|FIBER|ROCK)(_LEVEL\d)?$")


def craft_label(recipe: Recipe) -> str:
    """Human category used by the dashboard filter pills."""
    out = recipe.output.split("@")[0]
    cat = recipe.category
    if cat in REFINING_LABELS:
        return REFINING_LABELS[cat]
    if not cat and _RAW_RESOURCE.search(out):
        return "Transmutation"  # raw resource -> next tier/enchant for silver
    if "_ROYAL" in out:
        return "Royal Crafting"
    if "CAPEITEM_" in out:
        return "Faction Capes"
    if cat == "bag":
        return "Bag Crafting"
    if cat == "cape":
        return "Cape Crafting"
    if cat in ("potion", "food") or cat.startswith("meat_"):
        return "Consumable"
    if cat == "offhand":
        return "Off-hand"
    if cat in ("tools", "gatherergear"):
        return "Tools & Gathering"
    if "_ARMOR_" in out or "_HEAD_" in out or "_SHOES_" in out or cat.endswith(("_armor", "_helmet", "_shoes")):
        return "Armor"
    if "_MAIN_" in out or "_2H_" in out:
        return "Weapon"
    return "Other"


_RESOURCE_LEVEL = re.compile(r"_LEVEL(\d)$")


def _market_output(item_id: str) -> str:
    """Enchanted resources trade as T5_PLANKS_LEVEL1@1 (older recipe files lack the @)."""
    m = _RESOURCE_LEVEL.search(item_id)
    return f"{item_id}@{m.group(1)}" if m else item_id


def _parse(raw: dict[str, Any]) -> RecipeBook:
    recipes = tuple(
        Recipe(
            output=_market_output(r["out"]),
            category=r.get("cat", ""),
            amount=max(1, int(r.get("n", 1))),
            resources=tuple((rid, int(cnt), bool(ret)) for rid, cnt, ret in r["res"]),
            silver=int(r.get("silver", 0)),
            focus=int(r.get("focus", 0)),
            item_value=float(r.get("value", 0.0)),
        )
        for r in raw.get("recipes", [])
    )
    upgrades = {
        base: {int(level): (mat, int(qty)) for level, (mat, qty) in levels.items()}
        for base, levels in raw.get("upgrades", {}).items()
    }
    weights = {k: float(v.get("w", 0.0)) for k, v in raw.get("items", {}).items()}
    return RecipeBook(
        recipes=recipes,
        upgrades=upgrades,
        weights=weights,
        base_bonus={k: float(v) for k, v in raw.get("base_bonus", {}).items()},
        city_modifiers={k: {kk: float(vv) for kk, vv in v.items()} for k, v in raw.get("city_modifiers", {}).items()},
    )


@lru_cache(maxsize=1)
def load_recipe_book(path: str | None = None) -> RecipeBook:
    p = Path(path) if path else data_file("recipes.json")
    try:
        return _parse(json.loads(p.read_text(encoding="utf-8")))
    except FileNotFoundError:
        logger.warning("Recipe data not found at %s; run tools/build_recipes.py", p)
        return RecipeBook((), {}, {}, {}, {})
