"""Regression tests for bugs found during the October 2026 review."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

from albion_flips.analyzer import analyze_crafting
from albion_flips.config import AppConfig
from albion_flips.models import PriceRecord
from albion_flips.recipes import load_recipe_book, return_rate
from albion_flips.web import FlipDataStore, location_to_city, start_web_server

NOW = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)


def quote(item_id: str, city: str, sell: int = 0, buy: int = 0, quality: int = 1, age_min: int = 10) -> PriceRecord:
    ts = (NOW - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%S")
    return PriceRecord(
        item_id=item_id, city=city, quality=quality,
        sell_price_min=sell, sell_price_min_date=ts if sell else "",
        sell_price_max=sell, sell_price_max_date=ts if sell else "",
        buy_price_min=buy, buy_price_min_date=ts if buy else "",
        buy_price_max=buy, buy_price_max_date=ts if buy else "",
    )


def test_server_aliases_from_readme_are_accepted() -> None:
    assert AppConfig.from_dict({"server": "west"}).server == "americas"
    assert AppConfig.from_dict({"server": "east"}).server == "asia"
    assert AppConfig.from_dict({"server": "Europe"}).server == "europe"


def test_client_location_ids() -> None:
    assert location_to_city("0007") == "Thetford"
    assert location_to_city("7") == "Thetford"  # leading zeros dropped
    assert location_to_city("3013-Auction2") == "Caerleon"
    assert location_to_city("3003") == "Black Market"
    assert location_to_city("1012") is None  # Merlyn's Rest has its own order book
    assert location_to_city("0301") is None  # portal market


def test_return_rates_match_known_values() -> None:
    assert round(return_rate(0.18), 3) == 0.153          # royal city, no bonus
    assert round(return_rate(0.18 + 0.40), 3) == 0.367   # refining bonus city
    assert round(return_rate(0.18 + 0.40 + 0.59), 3) == 0.539
    assert round(return_rate(0.18 + 0.15), 3) == 0.248   # crafting specialization


def test_recipes_come_from_game_data() -> None:
    book = load_recipe_book()
    recipes = {r.output: r for r in book.recipes}
    assert recipes["T4_BAG"].resources == (("T4_CLOTH", 8, True), ("T4_LEATHER", 8, True))
    # Enchanted refining uses the enchanted lower tier, not the flat one.
    assert ("T4_PLANKS_LEVEL1@1", 1, True) in recipes["T5_PLANKS_LEVEL1@1"].resources
    # Royal sigils scale with tier and are never returned.
    assert ("QUESTITEM_TOKEN_ROYAL_T6", 16, False) in recipes["T6_ARMOR_PLATE_ROYAL"].resources
    assert book.upgrades["T4_BAG"][1] == ("T4_RUNE", 192)


def test_crafting_ignores_masterpiece_and_stale_prices() -> None:
    cfg = AppConfig(premium=True)
    prices = [
        quote("T4_CLOTH", "Lymhurst", sell=100),
        quote("T4_LEATHER", "Lymhurst", sell=100),
        quote("T4_CLOTH", "Brecilien", sell=100),
        quote("T4_LEATHER", "Brecilien", sell=100),
        quote("T4_BAG", "Lymhurst", sell=3000, quality=1),
        quote("T4_BAG", "Caerleon", sell=90000, quality=5),          # Masterpiece
        quote("T4_BAG", "Thetford", sell=50000, quality=1, age_min=60 * 24),  # a day old
    ]
    bag = next(c for c in analyze_crafting(prices, cfg, now=NOW) if c.item_id == "T4_BAG")
    assert bag.sell_city == "Lymhurst"
    assert bag.sell_price == 3000
    assert bag.craft_city == "Brecilien"  # bags have a +15% bonus there


def test_post_endpoints_do_not_hang() -> None:
    """The handler used to read the request body twice, blocking these endpoints."""
    store = FlipDataStore()
    store.update(server="europe", last_refresh="now", opportunities=[], config=AppConfig())
    server = start_web_server(store, port=18771)
    try:
        for path, payload in (
            ("/api/override", {"item_id": "T4_BAG", "city": "Martlock", "quality": 1, "price": 1234}),
            ("/api/fulfill", {"item_id": "T4_BAG", "city": "Black Market", "quality": 1}),
        ):
            req = Request(
                f"http://127.0.0.1:18771{path}",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                assert resp.status == 200
    finally:
        store.clear_overrides()
        server.shutdown()
        server.server_close()


def test_client_upload_paths_are_routed() -> None:
    """albiondata-client posts to <base>/<topic>; unknown topics must still return 200."""
    store = FlipDataStore()
    store.update(server="europe", last_refresh="now", opportunities=[], config=AppConfig())
    server = start_web_server(store, port=18772)
    try:
        orders = {"Orders": [{
            "Id": 1, "ItemTypeId": "T5_BAG", "LocationId": "3008", "QualityLevel": 1,
            "EnchantmentLevel": 0, "UnitPriceSilver": 25000000, "Amount": 3,
            "AuctionType": "offer", "Expires": "2026-11-01T00:00:00",
        }]}
        for topic, body in (("marketorders.ingest", orders), ("goldprices.ingest", {"Prices": [1], "Timestamps": [1]})):
            req = Request(
                f"http://127.0.0.1:18772/api/ingest/{topic}",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                assert resp.status == 200
        assert any(p.item_id == "T5_BAG" and p.city == "Martlock" and p.sell_price_min == 2500 for p in store.last_prices)
    finally:
        server.shutdown()
        server.server_close()
