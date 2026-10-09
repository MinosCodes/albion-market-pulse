from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest

from albion_flips.analyzer import analyze_crafting, analyze_flips, calculate_deal_score
from albion_flips.config import AppConfig
from albion_flips.models import ExitType, HistoryRecord, PriceRecord
from albion_flips.profit import (
    calculate_crafting_profit,
    calculate_instant_profit,
    calculate_margin,
    calculate_sell_order_profit,
)


FIXTURES_DIR = Path("tests/fixtures")
TEST_NOW = datetime(2026, 10, 9, 8, 30, 0, tzinfo=timezone.utc)


def load_fixtures() -> tuple[list[PriceRecord], list[HistoryRecord]]:
    with open(FIXTURES_DIR / "prices_sample.json", "r", encoding="utf-8") as f:
        raw_prices = json.load(f)
    with open(FIXTURES_DIR / "history_sample.json", "r", encoding="utf-8") as f:
        raw_history = json.load(f)

    prices = [PriceRecord.from_dict(p) for p in raw_prices]
    history = [HistoryRecord.from_dict(h) for h in raw_history]
    return prices, history


def get_test_config() -> AppConfig:
    """Config matching Section 9 test requirements:

    now = 2026-10-09T08:30:00 UTC, max_price_age_minutes = 120, premium on,
    setup fee 0.025, min_profit_silver = 100, min_margin_pct = 5, min_daily_volume = 50.
    """
    return AppConfig(
        server="europe",
        premium=True,
        tax_rate_premium=0.04,
        tax_rate_standard=0.08,
        setup_fee_rate=0.025,
        min_profit_silver=100,
        min_margin_pct=5.0,
        max_margin_pct=300.0,
        min_daily_volume=50,
        volume_days=3,
        max_price_age_minutes=120,
        stack_size=100,
    )


# Case 1: profit_sellorder with buy 1000, sell 1300, premium -> 215.5 silver
def test_case_1_profit_sellorder_premium() -> None:
    profit = calculate_sell_order_profit(
        buy_price=1000,
        sell_price=1300,
        tax_rate=0.04,
        setup_fee_rate=0.025,
    )
    assert profit == pytest.approx(215.5, abs=1e-4)


# Case 2: profit_sellorder with buy 1000, sell 1300, no premium (8%) -> 163.5 silver
def test_case_2_profit_sellorder_no_premium() -> None:
    profit = calculate_sell_order_profit(
        buy_price=1000,
        sell_price=1300,
        tax_rate=0.08,
        setup_fee_rate=0.025,
    )
    assert profit == pytest.approx(163.5, abs=1e-4)


# Case 3: profit_instant with buy 1000, buyer pays 1250, premium -> 200 silver
def test_case_3_profit_instant_premium() -> None:
    profit = calculate_instant_profit(
        buy_price=1000,
        buyer_bid=1250,
        tax_rate=0.04,
    )
    assert profit == pytest.approx(200.0, abs=1e-4)


# Case 4: Margin for case 1 -> 21.55 %
def test_case_4_margin_calculation() -> None:
    profit = 215.5
    margin = calculate_margin(profit=profit, buy_price=1000)
    assert margin == pytest.approx(21.55, abs=1e-4)


# Cases 5 to 11 against fixtures
def test_cases_5_to_11_fixtures_pipeline() -> None:
    prices, history = load_fixtures()
    config = get_test_config()
    flips = analyze_flips(prices=prices, history=history, config=config, now=TEST_NOW)

    # Case 5: T4_PLANKS Bridgewatch to Martlock included, sell-order exit better (215.5 vs 200)
    planks_flips = [f for f in flips if f.item_id == "T4_PLANKS" and f.buy_city == "Bridgewatch" and f.sell_city == "Martlock"]
    assert len(planks_flips) == 1
    top_planks = planks_flips[0]
    assert top_planks.profit_per_item == pytest.approx(215.5, abs=1e-4)
    assert top_planks.margin_pct == pytest.approx(21.55, abs=1e-4)
    assert top_planks.exit_type == ExitType.SELL_ORDER
    assert top_planks.avg_daily_volume == pytest.approx(133.333, abs=0.1)
    assert top_planks.history_missing is False

    # Case 6: T4_PLANKS Lymhurst price dated 2026-10-06 excluded as stale
    lymhurst_flips = [f for f in flips if f.item_id == "T4_PLANKS" and (f.buy_city == "Lymhurst" or f.sell_city == "Lymhurst")]
    assert len(lymhurst_flips) == 0

    # Case 7: T4_METALBAR Fort Sterling 500 to Thetford 520 excluded, profit is -13.8
    metalbar_flips = [f for f in flips if f.item_id == "T4_METALBAR"]
    assert len(metalbar_flips) == 0

    # Case 8: T4_LEATHER with zero price and 0001-01-01 date excluded as no data
    leather_flips = [f for f in flips if f.item_id == "T4_LEATHER"]
    assert len(leather_flips) == 0

    # Case 9: T4_CLOTH with margin 2237.5% > 300% max_margin_pct excluded as suspicious
    cloth_flips = [f for f in flips if f.item_id == "T4_CLOTH"]
    assert len(cloth_flips) == 0

    # Case 10: T4_STONEBLOCK destination volume (10) below min_daily_volume (50) excluded
    stone_flips = [f for f in flips if f.item_id == "T4_STONEBLOCK"]
    assert len(stone_flips) == 0

    # Case 11: T4_HIDE missing history kept, volume n/a, warning flag set
    hide_flips = [f for f in flips if f.item_id == "T4_HIDE"]
    assert len(hide_flips) == 1
    hide_flip = hide_flips[0]
    assert hide_flip.buy_city == "Bridgewatch"
    assert hide_flip.sell_city == "Fort Sterling"
    assert hide_flip.avg_daily_volume is None
    assert hide_flip.history_missing is True
    assert hide_flip.profit_per_item == pytest.approx(120.75, abs=1e-4)


def test_calculate_deal_score() -> None:
    score, tier, label = calculate_deal_score(
        profit_per_item=55000,
        margin_pct=35.0,
        data_age_minutes=10.0,
        avg_daily_volume=80.0,
        risk="low",
        exit_type=ExitType.SELL_ORDER,
    )
    assert score >= 80
    assert tier == "S"
    assert "TOP PICK" in label

    # Stale, low volume, high risk
    score_low, tier_low, label_low = calculate_deal_score(
        profit_per_item=500,
        margin_pct=6.0,
        data_age_minutes=350.0,
        avg_daily_volume=1.0,
        risk="high",
        exit_type=ExitType.SELL_ORDER,
    )
    assert score_low < 45
    assert tier_low in ("B", "C")


def test_calculate_crafting_profit_and_analyzer() -> None:
    # 1000 sell price, effective mat cost 600, premium 4% + 2.5% fee
    # Net rev = 1000 * 0.935 = 935. Profit = 935 - 600 = 335
    profit = calculate_crafting_profit(
        sell_price=1000,
        effective_material_cost=600.0,
        tax_rate=0.04,
        setup_fee_rate=0.025,
    )
    assert profit == pytest.approx(335.0, abs=1e-4)

    # Test analyze_crafting with sample prices
    cfg = get_test_config()
    prices = [
        PriceRecord(
            item_id="T4_WOOD",
            city="Fort Sterling",
            quality=1,
            sell_price_min=100,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=120,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=80,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T3_PLANKS",
            city="Fort Sterling",
            quality=1,
            sell_price_min=80,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=100,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=60,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_PLANKS",
            city="Caerleon",
            quality=1,
            sell_price_min=600,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=650,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=500,
            buy_price_max_date="",
        ),
    ]
    crafts = analyze_crafting(prices, cfg, focus=False)
    assert len(crafts) >= 1
    t4_plank_craft = [c for c in crafts if c.item_id == "T4_PLANKS"][0]
    assert t4_plank_craft.craft_city == "Fort Sterling"
    assert t4_plank_craft.sell_city == "Caerleon"
    assert t4_plank_craft.profit_per_item > 0
    assert t4_plank_craft.resource_return_rate == pytest.approx(0.367, abs=1e-3)


def test_black_market_flips() -> None:
    """Verify Black Market flip detection, resource exclusion, and one-way buy restriction."""
    from albion_flips.analyzer import is_resource_item

    assert is_resource_item("T4_PLANKS") is True
    assert is_resource_item("T4_BAG") is False
    assert is_resource_item("T6_2H_CURSEDSTAFF@2") is False

    cfg = get_test_config()
    cfg.cities = ["Martlock", "Black Market"]
    cfg.min_daily_volume = 1

    prices = [
        # Gear: T4_BAG available in Martlock and wanted at Black Market
        PriceRecord(
            item_id="T4_BAG",
            city="Martlock",
            quality=1,
            sell_price_min=5000,
            sell_price_min_date="2026-10-09T08:15:00",
            sell_price_max=5500,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_BAG",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="0001-01-01T00:00:00",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=8000,  # Instant buy order by Black Market system
            buy_price_max_date="2026-10-09T08:20:00",
        ),
        # Resource: T4_PLANKS with a Black Market bid (should be excluded)
        PriceRecord(
            item_id="T4_PLANKS",
            city="Martlock",
            quality=1,
            sell_price_min=100,
            sell_price_min_date="2026-10-09T08:15:00",
            sell_price_max=120,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_PLANKS",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="0001-01-01T00:00:00",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=500,
            buy_price_max_date="2026-10-09T08:20:00",
        ),
        # Black Market sell price (attempting to buy FROM Black Market -> must NOT generate flip)
        PriceRecord(
            item_id="T4_CAPE",
            city="Black Market",
            quality=1,
            sell_price_min=1000,
            sell_price_min_date="2026-10-09T08:15:00",
            sell_price_max=1200,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_CAPE",
            city="Martlock",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="0001-01-01T00:00:00",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=5000,
            buy_price_max_date="2026-10-09T08:20:00",
        ),
    ]

    history = [
        HistoryRecord(
            location="Black Market",
            item_id="T4_BAG",
            quality=1,
            data=[],
        )
    ]

    flips = analyze_flips(prices, history, cfg, now=TEST_NOW)
    bm_flips = [f for f in flips if f.sell_city == "Black Market"]
    
    # 1. T4_BAG is found
    assert len(bm_flips) == 1
    assert bm_flips[0].item_id == "T4_BAG"
    assert bm_flips[0].buy_city == "Martlock"
    assert bm_flips[0].sell_city == "Black Market"
    assert bm_flips[0].exit_type == ExitType.INSTANT_SELL
    # Profit: 8000 * 0.96 - 5000 = 7680 - 5000 = 2680
    assert bm_flips[0].profit_per_item == pytest.approx(2680.0)

    # 2. T4_PLANKS is NOT in Black Market flips
    assert not any(f.item_id == "T4_PLANKS" and f.sell_city == "Black Market" for f in flips)

    # 3. No flips buying from Black Market
    assert not any(f.buy_city == "Black Market" for f in flips)


def test_cross_quality_matching() -> None:
    """Verifies that higher quality items can fill lower quality buy orders (e.g. Q2/Q3 to Q1 BM buy order)."""
    cfg = get_test_config()
    prices = [
        # Martlock has Quality 2 (Good) Bag for 5,000
        PriceRecord(
            item_id="T4_BAG",
            city="Martlock",
            quality=2,
            sell_price_min=5000,
            sell_price_min_date="2026-10-09T08:20:00",
            sell_price_max=5000,
            sell_price_max_date="2026-10-09T08:20:00",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        # Black Market has Quality 1 (Normal) Buy Order for 8,000
        PriceRecord(
            item_id="T4_BAG",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=8000,
            buy_price_max_date="2026-10-09T08:20:00",
        ),
    ]

    flips = analyze_flips(prices, [], cfg, now=TEST_NOW)
    bm_flips = [f for f in flips if f.sell_city == "Black Market"]

    assert len(bm_flips) == 1
    assert bm_flips[0].item_id == "T4_BAG"
    assert bm_flips[0].quality == 2  # The purchased item quality is 2
    assert bm_flips[0].sell_price == 8000
    assert bm_flips[0].exit_type == ExitType.INSTANT_SELL
    assert bm_flips[0].profit_per_item == pytest.approx(2680.0)


def test_tier_3_refining_supported() -> None:
    """Verifies that Tier 3 refining recipes are properly analyzed."""
    cfg = get_test_config()
    prices = [
        # T3 Wood in Fort Sterling
        PriceRecord.from_dict({
            "item_id": "T3_WOOD", "city": "Fort Sterling", "quality": 1,
            "sell_price_min": 50, "sell_price_min_date": "2026-10-09T08:20:00",
            "sell_price_max": 50, "sell_price_max_date": "2026-10-09T08:20:00",
            "buy_price_min": 0, "buy_price_min_date": "", "buy_price_max": 0, "buy_price_max_date": ""
        }),
        # T2 Planks in Fort Sterling
        PriceRecord.from_dict({
            "item_id": "T2_PLANKS", "city": "Fort Sterling", "quality": 1,
            "sell_price_min": 30, "sell_price_min_date": "2026-10-09T08:20:00",
            "sell_price_max": 30, "sell_price_max_date": "2026-10-09T08:20:00",
            "buy_price_min": 0, "buy_price_min_date": "", "buy_price_max": 0, "buy_price_max_date": ""
        }),
        # T3 Planks selling in Lymhurst
        PriceRecord.from_dict({
            "item_id": "T3_PLANKS", "city": "Lymhurst", "quality": 1,
            "sell_price_min": 250, "sell_price_min_date": "2026-10-09T08:20:00",
            "sell_price_max": 250, "sell_price_max_date": "2026-10-09T08:20:00",
            "buy_price_min": 0, "buy_price_min_date": "", "buy_price_max": 0, "buy_price_max_date": ""
        }),
    ]

    crafts = analyze_crafting(prices, cfg, focus=False)
    t3_wood_craft = [c for c in crafts if c.item_id == "T3_PLANKS"]
    assert len(t3_wood_craft) >= 1
    assert t3_wood_craft[0].tier == 3
    assert t3_wood_craft[0].craft_type == "Wood Refining"
    assert t3_wood_craft[0].craft_city == "Fort Sterling"


def test_price_deduplication_newer_overwrites_older() -> None:
    """Verifies that when multiple price scans for the same item/city exist, newer overwrites older."""
    cfg = get_test_config()
    cfg.cities = ["Martlock", "Black Market"]

    # Two records for the same item at Black Market: older high price, newer lower price
    prices = [
        PriceRecord(
            item_id="T4_BAG",
            city="Martlock",
            quality=1,
            sell_price_min=5000,
            sell_price_min_date="2026-10-09T08:20:00",
            sell_price_max=5000,
            sell_price_max_date="2026-10-09T08:20:00",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        # Older scan with high buy order (e.g. 50,000)
        PriceRecord(
            item_id="T4_BAG",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=50000,
            buy_price_max_date="2026-10-09T08:00:00",
        ),
        # Newer scan with fulfilled/lower buy order (e.g. 5,200)
        PriceRecord(
            item_id="T4_BAG",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=5200,
            buy_price_max_date="2026-10-09T08:25:00",
        ),
    ]

    flips = analyze_flips(prices, [], cfg, now=TEST_NOW)
    bm_flips = [f for f in flips if f.sell_city == "Black Market"]
    # The newer scan (5200) overwrote the old scan (50000), resulting in no flip above min_profit
    assert len(bm_flips) == 0


def test_fulfilled_order_removes_phantom_profit() -> None:
    """Verifies that marking an order as fulfilled removes it from flip opportunities."""
    cfg = get_test_config()
    cfg.cities = ["Martlock", "Black Market"]

    prices = [
        PriceRecord(
            item_id="T4_BAG",
            city="Martlock",
            quality=1,
            sell_price_min=5000,
            sell_price_min_date="2026-10-09T08:20:00",
            sell_price_max=5000,
            sell_price_max_date="2026-10-09T08:20:00",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_BAG",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=12000,
            buy_price_max_date="2026-10-09T08:15:00",
        ),
    ]

    # Without fulfillment marker, flip is found
    flips_active = analyze_flips(prices, [], cfg, now=TEST_NOW)
    assert len(flips_active) == 1

    # With fulfillment marker marked at 08:20 (after or equal to 08:15 price scan)
    fulfilled = {("T4_BAG", "Black Market", 1): TEST_NOW}
    flips_fulfilled = analyze_flips(prices, [], cfg, now=TEST_NOW, fulfilled_orders=fulfilled)
    assert len(flips_fulfilled) == 0


def test_price_override_recalculates_profit() -> None:
    """Verifies that overriding a price updates the profit computation directly."""
    cfg = get_test_config()
    cfg.cities = ["Martlock", "Black Market"]

    prices = [
        PriceRecord(
            item_id="T4_BAG",
            city="Martlock",
            quality=1,
            sell_price_min=5000,
            sell_price_min_date="2026-10-09T08:20:00",
            sell_price_max=5000,
            sell_price_max_date="2026-10-09T08:20:00",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_BAG",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=100000,  # Old stale price
            buy_price_max_date="2026-10-09T08:10:00",
        ),
    ]

    # User overrides price to 20,000
    overrides = {("T4_BAG", "Black Market", 1): 20000}
    flips = analyze_flips(prices, [], cfg, now=TEST_NOW, price_overrides=overrides)
    assert len(flips) == 1
    assert flips[0].sell_price == 20000


def test_martlock_cape_crafting() -> None:
    """Verifies that Martlock and faction cape crafting recipes are correctly analyzed."""
    cfg = get_test_config()
    prices = [
        # Base Cape T4
        PriceRecord(
            item_id="T4_CAPE",
            city="Martlock",
            quality=1,
            sell_price_min=10000,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=12000,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=8000,
            buy_price_max_date="",
        ),
        # Martlock Crest T4
        PriceRecord(
            item_id="T4_CAPEITEM_FW_MARTLOCK_BP",
            city="Martlock",
            quality=1,
            sell_price_min=15000,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=16000,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=12000,
            buy_price_max_date="",
        ),
        # Rockheart (Highland token)
        PriceRecord(
            item_id="T1_FACTION_HIGHLAND_TOKEN_1",
            city="Martlock",
            quality=1,
            sell_price_min=4000,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=5000,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=3000,
            buy_price_max_date="",
        ),
        # Finished Martlock Cape T4 selling in Caerleon
        PriceRecord(
            item_id="T4_CAPEITEM_FW_MARTLOCK",
            city="Caerleon",
            quality=1,
            sell_price_min=60000,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=65000,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=45000,
            buy_price_max_date="",
        ),
    ]

    crafts = analyze_crafting(prices, cfg, focus=False)
    martlock_crafts = [c for c in crafts if c.item_id == "T4_CAPEITEM_FW_MARTLOCK"]
    assert len(martlock_crafts) == 1

    op = martlock_crafts[0]
    assert op.craft_city == "Martlock"
    assert op.sell_city == "Caerleon"
    assert op.craft_type == "Faction Capes"
    # Material cost: 10000 (cape) + 15000 (crest) + 4000 (1x rockheart) = 29000
    assert op.material_cost == 29000.0
    assert op.effective_cost == 29000.0
    assert op.sell_price == 60000
    assert op.profit_per_item > 0
    assert op.resource_return_rate == 0.0
    assert "Rockheart" in op.ingredients_desc
    assert "Martlock Crest" in op.ingredients_desc


def test_crafting_synchronized_with_scanned_prices_and_overrides() -> None:
    """Verifies that newer scanned prices and manual overrides overwrite older data in crafting."""
    cfg = get_test_config()
    cfg.cities = ["Martlock", "Black Market", "Caerleon"]

    prices = [
        # Older scan: T4_CLOTH at 1,000
        PriceRecord(
            item_id="T4_CLOTH",
            city="Martlock",
            quality=1,
            sell_price_min=1000,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=1000,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        # Newer scan: T4_CLOTH rose to 2,500
        PriceRecord(
            item_id="T4_CLOTH",
            city="Martlock",
            quality=1,
            sell_price_min=2500,
            sell_price_min_date="2026-10-09T08:30:00",
            sell_price_max=2500,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        # Leather
        PriceRecord(
            item_id="T4_LEATHER",
            city="Martlock",
            quality=1,
            sell_price_min=1000,
            sell_price_min_date="2026-10-09T08:00:00",
            sell_price_max=1000,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        # Black Market buy order for T4_BAG
        PriceRecord(
            item_id="T4_BAG",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=60000,
            buy_price_max_date="2026-10-09T08:20:00",
        ),
    ]

    # Without overrides: newer cloth price (2500) overwrote older (1000)
    # T4 Bag material cost: 8 * 2500 + 8 * 1000 = 20000 + 8000 = 28000
    crafts = analyze_crafting(prices, cfg, focus=False)
    t4_bag = [c for c in crafts if c.item_id == "T4_BAG"][0]
    assert t4_bag.material_cost == 28000.0
    assert t4_bag.sell_city == "Black Market"
    assert t4_bag.sell_price == 60000

    # With user override: user manually sets T4_CLOTH in Martlock to 500
    overrides = {("T4_CLOTH", "Martlock", 1): 500}
    crafts_overridden = analyze_crafting(prices, cfg, focus=False, price_overrides=overrides)
    t4_bag_ov = [c for c in crafts_overridden if c.item_id == "T4_BAG"][0]
    # Overridden cost: 8 * 500 + 8 * 1000 = 4000 + 8000 = 12000
    assert t4_bag_ov.material_cost == 12000.0
    assert t4_bag_ov.profit_per_item > t4_bag.profit_per_item





