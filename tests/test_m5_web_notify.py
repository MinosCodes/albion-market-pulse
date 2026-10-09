from __future__ import annotations

import json
from urllib.request import urlopen
from albion_flips.models import ExitType, FlipOpportunity
from albion_flips.notify import Notifier
from albion_flips.web import FlipDataStore, start_web_server


def test_web_server_endpoints() -> None:
    store = FlipDataStore()
    flip = FlipOpportunity(
        item_id="T4_PLANKS",
        buy_city="Bridgewatch",
        sell_city="Martlock",
        buy_price=1000,
        sell_price=1300,
        exit_type=ExitType.SELL_ORDER,
        profit_per_item=215.5,
        margin_pct=21.55,
        total_profit=21550.0,
        avg_daily_volume=133.3,
        data_age_minutes=30.0,
        risk="low",
    )
    store.update(server="europe", last_refresh="2026-10-09 08:30:00 UTC", opportunities=[flip])

    server = start_web_server(store, port=18765)
    try:
        # Test GET /
        with urlopen("http://127.0.0.1:18765/") as response:
            assert response.status == 200
            html = response.read().decode("utf-8")
            assert "Albion Market Analyzer" in html
            assert "flips-table" in html

        # Test GET /api/flips
        with urlopen("http://127.0.0.1:18765/api/flips") as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
            assert data["server"] == "europe"
            assert len(data["flips"]) == 1
            assert data["flips"][0]["item_id"] == "T4_PLANKS"
            assert data["flips"][0]["profit_per_item"] == 215.5

        # Test GET /api/advisor
        with urlopen("http://127.0.0.1:18765/api/advisor?query=Martlock") as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
            assert "response" in data
            assert "Martlock" in data["response"]

        # Test POST /api/advisor
        from urllib.request import Request
        req = Request(
            "http://127.0.0.1:18765/api/advisor",
            data=json.dumps({"query": "What should I do in Thetford?"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urlopen(req) as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
            assert "response" in data
            assert "Thetford" in data["response"]
    finally:
        server.shutdown()
        server.server_close()


def test_notifier_alerts() -> None:
    notifier = Notifier(threshold_silver=10000)
    flip_below = FlipOpportunity(
        item_id="T4_CHEAP",
        buy_city="Bridgewatch",
        sell_city="Martlock",
        buy_price=100,
        sell_price=150,
        exit_type=ExitType.SELL_ORDER,
        profit_per_item=40.0,
        margin_pct=40.0,
        total_profit=4000.0,  # Below threshold
        avg_daily_volume=100.0,
        data_age_minutes=10.0,
        risk="low",
    )
    flip_above = FlipOpportunity(
        item_id="T4_EXPENSIVE",
        buy_city="Bridgewatch",
        sell_city="Martlock",
        buy_price=1000,
        sell_price=1300,
        exit_type=ExitType.SELL_ORDER,
        profit_per_item=215.5,
        margin_pct=21.55,
        total_profit=21550.0,  # Above threshold
        avg_daily_volume=100.0,
        data_age_minutes=10.0,
        risk="low",
    )

    notifier.check_and_notify([flip_below, flip_above])
    assert ("T4_EXPENSIVE", "Bridgewatch", "Martlock", "sell_order") in notifier._notified_keys
    assert ("T4_CHEAP", "Bridgewatch", "Martlock", "sell_order") not in notifier._notified_keys

    # Second run should not duplicate
    key_count_before = len(notifier._notified_keys)
    notifier.check_and_notify([flip_above])
    assert len(notifier._notified_keys) == key_count_before
