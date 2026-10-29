"""Tests for the linux-server branch: robust pricing, storage, rate limiting, refining fix, server auth and ingest."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection

import pytest

from albion_flips.analyzer import analyze_crafting, analyze_flips, references_from_history
from albion_flips.api import OfflineClient
from albion_flips.config import AppConfig, ConfigError, ServiceConfig
from albion_flips.models import HistoryPoint, HistoryRecord, PriceRecord
from albion_flips.ratelimit import RateLimiter
from albion_flips.robust import Reference, check_buy_price, check_sell_price, units_per_day
from albion_flips.server import Auth, Poller, make_handler, merge_prices, split_ingest_path
from albion_flips.storage import MarketStore
from albion_flips.weights import get_item_value
from albion_flips.web import FlipDataStore, start_web_server

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)


def rec(item: str, city: str, sell: int = 0, buy: int = 0, minutes_ago: float = 10, q: int = 1) -> PriceRecord:
    ts = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    return PriceRecord(item, city, q, sell, ts if sell else "", sell, ts if sell else "", buy, ts if buy else "", buy, ts if buy else "")


def hist(item: str, city: str, price: int, count: int, days: int = 10, q: int = 1) -> HistoryRecord:
    pts = [HistoryPoint(count, price, (NOW - timedelta(days=d)).strftime("%Y-%m-%dT00:00:00")) for d in range(0, days + 1)]
    return HistoryRecord(city, item, q, pts)


# ------------------------------------------------------------------ robust.py
def test_bait_buy_price_is_replaced_by_normal_price() -> None:
    ref = Reference(avg7=1500)
    c = check_buy_price(1011, ref)
    assert c.adjusted and c.reason == "bait" and c.price == pytest.approx(1425)  # 95 % of 1500


def test_normal_buy_price_is_kept() -> None:
    c = check_buy_price(1400, Reference(avg7=1500))
    assert not c.adjusted and c.price == 1400


def test_inverted_order_book_detected_within_one_hour() -> None:
    c = check_buy_price(1011, Reference(avg7=1050), live_buy_max=1024, seconds_between=0)
    assert c.reason == "inverted_book" and c.price == pytest.approx(997.5 if 1050 * 0.95 > 1011 else 1011)
    # same prices but seen 5 hours apart: not a contradiction
    assert not check_buy_price(1011, Reference(avg7=1050), live_buy_max=1024, seconds_between=5 * 3600).adjusted
    # no reference at all: fall back to the (higher) buy order
    c2 = check_buy_price(1011, None, live_buy_max=1024, seconds_between=0)
    assert c2.price == 1024 and c2.reason == "inverted_book"


def test_sell_price_capped_at_110_percent() -> None:
    c = check_sell_price(2000, Reference(avg7=1000))
    assert c.adjusted and c.price == pytest.approx(1100)
    assert not check_sell_price(1050, Reference(avg7=1000)).adjusted
    assert not check_sell_price(5000, None).adjusted


def test_units_per_day_takes_the_tightest_limit() -> None:
    assert units_per_day(1000) == pytest.approx(100)                                   # 10 % market share
    assert units_per_day(1000, capital=20000, unit_cost=500) == pytest.approx(40)      # capital
    assert units_per_day(1000, units_per_trip=30, trips_per_day=1) == pytest.approx(30)  # mount
    assert units_per_day(None) == 0 and units_per_day(0) == 0


# ------------------------------------------------------------------ analyzer integration
def test_bait_flip_from_live_data_is_corrected() -> None:
    """Real case from 2026-10-29: T6 stone block 1011 in Lymhurst (avg 1502) looked like a 1M/day flip."""
    cfg = AppConfig(cities=["Lymhurst", "Fort Sterling"], qualities=[1], min_profit_silver=0, min_margin_pct=0, min_daily_volume=0)
    prices = [rec("T6_STONEBLOCK", "Lymhurst", sell=1011, buy=1024), rec("T6_STONEBLOCK", "Fort Sterling", sell=1727, buy=1353)]
    history = [hist("T6_STONEBLOCK", "Lymhurst", 1502, 22724), hist("T6_STONEBLOCK", "Fort Sterling", 1658, 22819)]
    flips = analyze_flips(prices, history, cfg, now=NOW)
    f = [x for x in flips if x.buy_city == "Lymhurst" and x.sell_city == "Fort Sterling"][0]
    assert f.raw_buy_price == 1011
    assert f.buy_price >= 1426  # 95 % of the 7-day average, not the bait price
    assert "bait" in f.price_note
    assert f.profit_per_item < 300  # was ~600 with the bait price
    assert f.units_per_day == pytest.approx(22819 * 0.10, rel=0.01)

    off = AppConfig(cities=cfg.cities, qualities=[1], min_profit_silver=0, min_margin_pct=0, min_daily_volume=0, robust_prices=False)
    raw = [x for x in analyze_flips(prices, history, off, now=NOW) if x.buy_city == "Lymhurst" and x.sell_city == "Fort Sterling"][0]
    assert raw.buy_price == 1011 and raw.price_note == ""


def test_destination_without_history_is_capped_by_other_cities() -> None:
    """Live case: rarely traded enchanted gear listed for millions in a city with no history."""
    cfg = AppConfig(cities=["Thetford", "Bridgewatch", "Martlock"], qualities=[1], min_profit_silver=0,
                    min_margin_pct=0, min_daily_volume=0, max_margin_pct=10_000)
    item = "T7_MAIN_SPEAR@3"
    prices = [rec(item, "Thetford", sell=4_000_000), rec(item, "Bridgewatch", sell=15_000_000)]
    history = [hist(item, "Thetford", 4_100_000, 3), hist(item, "Martlock", 4_300_000, 2)]  # none for Bridgewatch
    f = [x for x in analyze_flips(prices, history, cfg, now=NOW) if x.sell_city == "Bridgewatch"][0]
    assert f.price_note == "sell_cap_cross"
    assert f.sell_price == pytest.approx(1.5 * 4_200_000, rel=0.001)  # median of 4.1M and 4.3M, x 1.5
    assert f.profit_per_item < 3_000_000  # instead of ~10M


def test_flip_without_any_history_is_flagged() -> None:
    cfg = AppConfig(cities=["Thetford", "Bridgewatch"], qualities=[1], min_profit_silver=0, min_margin_pct=0, min_daily_volume=0)
    prices = [rec("T4_BAG", "Thetford", sell=1000), rec("T4_BAG", "Bridgewatch", sell=1500)]
    f = analyze_flips(prices, [], cfg, now=NOW)[0]
    assert f.price_note == "no_history" and f.sell_price == 1500


def test_dashboard_keeps_verified_flips_when_cutting_the_list() -> None:
    from albion_flips.models import ExitType, FlipOpportunity
    store = FlipDataStore()
    def flip(i: int, missing: bool, profit: float) -> FlipOpportunity:
        return FlipOpportunity(f"T4_X{i}", "Thetford", "Martlock", 100, 200, ExitType.SELL_ORDER, profit, 50, 0,
                               None if missing else 100.0, 5, "low", history_missing=missing)
    opps = [flip(i, True, 1_000_000 + i) for i in range(2600)] + [flip(9999, False, 500)]
    store.update("europe", "now", opps, now=NOW, config=AppConfig())
    ids = {f["item_id"] for f in store.get_data()["flips"]}
    assert "T4_X9999" in ids and len(ids) == 2500


def test_references_from_history_exclude_today() -> None:
    h = HistoryRecord("Martlock", "T4_BAG", 1, [
        HistoryPoint(100, 5000, (NOW - timedelta(days=0)).strftime("%Y-%m-%dT00:00:00")),  # today: ignored
        HistoryPoint(10, 1000, (NOW - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")),
        HistoryPoint(30, 2000, (NOW - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")),
    ])
    r = references_from_history([h], NOW)[("T4_BAG", "Martlock", 1)]
    assert r.avg7 == pytest.approx((10 * 1000 + 30 * 2000) / 40)
    assert r.vol7 == pytest.approx(40 / 7)


# ------------------------------------------------------------------ refining fixes
def _ref_prices(enchant: bool) -> list[PriceRecord]:
    e = "_LEVEL1@1" if enchant else ""
    return [
        rec(f"T5_ORE{e}", "Thetford", sell=400),
        rec("T4_METALBAR", "Thetford", sell=300),          # plain previous tier (cheap)
        rec("T4_METALBAR_LEVEL1@1", "Thetford", sell=900),  # enchanted previous tier (what the recipe really needs)
        rec(f"T5_METALBAR{e}", "Martlock", sell=5000),
    ]


def test_enchanted_refining_uses_enchanted_previous_tier() -> None:
    cfg = AppConfig(cities=["Thetford", "Martlock"], qualities=[1])
    crafts = analyze_crafting(_ref_prices(True), cfg, focus=False, now=NOW)
    c = [x for x in crafts if x.item_id == "T5_METALBAR_LEVEL1@1"][0]
    assert "T4_METALBAR_LEVEL1@1" in c.ingredients_desc
    assert c.material_cost == pytest.approx(3 * 400 + 900)


def test_refining_skips_missing_previous_tier_instead_of_inventing_a_price() -> None:
    cfg = AppConfig(cities=["Thetford", "Martlock"], qualities=[1])
    prices = [rec("T5_ORE", "Thetford", sell=400), rec("T5_METALBAR", "Martlock", sell=5000)]  # no T4 bar price at all
    assert not [x for x in analyze_crafting(prices, cfg, focus=False, now=NOW) if x.item_id == "T5_METALBAR"]


def test_crafting_ignores_stale_prices() -> None:
    cfg = AppConfig(cities=["Thetford", "Martlock"], qualities=[1], max_price_age_minutes=120)
    prices = _ref_prices(False)
    prices[-1] = rec("T5_METALBAR", "Martlock", sell=5000, minutes_ago=600)  # 10 h old sell price
    assert not [x for x in analyze_crafting(prices, cfg, focus=False, now=NOW) if x.item_id == "T5_METALBAR"]


def test_refined_item_values_match_game_data() -> None:
    assert [get_item_value(f"T{t}_METALBAR") for t in range(3, 9)] == [8, 16, 32, 64, 128, 256]
    assert get_item_value("T6_PLANKS@1") == 128


# ------------------------------------------------------------------ rate limiter
def test_rate_limiter_waits_when_window_is_full() -> None:
    clock = [0.0]
    slept: list[float] = []
    rl = RateLimiter(windows=((3, 60.0),), clock=lambda: clock[0], sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
    for _ in range(3):
        assert rl.acquire() == 0
    clock[0] = 10.0
    waited = rl.acquire()
    assert waited == pytest.approx(50.0)  # first request at t=0 leaves the 60 s window at t=60


# ------------------------------------------------------------------ storage
def test_storage_only_writes_changes_and_keeps_newest(tmp_path) -> None:
    db = MarketStore(tmp_path / "m.db")
    assert db.record_prices([rec("T4_ORE", "Thetford", sell=100, buy=90, minutes_ago=30)]) == 1
    assert db.record_prices([rec("T4_ORE", "Thetford", sell=100, buy=90, minutes_ago=20)]) == 0  # same price
    assert db.record_prices([rec("T4_ORE", "Thetford", sell=120, buy=90, minutes_ago=5)], "live") == 1
    assert db.record_prices([rec("T4_ORE", "Thetford", sell=100, buy=90, minutes_ago=60)]) == 0  # older API data
    latest = {p.item_id: p for p in db.load_latest()}
    assert latest["T4_ORE"].sell_price_min == 120
    assert db.stats()["samples"] == 2
    db.close()


def test_storage_references_and_prune(tmp_path) -> None:
    db = MarketStore(tmp_path / "m.db")
    db.record_history([hist("T4_BAG", "Martlock", 2000, 50, days=40)])
    r = db.references(NOW)[("T4_BAG", "Martlock", 1)]
    assert r.avg7 == pytest.approx(2000) and r.vol7 == pytest.approx(50)
    db.prune(NOW, raw_days=7, daily_days=20)
    assert db.stats()["daily"] <= 21
    db.close()


# ------------------------------------------------------------------ server helpers
def test_split_ingest_path() -> None:
    assert split_ingest_path("/api/ingest/abc123/marketorders.ingest") == ("abc123", "/api/ingest/marketorders.ingest")
    assert split_ingest_path("/api/ingest/marketorders.ingest") == (None, "/api/ingest/marketorders.ingest")
    assert split_ingest_path("/api/ingest") == (None, "/api/ingest")


def test_merge_keeps_newer_live_price() -> None:
    api = [rec("T4_ORE", "Thetford", sell=100, buy=80, minutes_ago=30)]
    live = [rec("T4_ORE", "Thetford", sell=110, buy=0, minutes_ago=1)]
    m = merge_prices(api, live)[0]
    assert m.sell_price_min == 110 and m.buy_price_max == 80


def test_service_keys_must_be_long_enough() -> None:
    with pytest.raises(ConfigError):
        AppConfig(service=ServiceConfig(access_key="short")).validate()


# ------------------------------------------------------------------ HTTP server
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def server():
    store = FlipDataStore()
    store.last_config = AppConfig()
    auth = Auth(access_key="A" * 32, ingest_key="I" * 32, public_read=False, allow_local_ingest=False)
    port = _free_port()
    httpd = start_web_server(store, port=port, host="127.0.0.1", handler_cls=make_handler(auth, lambda: {"ok": 1}))
    yield port, store, auth
    httpd.shutdown()


def _req(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict(r.getheaders()), data


ORDER = json.dumps({"Orders": [{"ItemTypeId": "T4_BAG", "LocationId": "3008", "QualityLevel": 1,
                                "UnitPriceSilver": 12340000, "AuctionType": "offer"}]}).encode()


def test_dashboard_requires_login(server) -> None:
    port, _, auth = server
    assert _req(port, "GET", "/")[0] == 302
    assert _req(port, "GET", "/api/flips")[0] == 401
    assert _req(port, "GET", "/healthz")[0] == 200
    st, h, _ = _req(port, "GET", "/login?k=" + "A" * 32)
    assert st == 302 and "amp_auth=" in h["Set-Cookie"] and "HttpOnly" in h["Set-Cookie"]
    cookie = h["Set-Cookie"].split(";")[0]
    assert _req(port, "GET", "/api/flips", headers={"Cookie": cookie})[0] == 200
    assert _req(port, "GET", "/api/server_status", headers={"Cookie": cookie})[0] == 200
    assert _req(port, "GET", "/login?k=wrong")[0] == 401


def test_changes_require_login_even_with_public_read(server) -> None:
    port, _, auth = server
    auth.public_read = True
    assert _req(port, "GET", "/api/flips")[0] == 200
    body = json.dumps({"item_id": "T4_BAG", "city": "Martlock", "price": 1}).encode()
    assert _req(port, "POST", "/api/override", body, {"Content-Type": "application/json"})[0] == 401
    assert _req(port, "GET", "/api/refresh")[0] == 401


def test_ingest_needs_the_key(server) -> None:
    port, store, _ = server
    assert _req(port, "POST", "/api/ingest/marketorders.ingest", ORDER)[0] == 401
    assert _req(port, "POST", "/api/ingest/" + "x" * 32 + "/marketorders.ingest", ORDER)[0] == 401
    st, _, _ = _req(port, "POST", "/api/ingest/" + "I" * 32 + "/marketorders.ingest", ORDER)
    assert st == 200
    assert any(p.item_id == "T4_BAG" and p.sell_price_min == 1234 for p in store.last_prices)


def test_proxied_requests_are_never_local() -> None:
    store = FlipDataStore()
    store.last_config = AppConfig()
    auth = Auth(access_key="A" * 32, ingest_key="I" * 32, allow_local_ingest=True)
    port = _free_port()
    httpd = start_web_server(store, port=port, host="127.0.0.1", handler_cls=make_handler(auth, dict))
    try:
        assert _req(port, "POST", "/api/ingest/marketorders.ingest", ORDER)[0] == 200  # Data Client on the same machine
        assert _req(port, "POST", "/api/ingest/marketorders.ingest", ORDER, {"X-Forwarded-For": "8.8.8.8"})[0] == 401
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ one full server sweep
def test_poller_cycle_with_fixture_data(tmp_path) -> None:
    fixture_now = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)
    cfg = AppConfig(cities=["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford"], qualities=[1],
                    min_profit_silver=100, min_daily_volume=50)
    market = MarketStore(tmp_path / "m.db")
    store = FlipDataStore()
    store.last_config = cfg
    poller = Poller(cfg, ["T4_PLANKS", "T4_METALBAR", "T4_LEATHER"], OfflineClient("tests/fixtures"), market, store, now_fn=lambda: fixture_now)
    n = poller.cycle()
    assert n >= 1
    assert store.get_data()["flips"][0]["item_id"] == "T4_PLANKS"
    assert market.stats()["latest"] > 0 and market.stats()["daily"] > 0
    assert poller.status()["cycles"] == 1
    market.close()
