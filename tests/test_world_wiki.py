"""World map routes and the item wiki."""
from __future__ import annotations

import pytest

from albion_flips import wiki, world
from albion_flips.storage import MarketStore

from test_insights import NOW, _login, _req, _store, market, rec  # noqa: F401  (fixtures)
from test_orders import server  # noqa: F401  (fixture)


def _zone(name, kind, pos, exits, tier=4, **kw):
    return {"name": name, "kind": kind, "tier": tier, "pos": pos, "exits": exits, "region": kw.get("region"),
            "biome": None, "faction": None, "resources": kw.get("resources", {})}


# A -- B -- C (city)       A to C through B (blue) or through R (red, shorter) or K (black)
#  \-- R --/  \-- K
W = world._prepare({
    "cities": {"Town": "C", "Home": "A"},
    "zones": {
        "A": _zone("Home", "city", [0, 0], ["B", "R"]),
        "B": _zone("Blue Field", "blue", [1, 0], ["A", "C", "Y"], resources={"FIBER": [[4, 10], [3, 2]]}),
        "Y": _zone("Yellow Hill", "yellow", [1, 1], ["B", "C"], resources={"FIBER": [[4, 25]]}),
        "C": _zone("Town", "city", [2, 0], ["B"]),       # exit to R only listed on R's side
        "R": _zone("Red Pass", "red", [1, -1], ["A", "C"]),
        "K": _zone("Black Pit", "black", [3, 0], ["C"], resources={"FIBER": [[4, 99]]}),
    },
})


def test_route_avoids_red_and_black() -> None:
    r = world.route(W, "A", "C")
    assert r["path"] == ["A", "B", "C"] and r["minutes"] == round(2.5 + 1.25, 1) and r["kinds"] == {"blue": 1, "city": 1}
    assert world.route(W, "A", "K") is None  # black zones are never entered
    # with red zones allowed the red pass costs 1.5 x 2.5 = 3.75, more than blue 2.5: still the blue way
    assert world.route(W, "A", "C", allow_red=True)["path"] == ["A", "B", "C"]
    # a red zone you pick as destination is reachable
    assert world.route(W, "A", "R")["path"] == ["A", "R"]
    assert world.route(W, "A", "A")["minutes"] == 0
    assert world.route(W, "A", "nope") is None


def test_exits_work_both_ways() -> None:
    assert "R" in W["links"]["C"]


def test_tour_and_names() -> None:
    assert world.resolve(W, "Town") == "C" and world.resolve(W, "blue field") == "B" and world.resolve(W, "?") is None
    t = world.tour(W, ["Home", "Town", "Yellow Hill"])
    assert t["order"][0] == "A" and t["order"][-1] == "A" and len(t["legs"]) == 3
    one_way = world.tour(W, ["Home", "Town", "Yellow Hill"], round_trip=False)
    assert one_way["minutes"] < t["minutes"] and len(one_way["legs"]) == 2
    with pytest.raises(ValueError):
        world.tour(W, ["Home", "Atlantis"])
    with pytest.raises(ValueError):
        world.tour(W, [str(i) for i in range(9)])
    assert world.tour(W, ["Home"]) is None


def test_resource_zones_blue_and_yellow_only() -> None:
    zs = world.resource_zones(W, "FIBER", 4)
    assert [z["id"] for z in zs] == ["Y", "B"] and zs[0]["nodes"] == 25


def test_real_world_file() -> None:
    w = world.load_world()
    assert len(w["zones"]) > 400 and set(w["cities"]) >= {"Thetford", "Martlock", "Caerleon"}
    safe = world.travel_minutes(w, "Thetford", "Martlock")
    assert safe and 5 < safe < 60
    assert world.travel_minutes(w, "Thetford", "Caerleon") is None  # only through red zones
    assert world.travel_minutes(w, "Thetford", "Caerleon", allow_red=True)


# ------------------------------------------------------------ wiki
def test_wiki_page_with_recipe_and_prices(tmp_path) -> None:
    m = MarketStore(tmp_path / "m.db")
    m.record_prices([rec("T4_PLANKS", "Martlock", sell=500, buy=400), rec("T4_PLANKS", "Thetford", sell=480),
                     rec("T3_PLANKS", "Thetford", sell=100), rec("T4_WOOD", "Thetford", sell=60)])
    data = {"items": {"T4_PLANKS": {"name": "Pine Planks", "name_de": "Kiefernbretter", "tier": 4, "weight": 1.0, "cat": "refined"}},
            "recipes": {"T4_PLANKS": {"materials": [["T4_WOOD", 2], ["T3_PLANKS", 1]], "focus": 54, "amount": 1},
                        "T4_2H_BOW": {"materials": [["T4_PLANKS", 32]], "focus": 0, "amount": 1}},
            "refining_bonus": {"PLANKS": "Fort Sterling"}}
    page = wiki.item_page("T4_PLANKS", 1, _store(), m, ["Martlock", "Thetford"], data=data, now=NOW.timestamp())
    assert page["name_de"] == "Kiefernbretter" and page["weight"] == 1.0
    assert {p["city"] for p in page["prices"]} == {"Martlock", "Thetford"}
    assert page["best_sale"]["city"] == "Martlock" and page["best_sale"]["net_instant"] == 384
    rc = page["recipe"]
    assert rc["cost"] == 2 * 60 + 100 and rc["profit_instant"] == 384 - 220
    assert [u["id"] for u in page["used_in"]] == ["T4_2H_BOW"]
    assert page["found"]["refine_city"] == "Fort Sterling"
    # enchanted planks: id with _LEVEL, recipe needs enchanted materials, which have no price here
    ench = wiki.item_page("T4_PLANKS_LEVEL1@1", 1, _store(), m, ["Martlock"], data=data, now=NOW.timestamp())
    assert ench["base_id"] == "T4_PLANKS" and ench["enchant"] == 1
    assert ench["recipe"]["materials"][1]["id"] == "T3_PLANKS_LEVEL1@1" and ench["recipe"]["cost"] is None
    m.close()


def test_wiki_raw_resource_and_farm_facts() -> None:
    page = wiki.item_page("T4_FIBER", 1, _store(), None, [])
    assert page["found"]["refine_into"] == "T4_CLOTH" and page["found"]["refine_city"] == "Lymhurst"
    seed = wiki.item_page("T3_FARM_WHEAT_SEED", 1, _store(), None, [])
    assert seed["found"]["farm"]["seed_chance_watered"] == 1.4
    baby = wiki.item_page("T3_FARM_CHICKEN_BABY", 1, _store(), None, [])
    assert baby["found"]["animal"]["favorite"] == "T3_WHEAT"
    assert wiki.item_page("T4_2H_BOW", 1, _store(), None, [])["recipe"]["materials"]


def test_http_world_route_tour_wiki(server) -> None:  # noqa: F811
    port, _ = server
    st, d = _req(port, "GET", "/api/world")
    assert st == 200 and len(d["zones"]) > 400 and "links" not in d
    st, d = _req(port, "GET", "/api/route?from=Thetford&to=Martlock")
    assert st == 200 and d["route"]["path"][0] == d["from"]
    assert _req(port, "GET", "/api/route?from=Thetford&to=Caerleon")[1]["route"] is None
    assert _req(port, "GET", "/api/route?from=Thetford&to=Caerleon&red=1")[1]["route"]
    assert _req(port, "GET", "/api/route?from=Thetford&to=Atlantis")[0] == 400
    st, d = _req(port, "GET", "/api/tour?stops=Thetford,Lymhurst,Martlock")
    assert st == 200 and len(d["tour"]["legs"]) == 3
    assert _req(port, "GET", "/api/tour?stops=" + ",".join(["Thetford"] + ["x"] * 3))[0] == 400
    st, d = _req(port, "GET", "/api/wiki?id=T4_BAG")
    assert st == 200 and d["prices"] and d["prices"][0]["city"] == "Martlock"
    assert _req(port, "GET", "/api/wiki?id=<x>")[0] == 400
    st, page = _req(port, "GET", "/")
    assert b"view-map" in page and b"view-wiki" in page
