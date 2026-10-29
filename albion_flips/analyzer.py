from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Collection, Mapping, Sequence
import logging
import re

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
from albion_flips.names import human_name, load_item_names
from albion_flips.recipes import (
    DEFAULT_BASE_BONUS,
    FOCUS_BONUS,
    RecipeBook,
    city_return_rates,
    craft_label,
    load_recipe_book,
    return_rate,
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


    # An item can fill buy orders of every quality up to its own, which yields one
    # candidate per order quality. Keep only the best exit per route.
    best_per_route: dict[tuple[str, str, str, int], FlipOpportunity] = {}
    for opp in opportunities:
        key = (opp.item_id, opp.buy_city, opp.sell_city, opp.quality)
        current = best_per_route.get(key)
        if current is None or opp.profit_per_item > current.profit_per_item:
            best_per_route[key] = opp
    opportunities = list(best_per_route.values())

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


def _fresh(price: int, date_str: str, now: datetime, max_age_minutes: float) -> bool:
    """True when a quote has a price and is no older than max_age_minutes."""
    if price <= 0:
        return False
    age = calculate_age_minutes(date_str, now)
    return age is not None and age <= max_age_minutes


def build_market_index(
    prices: Sequence[PriceRecord],
    config: AppConfig,
    now: datetime,
    max_age_minutes: float,
    quality: int = 1,
) -> tuple[dict[str, tuple[int, str]], dict[str, tuple[int, str, float, bool]]]:
    """Indexes fresh quotes for one quality.

    Returns:
        buy_index:  item_id -> (cheapest instant-buy price, city)   [lowest sell order]
        sell_index: item_id -> (gross price, city, net silver after tax/fees, is_instant)
                    for the most lucrative exit: a sell order, a city buy order, or a
                    Black Market buy order.
    """
    tax_rate = config.active_tax_rate
    setup_rate = config.setup_fee_rate
    buy_index: dict[str, tuple[int, str]] = {}
    sell_index: dict[str, tuple[int, str, float, bool]] = {}

    def offer_exit(item_id: str, gross: int, city: str, instant: bool) -> None:
        net = gross * (1.0 - tax_rate - (0.0 if instant else setup_rate))
        current = sell_index.get(item_id)
        if current is None or net > current[2]:
            sell_index[item_id] = (gross, city, net, instant)

    for p in prices:
        if p.quality != quality:
            continue
        if p.city != "Black Market" and _fresh(p.sell_price_min, p.sell_price_min_date, now, max_age_minutes):
            cur = buy_index.get(p.item_id)
            if cur is None or p.sell_price_min < cur[0]:
                buy_index[p.item_id] = (p.sell_price_min, p.city)
            offer_exit(p.item_id, p.sell_price_min, p.city, instant=False)
        if _fresh(p.buy_price_max, p.buy_price_max_date, now, max_age_minutes):
            offer_exit(p.item_id, p.buy_price_max, p.city, instant=True)

    return buy_index, sell_index


# Crafting ingredients that are not sold everywhere, so they are bought in the cheapest
# city instead of the craft city: artifacts, royal sigils, Avalonian shards / Mists
# tokens, faction hearts and faction crests.
_BOUGHT_ANYWHERE = re.compile(r"(ARTEFACT_|QUESTITEM_TOKEN_|FACTION_\w+_TOKEN|CAPEITEM_\w+_BP)")


def is_bought_anywhere(item_id: str) -> bool:
    return bool(_BOUGHT_ANYWHERE.search(item_id))


# Base chance of each crafted quality per roll (Normal, Good, Outstanding, Excellent,
# Masterpiece). Specialization and focus add extra rolls; the best roll is kept.
QUALITY_ODDS: tuple[float, ...] = (0.689, 0.25, 0.05, 0.01, 0.001)
MAX_QUALITY_ROLLS = 4  # most rolls the dashboard lets the player pick


def quality_chances(rolls: int = 1) -> list[float]:
    """Chance of ending with each quality when the best of `rolls` rolls is kept."""
    out, cumulative, previous = [], 0.0, 0.0
    for odd in QUALITY_ODDS:
        cumulative = min(1.0, cumulative + odd)
        best = cumulative ** max(1, rolls)
        out.append(best - previous)
        previous = best
    return out


def quality_exit_index(
    prices: Sequence[PriceRecord], config: AppConfig, now: datetime, max_age_minutes: float
) -> dict[str, list[tuple[float, str] | None]]:
    """item_id -> best (net silver, description) for an item of quality 1..5.

    A buy order (city or Black Market) accepts its own quality or better; a sell order
    is listed against items of the same quality. A better item can always take a
    lower-quality exit, so the values never go down with quality.
    """
    tax, setup = config.active_tax_rate, config.setup_fee_rate
    raw: dict[str, list[tuple[float, str] | None]] = {}

    def offer(item_id: str, quality: int, net: float, desc: str) -> None:
        slots = raw.setdefault(item_id, [None] * 5)
        q = max(1, min(5, quality)) - 1
        if slots[q] is None or net > slots[q][0]:
            slots[q] = (net, desc)

    for p in prices:
        if _fresh(p.buy_price_max, p.buy_price_max_date, now, max_age_minutes):
            offer(p.item_id, p.quality, p.buy_price_max * (1.0 - tax), f"{p.city} buy order {p.buy_price_max:,}")
        if p.city != "Black Market" and _fresh(p.sell_price_min, p.sell_price_min_date, now, max_age_minutes):
            offer(p.item_id, p.quality, p.sell_price_min * (1.0 - tax - setup), f"{p.city} sell order {p.sell_price_min:,}")

    for item_id, slots in raw.items():
        best: tuple[float, str] | None = None
        for q in range(5):
            if slots[q] is not None and (best is None or slots[q][0] > best[0]):
                best = slots[q]
            slots[q] = best
    return raw


def analyze_crafting(
    prices: Sequence[PriceRecord],
    config: AppConfig,
    focus: bool = False,
    item_names: dict[str, str] | None = None,
    now: datetime | None = None,
    fulfilled_orders: dict[tuple[str, str, int], datetime] | None = None,
    price_overrides: dict[tuple[str, str, int], int] | None = None,
    recipe_book: RecipeBook | None = None,
    history: Sequence[HistoryRecord] | Mapping[tuple[str, str, int], HistoryRecord] | None = None,
    material_quotes: Collection[tuple[str, str]] | None = None,
) -> list[CraftingOpportunity]:
    """Calculates crafting and refining profit for every recipe in the game data.

    Rules:
    - Recipes, item values and city bonuses come from data/recipes.json (ao-bin-dumps).
    - Only quality-1 (Normal) quotes are used: crafts come out mostly Normal, and
      using Masterpiece prices would inflate profits.
    - Quotes older than config.craft_max_price_age_minutes are ignored.
    - Ingredients are bought at the cheapest fresh sell order in any royal city.
    - The output is sold through the best exit: sell order (tax + setup fee) or an
      instant sale into a city / Black Market buy order (tax only).
    - Materials are bought in the craft city. Artifacts, royal sigils and similar
      special items (see is_bought_anywhere) are bought in the cheapest city.
    - Every crafting city is tried with its own material prices and return rate (which
      depends on the recipe category and the city); the most profitable one is kept.
    - Returnable ingredients cost (1 - return rate), non-returnable ones cost full price.
    - material_quotes, when given, limits material prices to these (item_id, city) pairs
      (used for "sniffer scans only").
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    book = recipe_book or load_recipe_book()
    names = item_names if item_names is not None else load_item_names()

    clean_prices = deduplicate_prices(
        prices=prices,
        now=now,
        fulfilled_orders=fulfilled_orders,
        price_overrides=price_overrides,
    )
    buy_index, sell_index = build_market_index(
        clean_prices, config, now, max_age_minutes=config.craft_max_price_age_minutes
    )
    quality_exits = quality_exit_index(clean_prices, config, now, config.craft_max_price_age_minutes)
    chances = quality_chances(1)
    tax_rate = config.active_tax_rate
    history_map: Mapping[tuple[str, str, int], HistoryRecord] = {}
    if history:
        history_map = history if isinstance(history, Mapping) else {(h.item_id, h.location, h.quality): h for h in history}
    no_city_bonus = DEFAULT_BASE_BONUS + (FOCUS_BONUS if focus else 0.0)
    allowed = set(material_quotes) if material_quotes is not None else None

    # item_id -> {city: cheapest fresh sell order}. Ingredients of any quality work in a
    # recipe (e.g. a Good base cape for a faction cape), so every quality counts.
    city_prices: dict[str, dict[str, int]] = {}
    for p in clean_prices:
        if p.city == "Black Market":
            continue
        if allowed is not None and (p.item_id, p.city) not in allowed:
            continue
        if _fresh(p.sell_price_min, p.sell_price_min_date, now, config.craft_max_price_age_minutes):
            by_city = city_prices.setdefault(p.item_id, {})
            if p.city not in by_city or p.sell_price_min < by_city[p.city]:
                by_city[p.city] = p.sell_price_min

    def cheapest(item_id: str) -> tuple[int, str] | None:
        by_city = city_prices.get(item_id)
        if not by_city:
            return None
        city = min(by_city, key=by_city.__getitem__)
        return by_city[city], city

    opportunities: list[CraftingOpportunity] = []
    for recipe in book.recipes:
        exit_quote = sell_index.get(recipe.output)
        q_exits = quality_exits.get(recipe.output)
        if exit_quote is None and not q_exits:
            continue
        if exit_quote is None:
            # Nobody buys a Normal one: only better rolls sell (see quality_nets).
            exit_quote = (0, "", 0.0, True)
        q_nets = [e[0] if e else 0.0 for e in q_exits] if q_exits else [0.0] * 5
        expected_revenue = sum(ch * n for ch, n in zip(chances, q_nets))
        if focus and recipe.focus <= 0:
            continue  # focus changes nothing for this recipe

        # Special items (artifacts, sigils...) come from the cheapest city.
        special: dict[str, tuple[int, str]] = {}
        for res_id, _, _ in recipe.resources:
            if is_bought_anywhere(res_id):
                quote = cheapest(res_id)
                if quote is not None:
                    special[res_id] = quote
        if any(is_bought_anywhere(r) and r not in special for r, _, _ in recipe.resources):
            continue

        candidates: list[tuple[float, str, float, list[tuple[str, int, bool, int, str]]]] = []
        for city, base in book.base_bonus.items():
            picks: list[tuple[str, int, bool, int, str]] = []
            for res_id, count, returnable in recipe.resources:
                if res_id in special:
                    price, src = special[res_id]
                else:
                    price = city_prices.get(res_id, {}).get(city)
                    src = city
                    if price is None:
                        break
                picks.append((res_id, count, returnable, price, src))
            else:
                bonus = base + book.city_modifiers.get(city, {}).get(recipe.category, 0.0)
                if focus:
                    bonus += FOCUS_BONUS
                rrr = return_rate(bonus)
                cost = sum(c * pr * (1.0 - rrr if ret else 1.0) for _, c, ret, pr, _ in picks)
                candidates.append((cost, city, bonus, picks))
        if not candidates:
            continue  # no city sells all the local materials
        candidates.sort(key=lambda cand: cand[0])
        _, craft_city, bonus, picks = candidates[0]
        sell_price, sell_city, _, instant = exit_quote
        setup_rate = 0.0 if instant else config.setup_fee_rate

        def city_numbers(cand_bonus: float, cand_picks: list[tuple[str, int, bool, int, str]]) -> tuple[float, float, float, float, float]:
            """(effective cost, profit, margin %, return rate, returned silver) per item."""
            mat = sum(c * pr for _, c, _, pr, _ in cand_picks)
            ret_cost = sum(c * pr for _, c, ret, pr, _ in cand_picks if ret)
            cand_rrr = return_rate(cand_bonus) if ret_cost > 0 else 0.0
            eff = (mat - ret_cost * cand_rrr + recipe.silver) / recipe.amount
            prof = calculate_crafting_profit(sell_price=sell_price, effective_material_cost=eff,
                                             tax_rate=tax_rate, setup_fee_rate=setup_rate)
            return eff, prof, calculate_margin(prof, int(round(eff))), cand_rrr, ret_cost * cand_rrr / recipe.amount

        city_options: list[dict] = []
        for _, cand_city, cand_bonus, cand_picks in candidates[1:]:
            eff, prof, marg, cand_rrr, returned = city_numbers(cand_bonus, cand_picks)
            if marg <= config.max_margin_pct:
                city_options.append({
                    "city": cand_city,
                    "effective_cost": float(eff),
                    "profit_per_item": float(prof),
                    "margin_pct": float(marg),
                    "resource_return_rate": round(cand_rrr, 4),
                    "returned_value": float(returned),
                    "city_bonus": cand_bonus > no_city_bonus + 1e-9,
                    "prices": [pr for _, _, _, pr, _ in cand_picks],
                    "cities": [src for _, _, _, _, src in cand_picks],
                })

        material_cost = 0.0
        returnable_cost = 0.0
        desc_parts: list[str] = []
        ingredients: list[dict] = []
        for res_id, count, returnable, price, src in picks:
            cost = count * price
            material_cost += cost
            if returnable:
                returnable_cost += cost
            res_name = human_name(res_id, names)
            desc_parts.append(f"{count}x {res_name}")
            ingredients.append({
                "id": res_id,
                "name": res_name,
                "count": count,
                "unit_price": price,
                "city": src,
                "returnable": bool(returnable),
                "bought_elsewhere": res_id in special,
            })

        rrr = return_rate(bonus) if returnable_cost > 0 else 0.0
        effective_cost = (material_cost - returnable_cost * rrr + recipe.silver) / recipe.amount
        city_rates = city_return_rates(book, recipe.category, focus) if returnable_cost > 0 else {}

        profit = calculate_crafting_profit(
            sell_price=sell_price,
            effective_material_cost=effective_cost,
            tax_rate=tax_rate,
            setup_fee_rate=setup_rate,
        )
        margin = calculate_margin(profit, int(round(effective_cost)))
        if margin > config.max_margin_pct:
            continue  # almost always a stale or troll listing

        out = recipe.output
        tier = int(out[1]) if out[:1] == "T" and out[1:2].isdigit() else None
        enchant = int(out.split("@")[1]) if "@" in out else 0
        amount_note = f" (makes {recipe.amount})" if recipe.amount > 1 else ""

        opportunities.append(
            CraftingOpportunity(
                item_id=out,
                item_name=human_name(out, names),
                craft_type=craft_label(recipe),
                craft_city=craft_city,
                sell_city=sell_city,
                material_cost=float(material_cost / recipe.amount),
                effective_cost=float(effective_cost),
                sell_price=sell_price,
                profit_per_item=float(profit),
                margin_pct=float(margin),
                resource_return_rate=round(rrr, 4),
                ingredients_desc=" + ".join(desc_parts) + amount_note,
                city_bonus=bonus > no_city_bonus + 1e-9,
                focus=focus,
                tier=tier,
                enchant=enchant,
                item_value=recipe.item_value / recipe.amount,
                daily_volume=compute_daily_volume(history_map, out, sell_city, 1, config.volume_days) if history_map else None,
                ingredients=ingredients,
                returned_value=float(returnable_cost * rrr / recipe.amount),
                craft_silver=float(recipe.silver / recipe.amount),
                tax_paid=float(sell_price * (tax_rate + setup_rate)),
                sell_instant=instant,
                city_return_rates=city_rates,
                city_options=city_options,
                quality_nets=[round(n, 1) for n in q_nets],
                quality_exits=[e[1] if e else "" for e in q_exits] if q_exits else [""] * 5,
                expected_profit=float(expected_revenue - effective_cost),
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

    Rules:
    - Material counts per step come from the game data (see calculate_enchanting_materials).
    - The base item and the enchanted result are compared at the same quality, because
      enchanting keeps quality. A Black Market buy order accepts any item of equal or
      higher quality.
    - Quotes older than config.craft_max_price_age_minutes are ignored.
    - Opportunities are only computed with real (live or user-entered) material prices;
      DEFAULT_RUNE_PRICES are reference values for the ticker only.

    Returns:
        (opportunities, material_status_dict)
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    max_age = config.craft_max_price_age_minutes
    clean_prices = deduplicate_prices(
        prices=prices,
        now=now,
        fulfilled_orders=fulfilled_orders,
        price_overrides=price_overrides,
    )
    tax_rate = config.active_tax_rate
    setup_fee_rate = config.setup_fee_rate

    # Build fast O(1) history index
    history_map: Mapping[tuple[str, str, int], HistoryRecord] = {}
    if history:
        if isinstance(history, Mapping):
            history_map = history
        else:
            history_map = {(h.item_id, h.location, h.quality): h for h in history}

    def get_target_volume_info(t_id: str, dest_city: str, quality: int) -> tuple[float | None, bool, str]:
        if not history_map:
            return None, True, "untracked"

        vol = compute_daily_volume(history_map, t_id, dest_city, quality=quality, volume_days=config.volume_days)
        if (vol is None or vol == 0) and quality != 1:
            q1_vol = compute_daily_volume(history_map, t_id, dest_city, quality=1, volume_days=config.volume_days)
            if q1_vol is not None:
                vol = q1_vol

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

    # 1. Harvest live Rune, Soul, Relic prices (lowest fresh sell order) per city
    city_mats: dict[tuple[str, str], int] = {}
    cheapest_mats: dict[str, tuple[int, str]] = {}
    for p in clean_prices:
        mat_id = p.item_id.upper()
        if mat_id not in DEFAULT_RUNE_PRICES or p.city == "Black Market":
            continue
        if not _fresh(p.sell_price_min, p.sell_price_min_date, now, max_age):
            continue
        price = p.sell_price_min
        if price < city_mats.get((mat_id, p.city), price + 1):
            city_mats[(mat_id, p.city)] = price
        if mat_id not in cheapest_mats or price < cheapest_mats[mat_id][0]:
            cheapest_mats[mat_id] = (price, p.city)

    # Build material status dict for the UI Artifact Foundry Ticker
    material_status: dict[str, Any] = {}
    for mat_id, def_price in DEFAULT_RUNE_PRICES.items():
        user_override = (custom_rune_prices or {}).get(mat_id)
        if user_override and user_override > 0:
            final_p, source = user_override, "override"
        elif mat_id in cheapest_mats:
            final_p, source = cheapest_mats[mat_id][0], "live"
        else:
            final_p, source = def_price, "default"

        material_status[mat_id] = {
            "id": mat_id,
            "name": MATERIAL_NAMES.get(mat_id, mat_id),
            "tier": int(mat_id[1]),
            "type": mat_id.split("_")[1].lower(),  # rune, soul, relic
            "price": final_p,
            "source": source,
            "city": cheapest_mats.get(mat_id, (0, "All"))[1],
        }

    def get_mat_price(mat_id: str, city: str) -> int | None:
        """Real material price: user override, else same city, else cheapest city."""
        if custom_rune_prices and custom_rune_prices.get(mat_id, 0) > 0:
            return custom_rune_prices[mat_id]
        if (mat_id, city) in city_mats:
            return city_mats[(mat_id, city)]
        if mat_id in cheapest_mats:
            return cheapest_mats[mat_id][0]
        return None

    # 2. Index fresh gear prices by (item, city, quality)
    city_item_sell: dict[tuple[str, str, int], tuple[int, str]] = {}
    bm_buy: dict[tuple[str, int], tuple[int, str]] = {}
    all_keys: set[tuple[str, int]] = set()

    for p in clean_prices:
        if not is_enchantable_item(p.item_id):
            continue
        if p.city != "Black Market" and _fresh(p.sell_price_min, p.sell_price_min_date, now, max_age):
            city_item_sell[(p.item_id, p.city, p.quality)] = (p.sell_price_min, p.sell_price_min_date)
            all_keys.add((p.item_id, p.quality))
        if p.city == "Black Market" and _fresh(p.buy_price_max, p.buy_price_max_date, now, max_age):
            key = (p.item_id, p.quality)
            if p.buy_price_max > bm_buy.get(key, (0, ""))[0]:
                bm_buy[key] = (p.buy_price_max, p.buy_price_max_date)

    def best_bm_order(target_id: str, quality: int) -> tuple[int, str] | None:
        """Best Black Market buy order an item of `quality` can fill (order quality <= quality)."""
        best: tuple[int, str] | None = None
        for q in range(1, quality + 1):
            order = bm_buy.get((target_id, q))
            if order and (best is None or order[0] > best[0]):
                best = order
        return best

    def age_minutes(date_str: str | None) -> float:
        parsed = parse_aodp_datetime(date_str or "")
        return max(0.0, (now - parsed).total_seconds() / 60.0) if parsed else 0.0

    sell_by_item_quality: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for (it_id, c_name, q), (cost, _) in city_item_sell.items():
        sell_by_item_quality.setdefault((it_id, q), []).append((c_name, cost))

    # 3. Analyze all enchantable gear
    opportunities: list[EnchantingOpportunity] = []
    upgrades = load_recipe_book().upgrades

    for raw_id, quality in all_keys:
        base_stem = raw_id.split("@")[0]
        tier = int(base_stem[1]) if len(base_stem) > 1 and base_stem[1].isdigit() else 4
        from_enchant = int(raw_id.split("@")[1]) if "@" in raw_id else 0
        if from_enchant >= 3:
            continue

        slot_type, mat_qty = calculate_enchanting_materials(base_stem)
        game_upgrade = upgrades.get(base_stem, {}).get(1)
        if game_upgrade:
            mat_qty = game_upgrade[1]
        human = human_name(base_stem, item_names)
        step_mats = {1: f"T{tier}_RUNE", 2: f"T{tier}_SOUL", 3: f"T{tier}_RELIC"}

        for city, buy_cost in sell_by_item_quality.get((raw_id, quality), []):
            for to_enchant in range(from_enchant + 1, 4):
                target_id = f"{base_stem}@{to_enchant}"
                mat_ids = [step_mats[lvl] for lvl in range(from_enchant + 1, to_enchant + 1)]
                unit_prices = [get_mat_price(m, city) for m in mat_ids]
                if any(u is None for u in unit_prices):
                    continue  # no real price for a rune/soul/relic yet

                total_mat_cost = sum(mat_qty * int(u) for u in unit_prices)
                mat_desc = " + ".join(
                    f"{mat_qty}x {MATERIAL_NAMES.get(m, m)} (@ {int(u):,}s)" for m, u in zip(mat_ids, unit_prices)
                )
                exits: list[tuple[str, int, str | None, bool]] = []
                local = city_item_sell.get((target_id, city, quality))
                if local:
                    exits.append((city, local[0], local[1], False))
                bm = best_bm_order(target_id, quality)
                if bm:
                    exits.append(("Black Market", bm[0], bm[1], True))

                for sell_city, sell_price, date_str, is_bm in exits:
                    tot_cost, profit, margin = calculate_enchanting_profit(
                        base_item_price=buy_cost,
                        enchant_mat_cost=total_mat_cost,
                        sell_price=sell_price,
                        tax_rate=tax_rate,
                        setup_fee_rate=0.0 if is_bm else setup_fee_rate,
                        is_buy_order_exit=is_bm,
                    )
                    t_vol, t_missing, t_liq = get_target_volume_info(target_id, sell_city, quality)
                    opportunities.append(EnchantingOpportunity(
                        base_item_id=raw_id,
                        target_item_id=target_id,
                        item_name=human,
                        tier=tier,
                        from_enchant=from_enchant,
                        to_enchant=to_enchant,
                        city=city,
                        sell_city=sell_city,
                        base_item_price=buy_cost,
                        enchant_mat_id=mat_ids[0] if len(mat_ids) == 1 else " + ".join(mat_ids),
                        enchant_mat_name=mat_desc,
                        enchant_mat_qty=mat_qty * len(mat_ids),
                        enchant_mat_unit_price=int(unit_prices[0]),
                        enchant_mat_total_cost=total_mat_cost,
                        total_cost=tot_cost,
                        sell_price=sell_price,
                        net_revenue=tot_cost + profit,
                        profit_per_item=profit,
                        margin_pct=round(margin, 1),
                        is_profitable=(profit > 0),
                        mass_batch_size=mass_batch_size,
                        mass_profit=profit * mass_batch_size,
                        item_type=slot_type,
                        data_age_minutes=age_minutes(date_str),
                        target_daily_volume=t_vol,
                        target_history_missing=t_missing,
                        liquidity_status=t_liq,
                        quality=quality,
                    ))

    opportunities.sort(key=lambda o: (o.is_profitable, o.profit_per_item, o.margin_pct), reverse=True)
    return opportunities, material_status
