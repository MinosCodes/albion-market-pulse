"""Overlay feed: key check, summary content, item cards and the live event stream."""
from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection

import pytest

from albion_flips.config import AppConfig
from albion_flips.models import PriceRecord
from albion_flips.overlay import OverlayHub, card_from_orders, item_card, summary, verdict
from albion_flips.robust import Reference
from albion_flips.server import Auth, attach_overlay, make_handler, split_overlay_path
from albion_flips.web import FlipDataStore, start_web_server

IK = "I" * 32
NOW = datetime.now(timezone.utc)


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def rec(item: str, city: str, sell: int = 0, buy: int = 0, q: int = 1) -> PriceRecord:
    d = _iso(5)
    return PriceRecord(item, city, q, sell, d if sell else "", sell, d if sell else "", buy, d if buy else "", buy, d if buy else "")


def _store() -> FlipDataStore:
    store = FlipDataStore()
    store.last_config = AppConfig()
    store.last_prices = [
        rec("T4_BAG", "Martlock", sell=1000, buy=900),
        rec("T4_BAG", "Lymhurst", sell=1500, buy=1300),
        rec("T4_BAG", "Black Market", buy=2000),
    ]
    store.reference = {
        ("T4_BAG", "Martlock", 1): Reference(avg7=1200.0, avg30=1200.0, vol7=50.0),
        ("T4_BAG", "Lymhurst", 1): Reference(avg7=1400.0, avg30=1400.0, vol7=50.0),
    }
    store.flips = [
        {"item_id": "T4_BAG", "quality": 1, "buy_city": "Martlock", "sell_city": "Black Market", "buy_price": 1000,
         "sell_price": 2000, "profit_per_item": 920, "est_daily_profit": 9000, "price_note": "", "history_missing": False},
        {"item_id": "T5_BAG", "quality": 1, "buy_city": "Martlock", "sell_city": "Lymhurst", "buy_price": 10,
         "sell_price": 99999, "profit_per_item": 99000, "est_daily_profit": 5_000_000, "price_note": "no_history", "history_missing": True},
    ]
    return store


def test_verdict_thresholds() -> None:
    assert verdict(800, 1000) == "cheap"
    assert verdict(1000, 1000) == "fair"
    assert verdict(1200, 1000) == "expensive"
    assert verdict(1000, None) == "unknown"


def test_item_card_compares_with_7_day_average_and_finds_best_city() -> None:
    card = item_card(_store(), "T4_BAG", 1, "Martlock", seen_sell=1000)
    assert card["seen"]["avg7"] == 1200 and card["seen"]["verdict"] == "cheap" and card["seen"]["vs_avg_pct"] == -17
    # Black Market instant sell: 2000 minus 4 % tax
    assert card["best_instant"] == {"city": "Black Market", "net": 1920}
    # Lymhurst sell order: 1500 capped at 110 % of 1400 = 1540 -> 1500, minus 6.5 %
    assert card["best_order"] == {"city": "Lymhurst", "net": round(1500 * (1 - 0.04 - 0.025))}
    assert [f["sell_city"] for f in card["flips"]] == ["Black Market"]


def test_summary_only_lists_verified_flips() -> None:
    s = summary(_store())
    assert s["flips_total"] == 2 and s["flips_verified"] == 1
    assert [f["item_id"] for f in s["top"]] == ["T4_BAG"]


def test_card_picks_item_with_most_orders() -> None:
    agg = {("T4_BAG", "Martlock", 1): {"min_sell": 1000, "max_buy": 0, "n": 40},
           ("T5_BAG", "Martlock", 1): {"min_sell": 5000, "max_buy": 0, "n": 2}}
    card = card_from_orders(_store(), agg)
    assert card and card["item_id"] == "T4_BAG" and card["orders"] == 40 and card["items_in_upload"] == 2


def test_hub_is_bounded_and_never_blocks() -> None:
    hub = OverlayHub(max_subscribers=1)
    q = hub.subscribe()
    assert q is not None and hub.subscribe() is None
    for i in range(200):  # more than the queue holds: must not block or raise
        hub.publish({"type": "x", "i": i})
    assert q.qsize() == 50
    hub.unsubscribe(q)
    assert hub.subscribers == 0


def test_split_overlay_path() -> None:
    assert split_overlay_path("/api/overlay/abc/summary") == ("abc", "summary")
    assert split_overlay_path("/api/overlay/abc/events") == ("abc", "events")
    assert split_overlay_path("/api/overlay/abc/../summary") is None
    assert split_overlay_path("/api/overlay//summary") is None
    assert split_overlay_path("/api/overlay/abc/delete") is None


# ------------------------------------------------------------ HTTP
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def server():
    store = _store()
    auth = Auth(access_key="A" * 32, ingest_key=IK, public_read=False, allow_local_ingest=False)
    hub = attach_overlay(store)
    port = _free_port()
    httpd = start_web_server(store, port=port, host="127.0.0.1", handler_cls=make_handler(auth, dict, hub, store))
    yield port, store, hub
    httpd.shutdown()


def _get(port: int, path: str, headers: dict | None = None):
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def test_overlay_needs_the_ingest_key(server) -> None:
    port, _, _ = server
    assert _get(port, "/api/overlay/" + "x" * 32 + "/summary")[0] == 401
    assert _get(port, "/api/overlay/session/summary")[0] == 401
    st, body = _get(port, f"/api/overlay/{IK}/summary")
    assert st == 200 and json.loads(body)["top"][0]["item_id"] == "T4_BAG"
    st, body = _get(port, f"/api/overlay/{IK}/item?id=T4_BAG&q=1")
    assert st == 200 and json.loads(body)["best_instant"]["city"] == "Black Market"


def test_overlay_with_dashboard_cookie(server) -> None:
    port, _, _ = server
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/login?k=" + "A" * 32)
    cookie = c.getresponse().getheader("Set-Cookie").split(";")[0]
    c.close()
    assert _get(port, "/api/overlay/session/summary", {"Cookie": cookie})[0] == 200


def test_event_stream_delivers_item_card_after_ingest(server) -> None:
    port, _, hub = server
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", f"/api/overlay/{IK}/events")
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")

    def read_event() -> dict:
        data = None
        while True:
            line = r.fp.readline().decode().rstrip("\n")
            if line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and data is not None:
                return data

    assert read_event()["type"] == "summary"
    deadline = time.time() + 5
    while hub.subscribers == 0 and time.time() < deadline:
        time.sleep(0.02)

    orders = [{"ItemTypeId": "T4_BAG", "LocationId": "3008", "QualityLevel": 1, "UnitPriceSilver": p * 10000, "AuctionType": "offer"}
              for p in (950, 960, 970)]
    body = json.dumps({"Orders": orders}).encode()

    def post() -> None:
        pc = HTTPConnection("127.0.0.1", port, timeout=10)
        pc.request("POST", f"/api/ingest/{IK}/marketorders.ingest", body=body, headers={"Content-Type": "application/json"})
        assert pc.getresponse().status == 200
        pc.close()

    threading.Thread(target=post).start()
    card = read_event()
    assert card["type"] == "item" and card["item_id"] == "T4_BAG" and card["orders"] == 3
    assert card["seen"]["sell_min"] == 950 and card["seen"]["verdict"] == "cheap"
    c.close()
