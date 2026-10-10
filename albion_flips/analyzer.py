from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Sequence
import logging

from albion_flips.config import AppConfig
from albion_flips.models import (
    CraftingOpportunity,
    EnchantingOpportunity,
    ExitType,
    FlipOpportunity,
    HistoryRecord,
    PriceRecord,
)
from albion_flips.profit import (
    calculate_crafting_profit,
    calculate_enchanting_materials,
    calculate_enchanting_profit,
    calculate_instant_profit,
    calculate_margin,
    calculate_profit_per_kg,
    calculate_sell_order_profit,
    calculate_total_profit,
)
from albion_flips.weights import get_item_weight


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
    history_records: Sequence[HistoryRecord] | Mapping[tuple[str, str, int], HistoryRecord],
    item_id: str,
    city: str,
    quality: int = 1,
    volume_days: int = 3,
) -> float | None:
    """Computes average daily volume sold in destination city over the volume_days window.

    Supports both pre-indexed dictionary lookups for O(1) performance across thousands
    of items, and standard sequence iteration. Returns None if history is missing for the item/city.
    """
    match: HistoryRecord | None = None
    if isinstance(history_records, Mapping):
        match = history_records.get((item_id, city, quality))
    else:
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



def deduplicate_prices(
    prices: Sequence[PriceRecord],
    now: datetime | None = None,
    fulfilled_orders: dict[tuple[str, str, int], datetime] | None = None,
    price_overrides: dict[tuple[str, str, int], int] | None = None,
) -> list[PriceRecord]:
    """Deduplicates prices by (item_id, city, quality), strictly keeping the freshest record.

    Applies any user-marked fulfilled orders (zeroing out dead buy orders) and manual
    price overrides, ensuring that all modules (flips, Black Market, crafting, and recent prices)
    operate on an identical, synchronized, and up-to-date price set.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 1. Deduplicate prices by (item_id, city, quality), strictly keeping the freshest record.
    deduped_prices: dict[tuple[str, str, int], PriceRecord] = {}
    for p in prices:
        key = (p.item_id, p.city, p.quality)
        existing = deduped_prices.get(key)
        if existing is None:
            deduped_prices[key] = p
        else:
            p_date = max(p.sell_price_min_date, p.buy_price_max_date)
            ex_date = max(existing.sell_price_min_date, existing.buy_price_max_date)
            if p_date >= ex_date:
                deduped_prices[key] = p

    # 2. Apply manual fulfillment markers & price overrides
    active_records: list[PriceRecord] = []
    for key, p in deduped_prices.items():
        item_id, city, quality = key
        # Check if user marked order as fulfilled (dead/completed in-game)
        if fulfilled_orders and key in fulfilled_orders:
            fulfilled_time = fulfilled_orders[key]
            if p.buy_price_max > 0 and not p.buy_price_max_date.startswith("0001"):
                try:
                    b_date = datetime.fromisoformat(p.buy_price_max_date).replace(tzinfo=timezone.utc)
                    if b_date <= fulfilled_time:
                        p = PriceRecord(
                            item_id=p.item_id,
                            city=p.city,
                            quality=p.quality,
                            sell_price_min=p.sell_price_min,
                            sell_price_min_date=p.sell_price_min_date,
                            sell_price_max=p.sell_price_max,
                            sell_price_max_date=p.sell_price_max_date,
                            buy_price_min=p.buy_price_min,
                            buy_price_min_date=p.buy_price_min_date,
                            buy_price_max=0,
                            buy_price_max_date="0001-01-01T00:00:00",
                        )
                except Exception:
                    pass

        # Check if user manually overrode the price
        if price_overrides and key in price_overrides:
            override_val = price_overrides[key]
            if city == "Black Market":
                p = PriceRecord(
                    item_id=p.item_id,
                    city=p.city,
                    quality=p.quality,
                    sell_price_min=0 if override_val == 0 else p.sell_price_min,
                    sell_price_min_date=p.sell_price_min_date,
                    sell_price_max=p.sell_price_max,
                    sell_price_max_date=p.sell_price_max_date,
                    buy_price_min=p.buy_price_min,
                    buy_price_min_date=p.buy_price_min_date,
                    buy_price_max=override_val,
                    buy_price_max_date=now.isoformat() if override_val > 0 else "0001-01-01T00:00:00",
                )
            else:
                p = PriceRecord(
                    item_id=p.item_id,
                    city=p.city,
                    quality=p.quality,
                    sell_price_min=override_val,
                    sell_price_min_date=now.isoformat() if override_val > 0 else "0001-01-01T00:00:00",
                    sell_price_max=p.sell_price_max,
                    sell_price_max_date=p.sell_price_max_date,
                    buy_price_min=p.buy_price_min,
                    buy_price_min_date=p.buy_price_min_date,
                    buy_price_max=p.buy_price_max,
                    buy_price_max_date=p.buy_price_max_date,
                )

        active_records.append(p)

    # 3. Add any standalone price overrides that had no prior record
    if price_overrides:
        for (item_id, city, quality), override_val in price_overrides.items():
            if (item_id, city, quality) not in deduped_prices:
                active_records.append(
                    PriceRecord(
                        item_id=item_id,
                        city=city,
                        quality=quality,
                        sell_price_min=override_val if city != "Black Market" else 0,
                        sell_price_min_date=now.isoformat() if (city != "Black Market" and override_val > 0) else "0001-01-01T00:00:00",
                        sell_price_max=override_val if city != "Black Market" else 0,
                        sell_price_max_date=now.isoformat() if (city != "Black Market" and override_val > 0) else "0001-01-01T00:00:00",
                        buy_price_min=0,
                        buy_price_min_date="0001-01-01T00:00:00",
                        buy_price_max=override_val if city == "Black Market" else 0,
                        buy_price_max_date=now.isoformat() if (city == "Black Market" and override_val > 0) else "0001-01-01T00:00:00",
                    )
                )

    return active_records


def analyze_flips(
    prices: Sequence[PriceRecord],
    history: Sequence[HistoryRecord],
    config: AppConfig,
    now: datetime | None = None,
    fulfilled_orders: dict[tuple[str, str, int], datetime] | None = None,
    price_overrides: dict[tuple[str, str, int], int] | None = None,
) -> list[FlipOpportunity]:
    """Analyzes market prices and history to produce ranked flip opportunities.

    Applies domain rules in order:
    1. Deduplicate prices keeping freshest records and applying fulfillment & overrides.
    2. Filter out stale data older than max_age_hours.
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

    active_records = deduplicate_prices(
        prices=prices,
        now=now,
        fulfilled_orders=fulfilled_orders,
        price_overrides=price_overrides,
    )

    # Build fast O(1) index for history records
    history_map: Mapping[tuple[str, str, int], HistoryRecord]
    if isinstance(history, Mapping):
        history_map = history
    else:
        history_map = {(r.item_id, r.location, r.quality): r for r in history}

    # Group price records by item_id
    prices_by_item: dict[str, list[PriceRecord]] = {}
    for p in active_records:
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
                    history_records=history_map,
                    item_id=item_id,
                    city=sell_rec.city,
                    quality=sell_rec.quality,
                    volume_days=config.volume_days,
                )
                if (dest_volume is None or dest_volume == 0.0) and sell_rec.quality > 1:
                    fallback_vol = compute_daily_volume(
                        history_records=history_map,
                        item_id=item_id,
                        city=sell_rec.city,
                        quality=1,
                        volume_days=config.volume_days,
                    )
                    if fallback_vol is not None:
                        dest_volume = fallback_vol

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

                # Est. Daily Profit / Turnover Velocity
                if dest_volume is not None and dest_volume > 0:
                    est_daily_profit = chosen_profit * dest_volume
                else:
                    est_daily_profit = 0.0

                item_weight = get_item_weight(item_id)
                profit_kg = calculate_profit_per_kg(chosen_profit, item_weight)

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
                        est_daily_profit=est_daily_profit,
                        weight=item_weight,
                        profit_per_kg=profit_kg,
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
        elif avg_daily_volume > 0:
            score += 4.0
        else:
            # avg_daily_volume == 0.0 (verified dead item)
            score -= 15.0
    else:
        score += 3.0  # unknown history / unverified liquidity

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

    if avg_daily_volume == 0.0:
        return min(35, final_score), "D", "💀 DEAD ITEM"
    elif final_score >= 80:
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
    now: datetime | None = None,
    fulfilled_orders: dict[tuple[str, str, int], datetime] | None = None,
    price_overrides: dict[tuple[str, str, int], int] | None = None,
) -> list[CraftingOpportunity]:
    """Calculates crafting and refining profit across royal cities, Caerleon, and Black Market.

    Identifies profitable refining (Planks, Metal Bars, Cloth, Leather, Stone Blocks)
    and equipment crafting (Bags, Capes, Faction Capes, Weapons, Armors, Royal Gear).
    """
    clean_prices = deduplicate_prices(
        prices=prices,
        now=now,
        fulfilled_orders=fulfilled_orders,
        price_overrides=price_overrides,
    )
    tax_rate = config.tax_rate_premium if config.premium else config.tax_rate_standard
    setup_fee_rate = config.setup_fee_rate

    cheapest_sell: dict[str, tuple[int, str]] = {}
    highest_sell: dict[str, tuple[int, str]] = {}
    city_prices: dict[tuple[str, str], int] = {}

    for p in clean_prices:
        if p.sell_price_min > 0:
            curr = (p.sell_price_min, p.city)
            if p.item_id not in cheapest_sell or p.sell_price_min < cheapest_sell[p.item_id][0]:
                cheapest_sell[p.item_id] = curr
            if p.item_id not in highest_sell or p.sell_price_min > highest_sell[p.item_id][0]:
                highest_sell[p.item_id] = curr
            city_prices[(p.item_id, p.city)] = p.sell_price_min

        # If item has a Black Market buy order, crafters can sell directly to Black Market!
        if p.city == "Black Market" and p.buy_price_max > 0:
            curr_bm = (p.buy_price_max, p.city)
            if p.item_id not in highest_sell or p.buy_price_max > highest_sell[p.item_id][0]:
                highest_sell[p.item_id] = curr_bm

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

                fee = 0.0 if sell_city == "Black Market" else setup_fee_rate
                profit = calculate_crafting_profit(
                    sell_price=sell_price,
                    effective_material_cost=effective_cost,
                    tax_rate=tax_rate,
                    setup_fee_rate=fee,
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

    for eq_type, label in (("BAG", "Bag Crafting"), ("CAPE", "Cape Crafting")):
        for tier in (3, 4, 5, 6, 7, 8):
            for enchant in (0, 1, 2, 3, 4):
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

                if eq_type == "BAG":
                    q = 4 if tier == 3 else 8
                else:
                    q = 2 if tier == 3 else 4

                mat_cost = (q * cloth_p) + (q * leather_p)
                effective_cost = mat_cost * (1.0 - eq_rrr)

                if item_id not in highest_sell:
                    continue
                sell_price, sell_city = highest_sell[item_id]
                if sell_price <= 0:
                    continue

                fee = 0.0 if sell_city == "Black Market" else setup_fee_rate
                profit = calculate_crafting_profit(
                    sell_price=sell_price,
                    effective_material_cost=effective_cost,
                    tax_rate=tax_rate,
                    setup_fee_rate=fee,
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

    # 2b. Faction & Special Capes (Martlock, Bridgewatch, Fort Sterling, etc.)
    faction_capes = [
        ("CAPEITEM_FW_MARTLOCK", "Martlock Cape", "T1_FACTION_HIGHLAND_TOKEN_1", "Rockheart", "Martlock"),
        ("CAPEITEM_FW_BRIDGEWATCH", "Bridgewatch Cape", "T1_FACTION_STEPPE_TOKEN_1", "Beastheart", "Bridgewatch"),
        ("CAPEITEM_FW_FORTSTERLING", "Fort Sterling Cape", "T1_FACTION_MOUNTAIN_TOKEN_1", "Mountainheart", "Fort Sterling"),
        ("CAPEITEM_FW_LYMHURST", "Lymhurst Cape", "T1_FACTION_FOREST_TOKEN_1", "Treeheart", "Lymhurst"),
        ("CAPEITEM_FW_THETFORD", "Thetford Cape", "T1_FACTION_SWAMP_TOKEN_1", "Vineheart", "Thetford"),
        ("CAPEITEM_FW_CAERLEON", "Caerleon Cape", "T1_FACTION_CAERLEON_TOKEN_1", "Shadowheart", "Caerleon"),
        ("CAPEITEM_FW_BRECILIEN", "Brecilien Cape", "QUESTITEM_TOKEN_MISTS", "Faerie Fire", "Brecilien"),
        ("CAPEITEM_HERETIC", "Heretic Cape", "T1_FACTION_FOREST_TOKEN_1", "Treeheart", "Toolmaker"),
        ("CAPEITEM_UNDEAD", "Undead Cape", "T1_FACTION_MOUNTAIN_TOKEN_1", "Mountainheart", "Toolmaker"),
        ("CAPEITEM_KEEPER", "Keeper Cape", "T1_FACTION_HIGHLAND_TOKEN_1", "Rockheart", "Toolmaker"),
        ("CAPEITEM_MORGANA", "Morgana Cape", "T1_FACTION_SWAMP_TOKEN_1", "Vineheart", "Toolmaker"),
        ("CAPEITEM_DEMON", "Demon Cape", "T1_FACTION_STEPPE_TOKEN_1", "Beastheart", "Toolmaker"),
        ("CAPEITEM_AVALON", "Avalonian Cape", "QUESTITEM_TOKEN_AVALON", "Avalonian Energy", "Toolmaker"),
        ("CAPEITEM_SMUGGLER", "Smuggler Cape", "T1_FACTION_CAERLEON_TOKEN_1", "Shadowheart", "Toolmaker"),
    ]
    heart_qty_map = {4: 1, 5: 1, 6: 3, 7: 5, 8: 10}

    for cape_tag, cape_label, token_id, token_name, default_city in faction_capes:
        for tier in (4, 5, 6, 7, 8):
            for enchant in (0, 1, 2, 3, 4):
                suffix = f"@{enchant}" if enchant > 0 else ""
                item_id = f"T{tier}_{cape_tag}{suffix}"
                base_cape_id = f"T{tier}_CAPE{suffix}"
                crest_id = f"T{tier}_{cape_tag}_BP"
                token_qty = heart_qty_map[tier] * 15 if cape_tag == "CAPEITEM_AVALON" else heart_qty_map[tier]

                cape_p, cape_city = cheapest_sell.get(base_cape_id, (0, ""))
                crest_p, crest_city = cheapest_sell.get(crest_id, (0, ""))
                token_p, token_city = cheapest_sell.get(token_id, (0, ""))

                if cape_p <= 0 or crest_p <= 0 or token_p <= 0:
                    continue

                mat_cost = cape_p + crest_p + (token_qty * token_p)
                effective_cost = float(mat_cost)

                if item_id not in highest_sell:
                    continue
                sell_price, sell_city = highest_sell[item_id]
                if sell_price <= 0:
                    continue

                fee = 0.0 if sell_city == "Black Market" else setup_fee_rate
                profit = calculate_crafting_profit(
                    sell_price=sell_price,
                    effective_material_cost=effective_cost,
                    tax_rate=tax_rate,
                    setup_fee_rate=fee,
                )
                margin = calculate_margin(profit, int(round(effective_cost)))

                name = item_names.get(item_id) if item_names else None
                if not name:
                    name = item_id.replace("_", " ").title()

                craft_city = default_city if default_city != "Toolmaker" else (cape_city or "Any Royal City")

                opportunities.append(
                    CraftingOpportunity(
                        item_id=item_id,
                        item_name=name,
                        craft_type="Faction Capes",
                        craft_city=craft_city,
                        sell_city=sell_city,
                        material_cost=float(mat_cost),
                        effective_cost=float(effective_cost),
                        sell_price=sell_price,
                        profit_per_item=float(profit),
                        margin_pct=float(margin),
                        resource_return_rate=0.0,
                        ingredients_desc=f"1x Base Cape T{tier}{suffix} + 1x {cape_label.split()[0]} Crest + {token_qty}x {token_name}",
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

            fee = 0.0 if sell_city == "Black Market" else setup_fee_rate
            profit = calculate_crafting_profit(
                sell_price=sell_price,
                effective_material_cost=effective_cost,
                tax_rate=tax_rate,
                setup_fee_rate=fee,
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

    # 3. Royal Equipment Crafting (Royal Robe, Royal Jacket, Royal Armor, Royal Cowl, etc.)
    royal_recipes = [
        ("T{t}_ARMOR_CLOTH_ROYAL", "Royal Robe", "T{t}_ARMOR_CLOTH_SET1", 4),
        ("T{t}_ARMOR_LEATHER_ROYAL", "Royal Jacket", "T{t}_ARMOR_LEATHER_SET1", 4),
        ("T{t}_ARMOR_PLATE_ROYAL", "Royal Armor", "T{t}_ARMOR_PLATE_SET1", 4),
        ("T{t}_HEAD_CLOTH_ROYAL", "Royal Cowl", "T{t}_HEAD_CLOTH_SET1", 2),
        ("T{t}_HEAD_LEATHER_ROYAL", "Royal Hood", "T{t}_HEAD_LEATHER_SET1", 2),
        ("T{t}_HEAD_PLATE_ROYAL", "Royal Helmet", "T{t}_HEAD_PLATE_SET1", 2),
        ("T{t}_SHOES_CLOTH_ROYAL", "Royal Sandals", "T{t}_SHOES_CLOTH_SET1", 2),
        ("T{t}_SHOES_LEATHER_ROYAL", "Royal Shoes", "T{t}_SHOES_LEATHER_SET1", 2),
        ("T{t}_SHOES_PLATE_ROYAL", "Royal Boots", "T{t}_SHOES_PLATE_SET1", 2),
    ]

    for template, label, base_tpl, sigil_qty in royal_recipes:
        for tier in (4, 5, 6, 7, 8):
            out_id = template.format(t=tier)
            base_id = base_tpl.format(t=tier)
            sigil_id = f"QUESTITEM_TOKEN_ROYAL_T{tier}"

            base_p = cheapest_sell.get(base_id, (0, ""))[0]
            sigil_p = cheapest_sell.get(sigil_id, (0, ""))[0]

            if base_p <= 0 or sigil_p <= 0:
                continue

            mat_cost = base_p + (sigil_qty * sigil_p)
            effective_cost = (base_p * (1.0 - eq_rrr)) + (sigil_qty * sigil_p)

            if out_id not in highest_sell:
                continue
            sell_price, sell_city = highest_sell[out_id]
            if sell_price <= 0:
                continue

            fee = 0.0 if sell_city == "Black Market" else setup_fee_rate
            profit = calculate_crafting_profit(
                sell_price=sell_price,
                effective_material_cost=effective_cost,
                tax_rate=tax_rate,
                setup_fee_rate=fee,
            )
            margin = calculate_margin(profit, int(round(effective_cost)))
            name = item_names.get(out_id) if item_names else None
            if not name:
                name = out_id.replace("_", " ").title()

            opportunities.append(
                CraftingOpportunity(
                    item_id=out_id,
                    item_name=name,
                    craft_type="Royal Crafting",
                    craft_city="Royal Station",
                    sell_city=sell_city,
                    material_cost=float(mat_cost),
                    effective_cost=float(effective_cost),
                    sell_price=sell_price,
                    profit_per_item=float(profit),
                    margin_pct=float(margin),
                    resource_return_rate=0.0,
                    ingredients_desc=f"1x Base Gear + {sigil_qty}x Royal Sigil T{tier}",
                    city_bonus=False,
                    focus=focus,
                    tier=tier,
                    enchant=0,
                )
            )

    opportunities.sort(key=lambda x: x.profit_per_item, reverse=True)
    return opportunities


DEFAULT_RUNE_PRICES: dict[str, int] = {
    "T4_RUNE": 45,
    "T4_SOUL": 220,
    "T4_RELIC": 1200,
    "T5_RUNE": 180,
    "T5_SOUL": 950,
    "T5_RELIC": 4500,
    "T6_RUNE": 650,
    "T6_SOUL": 3200,
    "T6_RELIC": 16000,
    "T7_RUNE": 2400,
    "T7_SOUL": 12000,
    "T7_RELIC": 55000,
    "T8_RUNE": 9500,
    "T8_SOUL": 48000,
    "T8_RELIC": 220000,
}

MATERIAL_NAMES: dict[str, str] = {
    "T4_RUNE": "Adept's Rune",
    "T4_SOUL": "Adept's Soul",
    "T4_RELIC": "Adept's Relic",
    "T5_RUNE": "Expert's Rune",
    "T5_SOUL": "Expert's Soul",
    "T5_RELIC": "Expert's Relic",
    "T6_RUNE": "Master's Rune",
    "T6_SOUL": "Master's Soul",
    "T6_RELIC": "Master's Relic",
    "T7_RUNE": "Grandmaster's Rune",
    "T7_SOUL": "Grandmaster's Soul",
    "T7_RELIC": "Grandmaster's Relic",
    "T8_RUNE": "Elder's Rune",
    "T8_SOUL": "Elder's Soul",
    "T8_RELIC": "Elder's Relic",
}


def is_enchantable_item(item_id: str) -> bool:
    """Checks if an item ID represents gear that can be enchanted at the Artifact Foundry."""
    clean = item_id.split("@")[0].upper()
    if not (clean.startswith("T4_") or clean.startswith("T5_") or clean.startswith("T6_") or clean.startswith("T7_") or clean.startswith("T8_")):
        return False
    if any(m in clean for m in ("_RUNE", "_SOUL", "_RELIC", "_SHARD", "_POTION", "_MEAL", "_FISH", "_MOUNT", "_TOKEN", "_QUESTITEM")):
        return False
    return any(slot in clean for slot in ("_BAG", "_CAPE", "_MAIN_", "_2H_", "_ARMOR_", "_HEAD_", "_SHOES_", "_OFF_"))


def analyze_enchanting(
    prices: Sequence[PriceRecord],
    config: AppConfig,
    history: Sequence[HistoryRecord] | Mapping[tuple[str, str, int], HistoryRecord] | None = None,
    now: datetime | None = None,
    fulfilled_orders: dict[tuple[str, str, int], datetime] | None = None,
    price_overrides: dict[tuple[str, str, int], int] | None = None,
    custom_rune_prices: dict[str, int] | None = None,
    item_names: dict[str, str] | None = None,
    mass_batch_size: int = 10,
) -> tuple[list[EnchantingOpportunity], dict[str, Any]]:
    """Analyzes enchanting profitability across all monitored cities and the Black Market.

    Returns:
        (opportunities, material_status_dict)
    """
    clean_prices = deduplicate_prices(
        prices=prices,
        now=now,
        fulfilled_orders=fulfilled_orders,
        price_overrides=price_overrides,
    )
    tax_rate = config.tax_rate_premium if config.premium else config.tax_rate_standard
    setup_fee_rate = config.setup_fee_rate

    # Build fast O(1) history index
    history_map: Mapping[tuple[str, str, int], HistoryRecord] = {}
    if history:
        if isinstance(history, Mapping):
            history_map = history
        else:
            history_map = {(h.item_id, h.location, h.quality): h for h in history}

    def get_target_volume_info(t_id: str, dest_city: str) -> tuple[float | None, bool, str]:
        if not history_map:
            return None, True, "untracked"

        vol = compute_daily_volume(history_map, t_id, dest_city, quality=1, volume_days=config.volume_days)
        if vol is None or vol == 0:
            for q in (2, 3):
                q_vol = compute_daily_volume(history_map, t_id, dest_city, quality=q, volume_days=config.volume_days)
                if q_vol is not None and q_vol > 0:
                    vol = (vol or 0.0) + q_vol
                    break

        if vol is None:
            return None, True, "untracked"
        elif vol >= 20.0:
            return vol, False, "high"
        elif vol >= 5.0:
            return vol, False, "active"
        elif vol >= 1.0:
            return vol, False, "slow"
        else:
            return vol, False, "dead"

    # 1. Harvest live Rune, Soul, Relic prices per city and global lowest
    city_mats: dict[tuple[str, str], int] = {}
    cheapest_mats: dict[str, tuple[int, str]] = {}
    mat_dates: dict[tuple[str, str], str] = {}

    for p in clean_prices:
        mat_id = p.item_id.upper()
        if mat_id in DEFAULT_RUNE_PRICES:
            price = p.sell_price_min if p.sell_price_min > 0 else (p.buy_price_max if p.buy_price_max > 0 else 0)
            if price > 0:
                curr_p = city_mats.get((mat_id, p.city), 0)
                if curr_p == 0 or price < curr_p:
                    city_mats[(mat_id, p.city)] = price
                    mat_dates[(mat_id, p.city)] = p.sell_price_min_date or p.buy_price_max_date
                if mat_id not in cheapest_mats or price < cheapest_mats[mat_id][0]:
                    cheapest_mats[mat_id] = (price, p.city)

    # Build material status dict for the UI Artifact Foundry Ticker
    material_status: dict[str, Any] = {}
    for mat_id, def_price in DEFAULT_RUNE_PRICES.items():
        user_override = (custom_rune_prices or {}).get(mat_id)
        if user_override and user_override > 0:
            final_p = user_override
            source = "override"
        elif mat_id in cheapest_mats:
            final_p = cheapest_mats[mat_id][0]
            source = "live"
        else:
            final_p = def_price
            source = "default"

        tier = int(mat_id[1])
        mat_type = mat_id.split("_")[1].lower()  # rune, soul, relic
        material_status[mat_id] = {
            "id": mat_id,
            "name": MATERIAL_NAMES.get(mat_id, mat_id),
            "tier": tier,
            "type": mat_type,
            "price": final_p,
            "source": source,
            "city": cheapest_mats.get(mat_id, (0, "All"))[1],
        }

    # Helper function to get material unit price for a given city
    def get_mat_price(mat_id: str, city: str) -> int:
        if custom_rune_prices and mat_id in custom_rune_prices and custom_rune_prices[mat_id] > 0:
            return custom_rune_prices[mat_id]
        if (mat_id, city) in city_mats:
            return city_mats[(mat_id, city)]
        if mat_id in cheapest_mats:
            return cheapest_mats[mat_id][0]
        return DEFAULT_RUNE_PRICES.get(mat_id, 100)

    # 2. Build index of item prices
    city_item_sell: dict[tuple[str, str], int] = {}
    city_item_buy: dict[tuple[str, str], int] = {}
    item_dates: dict[tuple[str, str], str] = {}
    bm_buy: dict[str, int] = {}
    bm_dates: dict[str, str] = {}
    all_item_ids: set[str] = set()

    for p in clean_prices:
        all_item_ids.add(p.item_id)
        if p.sell_price_min > 0:
            curr_sell = city_item_sell.get((p.item_id, p.city), 0)
            if curr_sell == 0 or p.sell_price_min < curr_sell:
                city_item_sell[(p.item_id, p.city)] = p.sell_price_min
                item_dates[(p.item_id, p.city)] = p.sell_price_min_date
        if p.buy_price_max > 0:
            curr_buy = city_item_buy.get((p.item_id, p.city), 0)
            if p.buy_price_max > curr_buy:
                city_item_buy[(p.item_id, p.city)] = p.buy_price_max
        if p.city == "Black Market" and p.buy_price_max > 0:
            curr_bm = bm_buy.get(p.item_id, 0)
            if p.buy_price_max > curr_bm:
                bm_buy[p.item_id] = p.buy_price_max
                bm_dates[p.item_id] = p.buy_price_max_date

    item_cities_sell: dict[str, list[tuple[str, int]]] = {}
    for (it_id, c_name), b_cost in city_item_sell.items():
        if b_cost > 0:
            item_cities_sell.setdefault(it_id, []).append((c_name, b_cost))

    # 3. Analyze all enchantable gear
    opportunities: list[EnchantingOpportunity] = []
    seen_opp_keys: set[tuple[str, str, str, str]] = set()

    for raw_id in all_item_ids:
        if not is_enchantable_item(raw_id):
            continue

        base_stem = raw_id.split("@")[0]
        tier = int(base_stem[1]) if len(base_stem) > 1 and base_stem[1].isdigit() else 4
        from_enchant = int(raw_id.split("@")[1]) if "@" in raw_id else 0

        if from_enchant >= 3:
            continue

        slot_type, mat_qty = calculate_enchanting_materials(base_stem)
        human_name = (item_names or {}).get(base_stem) or base_stem.replace("_", " ").title()

        steps: list[tuple[int, list[str]]] = []
        if from_enchant == 0:
            steps.append((1, [f"T{tier}_RUNE"]))
            steps.append((2, [f"T{tier}_RUNE", f"T{tier}_SOUL"]))
            steps.append((3, [f"T{tier}_RUNE", f"T{tier}_SOUL", f"T{tier}_RELIC"]))
        elif from_enchant == 1:
            steps.append((2, [f"T{tier}_SOUL"]))
            steps.append((3, [f"T{tier}_SOUL", f"T{tier}_RELIC"]))
        elif from_enchant == 2:
            steps.append((3, [f"T{tier}_RELIC"]))

        # For every city where raw_id can be bought:
        for city, buy_cost in item_cities_sell.get(raw_id, []):

            for to_enchant, mat_ids in steps:
                target_id = f"{base_stem}@{to_enchant}"

                # Calculate total enchanting materials cost in this city
                total_mat_cost = 0
                mat_labels: list[str] = []
                for m_id in mat_ids:
                    u_price = get_mat_price(m_id, city)
                    total_mat_cost += mat_qty * u_price
                    mat_name = MATERIAL_NAMES.get(m_id, m_id)
                    mat_labels.append(f"{mat_qty}x {mat_name} (@ {u_price:,}s)")

                mat_desc = " + ".join(mat_labels)
                primary_mat_id = mat_ids[0] if len(mat_ids) == 1 else " + ".join(mat_ids)
                primary_mat_name = mat_desc

                # Check sell targets:
                # 1. Same City Market Sell Order (local foundry enchanting)
                if (target_id, city) in city_item_sell:
                    target_sell = city_item_sell[(target_id, city)]
                    if target_sell > 0:
                        tot_cost, profit, margin = calculate_enchanting_profit(
                            base_item_price=buy_cost,
                            enchant_mat_cost=total_mat_cost,
                            sell_price=target_sell,
                            tax_rate=tax_rate,
                            setup_fee_rate=setup_fee_rate,
                            is_buy_order_exit=False,
                        )
                        opp_key = (raw_id, target_id, city, city)
                        if opp_key not in seen_opp_keys:
                            seen_opp_keys.add(opp_key)
                            age_min = 0.0
                            date_str = item_dates.get((target_id, city)) or item_dates.get((raw_id, city))
                            if date_str and now:
                                parsed_dt = parse_aodp_datetime(date_str)
                                if parsed_dt:
                                    age_min = max(0.0, (now - parsed_dt).total_seconds() / 60.0)

                            t_vol, t_missing, t_liq = get_target_volume_info(target_id, city)
                            opportunities.append(EnchantingOpportunity(
                                base_item_id=raw_id,
                                target_item_id=target_id,
                                item_name=human_name,
                                tier=tier,
                                from_enchant=from_enchant,
                                to_enchant=to_enchant,
                                city=city,
                                sell_city=city,
                                base_item_price=buy_cost,
                                enchant_mat_id=primary_mat_id,
                                enchant_mat_name=primary_mat_name,
                                enchant_mat_qty=mat_qty * len(mat_ids),
                                enchant_mat_unit_price=get_mat_price(mat_ids[0], city),
                                enchant_mat_total_cost=total_mat_cost,
                                total_cost=tot_cost,
                                sell_price=target_sell,
                                net_revenue=tot_cost + profit,
                                profit_per_item=profit,
                                margin_pct=round(margin, 1),
                                is_profitable=(profit > 0),
                                mass_batch_size=mass_batch_size,
                                mass_profit=profit * mass_batch_size,
                                item_type=slot_type,
                                data_age_minutes=age_min,
                                target_daily_volume=t_vol,
                                target_history_missing=t_missing,
                                liquidity_status=t_liq,
                            ))

                # 2. Black Market Buy Order (instant sell)
                if target_id in bm_buy:
                    bm_sell = bm_buy[target_id]
                    if bm_sell > 0:
                        tot_cost, profit, margin = calculate_enchanting_profit(
                            base_item_price=buy_cost,
                            enchant_mat_cost=total_mat_cost,
                            sell_price=bm_sell,
                            tax_rate=tax_rate,
                            setup_fee_rate=0.0,
                            is_buy_order_exit=True,
                        )
                        opp_key = (raw_id, target_id, city, "Black Market")
                        if opp_key not in seen_opp_keys:
                            seen_opp_keys.add(opp_key)
                            age_min = 0.0
                            date_str = bm_dates.get(target_id)
                            if date_str and now:
                                parsed_dt = parse_aodp_datetime(date_str)
                                if parsed_dt:
                                    age_min = max(0.0, (now - parsed_dt).total_seconds() / 60.0)

                            bm_vol, bm_missing, bm_liq = get_target_volume_info(target_id, "Black Market")
                            opportunities.append(EnchantingOpportunity(
                                base_item_id=raw_id,
                                target_item_id=target_id,
                                item_name=human_name,
                                tier=tier,
                                from_enchant=from_enchant,
                                to_enchant=to_enchant,
                                city=city,
                                sell_city="Black Market",
                                base_item_price=buy_cost,
                                enchant_mat_id=primary_mat_id,
                                enchant_mat_name=primary_mat_name,
                                enchant_mat_qty=mat_qty * len(mat_ids),
                                enchant_mat_unit_price=get_mat_price(mat_ids[0], city),
                                enchant_mat_total_cost=total_mat_cost,
                                total_cost=tot_cost,
                                sell_price=bm_sell,
                                net_revenue=tot_cost + profit,
                                profit_per_item=profit,
                                margin_pct=round(margin, 1),
                                is_profitable=(profit > 0),
                                mass_batch_size=mass_batch_size,
                                mass_profit=profit * mass_batch_size,
                                item_type=slot_type,
                                data_age_minutes=age_min,
                                target_daily_volume=bm_vol,
                                target_history_missing=bm_missing,
                                liquidity_status=bm_liq,
                            ))

    opportunities.sort(key=lambda o: (o.is_profitable, o.profit_per_item, o.margin_pct), reverse=True)
    return opportunities, material_status


