"""Premium tax toggle, sniffer-sourced deals and crafting ingredient details."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from albion_flips import web
from albion_flips.analyzer import analyze_crafting
from albion_flips.config import AppConfig
from albion_flips.models import PriceRecord
from albion_flips.recipes import load_recipe_book, return_rate
from albion_flips.web import FlipDataStore


def quote(item_id: str, city: str, sell: int = 0, buy: int = 0, now: datetime | None = None, age_min: int = 10) -> PriceRecord:
    now = now or datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%S")
    return PriceRecord(
        item_id=item_id, city=city, quality=1,
        sell_price_min=sell, sell_price_min_date=ts if sell else "",
        sell_price_max=sell, sell_price_max_date=ts if sell else "",
        buy_price_min=buy, buy_price_min_date=ts if buy else "",
        buy_price_max=buy, buy_price_max_date=ts if buy else "",
    )


@pytest.fixture
def store(tmp_path, monkeypatch) -> FlipDataStore:
    monkeypatch.setattr(web, "CACHE_DIR", tmp_path)
    return FlipDataStore()


def bag_prices(now: datetime) -> list[PriceRecord]:
    return [
        quote("T4_CLOTH", "Lymhurst", sell=100, now=now),
        quote("T4_LEATHER", "Lymhurst", sell=100, now=now),
        quote("T4_BAG", "Thetford", sell=3000, now=now),
    ]


def test_crafting_lists_ingredients_and_return_rate() -> None:
    now = datetime.now(timezone.utc)
    book = load_recipe_book()
    craft = next(c for c in analyze_crafting(bag_prices(now), AppConfig(), now=now, recipe_book=book) if c.item_id == "T4_BAG")
    assert [(i["id"], i["count"], i["unit_price"], i["city"]) for i in craft.ingredients] == [
        ("T4_CLOTH", 8, 100, "Lymhurst"),
        ("T4_LEATHER", 8, 100, "Lymhurst"),
    ]
    rrr = craft.resource_return_rate
    assert craft.returned_value == pytest.approx(1600 * rrr, rel=1e-3)
    assert craft.effective_cost == pytest.approx(1600 - craft.returned_value + craft.craft_silver)
    # Materials are only sold in Lymhurst, so the bag is crafted there (no bag bonus).
    assert craft.craft_city == "Lymhurst"
    assert rrr == pytest.approx(return_rate(0.18), abs=1e-3)
    # Return rate depends on the city: Brecilien has the bag bonus.
    assert craft.city_return_rates["Brecilien"] == pytest.approx(return_rate(0.33), abs=1e-3)
    # Sell order: 4% premium tax + 2.5% setup fee.
    assert craft.tax_paid == pytest.approx(3000 * 0.065)


def test_premium_toggle_changes_every_profit(store) -> None:
    now = datetime.now(timezone.utc)
    prices = bag_prices(now) + [quote("T4_BAG", "Martlock", sell=2000, now=now)]
    store.update(server="europe", last_refresh="now", opportunities=[], prices=prices, now=now, config=AppConfig())
    premium = store.get_data()
    assert premium["premium"] is True and premium["tax_rate"] == 0.04
    bag_premium = next(c for c in premium["crafting"] if c["item_id"] == "T4_BAG")

    store.set_premium(False)
    normal = store.get_data()
    assert normal["premium"] is False and normal["tax_rate"] == 0.08
    bag_normal = next(c for c in normal["crafting"] if c["item_id"] == "T4_BAG")
    assert bag_premium["profit_per_item"] - bag_normal["profit_per_item"] == pytest.approx(3000 * 0.04)
    flip = next(f for f in normal["flips"] if f["item_id"] == "T4_BAG")
    assert flip["profit_per_item"] == pytest.approx(3000 * (1 - 0.08 - 0.025) - 2000)

    # The setting survives a restart and wins over config.json.
    again = FlipDataStore()
    assert again.premium_override is False


def test_sniffer_prices_survive_api_refresh_and_flag_crafts(store) -> None:
    now = datetime.now(timezone.utc)
    store.update(server="europe", last_refresh="now", opportunities=[], prices=bag_prices(now), now=now, config=AppConfig())
    # The sniffer sees cheaper cloth in Lymhurst (the API doesn't know yet).
    store.ingest_market_orders([{
        "ItemTypeId": "T4_CLOTH", "LocationId": "1002", "QualityLevel": 1,
        "UnitPriceSilver": 80 * 10000, "AuctionType": "offer",
    }])
    # An API refresh with the old price must not undo the sniffer price.
    store.update(server="europe", last_refresh="later", opportunities=[], prices=bag_prices(now), now=datetime.now(timezone.utc), config=AppConfig())
    bag = next(c for c in store.get_data()["crafting"] if c["item_id"] == "T4_BAG")
    cloth = next(i for i in bag["ingredients"] if i["id"] == "T4_CLOTH")
    assert cloth["unit_price"] == 80
    assert cloth["is_live_sniffer"] is True
    assert bag["is_live_sniffer"] is True and bag["output_sniffed"] is False


def test_sniffer_pages_keep_the_cheapest_order(store) -> None:
    now = datetime.now(timezone.utc)
    store.update(server="europe", last_refresh="now", opportunities=[], prices=bag_prices(now), now=now, config=AppConfig())
    for price in (80, 95):  # two pages of the same market list
        store.ingest_market_orders([{
            "ItemTypeId": "T4_CLOTH", "LocationId": "1002", "QualityLevel": 1,
            "UnitPriceSilver": price * 10000, "AuctionType": "offer",
        }])
    cloth = next(p for p in store.last_prices if p.item_id == "T4_CLOTH" and p.city == "Lymhurst")
    assert cloth.sell_price_min == 80


def test_black_market_red_zone_notice_removed() -> None:
    assert "Red Zone Notice" not in web.HTML_TEMPLATE
    assert "btn-premium" in web.HTML_TEMPLATE
    assert "btn-sniffer-only" in web.HTML_TEMPLATE


def test_materials_bought_in_craft_city_except_artifacts_and_sigils() -> None:
    now = datetime.now(timezone.utc)
    book = load_recipe_book()
    cfg = AppConfig(max_margin_pct=10000)
    prices = [
        quote("T6_ARMOR_PLATE_UNDEAD", "Caerleon", sell=900000, now=now),
        quote("T6_ARMOR_PLATE_ROYAL", "Caerleon", sell=900000, now=now),
        # Bars: cheapest in Thetford, 5% dearer in Bridgewatch (plate armor bonus city).
        quote("T6_METALBAR", "Thetford", sell=1000, now=now),
        quote("T6_METALBAR", "Bridgewatch", sell=1050, now=now),
        quote("T6_ARMOR_PLATE_SET1", "Thetford", sell=50000, now=now),
        # Artifact and royal sigils only in Martlock.
        quote("T6_ARTEFACT_ARMOR_PLATE_UNDEAD", "Martlock", sell=200000, now=now),
        quote("QUESTITEM_TOKEN_ROYAL_T6", "Martlock", sell=5000, now=now),
    ]
    crafts = {c.item_id: c for c in analyze_crafting(prices, cfg, now=now, recipe_book=book)}

    undead = crafts["T6_ARMOR_PLATE_UNDEAD"]
    by_id = {i["id"]: i for i in undead.ingredients}
    assert by_id["T6_ARTEFACT_ARMOR_PLATE_UNDEAD"]["city"] == "Martlock"
    assert by_id["T6_ARTEFACT_ARMOR_PLATE_UNDEAD"]["bought_elsewhere"] is True
    # The return-rate bonus outweighs 5% dearer bars, and bars come from the craft city.
    assert undead.craft_city == "Bridgewatch"
    assert by_id["T6_METALBAR"]["city"] == "Bridgewatch"
    assert by_id["T6_METALBAR"]["unit_price"] == 1050

    royal = crafts["T6_ARMOR_PLATE_ROYAL"]
    by_id = {i["id"]: i for i in royal.ingredients}
    assert by_id["QUESTITEM_TOKEN_ROYAL_T6"]["city"] == "Martlock"
    assert royal.craft_city == by_id["T6_ARMOR_PLATE_SET1"]["city"] == "Thetford"


def test_sniffer_only_materials() -> None:
    now = datetime.now(timezone.utc)
    prices = bag_prices(now) + [
        quote("T4_CLOTH", "Martlock", sell=120, now=now),
        quote("T4_LEATHER", "Martlock", sell=120, now=now),
    ]
    sniffed = {("T4_CLOTH", "Martlock"), ("T4_LEATHER", "Martlock")}
    crafts = analyze_crafting(prices, AppConfig(), now=now, material_quotes=sniffed)
    bag = next(c for c in crafts if c.item_id == "T4_BAG")
    assert bag.craft_city == "Martlock"
    assert {i["unit_price"] for i in bag.ingredients} == {120}
    # Nothing sniffed for planks etc. -> those recipes disappear.
    assert analyze_crafting(prices, AppConfig(), now=now, material_quotes=set()) == []


def test_dashboard_sniffer_crafting_uses_only_sniffed_materials(store) -> None:
    now = datetime.now(timezone.utc)
    store.update(server="europe", last_refresh="now", opportunities=[], prices=bag_prices(now), now=now, config=AppConfig())
    assert store.get_data()["sniffer_crafting"] == []
    for item_id, price in (("T4_CLOTH", 90), ("T4_LEATHER", 95)):
        store.ingest_market_orders([{
            "ItemTypeId": item_id, "LocationId": "1002", "QualityLevel": 1,
            "UnitPriceSilver": price * 10000, "AuctionType": "offer",
        }])
    bag = next(c for c in store.get_data()["sniffer_crafting"] if c["item_id"] == "T4_BAG")
    assert bag["craft_city"] == "Lymhurst"
    assert all(i["is_live_sniffer"] for i in bag["ingredients"])
    assert sorted(i["unit_price"] for i in bag["ingredients"]) == [90, 95]


def test_enchanted_refining_uses_market_ids() -> None:
    book = load_recipe_book()
    outputs = book.output_ids()
    assert "T5_PLANKS_LEVEL1@1" in outputs and "T5_PLANKS_LEVEL1" not in outputs
    assert "T6_METALBAR_LEVEL3@3" in outputs


def test_dashboard_lists_only_profitable_crafts(store) -> None:
    now = datetime.now(timezone.utc)
    prices = bag_prices(now) + [quote("T4_PLANKS", "Lymhurst", sell=10, now=now),
                                quote("T5_WOOD", "Lymhurst", sell=500, now=now),
                                quote("T5_PLANKS", "Lymhurst", sell=100, now=now)]  # loses money
    store.update(server="europe", last_refresh="now", opportunities=[], prices=prices, now=now, config=AppConfig())
    crafts = store.get_data()["crafting"]
    assert any(c["item_id"] == "T4_BAG" for c in crafts)
    assert all(c["profit_per_item"] > 0 for c in crafts)


def test_watchlist_is_saved_and_shows_prices(store) -> None:
    now = datetime.now(timezone.utc)
    store.update(server="europe", last_refresh="now", opportunities=[], prices=bag_prices(now), now=now, config=AppConfig())
    store.set_watchlist(add=["T4_CLOTH", "T4_BAG"])
    store.set_watchlist(remove=["T4_BAG"])
    data = store.get_data()
    assert data["watchlist"] == ["T4_CLOTH"]
    assert data["watchlist_prices"]["T4_CLOTH"][0]["city"] == "Lymhurst"
    assert data["watchlist_prices"]["T4_CLOTH"][0]["sell_price_min"] == 100
    # Survives a restart (the desktop window's browser storage does not).
    assert FlipDataStore().watchlist == ["T4_CLOTH"]
    store.set_watchlist(clear=True)
    assert FlipDataStore().watchlist == []


def morgana_prices(now: datetime) -> list[PriceRecord]:
    def q(item_id, city, sell=0, buy=0, quality=1):
        r = quote(item_id, city, sell=sell, buy=buy, now=now)
        r.quality = quality
        return r
    return [
        # Thetford only lists the base cape in Good quality right now.
        q("T8_CAPE@2", "Thetford", sell=1_150_000, quality=2),
        q("T8_CAPE@2", "Lymhurst", sell=1_130_000),
        q("T8_CAPEITEM_MORGANA_BP", "Thetford", sell=2_150_000),
        q("T1_FACTION_SWAMP_TOKEN_1", "Thetford", sell=32_100),
        q("T8_CAPEITEM_MORGANA@2", "Black Market", buy=4_800_000),
    ]


def test_crafting_uses_ingredients_of_any_quality_and_keeps_other_cities() -> None:
    now = datetime.now(timezone.utc)
    craft = next(c for c in analyze_crafting(morgana_prices(now), AppConfig(), now=now)
                 if c.item_id == "T8_CAPEITEM_MORGANA@2")
    assert craft.sell_city == "Black Market"
    assert craft.craft_city == "Lymhurst"  # cheapest base cape
    thetford = next(o for o in craft.city_options if o["city"] == "Thetford")
    assert thetford["prices"][0] == 1_150_000  # the Good-quality cape counts
    assert 0 < thetford["profit_per_item"] < craft.profit_per_item


def test_sniffer_crafting_counts_any_quality_scan(store) -> None:
    now = datetime.now(timezone.utc)
    store.update(server="europe", last_refresh="now", opportunities=[], prices=morgana_prices(now), now=now, config=AppConfig())
    for item_id, quality, price in (("T8_CAPE@2", 2, 1_150_000), ("T8_CAPEITEM_MORGANA_BP", 1, 2_150_000),
                                    ("T1_FACTION_SWAMP_TOKEN_1", 1, 32_100)):
        store.ingest_market_orders([{"ItemTypeId": item_id, "LocationId": "0007", "QualityLevel": quality,
                                     "UnitPriceSilver": price * 10000, "AuctionType": "offer"}])
    cape = next(c for c in store.get_data()["sniffer_crafting"] if c["item_id"] == "T8_CAPEITEM_MORGANA@2")
    assert cape["craft_city"] == "Thetford"
    assert all(i["is_live_sniffer"] for i in cape["ingredients"])


def test_quality_chances_match_game_odds() -> None:
    from albion_flips.analyzer import quality_chances
    one = quality_chances(1)
    assert one == pytest.approx([0.689, 0.25, 0.05, 0.01, 0.001])
    two = quality_chances(2)
    assert sum(two) == pytest.approx(1.0)
    assert two[0] == pytest.approx(0.689 ** 2)  # Normal only if both rolls are Normal


def morgana83_prices(now: datetime, good_bm: int = 7_023_754) -> list[PriceRecord]:
    def q(item_id, city, sell=0, buy=0, quality=1):
        r = quote(item_id, city, sell=sell, buy=buy, now=now)
        r.quality = quality
        return r
    return [
        q("T8_CAPE@3", "Thetford", sell=3_800_000, quality=2),
        q("T8_CAPEITEM_MORGANA_BP", "Thetford", sell=2_149_957),
        q("T1_FACTION_SWAMP_TOKEN_1", "Thetford", sell=33_360),
        q("T8_CAPEITEM_MORGANA@3", "Black Market", buy=844_101, quality=1),
        q("T8_CAPEITEM_MORGANA@3", "Black Market", buy=good_bm, quality=2),
    ]


def test_crafting_values_every_quality_roll() -> None:
    now = datetime.now(timezone.utc)
    craft = next(c for c in analyze_crafting(morgana83_prices(now), AppConfig(), now=now)
                 if c.item_id == "T8_CAPEITEM_MORGANA@3")
    # Normal fills only the Normal order; Good and better fill the 7.0M Good order.
    assert craft.quality_nets[0] == pytest.approx(844_101 * 0.96, rel=1e-4)
    assert craft.quality_nets[1:] == pytest.approx([7_023_754 * 0.96] * 4, rel=1e-4)
    assert craft.profit_per_item < 0
    expected = 0.689 * 844_101 * 0.96 + 0.311 * 7_023_754 * 0.96 - craft.effective_cost
    assert craft.expected_profit == pytest.approx(expected, rel=1e-3)
    assert craft.expected_profit < 0  # a gamble that loses on average with one roll


def test_dashboard_keeps_deals_profitable_on_average(store) -> None:
    now = datetime.now(timezone.utc)
    # A huge Good-quality order makes the craft profitable on average, never as Normal.
    store.update(server="europe", last_refresh="now", opportunities=[], prices=morgana83_prices(now, good_bm=30_000_000),
                 now=now, config=AppConfig())
    cape = next(c for c in store.get_data()["crafting"] if c["item_id"] == "T8_CAPEITEM_MORGANA@3")
    assert cape["profit_per_item"] < 0 < cape["expected_profit"]
    assert cape["quality_exits"][1].startswith("Black Market buy order")


def test_old_configs_gain_brecilien_and_enchanted_ids_are_normalised(store) -> None:
    cfg = AppConfig.from_dict({"cities": ["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford", "Caerleon", "Black Market"]})
    assert cfg.cities.index("Brecilien") == cfg.cities.index("Black Market") - 1
    store.ingest_market_orders([{"ItemTypeId": "T8_CAPE", "EnchantmentLevel": 3, "LocationId": "0007", "QualityLevel": 1,
                                 "UnitPriceSilver": 100 * 10000, "AuctionType": "offer"}])
    assert any(p.item_id == "T8_CAPE@3" for p in store.last_prices)
