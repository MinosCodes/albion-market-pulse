"""Price history, item search and price alerts (server mode)."""
from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection

import pytest

from albion_flips.config import AppConfig
from albion_flips.insights import AlertBook, history_payload, quotes_from_orders, search_items, store_quotes
from albion_flips.models import HistoryPoint, HistoryRecord, PriceRecord
from albion_flips.server import Auth, Insights, attach_overlay, inject_server_ui, make_handler
from albion_flips.storage import MarketStore
from albion_flips.web import HTML_TEMPLATE, FlipDataStore, start_web_server

NOW = datetime.now(timezone.utc)
AK, IK = "A" * 32, "I" * 32


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def rec(item: str, city: str, sell: int = 0, buy: int = 0, minutes_ago: float = 5, q: int = 1) -> PriceRecord:
    d = iso(NOW - timedelta(minutes=minutes_ago))
    return PriceRecord(item, city, q, sell, d if sell else "", sell, d if sell else "", buy, d if buy else "", buy, d if buy else "")


# ------------------------------------------------------------ search
def test_search_finds_by_name_tier_and_enchant() -> None:
    ids = ["T4_BAG", "T5_BAG", "T4_BAG@1", "T6_2H_BOW", "T6_2H_BOW@1", "T5_PLANKS"]
    assert search_items("bag", ids)[0]["id"] == "T4_BAG"
    assert [h["id"] for h in search_items("bag 5", ids)] == ["T5_BAG"]
    assert [h["id"] for h in search_items("6.1 bow", ids)] == ["T6_2H_BOW@1"]
    assert search_items("t5_planks", ids)[0]["id"] == "T5_PLANKS"
    assert search_items("", ids) == []


# ------------------------------------------------------------ history
@pytest.fixture()
def market(tmp_path):
    m = MarketStore(tmp_path / "market.db")
    days = [NOW - timedelta(days=d) for d in range(1, 11)]
    m.record_history([
        HistoryRecord("Martlock", "T4_BAG", 1, [HistoryPoint(10 + i, 1000 + 10 * i, iso(d)) for i, d in enumerate(days)]),
        HistoryRecord("Lymhurst", "T4_BAG", 1, [HistoryPoint(5, 1500, iso(days[0]))]),
    ])
    m.record_prices([rec("T4_BAG", "Martlock", sell=900, buy=800), rec("T4_BAG", "Lymhurst", sell=1600, minutes_ago=60 * 10)])
    yield m
    m.close()


def _store(market: MarketStore | None = None) -> FlipDataStore:
    s = FlipDataStore()
    s.last_config = AppConfig()
    s.last_prices = [rec("T4_BAG", "Martlock", sell=900, buy=800), rec("T4_BAG", "Black Market", buy=2000)]
    if market is not None:
        s.reference = market.references(NOW)
    return s


def test_history_payload_per_city(market) -> None:
    p = history_payload(market, _store(market), "T4_BAG", 1, days=30, now=NOW.timestamp())
    mart, lym = p["cities"]["Martlock"], p["cities"]["Lymhurst"]
    assert len(mart["daily"]) == 10 and mart["daily"][0][1] == 1090  # oldest first
    assert mart["sell_min"] == 900 and mart["buy_max"] == 800
    assert mart["avg7"] and mart["vs_avg_pct"] < 0
    assert mart["orders"] and mart["orders"][-1][1:] == [900, 800]
    assert lym["sell_min"] == 0  # 10 hours old: not shown as current
    assert p["days"] == 30 and history_payload(market, _store(), "T4_BAG", 1, days=999)["days"] == 30


def test_history_period_limits_points(market) -> None:
    p = history_payload(market, _store(market), "T4_BAG", 1, days=7, now=NOW.timestamp())
    assert len(p["cities"]["Martlock"]["daily"]) <= 8


# ------------------------------------------------------------ alerts
def test_alert_validation() -> None:
    cities = ["Martlock", "Black Market"]
    ok = AlertBook.validate({"item_id": "t4_bag", "kind": "sell_below", "price": "1000", "city": "Martlock"}, cities)
    assert ok["item_id"] == "T4_BAG" and ok["price"] == 1000 and ok["quality"] == 1
    for bad in ({"item_id": "x;drop", "kind": "sell_below", "price": 1},
                {"item_id": "T4_BAG", "kind": "steal", "price": 1},
                {"item_id": "T4_BAG", "kind": "sell_below", "price": 0},
                {"item_id": "T4_BAG", "kind": "sell_below", "price": 5, "city": "Atlantis"},
                {"item_id": "T4_BAG", "kind": "sell_below", "price": 5, "quality": 9}):
        with pytest.raises(ValueError):
            AlertBook.validate(bad, cities)


def test_alert_fires_once_and_rearms(tmp_path) -> None:
    book = AlertBook(tmp_path / "alerts.json")
    book.add({"item_id": "T4_BAG", "quality": 1, "city": "", "kind": "sell_below", "price": 1000, "note": ""})
    assert book.check([("T4_BAG", "Martlock", 1, 1200, 0)]) == []
    fired = book.check([("T4_BAG", "Martlock", 1, 950, 0), ("T4_BAG", "Lymhurst", 1, 900, 0)])
    assert len(fired) == 1 and fired[0]["price"] == 900 and fired[0]["city"] == "Lymhurst"
    assert book.check([("T4_BAG", "Martlock", 1, 950, 0)]) == []  # still low: no repeat
    assert book.check([("T5_BAG", "Martlock", 1, 1, 0)]) == []    # other item: state unchanged
    book.check([("T4_BAG", "Martlock", 1, 1300, 0)])              # recovered: armed again
    assert len(book.check([("T4_BAG", "Martlock", 1, 990, 0)])) == 1
    # persisted
    again = AlertBook(tmp_path / "alerts.json")
    assert len(again.rules) == 1 and again.rules[0]["fired_count"] == 2 and len(again.events) == 2


def test_buy_above_and_city_filter(tmp_path) -> None:
    book = AlertBook()
    book.add({"item_id": "T4_BAG", "quality": 1, "city": "Black Market", "kind": "buy_above", "price": 1800, "note": ""})
    assert book.check([("T4_BAG", "Martlock", 1, 0, 5000)]) == []
    fired = book.check([("T4_BAG", "Black Market", 1, 0, 2000)])
    assert fired and fired[0]["price"] == 2000


def test_quotes_skip_stale_prices() -> None:
    s = _store()
    s.last_prices.append(rec("T4_BAG", "Lymhurst", sell=10, minutes_ago=60 * 24))
    q = {(c, sell) for _, c, _, sell, _ in store_quotes(s)}
    assert ("Lymhurst", 0) in q and ("Martlock", 900) in q
    assert quotes_from_orders({("T4_BAG", "Martlock", 1): {"min_sell": 5, "max_buy": 0, "n": 3}}) == [("T4_BAG", "Martlock", 1, 5, 0)]


# ------------------------------------------------------------ dashboard page
def test_server_ui_is_injected() -> None:
    html = inject_server_ui(HTML_TEMPLATE)
    assert 'id="tab-history"' in html and 'id="view-alerts"' in html and "openPriceHistory" in html
    assert html.index('id="tab-history"') < html.index('id="tab-prices"')
    assert inject_server_ui("<html>changed</html>") == "<html>changed</html>"


# ------------------------------------------------------------ HTTP
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def server(market, tmp_path):
    store = _store(market)
    auth = Auth(access_key=AK, ingest_key=IK, public_read=True, allow_local_ingest=False)
    ins = Insights(market, store, AlertBook(tmp_path / "a.json"), ["Martlock", "Lymhurst"], ["T4_BAG", "T5_BAG"])
    hub = attach_overlay(store, None, ins)
    port = _free_port()
    httpd = start_web_server(store, port=port, host="127.0.0.1", handler_cls=make_handler(auth, dict, hub, store, ins))
    yield port, ins, hub
    httpd.shutdown()


def _req(port: int, method: str, path: str, body: dict | None = None, cookie: str = ""):
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data and r.getheader("Content-Type", "").startswith("application/json") else data)


def _login(port: int) -> str:
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/login?k=" + AK)
    cookie = c.getresponse().getheader("Set-Cookie").split(";")[0]
    c.close()
    return cookie


def test_http_history_search_and_page(server) -> None:
    port, _, _ = server
    st, d = _req(port, "GET", "/api/items?q=bag")
    assert st == 200 and d["items"][0]["id"] == "T4_BAG"
    st, d = _req(port, "GET", "/api/history?id=T4_BAG&q=1&days=7")
    assert st == 200 and "Martlock" in d["cities"]
    assert _req(port, "GET", "/api/history?id=<script>")[0] == 400
    st, html = _req(port, "GET", "/")
    assert st == 200 and b'id="tab-history"' in html


def test_http_alerts_need_login_to_change(server) -> None:
    port, ins, _ = server
    rule = {"item_id": "T4_BAG", "kind": "sell_below", "price": 950, "city": "Martlock"}
    assert _req(port, "POST", "/api/alerts", rule)[0] == 401
    st, d = _req(port, "GET", "/api/alerts")
    assert st == 200 and d["rules"] == [] and d["can_edit"] is False

    cookie = _login(port)
    st, d = _req(port, "POST", "/api/alerts", rule, cookie)
    assert st == 200 and d["rule"]["item_id"] == "T4_BAG"
    # the current Martlock price (900) already matches: it fires right away
    st, d = _req(port, "GET", "/api/alerts", cookie=cookie)
    assert d["can_edit"] is True and len(d["events"]) == 1 and d["events"][0]["price"] == 900
    assert _req(port, "POST", "/api/alerts", {"item_id": "T4_BAG", "kind": "x", "price": 1}, cookie)[0] == 400
    assert _req(port, "POST", "/api/alerts/delete", {"id": d["rules"][0]["id"]}, cookie)[1]["ok"] is True
    assert ins.alerts.rules == []


def test_live_upload_triggers_alert(server) -> None:
    port, ins, _ = server
    ins.alerts.add({"item_id": "T5_BAG", "quality": 1, "city": "", "kind": "sell_below", "price": 5000, "note": ""})
    orders = [{"ItemTypeId": "T5_BAG", "LocationId": "3008", "QualityLevel": 1, "UnitPriceSilver": 4500 * 10000, "AuctionType": "offer"}]
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", f"/api/ingest/{IK}/marketorders.ingest", body=json.dumps({"Orders": orders}).encode(), headers={"Content-Type": "application/json"})
    assert c.getresponse().status == 200
    c.close()
    assert ins.alerts.events and ins.alerts.events[0]["price"] == 4500 and ins.alerts.events[0]["item_id"] == "T5_BAG"
