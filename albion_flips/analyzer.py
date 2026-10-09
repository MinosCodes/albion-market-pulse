from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
import logging

from albion_flips.config import AppConfig
from albion_flips.models import (
    CraftingOpportunity,
    ExitType,
    FlipOpportunity,
    HistoryRecord,
    PriceRecord,
)
from albion_flips.profit import (
    calculate_crafting_profit,
    calculate_instant_profit,
    calculate_margin,
    calculate_sell_order_profit,
    calculate_total_profit,
)


logger = logging.getLogger(__name__)

EMPTY_DATE = "0001-01-01T00:00:00"

RESOURCE_BASES = {
    "WOOD", "ROCK", "ORE", "FIBER", "HIDE",
    "PLANKS", "STONEBLOCK", "METALBAR", "CLOTH", "LEATHER",
}


def is_resource_item(item_id: str) -> bool:
    """Checks if an item ID represents raw or refined resources (not accepted by Black Market)."""
    parts = item_id.split("@")[0].split("_")
    if len(parts) >= 2 and parts[1] in RESOURCE_BASES:
        if len(parts) == 2 or (len(parts) == 3 and parts[2].startswith("LEVEL")):
            return True
    return False


def parse_aodp_datetime(dt_str: str) -> datetime | None:
    """Parses an AODP timestamp string as UTC datetime.

    Returns None if empty or invalid.
    """
    if not dt_str or dt_str.startswith("0001-01-01"):
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def calculate_age_minutes(dt_str: str, now: datetime) -> float | None:
    """Returns the age in minutes of a timestamp relative to `now`."""
    dt = parse_aodp_datetime(dt_str)
    if dt is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_seconds = (now - dt).total_seconds()
    return max(0.0, age_seconds / 60.0)


def compute_daily_volume(
    history_records: Sequence[HistoryRecord],
    item_id: str,
    city: str,
    quality: int = 1,
    volume_days: int = 3,
) -> float | None:
    """Computes average daily volume sold in destination city over the volume_days window.

    Returns None if history is missing for the item/city.
    """
    match: HistoryRecord | None = None
    for rec in history_records:
        if rec.item_id == item_id and rec.location == city and rec.quality == quality:
            match = rec
            break

    if match is None or not match.data:
        return None

    # Sort descending by timestamp and take up to volume_days
    sorted_points = sorted(match.data, key=lambda p: p.timestamp, reverse=True)
    recent_points = sorted_points[:volume_days]
    if not recent_points:
        return None

    total_count = sum(p.item_count for p in recent_points)
    # Average across volume_days window
    divisor = float(volume_days) if volume_days > 0 else 1.0
    return total_count / divisor


def analyze_flips(
    prices: Sequence[PriceRecord],
    history: Sequence[HistoryRecord],
    config: AppConfig,
    now: datetime | None = None,
) -> list[FlipOpportunity]:
    """Analyzes market price records and history against config criteria.

    Filtering rules applied in strict order:
    1. Drop any price field equal to 0 or with date 0001-01-01T00:00:00.
    2. Drop a price whose date is older than max_price_age_minutes relative to injected now.
    3. Drop flips with profit below min_profit_silver or margin below min_margin_pct.
    4. Drop flips with margin above max_margin_pct (unrealistic/troll data).
    5. Drop flips whose destination average daily volume is below min_daily_volume.
       (If history is missing, keep it with volume=None and history_missing=True).
    6. Attach risk labels for both cities.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # Group price records by item_id
    prices_by_item: dict[str, list[PriceRecord]] = {}
    for p in prices:
        prices_by_item.setdefault(p.item_id, []).append(p)

    tax_rate = config.active_tax_rate
    setup_rate = config.setup_fee_rate

    opportunities: list[FlipOpportunity] = []

    for item_id, records in prices_by_item.items():
        # Compare every pair of distinct cities: buy_rec (city A) -> sell_rec (city B)
        for buy_rec in records:
            # Players cannot purchase items from the Black Market
            if buy_rec.city == "Black Market":
                continue

            # Rule 1 & 2 for buy cost (instant buy: sell_price_min in city A)
            if buy_rec.sell_price_min <= 0 or buy_rec.sell_price_min_date.startswith("0001-01-01"):
                continue

            buy_age = calculate_age_minutes(buy_rec.sell_price_min_date, now)
            if buy_age is None or buy_age > config.max_price_age_minutes:
                continue

            buy_price = buy_rec.sell_price_min
            buy_quality = buy_rec.quality

            for sell_rec in records:
                if sell_rec.city == buy_rec.city:
                    continue

                # Black Market does not purchase raw or refined resources
                if sell_rec.city == "Black Market" and is_resource_item(item_id):
                    continue

                # Evaluate Exit 1: Sell Order (sell_price_min in city B)
                # Listing a sell order in City B requires the exact same quality as purchased
                opt_order: tuple[float, float, int, float] | None = None
                if sell_rec.quality == buy_quality:
                    if sell_rec.sell_price_min > 0 and not sell_rec.sell_price_min_date.startswith("0001-01-01"):
                        sell_order_age = calculate_age_minutes(sell_rec.sell_price_min_date, now)
                        if sell_order_age is not None and sell_order_age <= config.max_price_age_minutes:
                            profit_order = calculate_sell_order_profit(
                                buy_price=buy_price,
                                sell_price=sell_rec.sell_price_min,
                                tax_rate=tax_rate,
                                setup_fee_rate=setup_rate,
                            )
                            margin_order = calculate_margin(profit_order, buy_price)
                            flip_order_age = max(buy_age, sell_order_age)
                            opt_order = (profit_order, margin_order, sell_rec.sell_price_min, flip_order_age)

                # Evaluate Exit 2: Instant Sell into Buy Order (buy_price_max in city B)
                # In Albion Online, any item of quality >= buy order quality can fulfill the buy order!
                opt_instant: tuple[float, float, int, float] | None = None
                if buy_quality >= sell_rec.quality:
                    if sell_rec.buy_price_max > 0 and not sell_rec.buy_price_max_date.startswith("0001-01-01"):
                        instant_age = calculate_age_minutes(sell_rec.buy_price_max_date, now)
                        if instant_age is not None and instant_age <= config.max_price_age_minutes:
                            profit_instant = calculate_instant_profit(
                                buy_price=buy_price,
                                buyer_bid=sell_rec.buy_price_max,
                                tax_rate=tax_rate,
                            )
                            margin_instant = calculate_margin(profit_instant, buy_price)
                            flip_instant_age = max(buy_age, instant_age)
                            opt_instant = (profit_instant, margin_instant, sell_rec.buy_price_max, flip_instant_age)

                # If neither exit is valid and fresh, skip
                if opt_order is None and opt_instant is None:
                    continue

                # Select best exit
                chosen_exit: ExitType
                chosen_profit: float
                chosen_margin: float
                chosen_sell_price: int
                chosen_age: float

                if opt_order is not None and opt_instant is not None:
                    # Choose higher profit exit
                    if opt_order[0] >= opt_instant[0]:
                        chosen_exit = ExitType.SELL_ORDER
                        chosen_profit, chosen_margin, chosen_sell_price, chosen_age = opt_order
                    else:
                        chosen_exit = ExitType.INSTANT_SELL
                        chosen_profit, chosen_margin, chosen_sell_price, chosen_age = opt_instant
                elif opt_order is not None:
                    chosen_exit = ExitType.SELL_ORDER
                    chosen_profit, chosen_margin, chosen_sell_price, chosen_age = opt_order
                else:
                    assert opt_instant is not None
                    chosen_exit = ExitType.INSTANT_SELL
                    chosen_profit, chosen_margin, chosen_sell_price, chosen_age = opt_instant

                # Rule 3: Drop flips with profit below min_profit_silver or margin below min_margin_pct
                if chosen_profit < config.min_profit_silver or chosen_margin < config.min_margin_pct:
                    continue

                # Rule 4: Drop flips with margin above max_margin_pct
                if chosen_margin > config.max_margin_pct:
                    continue

                # Rule 5: Destination average daily volume
                dest_volume = compute_daily_volume(
                    history_records=history,
                    item_id=item_id,
                    city=sell_rec.city,
                    quality=sell_rec.quality,
                    volume_days=config.volume_days,
                )
                if dest_volume is None and sell_rec.quality > 1:
                    dest_volume = compute_daily_volume(
                        history_records=history,
                        item_id=item_id,
                        city=sell_rec.city,
                        quality=1,
                        volume_days=config.volume_days,
                    )

                history_missing = (dest_volume is None)
                if not history_missing and dest_volume < config.min_daily_volume:
                    # Volume below threshold -> exclude
                    continue

                # Rule 6: Attach risk labels
                buy_risk = config.risk.get(buy_rec.city, "low")
                sell_risk = config.risk.get(sell_rec.city, "low")
                if "high" in (buy_risk, sell_risk):
                    risk_label = "high"
                elif "medium" in (buy_risk, sell_risk):
                    risk_label = "medium"
                else:
                    risk_label = "low"

                total_profit = calculate_total_profit(chosen_profit, config.stack_size)

                opportunities.append(
                    FlipOpportunity(
                        item_id=item_id,
                        buy_city=buy_rec.city,
                        sell_city=sell_rec.city,
                        buy_price=buy_price,
                        sell_price=chosen_sell_price,
                        exit_type=chosen_exit,
                        profit_per_item=chosen_profit,
                        margin_pct=chosen_margin,
                        total_profit=total_profit,
                        avg_daily_volume=dest_volume,
                        data_age_minutes=chosen_age,
                        risk=risk_label,
                        quality=buy_quality,
                        history_missing=history_missing,
                    )
                )

    # Sort opportunities descending by profit_per_item
    opportunities.sort(key=lambda x: x.profit_per_item, reverse=True)
    return opportunities


def calculate_deal_score(
    profit_per_item: float,
    margin_pct: float,
    data_age_minutes: float,
    avg_daily_volume: float | None,
    risk: str,
    exit_type: ExitType,
) -> tuple[int, str, str]:
    """Calculates a 0-100 quality score, tier label ('S', 'A', 'B', 'C'), and friendly badge for a flip."""
    score = 0.0

    # Freshness factor (max 40 pts)
    if data_age_minutes <= 15:
        score += 40.0
    elif data_age_minutes <= 45:
        score += 35.0
    elif data_age_minutes <= 90:
        score += 25.0
    elif data_age_minutes <= 180:
        score += 15.0
    elif data_age_minutes <= 360:
        score += 8.0
    else:
        score += 2.0

    # Volume / Liquidity factor (max 30 pts)
    if avg_daily_volume is not None:
        if avg_daily_volume >= 50:
            score += 30.0
        elif avg_daily_volume >= 20:
            score += 25.0
        elif avg_daily_volume >= 8:
            score += 18.0
        elif avg_daily_volume >= 2:
            score += 10.0
        else:
            score += 4.0
    else:
        score += 10.0  # unknown history

    # Profit & Margin Sweet Spot (max 20 pts)
    if 15.0 <= margin_pct <= 90.0:
        score += 20.0
    elif 8.0 <= margin_pct < 15.0 or 90.0 < margin_pct <= 180.0:
        score += 14.0
    else:
        score += 8.0

    # Profit magnitude bonus (up to 10 pts)
    if profit_per_item >= 50000:
        score += 10.0
    elif profit_per_item >= 10000:
        score += 7.0
    elif profit_per_item >= 2000:
        score += 4.0
    else:
        score += 2.0

    # Safety / Route factor adjustments
    if risk.lower() == "high":
        score -= 15.0
    elif risk.lower() == "medium":
        score -= 5.0
    else:
        score += 5.0

    # Instant sell bonus (zero listing risk, instant silver)
    if exit_type == ExitType.INSTANT_SELL:
        score += 8.0

    final_score = max(5, min(99, int(round(score))))

    if final_score >= 80:
        return final_score, "S", "🌟 TOP PICK"
    elif final_score >= 62:
        return final_score, "A", "🟢 GOOD DEAL"
    elif final_score >= 42:
        return final_score, "B", "🟡 MODERATE"
    else:
        return final_score, "C", "⚠️ SPECULATIVE"


def analyze_crafting(
    prices: Sequence[PriceRecord],
    config: AppConfig,
    focus: bool = False,
    item_names: dict[str, str] | None = None,
) -> list[CraftingOpportunity]:
    """Calculates crafting and refining profit across royal cities and Caerleon.

    Identifies profitable refining (Planks, Metal Bars, Cloth, Leather, Stone Blocks)
    and equipment crafting (Bags, Capes, Cursed Staffs, Swords, Bows, Armor sets).
    """
    tax_rate = config.tax_rate_premium if config.premium else config.tax_rate_standard
    setup_fee_rate = config.setup_fee_rate

    cheapest_sell: dict[str, tuple[int, str]] = {}
    highest_sell: dict[str, tuple[int, str]] = {}
    city_prices: dict[tuple[str, str], int] = {}

    for p in prices:
        if p.sell_price_min <= 0:
            continue
        curr = (p.sell_price_min, p.city)
        if p.item_id not in cheapest_sell or p.sell_price_min < cheapest_sell[p.item_id][0]:
            cheapest_sell[p.item_id] = curr
        if p.item_id not in highest_sell or p.sell_price_min > highest_sell[p.item_id][0]:
            highest_sell[p.item_id] = curr
        city_prices[(p.item_id, p.city)] = p.sell_price_min

    opportunities: list[CraftingOpportunity] = []

    # 1. Refining Recipes
    refining_defs = [
        ("PLANKS", "WOOD", "Fort Sterling", "Wood Refining"),
        ("METALBAR", "ORE", "Thetford", "Ore Refining"),
        ("CLOTH", "FIBER", "Lymhurst", "Fiber Refining"),
        ("LEATHER", "HIDE", "Martlock", "Hide Refining"),
        ("STONEBLOCK", "ROCK", "Bridgewatch", "Stone Refining"),
    ]
    raw_qty_map = {3: 2, 4: 2, 5: 3, 6: 4, 7: 5, 8: 5}

    for out_base, raw_base, bonus_city, craft_label in refining_defs:
        for tier in (3, 4, 5, 6, 7, 8):
            for enchant in (0, 1, 2, 3):
                if tier == 3 and enchant > 0:
                    continue
                if enchant == 0:
                    out_id = f"T{tier}_{out_base}"
                    raw_id = f"T{tier}_{raw_base}"
                else:
                    out_id = f"T{tier}_{out_base}_LEVEL{enchant}@{enchant}"
                    raw_id = f"T{tier}_{raw_base}_LEVEL{enchant}@{enchant}"

                prev_tier = tier - 1
                prev_id = f"T{prev_tier}_{out_base}"

                raw_info = city_prices.get((raw_id, bonus_city))
                if not raw_info and raw_id in cheapest_sell:
                    raw_info, _ = cheapest_sell[raw_id]

                if not raw_info or raw_info <= 0:
                    continue

                prev_info = city_prices.get((prev_id, bonus_city))
                if not prev_info and prev_id in cheapest_sell:
                    prev_info, _ = cheapest_sell[prev_id]
                if not prev_info or prev_info <= 0:
                    prev_info = 100 * (prev_tier - 2)

                raw_qty = raw_qty_map[tier]
                mat_cost = (raw_qty * raw_info) + prev_info

                rrr = 0.539 if focus else 0.367
                effective_cost = mat_cost * (1.0 - rrr)

                if out_id not in highest_sell:
                    continue
                sell_price, sell_city = highest_sell[out_id]
                if sell_price <= 0:
                    continue

                profit = calculate_crafting_profit(
                    sell_price=sell_price,
                    effective_material_cost=effective_cost,
                    tax_rate=tax_rate,
                    setup_fee_rate=setup_fee_rate,
                )
                margin = calculate_margin(profit, int(round(effective_cost)))

                name = item_names.get(out_id) if item_names else None
                if not name:
                    name = out_id.replace("_", " ").title()

                ingredients_desc = f"{raw_qty}x {raw_id.split('@')[0]} + 1x {prev_id}"

                opportunities.append(
                    CraftingOpportunity(
                        item_id=out_id,
                        item_name=name,
                        craft_type=craft_label,
                        craft_city=bonus_city,
                        sell_city=sell_city,
                        material_cost=float(mat_cost),
                        effective_cost=float(effective_cost),
                        sell_price=sell_price,
                        profit_per_item=float(profit),
                        margin_pct=float(margin),
                        resource_return_rate=rrr,
                        ingredients_desc=ingredients_desc,
                        city_bonus=True,
                        focus=focus,
                        tier=tier,
                        enchant=enchant,
                    )
                )

    # 2. Equipment Crafting (Bags, Capes, Staffs, Weapons, Armors)
    eq_rrr = 0.435 if focus else 0.152
    qty_by_tier = {3: 2, 4: 4, 5: 8, 6: 16}

    for eq_type, label in (("BAG", "Bag Crafting"), ("CAPE", "Cape Crafting")):
        for tier in (3, 4, 5, 6):
            for enchant in (0, 1, 2, 3):
                if tier == 3 and enchant > 0:
                    continue
                suffix = f"@{enchant}" if enchant > 0 else ""
                ench_lvl = f"_LEVEL{enchant}@{enchant}" if enchant > 0 else ""
                item_id = f"T{tier}_{eq_type}{suffix}"
                cloth_id = f"T{tier}_CLOTH{ench_lvl}"
                leather_id = f"T{tier}_LEATHER{ench_lvl}"

                cloth_p = cheapest_sell.get(cloth_id, (0, ""))[0]
                leather_p = cheapest_sell.get(leather_id, (0, ""))[0]
                if cloth_p <= 0 or leather_p <= 0:
                    continue

                q = qty_by_tier[tier]
                mat_cost = (q * cloth_p) + (q * leather_p)
                effective_cost = mat_cost * (1.0 - eq_rrr)

                if item_id not in highest_sell:
                    continue
                sell_price, sell_city = highest_sell[item_id]
                if sell_price <= 0:
                    continue

                profit = calculate_crafting_profit(
                    sell_price=sell_price,
                    effective_material_cost=effective_cost,
                    tax_rate=tax_rate,
                    setup_fee_rate=setup_fee_rate,
                )
                margin = calculate_margin(profit, int(round(effective_cost)))

                name = item_names.get(item_id) if item_names else None
                if not name:
                    name = item_id.replace("_", " ").title()

                opportunities.append(
                    CraftingOpportunity(
                        item_id=item_id,
                        item_name=name,
                        craft_type=label,
                        craft_city="Any Royal City",
                        sell_city=sell_city,
                        material_cost=float(mat_cost),
                        effective_cost=float(effective_cost),
                        sell_price=sell_price,
                        profit_per_item=float(profit),
                        margin_pct=float(margin),
                        resource_return_rate=eq_rrr,
                        ingredients_desc=f"{q}x Cloth + {q}x Leather",
                        city_bonus=False,
                        focus=focus,
                        tier=tier,
                        enchant=enchant,
                    )
                )

    weapon_recipes = [
        ("T{t}_MAIN_CURSEDSTAFF", "1H Cursed Staff", "Weapon", [("T{t}_PLANKS", 16), ("T{t}_METALBAR", 8)]),
        ("T{t}_2H_CURSEDSTAFF", "Great Cursed Staff", "Weapon", [("T{t}_PLANKS", 20), ("T{t}_METALBAR", 12)]),
        ("T{t}_MAIN_SWORD", "Broadsword", "Weapon", [("T{t}_METALBAR", 16), ("T{t}_LEATHER", 8)]),
        ("T{t}_2H_BOW", "Bow", "Weapon", [("T{t}_PLANKS", 32)]),
        ("T{t}_ARMOR_PLATE_SET1", "Soldier Armor", "Armor", [("T{t}_METALBAR", 16)]),
        ("T{t}_ARMOR_LEATHER_SET1", "Mercenary Jacket", "Armor", [("T{t}_LEATHER", 16)]),
        ("T{t}_ARMOR_CLOTH_SET1", "Mage Robe", "Armor", [("T{t}_CLOTH", 16)]),
    ]

    for template, label, category, ingredients in weapon_recipes:
        for tier in (4, 5, 6):
            item_id = template.format(t=tier)
            total_mat_cost = 0.0
            possible = True
            desc_parts = []
            for ing_tpl, qty in ingredients:
                ing_id = ing_tpl.format(t=tier)
                ing_p = cheapest_sell.get(ing_id, (0, ""))[0]
                if ing_p <= 0:
                    possible = False
                    break
                total_mat_cost += qty * ing_p
                ing_name = ing_id.split("_")[-1].capitalize()
                desc_parts.append(f"{qty}x {ing_name}")

            if not possible:
                continue

            effective_cost = total_mat_cost * (1.0 - eq_rrr)
            if item_id not in highest_sell:
                continue
            sell_price, sell_city = highest_sell[item_id]
            if sell_price <= 0:
                continue

            profit = calculate_crafting_profit(
                sell_price=sell_price,
                effective_material_cost=effective_cost,
                tax_rate=tax_rate,
                setup_fee_rate=setup_fee_rate,
            )
            margin = calculate_margin(profit, int(round(effective_cost)))

            name = item_names.get(item_id) if item_names else None
            if not name:
                name = item_id.replace("_", " ").title()

            opportunities.append(
                CraftingOpportunity(
                    item_id=item_id,
                    item_name=name,
                    craft_type=category,
                    craft_city="Any Royal City",
                    sell_city=sell_city,
                    material_cost=float(total_mat_cost),
                    effective_cost=float(effective_cost),
                    sell_price=sell_price,
                    profit_per_item=float(profit),
                    margin_pct=float(margin),
                    resource_return_rate=eq_rrr,
                    ingredients_desc=" + ".join(desc_parts),
                    city_bonus=False,
                    focus=focus,
                    tier=tier,
                    enchant=0,
                )
            )

    opportunities.sort(key=lambda x: x.profit_per_item, reverse=True)
    return opportunities

