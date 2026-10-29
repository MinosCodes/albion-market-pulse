"""Player profile: validation, storage, island defaults and suggestions."""
from __future__ import annotations

import pytest

from albion_flips import player

from test_insights import _login, _req, _store, market  # noqa: F401  (fixtures)
from test_orders import server  # noqa: F401  (fixture)

CITIES = ["Thetford", "Martlock", "Caerleon", "Black Market"]


def test_validate_fills_defaults_and_checks_ranges() -> None:
    p = player.validate({"name": "Engels", "home": "Martlock", "refine": {"CLOTH": 4}}, CITIES)
    assert p["name"] == "Engels" and p["home"] == "Martlock" and p["refine"]["CLOTH"] == 4
    assert p["refine"]["PLANKS"] == 3 and p["gather"]["FISH"] == 4  # untouched = default
    for bad in ({"home": "Black Market"}, {"home": "Atlantis"}, {"refine": {"CLOTH": 9}}, {"gather": {"ORE": "x"}},
                {"name": "<script>"}, {"island": {"farms": 99}}, {"craft": []}, {"focus_per_day": -1}):
        with pytest.raises(ValueError):
            player.validate(bad, CITIES)


def test_store_saves_and_loads(tmp_path) -> None:
    path = tmp_path / "profile.json"
    st = player.ProfileStore(path)
    assert st.saved is False and st.get()["home"] == "Thetford"
    st.set(player.validate({"home": "Martlock", "island": {"farms": 1, "herb_gardens": 1, "pastures": 3}}, CITIES))
    again = player.ProfileStore(path)
    assert again.saved and again.get()["home"] == "Martlock" and again.get()["island"]["pastures"] == 3
    path.write_text("{broken", encoding="utf-8")
    assert player.ProfileStore(path).saved is False  # a broken file falls back to defaults


def test_island_query_and_locks() -> None:
    p = player.validate({"premium": False, "island": {"farms": 2, "herb_gardens": 1, "pastures": 3},
                         "farming": {"crops": 4, "herbs": 2, "animals": 3}, "gather": {"FIBER": 5}}, CITIES)
    q = player.island_query(p)
    assert q["seeds"] == ["27"] and q["animals"] == ["27"] and q["yield"] == ["4.5"]
    payload = {"crops": [{"tier": 4, "kind": "crop"}, {"tier": 3, "kind": "herb"}],
               "animals": [{"tier": 4}], "gathering": [{"tier": 5, "type": "FIBER"}, {"tier": 6, "type": "FIBER"}]}
    player.island_locks(p, payload)
    assert [r["locked"] for r in payload["crops"]] == [None, 3]
    assert payload["animals"][0]["locked"] == 4
    assert [r["locked"] for r in payload["gathering"]] == [None, 6]


@pytest.mark.parametrize("item,kind", [("T4_PLANKS", ("refine", "PLANKS")), ("T5_METALBAR@1", ("refine", "METALBAR")),
                                       ("T4_LEATHER_LEVEL2@2", ("refine", "LEATHER")),
                                       ("T5_MOUNT_ARMORED_HORSE", ("craft", "saddler")), ("T6_MEAL_STEW", ("craft", "cook")),
                                       ("T4_POTION_HEAL", ("craft", "alchemist")), ("T4_2H_BOW", ("craft", "gear"))])
def test_craft_kind(item, kind) -> None:
    assert player.craft_kind(item) == kind


def test_suggestions_respect_tiers_and_safe_zones() -> None:
    p = player.validate({"refine": {"CLOTH": 4}, "craft": {"gear": 4}}, CITIES)
    crafting = [
        {"item_id": "T4_CLOTH", "item_name": "Cloth", "craft_city": "Lymhurst", "sell_city": "Martlock", "profit_per_item": 50},
        {"item_id": "T5_CLOTH", "item_name": "Fine Cloth", "craft_city": "Lymhurst", "sell_city": "Martlock", "profit_per_item": 500},
        {"item_id": "T4_2H_BOW", "item_name": "Bow", "craft_city": "Any Royal City", "sell_city": "Black Market", "profit_per_item": 9000},
        {"item_id": "T4_BAG", "item_name": "Bag", "craft_city": "Any Royal City", "sell_city": "Thetford", "profit_per_item": 300},
        {"item_id": "T4_SHOES", "item_name": "Shoes", "craft_city": "Thetford", "sell_city": "Thetford", "profit_per_item": -5},
    ]
    flips = [
        {"item_id": "T4_BAG", "buy_city": "Thetford", "sell_city": "Martlock", "est_daily_profit": 1000, "profit_per_item": 10},
        {"item_id": "T5_BAG", "buy_city": "Thetford", "sell_city": "Caerleon", "est_daily_profit": 9000, "profit_per_item": 90},
        {"item_id": "T6_BAG", "buy_city": "Thetford", "sell_city": "Martlock", "est_daily_profit": 5000, "history_missing": True},
    ]
    isl = {"crops": [{"crop": "T3_WHEAT", "profit_day": 100, "sell_city": "Thetford", "seed_city": "Thetford"},
                     {"crop": "T5_CABBAGE", "profit_day": 900, "locked": 5, "sell_city": "Thetford", "seed_city": "Thetford"}],
           "animals": [], "gathering": [{"id": "T4_FIBER", "type": "FIBER", "per_hour_home": 900, "zones": []},
                                        {"id": "T3_FIBER", "type": "FIBER", "per_hour_home": 500, "zones": [{"name": "X"}]}]}
    s = player.suggestions(p, crafting, [], flips, isl)
    assert [r["item_id"] for r in s["refine"]] == ["T4_CLOTH"]  # T5 is locked
    assert [r["item_id"] for r in s["craft"]] == ["T4_BAG"]      # the bow sells at the Black Market (red zone)
    assert [r["item_id"] for r in s["transport"]] == ["T4_BAG"]  # Caerleon and unchecked flips left out
    assert [r["crop"] for r in s["crops"]] == ["T3_WHEAT"]
    assert [r["id"] for r in s["gathering"]] == ["T3_FIBER"]     # no zone nearby: not suggested
    risky = player.validate({"safe_only": False, "craft": {"gear": 4}}, CITIES)
    assert player.suggestions(risky, crafting, [], flips, isl)["craft"][0]["item_id"] == "T4_2H_BOW"


def test_http_profile(server) -> None:  # noqa: F811
    port, ins = server
    st, d = _req(port, "GET", "/api/profile")
    assert st == 200 and d["saved"] is False and d["can_edit"] is False and "suggestions" in d
    body = {"home": "Martlock", "island": {"farms": 1, "herb_gardens": 0, "pastures": 2}, "farming": {"crops": 2}}
    assert _req(port, "POST", "/api/profile", body)[0] == 401
    cookie = _login(port)
    assert _req(port, "POST", "/api/profile", {"home": "Nowhere"}, cookie)[0] == 400
    st, d = _req(port, "POST", "/api/profile", body, cookie)
    assert st == 200 and d["profile"]["home"] == "Martlock"
    st, d = _req(port, "GET", "/api/profile", cookie=cookie)
    assert d["saved"] and d["can_edit"]
    # the island tab now starts from the profile: 9 seeds, 18 animals, home Martlock
    st, d = _req(port, "GET", "/api/island")
    assert d["settings"]["seeds"] == 9 and d["settings"]["animals"] == 18 and d["settings"]["home"] == "Martlock"
    assert d["profile_saved"] is True
    # explicit settings in the query still win
    assert _req(port, "GET", "/api/island?seeds=4")[1]["settings"]["seeds"] == 4
    st, page = _req(port, "GET", "/")
    assert b"view-profile" in page and b"tab-profile" in page
