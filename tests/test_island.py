"""Island farming, animals and gathering calculator."""
from __future__ import annotations

import pytest

from albion_flips import island
from albion_flips.robust import Reference

from test_insights import _req, _store, market  # noqa: F401  (fixtures)
from test_orders import server  # noqa: F401  (fixture)

DATA = {
    "plants": [
        {"seed": "T3_FARM_WHEAT_SEED", "crop": "T3_WHEAT", "kind": "crop", "tier": 3, "grow_s": 79200,
         "seed_chance": 0.6, "water_bonus": 0.8, "focus": 1000, "nutrition": 48},
        {"seed": "T1_FARM_CARROT_SEED", "crop": "T1_CARROT", "kind": "crop", "tier": 1, "grow_s": 79200,
         "seed_chance": 0, "water_bonus": 2, "focus": 1000, "nutrition": 48},
    ],
    "animals": [
        {"baby": "T3_FARM_CHICKEN_BABY", "grown": "T3_FARM_CHICKEN_GROWN", "tier": 3, "grow_s": 158400,
         "offspring": 0.6, "focus_bonus": 0.8, "focus": 1000, "food": "plants", "nutrition": 864,
         "favorite": "T3_WHEAT", "favorite_bonus": 1},
        {"baby": "T5_FARM_COUGAR_BABY", "grown": "T5_FARM_COUGAR_GROWN", "tier": 5, "grow_s": 504000,
         "offspring": 0, "focus_bonus": 0.1, "focus": 1000, "food": "meat", "nutrition": 1488,
         "favorite": None, "favorite_bonus": 0},
    ],
    "fish": ["T3_FISH_FRESHWATER_ALL_COMMON"],
    "biome": {"Thetford": ["FIBER", "HIDE", "WOOD"]},
    "zones": [
        {"name": "Blue Fields", "tier": 4, "kind": "blue", "region": "Thetford", "resources": {"FIBER": [[4, 12], [3, 5]]}},
        {"name": "Yellow Bog", "tier": 4, "kind": "yellow", "region": "Thetford", "resources": {}},
        {"name": "Far Away", "tier": 4, "kind": "blue", "region": "Martlock", "resources": {"FIBER": [[4, 30]]}},
    ],
    "names": {},
}
TAX, SETUP = 0.04, 0.025


def settings(**kw):
    q = {k: [str(v)] for k, v in kw.items()}
    return island.parse_settings(q, ["Thetford", "Martlock", "Black Market"])


def prices(quotes, refs=None):
    return island.Prices(quotes, refs, ["Thetford", "Martlock"], TAX, SETUP)


def test_settings_defaults_and_limits() -> None:
    s = settings()
    assert s["home"] == "Thetford" and s["markets"] == ["Thetford", "Martlock"] and s["seeds"] == 18
    assert s["crops_per_seed"] == 9.0 and s["water"] is False and s["sell_mode"] == "instant"
    s = settings(**{"home": "Black Market", "seeds": 99999, "yield": "x", "rates": "1,2,abc", "fish_rate": "nan"})
    assert s["crops_per_seed"] == 9.0
    assert s["home"] == "Thetford"  # the Black Market is no place to farm for
    assert s["seeds"] == 500 and s["rates"][2] == 1 and s["rates"][3] == 2 and s["rates"][4] == 300
    assert s["fish_rate"] == 90
    assert island.parse_settings({}, ["Thetford"], premium=False)["crops_per_seed"] == 4.5


def test_crop_profit_dry_and_watered() -> None:
    p = prices([("T3_FARM_WHEAT_SEED", "Thetford", 1, 1000, 0), ("T3_WHEAT", "Thetford", 1, 0, 200)])
    dry = island.crop_rows(DATA, p, settings())[0]
    # 9 crops x 192 after tax - seed 1000 x (1 - 0.6)
    assert dry["crop_net"] == 192 and dry["profit_per_seed"] == 1328 and dry["profit_day"] == 1328 * 18
    assert dry["seed_back"] == 0.6 and dry["focus_day"] == 0
    wet = island.crop_rows(DATA, p, settings(water=1))[0]
    # watered: 140 % seed back, the extra 0.4 seed is worth its price
    assert wet["seed_back"] == 1.4 and wet["profit_per_seed"] == 1728 + 400 and wet["focus_day"] == 18000
    assert wet["water_per_10k_focus"] == 8000  # 800 silver more per 1000 focus
    carrot = island.crop_rows(DATA, p, settings())[1]
    assert carrot["missing"] == "no crop price"


def test_bait_seed_price_is_not_trusted() -> None:
    refs = {("T3_FARM_WHEAT_SEED", "Thetford", 1): Reference(avg7=2000, vol7=50)}
    p = prices([("T3_FARM_WHEAT_SEED", "Thetford", 1, 100, 0), ("T3_WHEAT", "Thetford", 1, 0, 200)], refs)
    row = island.crop_rows(DATA, p, settings())[0]
    assert row["seed_price"] == 1900  # 95 % of the 7-day average, not the 100 silver bait


def test_sell_order_mode_and_best_city() -> None:
    p = prices([("T3_FARM_WHEAT_SEED", "Thetford", 1, 1000, 0),
                ("T3_WHEAT", "Thetford", 1, 301, 200), ("T3_WHEAT", "Martlock", 1, 0, 250)])
    inst = island.crop_rows(DATA, p, settings())[0]
    assert inst["sell_city"] == "Martlock" and inst["crop_net"] == 240 and inst["home_net"] == 192
    order = island.crop_rows(DATA, p, settings(mode="order"))[0]
    assert order["sell_city"] == "Thetford" and order["crop_net"] == round(300 * (1 - TAX - SETUP))


def test_animal_profit_with_favourite_food() -> None:
    p = prices([("T3_FARM_CHICKEN_BABY", "Thetford", 1, 5000, 4000), ("T3_FARM_CHICKEN_GROWN", "Thetford", 1, 0, 10000),
                ("T3_WHEAT", "Thetford", 1, 100, 0), ("T1_CARROT", "Thetford", 1, 60, 0),
                ("T5_FARM_COUGAR_BABY", "Thetford", 1, 50000, 0), ("T5_FARM_COUGAR_GROWN", "Thetford", 1, 0, 90000)])
    chicken, cougar = island.animal_rows(DATA, p, settings())
    # wheat is its favourite: 864 / (48 x 2) = 9 wheat for 900, cheaper than 18 carrots for 1080
    assert chicken["food"]["id"] == "T3_WHEAT" and chicken["food"]["units"] == 9 and chicken["feed_cost"] == 900
    # 9600 grown + 60 % of a 5000 baby - 5000 - 900
    assert chicken["offspring_value"] == 3000 and chicken["profit"] == 6700
    assert chicken["profit_day_per_spot"] == round(6700 / (158400 / 86400))
    focus = island.animal_rows(DATA, p, settings(afocus=1))[0]
    # 140 %: one baby saved plus a 0.4 chance of a second one, sold instantly for 4000 x 0.96
    assert focus["offspring"] == 1.4 and focus["offspring_value"] == round(5000 + 0.4 * 3840)
    assert cougar["feed_unknown"] is True and cougar["feed_cost"] is None


def test_gathering_rows_and_zones() -> None:
    p = prices([("T4_FIBER", "Thetford", 1, 0, 100), ("T4_FIBER", "Martlock", 1, 0, 150), ("T4_ORE", "Martlock", 1, 0, 400)])
    rows = {r["id"]: r for r in island.gathering_rows(DATA, p, settings(rates="600,450,300"))}
    fiber, ore = rows["T4_FIBER"], rows["T4_ORE"]
    assert fiber["rate"] == 300 and fiber["per_hour_home"] == 300 * 96 and fiber["per_hour_best"] == 300 * 144
    assert [z["name"] for z in fiber["zones"]] == ["Blue Fields", "Yellow Bog"] and fiber["zones"][0]["nodes"] == 12
    assert ore["per_hour_home"] is None and ore["in_biome"] is False and ore["zones"] == []


def test_real_data_file_is_complete() -> None:
    data = island.load_data()
    assert len(data["plants"]) >= 15 and len(data["animals"]) >= 20 and data["zones"]
    ids = island.island_items(data)
    assert "T3_FARM_WHEAT_SEED" in ids and "T8_FARM_COW_BABY" in ids and "T2_FIBER" in ids
    from albion_flips.config import load_items
    polled = set(load_items("data/items.json"))
    assert not [i for i in ids if i not in polled]


def test_http_island(server) -> None:  # noqa: F811
    port, _ = server
    st, d = _req(port, "GET", "/api/island?home=Martlock&seeds=9&water=1")
    assert st == 200 and d["settings"]["home"] == "Martlock" and d["settings"]["seeds"] == 9
    assert {"crops", "animals", "gathering", "food", "zones"} <= set(d)
    st, page = _req(port, "GET", "/")
    assert b"view-island" in page and b"tab-island" in page


@pytest.mark.parametrize("q", ["home=<script>", "seeds=-5&yield=abc", "rates=" + ",".join(["9" * 30] * 9)])
def test_http_island_bad_input(server, q) -> None:  # noqa: F811
    port, _ = server
    st, d = _req(port, "GET", "/api/island?" + q)
    assert st == 200 and d["settings"]["seeds"] >= 0 and all(v <= 5000 for v in d["settings"]["rates"].values())
