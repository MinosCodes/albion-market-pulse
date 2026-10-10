from __future__ import annotations

import pytest
from albion_flips.weights import (
    get_item_weight,
    get_item_value,
    parse_tier,
    parse_enchant,
    MOUNT_CAPACITIES,
)
from albion_flips.profit import (
    calculate_profit_per_kg,
    calculate_mount_trip,
    calculate_station_usage_fee,
)


def test_parse_tier_and_enchant():
    assert parse_tier("T4_BAG") == 4
    assert parse_tier("T8_MAIN_SWORD") == 8
    assert parse_enchant("T4_BAG") == 0
    assert parse_enchant("T6_PLANKS_LEVEL2@2") == 2
    assert parse_enchant("T7_ARMOR_PLATE_SET1@3") == 3


def test_item_weights():
    # Bag and cape
    assert get_item_weight("T4_BAG") == 1.5
    assert get_item_weight("T8_CAPE") == 1.5

    # Runes and tomes
    assert get_item_weight("T4_RUNE") == 0.1
    assert get_item_weight("T8_TOME") == 0.1

    # Resources
    t4_planks = get_item_weight("T4_PLANKS")
    t8_planks = get_item_weight("T8_PLANKS")
    assert 0.2 <= t4_planks <= 0.5
    assert t8_planks > t4_planks

    # Weapons (2H heavier than 1H)
    w_2h = get_item_weight("T6_2H_HALBERD")
    w_1h = get_item_weight("T6_MAIN_SWORD")
    assert w_2h > w_1h

    # Plate heavier than cloth
    plate = get_item_weight("T5_ARMOR_PLATE")
    cloth = get_item_weight("T5_ARMOR_CLOTH")
    assert plate > cloth


def test_item_value():
    v4 = get_item_value("T4_BAG")
    v5 = get_item_value("T5_BAG")
    v6 = get_item_value("T6_BAG")
    assert v5 > v4
    assert v6 > v5
    # Enchantment multiplies value
    v4_0 = get_item_value("T4_PLANKS")
    v4_1 = get_item_value("T4_PLANKS@1")
    assert v4_1 == v4_0 * 2


def test_profit_per_kg():
    # 10,000 profit on 0.5kg item -> 20,000 silver/kg
    density = calculate_profit_per_kg(10000.0, 0.5)
    assert density == 20000.0


def test_mount_trip_calculation():
    # T5 Ox = 1,607 kg capacity
    # Item = 2.0 kg, profit = 5,000 silver
    ox_capacity = MOUNT_CAPACITIES["T5 Transport Ox"]
    units, trip_profit = calculate_mount_trip(
        profit_per_item=5000.0,
        weight=2.0,
        mount_capacity_kg=ox_capacity,
    )
    assert units == int(1607.0 // 2.0)  # 803
    assert trip_profit == 803 * 5000.0


def test_station_usage_fee():
    # Item value = 1,000, fee = 500 silver / 100 nutrition
    # Fee = 1,000 * 0.001125 * 500 = 562.5 silver
    fee = calculate_station_usage_fee(item_value=1000.0, fee_per_100_nutrition=500.0)
    assert fee == pytest.approx(562.5)
