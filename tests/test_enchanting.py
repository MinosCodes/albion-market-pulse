from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from albion_flips.analyzer import analyze_enchanting
from albion_flips.config import AppConfig
from albion_flips.models import HistoryPoint, HistoryRecord, PriceRecord
from albion_flips.profit import calculate_enchanting_materials, calculate_enchanting_profit
from albion_flips.web import FlipDataStore, start_web_server


def test_calculate_enchanting_materials_rules() -> None:
    # 2H Weapons require 192 materials
    assert calculate_enchanting_materials("T4_2H_BOW") == ("2H Weapon", 192)
    assert calculate_enchanting_materials("T6_2H_CLAYMORE") == ("2H Weapon", 192)
    assert calculate_enchanting_materials("T5_2H_CROSSBOW@1") == ("2H Weapon", 192)

    # 1H Weapons require 144 materials
    assert calculate_enchanting_materials("T4_MAIN_SWORD") == ("1H Weapon", 144)
    assert calculate_enchanting_materials("T7_MAIN_AXE") == ("1H Weapon", 144)

    # Armor and Bags require 96 materials
    assert calculate_enchanting_materials("T4_ARMOR_CLOTH_SET1") == ("Armor", 96)
    assert calculate_enchanting_materials("T5_BAG") == ("Bag", 96)
    assert calculate_enchanting_materials("T4_BAG_INSIGHT") == ("Bag", 96)

    # Helmets, Shoes, Capes, Off-hands require 48 materials
    assert calculate_enchanting_materials("T4_HEAD_PLATE_SET1") == ("Helmet", 48)
    assert calculate_enchanting_materials("T5_SHOES_LEATHER_SET1") == ("Boots", 48)
    assert calculate_enchanting_materials("T4_CAPE") == ("Cape", 48)
    assert calculate_enchanting_materials("T4_OFF_SHIELD") == ("Off-hand", 48)


def test_calculate_enchanting_profit_math() -> None:
    # Buy flat bag for 1,200s, 96 runes @ 40s = 3,840s mats
    # Total cost = 5,040s
    # Sell enchanted bag for 7,500s (sell order: 4% tax + 2.5% fee = 6.5% total deduction)
    # Net revenue = 7,500 * 0.935 = 7,012s
    # Profit = 7,012 - 5,040 = 1,972s
    tot_cost, profit, margin = calculate_enchanting_profit(
        base_item_price=1200,
        enchant_mat_cost=3840,
        sell_price=7500,
        tax_rate=0.04,
        setup_fee_rate=0.025,
        is_buy_order_exit=False,
    )
    assert tot_cost == 5040
    assert profit == 1972
    assert round(margin, 1) == 39.1

    # Instant sell into Black Market buy order (4% tax only, no setup fee)
    # Sell for 6,000s -> Net revenue = 6,000 * 0.96 = 5,760s
    # Profit = 5,760 - 5,040 = 720s
    tot_cost_bm, profit_bm, margin_bm = calculate_enchanting_profit(
        base_item_price=1200,
        enchant_mat_cost=3840,
        sell_price=6000,
        tax_rate=0.04,
        setup_fee_rate=0.025,
        is_buy_order_exit=True,
    )
    assert tot_cost_bm == 5040
    assert profit_bm == 720
    assert round(margin_bm, 1) == 14.3


def test_analyze_enchanting_engine() -> None:
    now = datetime.now(timezone.utc)
    config = AppConfig(premium=True)

    prices = [
        # Martlock prices
        PriceRecord(
            item_id="T4_BAG",
            city="Martlock",
            quality=1,
            sell_price_min=1200,
            sell_price_min_date=now.isoformat(),
            sell_price_max=1200,
            sell_price_max_date=now.isoformat(),
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_RUNE",
            city="Martlock",
            quality=1,
            sell_price_min=40,
            sell_price_min_date=now.isoformat(),
            sell_price_max=40,
            sell_price_max_date=now.isoformat(),
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_BAG@1",
            city="Martlock",
            quality=1,
            sell_price_min=7500,
            sell_price_min_date=now.isoformat(),
            sell_price_max=7500,
            sell_price_max_date=now.isoformat(),
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        # Black market buy order for T4_BAG@1
        PriceRecord(
            item_id="T4_BAG@1",
            city="Black Market",
            quality=1,
            sell_price_min=0,
            sell_price_min_date="",
            sell_price_max=0,
            sell_price_max_date="",
            buy_price_min=8000,
            buy_price_min_date=now.isoformat(),
            buy_price_max=8000,
            buy_price_max_date=now.isoformat(),
        ),
    ]

    opps, mats = analyze_enchanting(prices, config, now=now)
    assert len(opps) >= 1

    # Check live material status
    assert "T4_RUNE" in mats
    assert mats["T4_RUNE"]["price"] == 40
    assert mats["T4_RUNE"]["source"] == "live"
    assert mats["T4_RUNE"]["city"] == "Martlock"

    # Find Martlock -> Martlock T4_BAG .0 -> .1
    martlock_opp = next(o for o in opps if o.base_item_id == "T4_BAG" and o.sell_city == "Martlock")
    assert martlock_opp.from_enchant == 0
    assert martlock_opp.to_enchant == 1
    assert martlock_opp.base_item_price == 1200
    assert martlock_opp.enchant_mat_qty == 96
    assert martlock_opp.enchant_mat_unit_price == 40
    assert martlock_opp.enchant_mat_total_cost == 3840
    assert martlock_opp.total_cost == 5040
    assert martlock_opp.profit_per_item == 1972
    assert martlock_opp.is_profitable is True
    assert martlock_opp.mass_profit == 19720  # 10x batch default


def test_web_store_enchanting_integration() -> None:
    store = FlipDataStore()
    config = AppConfig()
    store.update(server="europe", last_refresh="2026-10-10 12:00:00 UTC", opportunities=[], config=config)

    server = start_web_server(store, port=18768)
    try:
        # Simulate sniffer ingesting live rune and bag market orders
        orders = [
            {
                "Id": 3001,
                "ItemTypeId": "T4_RUNE",
                "LocationId": "3008",  # Martlock
                "QualityLevel": 1,
                "UnitPriceSilver": 450000,  # 45 silver
                "Amount": 500,
                "AuctionType": "offer",
            },
            {
                "Id": 3002,
                "ItemTypeId": "T4_BAG",
                "LocationId": "3008",  # Martlock
                "QualityLevel": 1,
                "UnitPriceSilver": 15000000,  # 1,500 silver
                "Amount": 10,
                "AuctionType": "offer",
            },
            {
                "Id": 3003,
                "ItemTypeId": "T4_BAG@1",
                "LocationId": "3008",  # Martlock
                "QualityLevel": 1,
                "UnitPriceSilver": 90000000,  # 9,000 silver
                "Amount": 5,
                "AuctionType": "offer",
            },
        ]
        store.ingest_market_orders(orders)

        data = store.get_data()
        assert "enchanting" in data
        assert "artifact_materials" in data
        assert data["enchant_count"] >= 1

        ench_list = data["enchanting"]
        bag_ench = next((e for e in ench_list if e["base_item_id"] == "T4_BAG" and e["to_enchant"] == 1), None)
        assert bag_ench is not None
        assert bag_ench["base_item_price"] == 1500
        assert bag_ench["enchant_mat_unit_price"] == 45
        assert bag_ench["total_cost"] == 1500 + (96 * 45)
        assert bag_ench["is_profitable"] is True
        assert bag_ench["is_live_sniffer"] is True

        # Test querying via HTTP
        with urlopen("http://127.0.0.1:18768/api/flips") as resp:
            assert resp.status == 200
            api_data = json.loads(resp.read().decode("utf-8"))
            assert "enchanting" in api_data
            assert api_data["enchant_count"] >= 1
    finally:
        server.shutdown()
        server.server_close()


def test_analyze_enchanting_with_sales_history_and_dead_item() -> None:
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    config = AppConfig(premium=True, volume_days=3)

    prices = [
        PriceRecord(
            item_id="T4_BAG",
            city="Martlock",
            quality=1,
            sell_price_min=1000,
            sell_price_min_date=now.isoformat(),
            sell_price_max=1000,
            sell_price_max_date=now.isoformat(),
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_RUNE",
            city="Martlock",
            quality=1,
            sell_price_min=30,
            sell_price_min_date=now.isoformat(),
            sell_price_max=30,
            sell_price_max_date=now.isoformat(),
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_BAG@1",
            city="Martlock",
            quality=1,
            sell_price_min=8000,
            sell_price_min_date=now.isoformat(),
            sell_price_max=8000,
            sell_price_max_date=now.isoformat(),
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        # A dead item: T4_ARMOR_CLOTH_SET1 (0 sales)
        PriceRecord(
            item_id="T4_ARMOR_CLOTH_SET1",
            city="Martlock",
            quality=1,
            sell_price_min=2000,
            sell_price_min_date=now.isoformat(),
            sell_price_max=2000,
            sell_price_max_date=now.isoformat(),
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
        PriceRecord(
            item_id="T4_ARMOR_CLOTH_SET1@1",
            city="Martlock",
            quality=1,
            sell_price_min=15000,
            sell_price_min_date=now.isoformat(),
            sell_price_max=15000,
            sell_price_max_date=now.isoformat(),
            buy_price_min=0,
            buy_price_min_date="",
            buy_price_max=0,
            buy_price_max_date="",
        ),
    ]

    history = [
        HistoryRecord(
            location="Martlock",
            item_id="T4_BAG@1",
            quality=1,
            data=[
                HistoryPoint(item_count=20, avg_price=8000, timestamp="2026-10-10T00:00:00"),
                HistoryPoint(item_count=20, avg_price=7900, timestamp="2026-10-09T00:00:00"),
                HistoryPoint(item_count=20, avg_price=8100, timestamp="2026-10-08T00:00:00"),
            ],
        ),
        HistoryRecord(
            location="Martlock",
            item_id="T4_ARMOR_CLOTH_SET1@1",
            quality=1,
            data=[
                HistoryPoint(item_count=0, avg_price=0, timestamp="2026-10-10T00:00:00"),
            ],
        ),
    ]

    opps, mats = analyze_enchanting(prices, config, history=history, now=now)
    bag_opp = next(o for o in opps if o.base_item_id == "T4_BAG")
    assert bag_opp.target_daily_volume == 20.0
    assert bag_opp.target_history_missing is False
    assert bag_opp.liquidity_status == "high"

    armor_opp = next(o for o in opps if o.base_item_id == "T4_ARMOR_CLOTH_SET1")
    assert armor_opp.target_daily_volume == 0.0
    assert armor_opp.target_history_missing is False
    assert armor_opp.liquidity_status == "dead"


def test_ingest_market_histories_radar_and_volume() -> None:
    store = FlipDataStore()
    config = AppConfig()
    store.update(server="europe", last_refresh="2026-10-10 12:00:00 UTC", opportunities=[], config=config)

    histories = [
        {
            "ItemTypeId": "T4_BAG@1",
            "LocationId": "3008",  # Martlock
            "QualityLevel": 1,
            "ItemCount": 45,
            "AvgPrice": 75000000,  # 7,500 silver
            "Timestamp": "2026-10-10T11:00:00",
        }
    ]
    res = store.ingest_market_histories(histories)
    assert res["histories_processed"] == 1

    data = store.get_data()
    assert "live_sniffer_events" in data
    assert len(data["live_sniffer_events"]) >= 1

    event = data["live_sniffer_events"][0]
    assert event["item_id"] == "T4_BAG@1"
    assert event["city"] == "Martlock"
    assert event["daily_volume"] is not None
    assert "Safe to mass enchant" in event["verdict"] or "VERIFIED" in event["verdict"]
