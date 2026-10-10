from __future__ import annotations

import json
from urllib.request import Request, urlopen

from albion_flips.config import AppConfig
from albion_flips.web import FlipDataStore, start_web_server


def test_ingest_market_orders_direct() -> None:
    store = FlipDataStore()
    config = AppConfig()
    store.update(server="europe", last_refresh="2026-10-10 12:00:00 UTC", opportunities=[], config=config)

    # Raw orders as emitted by albiondata-client
    # Notice UnitPriceSilver is multiplied by 10,000 in Albion game network protocol
    orders = [
        {
            "Id": 1001,
            "ItemTypeId": "T4_BAG",
            "LocationId": "3008",  # Martlock
            "QualityLevel": 1,
            "UnitPriceSilver": 12500000,  # 1,250 silver
            "Amount": 10,
            "AuctionType": "offer",
        },
        {
            "Id": 1002,
            "ItemTypeId": "T4_BAG",
            "LocationId": "3003",  # Black Market
            "QualityLevel": 1,
            "UnitPriceSilver": 38000000,  # 3,800 silver
            "Amount": 5,
            "AuctionType": "request",
        },
    ]

    res = store.ingest_market_orders(orders)
    assert res["orders_processed"] == 2
    assert res["items_updated"] == 2

    # Check that price was divided by 10000
    prices = { (p.item_id, p.city): p for p in store.last_prices }
    assert ("T4_BAG", "Martlock") in prices
    assert prices[("T4_BAG", "Martlock")].sell_price_min == 1250

    assert ("T4_BAG", "Black Market") in prices
    assert prices[("T4_BAG", "Black Market")].buy_price_max == 3800

    # Check store.get_data() has sniffer_status
    data = store.get_data()
    assert data["sniffer_status"]["active"] is True
    assert data["sniffer_status"]["packet_count"] == 2

    # Black market opportunity should be instantly computed
    bm = data["blackmarket"]
    assert len(bm) >= 1
    bm_bag = next(b for b in bm if b["item_id"] == "T4_BAG")
    assert bm_bag["buy_city"] == "Martlock"
    assert bm_bag["sell_city"] == "Black Market"
    assert bm_bag["buy_price"] == 1250
    assert bm_bag["sell_price"] == 3800
    assert bm_bag["is_live_sniffer"] is True
    assert bm_bag["sniffer_age_seconds"] is not None
    assert bm_bag["sniffer_age_seconds"] <= 5


def test_ingest_via_http_endpoints() -> None:
    store = FlipDataStore()
    config = AppConfig()
    store.update(server="europe", last_refresh="2026-10-10 12:00:00 UTC", opportunities=[], config=config)

    server = start_web_server(store, port=18767)
    try:
        # 1. Test POST /api/ingest
        payload = {
            "Orders": [
                {
                    "Id": 2001,
                    "ItemTypeId": "T4_MAIN_SWORD",
                    "LocationId": "2004",  # Bridgewatch
                    "QualityLevel": 1,
                    "UnitPriceSilver": 50000000,  # 5,000 silver
                    "Amount": 1,
                    "AuctionType": "offer",
                },
                {
                    "Id": 2002,
                    "ItemTypeId": "T4_MAIN_SWORD",
                    "LocationId": "3003",  # Black Market
                    "QualityLevel": 1,
                    "UnitPriceSilver": 120000000,  # 12,000 silver
                    "Amount": 2,
                    "AuctionType": "request",
                },
            ]
        }
        req = Request(
            "http://127.0.0.1:18767/api/ingest",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as resp:
            assert resp.status == 200
            resp_data = json.loads(resp.read().decode("utf-8"))
            assert resp_data["status"] == "ok"
            assert resp_data["ingested"] == 2

        # 2. Test POST /marketorders.ingest (standard AODC upload path)
        payload2 = {
            "Orders": [
                {
                    "Id": 2003,
                    "ItemTypeId": "T5_ARMOR_CLOTH_SET1",
                    "LocationId": "1002",  # Lymhurst
                    "QualityLevel": 1,
                    "UnitPriceSilver": 20000000,  # 2,000 silver
                    "Amount": 1,
                    "AuctionType": "offer",
                }
            ]
        }
        req2 = Request(
            "http://127.0.0.1:18767/marketorders.ingest",
            data=json.dumps(payload2).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req2) as resp2:
            assert resp2.status == 200

        # 3. Query /api/flips to verify data is instantly live
        with urlopen("http://127.0.0.1:18767/api/flips") as resp_get:
            assert resp_get.status == 200
            flips_data = json.loads(resp_get.read().decode("utf-8"))
            assert flips_data["sniffer_status"]["active"] is True
            assert flips_data["sniffer_status"]["packet_count"] == 3

    finally:
        server.shutdown()
        server.server_close()
