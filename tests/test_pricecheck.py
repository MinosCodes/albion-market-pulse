"""Realistic prices: live quotes are checked against what items really sold for."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from albion_flips.analyzer import analyze_crafting, analyze_flips, calculate_deal_score
from albion_flips.config import AppConfig
from albion_flips.models import ExitType, HistoryPoint, HistoryRecord, PriceRecord
from albion_flips.pricecheck import (
    BAIT, CAPPED, CAPPED_CROSS, INVERTED, NO_HISTORY, PriceCheck, Reference, references_from_history,
)
from albion_flips.recipes import Recipe, RecipeBook

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")


def hist(item: str, city: str, price: int, count: int = 100, quality: int = 1, days: int = 7) -> HistoryRecord:
    pts = [HistoryPoint(item_count=count, avg_price=price,
                        timestamp=(NOW - timedelta(days=d)).strftime("%Y-%m-%dT00:00:00")) for d in range(1, days + 1)]
    return HistoryRecord(location=city, item_id=item, quality=quality, data=pts)


def quote(item: str, city: str, sell_min: int = 0, buy_max: int = 0, quality: int = 1) -> PriceRecord:
    return PriceRecord(item_id=item, city=city, quality=quality,
                       sell_price_min=sell_min, sell_price_min_date=FRESH if sell_min else "0001-01-01T00:00:00",
                       sell_price_max=sell_min, sell_price_max_date=FRESH if sell_min else "0001-01-01T00:00:00",
                       buy_price_min=buy_max, buy_price_max=buy_max,
                       buy_price_min_date=FRESH if buy_max else "0001-01-01T00:00:00",
                       buy_price_max_date=FRESH if buy_max else "0001-01-01T00:00:00")


# ---------------------------------------------------------------- references
def test_reference_is_volume_weighted_and_skips_today() -> None:
    rec = HistoryRecord("Martlock", "T4_BAG", 1, [
        HistoryPoint(10, 1000, (NOW - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")),
        HistoryPoint(30, 2000, (NOW - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")),
        HistoryPoint(500, 9999, NOW.strftime("%Y-%m-%dT00:00:00")),          # today: partial day
        HistoryPoint(500, 9999, (NOW - timedelta(days=9)).strftime("%Y-%m-%dT00:00:00")),  # too old
    ])
    ref = references_from_history([rec], NOW)[("T4_BAG", "Martlock", 1)]
    assert ref.avg7 == (10 * 1000 + 30 * 2000) / 40 and ref.vol7 == 40 / 7


# ---------------------------------------------------------------- single checks
def test_sell_listing_is_capped_at_real_sales() -> None:
    pc = PriceCheck({("T6_WOOD_LEVEL3@3", "Fort Sterling", 1): Reference(avg7=29880)})
    c = pc.sell_order("T6_WOOD_LEVEL3@3", "Fort Sterling", 1, 49963)  # live case from 2026-11-01
    assert c.reason == CAPPED and c.price == round(29880 * 1.10) and c.live == 49963 and c.verified
    ok = pc.sell_order("T6_WOOD_LEVEL3@3", "Fort Sterling", 1, 30000)
    assert ok.price == 30000 and ok.reason == "" and not ok.adjusted


def test_sell_without_own_history_uses_other_cities() -> None:
    pc = PriceCheck({("X", "Lymhurst", 1): Reference(avg7=100), ("X", "Martlock", 1): Reference(avg7=120),
                     ("X", "Thetford", 1): Reference(avg7=5000), ("X", "Black Market", 1): Reference(avg7=9e9)})
    c = pc.sell_order("X", "Bridgewatch", 1, 10_000)
    assert c.reason == CAPPED_CROSS and c.price == round(120 * 1.25)  # median of the royal cities, not BM


def test_no_history_anywhere_is_unverified_but_kept() -> None:
    c = PriceCheck({}).sell_order("T8_2H_CROSSBOW_CANNON_AVALON@4", "Bridgewatch", 4, 75_000_000)
    assert c.price == 75_000_000 and c.reason == NO_HISTORY and not c.verified


def test_bait_buy_price_and_inverted_book() -> None:
    pc = PriceCheck({("X", "Thetford", 1): Reference(avg7=1000)})
    bait = pc.buy("X", "Thetford", 1, 500)
    assert bait.reason == BAIT and bait.price == 950
    assert pc.buy("X", "Thetford", 1, 900).reason == ""                  # 90 % of avg is fine
    inv = pc.buy("X", "Thetford", 1, 900, live_buy_max=1200, seconds_between=60)
    assert inv.reason == INVERTED and inv.price == 950
    # no history: the listing can be bought, so it stays and is not "unverified"
    plain = PriceCheck({}).buy("X", "Thetford", 1, 500)
    assert plain.price == 500 and plain.verified


def test_buy_check_ignores_other_cities() -> None:
    """Cheap here and dear elsewhere is the flip itself, not bait."""
    pc = PriceCheck({("X", "Martlock", 1): Reference(avg7=5000)})
    assert pc.buy("X", "Bridgewatch", 1, 1000).price == 1000


def test_instant_sale_into_buy_order_is_never_capped() -> None:
    pc = PriceCheck({("X", "Martlock", 1): Reference(avg7=1000)})
    c = pc.instant_sell("X", "Martlock", 1, 3000)
    assert c.price == 3000 and c.verified


def test_disabled_check_changes_nothing() -> None:
    pc = PriceCheck({("X", "Martlock", 1): Reference(avg7=1000)}, enabled=False)
    assert pc.sell_order("X", "Martlock", 1, 9000).price == 9000 and pc.buy("X", "Martlock", 1, 1).price == 1


# ---------------------------------------------------------------- whole analyses
def test_flip_profit_uses_real_sale_price() -> None:
    prices = [quote("T4_BAG", "Lymhurst", sell_min=1000), quote("T4_BAG", "Martlock", sell_min=3000)]
    history = [hist("T4_BAG", "Lymhurst", 1050), hist("T4_BAG", "Martlock", 1200)]
    flips = analyze_flips(prices, history, AppConfig(min_daily_volume=0, min_profit_silver=0), now=NOW)
    f = next(x for x in flips if x.buy_city == "Lymhurst")
    assert f.sell_price == 1320 and f.live_sell_price == 3000 and f.sell_check == CAPPED
    assert round(f.profit_per_item) == round(1320 * (1 - 0.04 - 0.025) - 1000)
    # without the check the old, too optimistic number comes back
    raw = analyze_flips(prices, history, AppConfig(min_daily_volume=0, min_profit_silver=0, robust_prices=False), now=NOW)
    assert next(x for x in raw if x.buy_city == "Lymhurst").sell_price == 3000


def test_flip_that_only_exists_on_paper_disappears() -> None:
    """Listing 50 % above real sales: after the cap the flip loses silver and is dropped."""
    prices = [quote("T6_WOOD", "Lymhurst", sell_min=31462), quote("T6_WOOD", "Fort Sterling", sell_min=49963)]
    history = [hist("T6_WOOD", "Lymhurst", 27758), hist("T6_WOOD", "Fort Sterling", 29880)]
    assert not analyze_flips(prices, history, AppConfig(min_daily_volume=0), now=NOW)


def test_unverified_flip_is_never_a_top_pick() -> None:
    prices = [quote("T8_RARE", "Lymhurst", sell_min=1_000_000), quote("T8_RARE", "Bridgewatch", sell_min=2_000_000)]
    (f,) = [x for x in analyze_flips(prices, [], AppConfig(min_daily_volume=0), now=NOW) if x.buy_city == "Lymhurst"]
    assert not f.price_verified and f.sell_check == NO_HISTORY
    score, tier, label = calculate_deal_score(f.profit_per_item, f.margin_pct, 5, 500, "low", ExitType.SELL_ORDER,
                                              price_verified=f.price_verified)
    assert tier == "C" and score <= 41 and "UNVERIFIED" in label


def test_crafting_caps_output_and_marks_bait_materials() -> None:
    book = RecipeBook(
        recipes=(Recipe("T4_PLANKS", "planks", 1, (("T4_WOOD", 2, True),), 0, 0, 10.0),),
        upgrades={}, weights={}, base_bonus={"Lymhurst": 0.18}, city_modifiers={},
    )
    prices = [quote("T4_WOOD", "Lymhurst", sell_min=50), quote("T4_PLANKS", "Lymhurst", sell_min=1000)]
    history = [hist("T4_WOOD", "Lymhurst", 100), hist("T4_PLANKS", "Lymhurst", 300)]
    (c,) = analyze_crafting(prices, AppConfig(), recipe_book=book, item_names={}, now=NOW, history=history)
    assert c.sell_price == 330 and c.live_sell_price == 1000 and c.sell_check == CAPPED and c.price_verified
    wood = c.ingredients[0]
    assert wood["unit_price"] == 95 and wood["live_price"] == 50 and wood["price_check"] == BAIT
