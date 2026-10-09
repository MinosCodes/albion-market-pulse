from __future__ import annotations

import pytest
from albion_flips.config import (
    AppConfig,
    ConfigError,
    expand_item_group,
    expand_items,
    load_config,
    load_items,
)


def test_case_12_item_expansion() -> None:
    """Case 12: Item expansion for PLANKS, tiers [4], enchants [0, 1]."""
    expanded = expand_item_group(bases=["PLANKS"], tiers=[4], enchants=[0, 1])
    assert expanded == ["T4_PLANKS", "T4_PLANKS_LEVEL1@1"]


def test_case_13_config_unknown_city() -> None:
    """Case 13: Config with an unknown city produces a clear error."""
    invalid_data = {
        "server": "europe",
        "cities": ["Bridgewatch", "Atlantis"],
    }
    with pytest.raises(ConfigError) as exc_info:
        AppConfig.from_dict(invalid_data)
    assert "Unknown city 'Atlantis'" in str(exc_info.value)


def test_config_unknown_server() -> None:
    """Verify unknown server validation."""
    with pytest.raises(ConfigError) as exc_info:
        AppConfig.from_dict({"server": "antarctica"})
    assert "Unknown server 'antarctica'" in str(exc_info.value)


def test_config_negative_tax_rate() -> None:
    """Verify negative tax rate validation."""
    with pytest.raises(ConfigError) as exc_info:
        AppConfig.from_dict({"tax_rate_premium": -0.05})
    assert "cannot be negative" in str(exc_info.value)


def test_load_example_config_and_items() -> None:
    """Verify default example config and items load properly."""
    cfg = load_config("config.example.json")
    assert cfg.server == "europe"
    assert "Bridgewatch" in cfg.cities
    assert cfg.active_tax_rate == 0.04

    items = load_items("data/items.json")
    assert "T4_WOOD" in items
    assert "T4_PLANKS" in items
    assert "T4_BAG" in items
    assert len(items) > 10


def test_all_items_exist_and_render_ready() -> None:
    """Verify that all valid Albion Online items exist in items.json and are render-ready."""
    import json
    with open("data/item_names.json", "r", encoding="utf-8") as f:
        item_names = json.load(f)

    items = load_items("data/items.json")
    item_set = set(items)

    # Over 5,000 active trade items and over 11,000 game items
    assert len(items) >= 5000
    assert len(item_names) >= 11000

    # Key categories must be fully present across tiers and enchants
    assert "T4_MAIN_SWORD" in item_set
    assert "T8_2H_AXE_AVALON@4" in item_set
    assert "T6_CAPEITEM_FW_BRIDGEWATCH@2" in item_set
    assert "T8_MOUNT_HORSE" in item_set
    assert "T7_POTION_REVIVE" in item_set
    assert "T7_MEAL_OMELETTE" in item_set
    assert "T5_FISH_FRESHWATER_ALL_COMMON" in item_set
    assert "T4_ARTEFACT_2H_BOW_AVALON" in item_set
    assert "T3_WOOD" in item_set
    assert "T3_2H_BOW" in item_set

    # Check sample items exist in item_names
    for sample_id in ["T4_BAG@1", "T6_2H_AXE_AVALON@3", "T8_MOUNT_HORSE"]:
        assert sample_id in item_set
        assert sample_id in item_names

