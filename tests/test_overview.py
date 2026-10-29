"""Overview: one ranking across transport, refining, crafting, island and gathering."""
from __future__ import annotations

from albion_flips import overview, player
from albion_flips.robust import Reference

from test_insights import _login, _req, _store, market  # noqa: F401  (fixtures)
from test_orders import server  # noqa: F401  (fixture)

CITIES = ["Thetford", "Martlock", "Lymhurst", "Caerleon", "Black Market"]


def _prof(**kw):
    return player.validate(kw, CITIES)


FLIPS = [
    {"item_id": "T4_BAG", "item_name": "Bag", "buy_city": "Thetford", "sell_city": "Martlock", "buy_price": 1000,
     "sell_price": 1500, "profit_per_item": 400, "est_daily_profit": 20000, "units_per_day": 50},
    {"item_id": "T5_BAG", "item_name": "Big Bag", "buy_city": "Thetford", "sell_city": "Caerleon",
     "profit_per_item": 900, "est_daily_profit": 90000, "units_per_day": 100},
    {"item_id": "T6_BAG", "buy_city": "Thetford", "sell_city": "Martlock", "profit_per_item": 5000,
     "est_daily_profit": 500000, "history_missing": True},
]
BM = [{"item_id": "T4_2H_BOW", "item_name": "Bow", "buy_city": "Martlock", "sell_city": "Black Market",
       "profit_per_item": 2000, "est_daily_profit": 40000, "units_per_day": 20}]
CRAFT = [
    {"item_id": "T4_CLOTH", "item_name": "Cloth", "craft_city": "Lymhurst", "sell_city": "Martlock", "profit_per_item": 30,
     "effective_cost": 200, "sell_price": 260, "ingredients_desc": "2x T4_FIBER + 1x T3_CLOTH"},
    {"item_id": "T6_CLOTH", "item_name": "Fine Cloth", "craft_city": "Lymhurst", "sell_city": "Martlock", "profit_per_item": 500},
    {"item_id": "T4_SHOES", "item_name": "Shoes", "craft_city": "Thetford", "sell_city": "Thetford", "profit_per_item": 800},
]
REFS = {("T4_CLOTH", "Martlock", 1): Reference(avg7=250, vol7=10000), ("T6_CLOTH", "Martlock", 1): Reference(avg7=900, vol7=1000),
        ("T4_SHOES", "Thetford", 1): Reference(avg7=5000, vol7=5)}  # 0.5 shoes a day: not a daily income
ISLAND = {
    "crops": [{"crop": "T3_WHEAT", "name": "Wheat", "kind": "crop", "tier": 3, "made_day": 162, "profit_day": 30000,
               "profit_per_seed": 1600, "seed_price": 5000, "seed_city": "Thetford", "grow_h": 22, "seed_back": 0.6,
               "sell_city": "Thetford", "crop_net": 450, "volume": 40000}],
    "animals": [],
    "gathering": [
        {"id": "T4_FIBER", "name": "Hemp", "type": "FIBER", "tier": 4, "rate": 300, "home_net": 90, "per_hour_home": 27000,
         "zones": [{"name": "Dewleaf Fen"}], "volume": 100000},
        {"id": "T6_FIBER", "name": "Cotton", "type": "FIBER", "tier": 6, "rate": 100, "home_net": 1000, "per_hour_home": 100000,
         "zones": [{"name": "Far Fen"}], "locked": 6},
        {"id": "T5_FIBER", "name": "Flax", "type": "FIBER", "tier": 5, "rate": 180, "home_net": 300, "per_hour_home": 54000, "zones": []},
    ],
}


def test_entries_follow_profile() -> None:
    p = _prof(refine={"CLOTH": 4}, hours_per_day=2)
    es = {e["id"]: e for e in overview.build_entries(p, FLIPS, BM, CRAFT, [], ISLAND, REFS)}
    bag = es["flip:T4_BAG:1:Thetford:Martlock"]
    assert bag["mine"] and bag["per_day"] == 20000 and 0.2 <= bag["active_h"] < 2 and bag["steps"]
    assert es["flip:T5_BAG:1:Thetford:Caerleon"]["why_not"] == "needs red zones"
    assert es["flip:T4_2H_BOW:1:Martlock:Black Market"]["why_not"] == "needs red zones"
    assert not any("T6_BAG" in k for k in es)                     # unchecked flips are no income
    cloth = es["craft:T4_CLOTH:"]
    assert cloth["mine"] and cloth["per_day"] == 30 * 1000 and cloth["kind"] == "Refining"  # 10 % of 10,000 a day
    assert es["craft:T6_CLOTH:"]["why_not"] == "needs T6 cloth"
    assert not any("T4_SHOES" in k for k in es)                    # hardly traded
    assert es["crop:T3_WHEAT"]["per_day"] == 30000
    hemp = es["gather:T4_FIBER"]
    assert hemp["per_day"] == 27000 * 2 and hemp["per_active_h"] == 27000
    assert es["gather:T6_FIBER"]["why_not"] == "needs T6 fiber"
    assert "gather:T5_FIBER" not in es                             # no zone near home
    risky = {e["id"]: e for e in overview.build_entries(_prof(safe_only=False), FLIPS, BM, CRAFT, [], ISLAND, REFS)}
    assert risky["flip:T5_BAG:1:Thetford:Caerleon"]["mine"]


def test_capital_limits_transport_and_crafting() -> None:
    p = _prof(refine={"CLOTH": 4}, capital=10_000)
    es = {e["id"]: e for e in overview.build_entries(p, FLIPS, BM, CRAFT, [], ISLAND, REFS)}
    bag = es["flip:T4_BAG:1:Thetford:Martlock"]
    assert bag["capital_limited"] and bag["per_day"] == 10 * 400 and bag["steps"][0].startswith("Buy 10 ×")
    cloth = es["craft:T4_CLOTH:"]
    assert cloth["capital_limited"] and cloth["per_day"] == 50 * 30  # 10,000 / 200 per item
    poor = {e["id"] for e in overview.build_entries(_prof(capital=0), FLIPS, BM, CRAFT, [], ISLAND, REFS)}
    # without silver nothing with a known price is left; the island does not need capital here
    assert "flip:T4_BAG:1:Thetford:Martlock" not in poor and "craft:T4_CLOTH:" not in poor and "crop:T3_WHEAT" in poor


def test_ranking_and_trend() -> None:
    p = _prof(refine={"CLOTH": 4})
    ov = overview.Overview()
    entries = overview.build_entries(p, FLIPS, BM, CRAFT, [], ISLAND, REFS)
    v = ov.build(p, entries, now=1000)
    assert v["best"]["id"] == "gather:T4_FIBER" and v["hidden"] == 4      # 27,000 x 3 h
    assert [e["per_day"] for e in v["rows"]] == sorted((e["per_day"] for e in v["rows"]), reverse=True)
    assert all(e["mine"] for e in v["rows"]) and v["rows"][0]["change_1h"] is None and v["up"] is None
    everything = ov.build(p, entries, all_rows=True, now=1000)
    assert everything["best"]["id"] == "gather:T6_FIBER" and everything["hidden"] == 0
    # an hour later the bag flip pays 5,000 more a day
    FLIPS2 = [dict(FLIPS[0], est_daily_profit=25000)] + FLIPS[1:]
    later = ov.build(p, overview.build_entries(p, FLIPS2, BM, CRAFT, [], ISLAND, REFS), now=1000 + 3600)
    bag = next(e for e in later["rows"] if e["id"].startswith("flip:T4_BAG"))
    assert bag["change_1h"] == 5000 and later["up"]["id"] == bag["id"] and later["down"] is None
    # points older than a day are dropped
    ov.build(p, entries, now=1000 + 3 * 86400)
    t = 1000 + 3 * 86400
    assert all(t - ts <= 86400 for pts in ov._hist.values() for ts, _ in pts)


def test_http_overview(server) -> None:  # noqa: F811
    port, ins = server
    st, d = _req(port, "GET", "/api/overview")
    assert st == 200 and {"rows", "best", "orders", "alerts_24h", "hidden"} <= set(d)
    assert d["orders"] == {"open": 0, "outbid": 0} and d["profile_saved"] is False
    st, d = _req(port, "GET", "/api/overview?all=1")
    assert st == 200 and d["all"] is True
    st, page = _req(port, "GET", "/")
    assert b"view-overview" in page and b"tab-overview" in page
