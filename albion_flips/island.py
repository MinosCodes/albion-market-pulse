"""Island farming, animal breeding and gathering calculator (server mode).

Game data (growth times, seed return, offspring chance, food) comes from ``data/island.json``,
which was extracted from the official game files (ao-bin-dumps). Prices come from the server's
current market data and are checked like everywhere else: a sell order far below the 7-day
average counts as bait, a sell price far above it is capped.

Model
-----
Crops and herbs: one seed per farm spot and day (a harvest takes 22 hours). A seed gives
``crops_per_seed`` crops (on average 4.5, 9 with premium) and comes back with the seed chance
from the game data. Watering costs focus and adds the watering bonus to that chance, e.g. a
wheat seed returns 60 % of the time unwatered and 140 % watered (sometimes two seeds).

Animals: buy a baby, feed it until it is grown, then sell it. The offspring chance gives a new
baby on top (higher with focus). Food is the cheapest crop or herb per nutrition point, and an
animal's favourite food counts double.

Gathering: units per hour are your own estimate (they depend on gear, spec and the zone), the
calculator only multiplies them with the net market price.

All three use the market price for things you produce yourself, because you could sell them
instead of using them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from albion_flips.robust import Reference, check_buy_price, check_sell_price
from albion_flips.web import FlipDataStore, get_human_name

ISLAND_FILE = Path(__file__).resolve().parent.parent / "data" / "island.json"
RESOURCE_TYPES = ("WOOD", "ROCK", "ORE", "FIBER", "HIDE")
DEFAULT_RATES = {2: 600, 3: 450, 4: 300, 5: 180, 6: 100, 7: 60, 8: 40}
NOT_A_MARKET = {"Black Market"}

_DATA: dict[str, Any] | None = None


def load_data(path: Path | str | None = None) -> dict[str, Any]:
    global _DATA
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if _DATA is None:
        _DATA = json.loads(ISLAND_FILE.read_text(encoding="utf-8")) if ISLAND_FILE.is_file() else {}
    return _DATA


def island_items(data: dict[str, Any] | None = None) -> list[str]:
    """Item ids the calculator needs prices for."""
    data = load_data() if data is None else data
    ids = [x for p in data.get("plants", []) for x in (p["seed"], p["crop"])]
    ids += [x for a in data.get("animals", []) for x in (a["baby"], a["grown"])]
    ids += list(data.get("fish", []))
    ids += [f"T{t}_{b}" for t in range(2, 9) for b in RESOURCE_TYPES]
    return list(dict.fromkeys(ids))


def name(item_id: str, data: dict[str, Any]) -> str:
    n = get_human_name(item_id)
    if n == item_id.replace("_", " ").title():
        return data.get("names", {}).get(item_id, n)
    return n


# ---------------------------------------------------------------- settings
def parse_settings(q: dict[str, list[str]], cities: Iterable[str], premium: bool = True) -> dict[str, Any]:
    """Query string -> validated settings (bad values fall back to defaults)."""
    markets = [c for c in cities if c not in NOT_A_MARKET]

    def one(key: str, default: str = "") -> str:
        return (q.get(key) or [default])[0]

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(one(key, str(default)))
        except ValueError:
            return default
        return default if v != v else max(lo, min(hi, v))  # NaN -> default

    home = one("home", "Thetford")
    rates = dict(DEFAULT_RATES)
    raw = one("rates")
    if raw:
        for tier, part in zip(range(2, 9), raw.split(",")):
            try:
                rates[tier] = max(0.0, min(5000.0, float(part)))
            except ValueError:
                pass
    return {
        "home": home if home in markets else (markets[0] if markets else "Thetford"),
        "markets": markets,
        "sell_mode": "order" if one("mode") == "order" else "instant",
        "seeds": int(num("seeds", 18, 0, 500)),
        "crops_per_seed": num("yield", 9.0 if premium else 4.5, 0, 50),
        "water": one("water") == "1",
        "animals": int(num("animals", 9, 0, 500)),
        "animal_focus": one("afocus") == "1",
        "rates": rates,
        "fish_rate": num("fish_rate", 90, 0, 2000),
    }


# ---------------------------------------------------------------- prices
class Prices:
    """Checked buy and net sell prices per item and city from the dashboard's current data."""

    def __init__(self, quotes: Iterable[tuple[str, str, int, int, int]], refs: dict[tuple[str, str, int], Reference] | None,
                 markets: Iterable[str], tax: float, setup: float) -> None:
        self.markets = list(markets)
        self.refs = refs or {}
        self.tax, self.setup = tax, setup
        self.q: dict[tuple[str, str], tuple[int, int]] = {}
        for item_id, city, quality, sell, buy in quotes:
            if quality == 1 and city in self.markets:
                self.q[(item_id, city)] = (sell, buy)

    def ref(self, item_id: str, city: str) -> Reference | None:
        return self.refs.get((item_id, city, 1))

    def buy(self, item_id: str, city: str) -> float | None:
        """What one unit costs when you buy it instantly in this city."""
        sell, buy = self.q.get((item_id, city), (0, 0))
        if not sell:
            return None
        return check_buy_price(sell, self.ref(item_id, city), buy, 0 if buy else None).price

    def sell(self, item_id: str, city: str, mode: str) -> float | None:
        """Silver you keep per unit after tax (and setup fee for a sell order)."""
        sell, buy = self.q.get((item_id, city), (0, 0))
        if mode == "order":
            if not sell:
                return None
            return check_sell_price(max(1, sell - 1), self.ref(item_id, city)).price * (1 - self.tax - self.setup)
        return buy * (1 - self.tax) if buy else None

    def best_buy(self, item_id: str, home: str) -> tuple[float, str] | None:
        """Cheapest city; home wins ties because you do not have to travel."""
        opts = [(p, c != home, c) for c in self.markets if (p := self.buy(item_id, c))]
        return (min(opts)[0], min(opts)[2]) if opts else None

    def best_sell(self, item_id: str, home: str, mode: str) -> tuple[float, str] | None:
        opts = [(-p, c != home, c) for c in self.markets if (p := self.sell(item_id, c, mode))]
        return (-min(opts)[0], min(opts)[2]) if opts else None

    def volume(self, item_id: str, city: str) -> float:
        r = self.ref(item_id, city)
        return float(r.vol7) if r is not None and r.vol7 else 0.0


def _r(x: float | None, nd: int = 0) -> float | None:
    return None if x is None else round(x, nd) if nd else round(x)


def _sale(prices: Prices, item_id: str, s: dict[str, Any]) -> dict[str, Any] | None:
    best = prices.best_sell(item_id, s["home"], s["sell_mode"])
    if best is None:
        return None
    home = prices.sell(item_id, s["home"], s["sell_mode"])
    return {"net": best[0], "city": best[1], "home_net": home, "volume": prices.volume(item_id, best[1])}


# ---------------------------------------------------------------- crops and herbs
def crop_rows(data: dict[str, Any], prices: Prices, s: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for p in data.get("plants", []):
        sale = _sale(prices, p["crop"], s)
        seed = prices.best_buy(p["seed"], s["home"])
        if sale is None or seed is None:
            out.append({"seed": p["seed"], "crop": p["crop"], "name": name(p["crop"], data), "kind": p["kind"],
                        "tier": p["tier"], "missing": "no crop price" if sale is None else "no seed price"})
            continue
        seed_price, seed_city = seed

        def per_seed(watered: bool) -> tuple[float, float]:
            back = p["seed_chance"] + (p["water_bonus"] if watered else 0)
            return s["crops_per_seed"] * sale["net"] - seed_price * (1 - back), back

        dry, back_dry = per_seed(False)
        wet, back_wet = per_seed(True)
        profit, back = (wet, back_wet) if s["water"] else (dry, back_dry)
        focus = p["focus"] if s["water"] else 0
        made = s["crops_per_seed"] * s["seeds"]
        out.append({
            "seed": p["seed"], "crop": p["crop"], "name": name(p["crop"], data), "kind": p["kind"], "tier": p["tier"],
            "grow_h": round(p["grow_s"] / 3600, 1),
            "seed_price": _r(seed_price), "seed_city": seed_city,
            "seed_back": round(back, 4), "seed_back_dry": round(back_dry, 4), "seed_back_wet": round(back_wet, 4),
            "crop_net": _r(sale["net"]), "sell_city": sale["city"], "home_net": _r(sale["home_net"]),
            "profit_per_seed": _r(profit), "profit_day": _r(profit * s["seeds"]),
            "focus_per_seed": focus, "focus_day": focus * s["seeds"],
            # extra silver per 10,000 focus when you water instead of not watering
            "water_per_10k_focus": _r((wet - dry) / p["focus"] * 10000) if p["focus"] else None,
            "made_day": round(made), "volume": round(sale["volume"]),
            "market_small": bool(sale["volume"]) and made > sale["volume"] * 0.25,
            "no_history": not sale["volume"],
        })
    return out


# ---------------------------------------------------------------- animals
def cheapest_food(data: dict[str, Any], prices: Prices, home: str) -> list[dict[str, Any]]:
    """Plants sorted by silver per nutrition point (cheapest first)."""
    foods = []
    for p in data.get("plants", []):
        if not p.get("nutrition"):
            continue
        b = prices.best_buy(p["crop"], home)
        if b:
            foods.append({"id": p["crop"], "name": name(p["crop"], data), "price": b[0], "city": b[1],
                          "nutrition": p["nutrition"], "per_point": b[0] / p["nutrition"]})
    return sorted(foods, key=lambda f: f["per_point"])


def animal_rows(data: dict[str, Any], prices: Prices, s: dict[str, Any]) -> list[dict[str, Any]]:
    foods = cheapest_food(data, prices, s["home"])
    out = []
    for a in data.get("animals", []):
        baby = prices.best_buy(a["baby"], s["home"])
        grown = _sale(prices, a["grown"], s)
        base = {"baby": a["baby"], "grown": a["grown"], "name": name(a["baby"], data),
                "grown_name": name(a["grown"], data), "tier": a["tier"]}
        if baby is None or grown is None:
            out.append(dict(base, missing="no baby price" if baby is None else "no price for the grown animal"))
            continue
        food = None
        if a["food"] == "plants" and foods:
            # favourite food gives (1 + bonus) times the nutrition
            def cost(f: dict[str, Any]) -> float:
                mult = 1 + a["favorite_bonus"] if f["id"] == a.get("favorite") else 1
                return f["per_point"] / mult
            pick = min(foods, key=cost)
            units = a["nutrition"] / (pick["nutrition"] * (1 + a["favorite_bonus"] if pick["id"] == a.get("favorite") else 1))
            food = {"id": pick["id"], "name": pick["name"], "units": round(units, 1), "cost": units * pick["price"],
                    "favorite": pick["id"] == a.get("favorite")}
        chance = a["offspring"] + (a["focus_bonus"] if s["animal_focus"] else 0)
        baby_back = prices.sell(a["baby"], s["home"], s["sell_mode"]) or 0
        # a new baby saves buying the next one, so it is worth the baby's purchase price
        offspring_value = min(1.0, chance) * baby[0] + max(0.0, chance - 1) * baby_back
        feed = food["cost"] if food else None
        profit = grown["net"] + offspring_value - baby[0] - (feed or 0)
        days = a["grow_s"] / 86400
        out.append(dict(
            base, baby_price=_r(baby[0]), baby_city=baby[1], grow_h=round(a["grow_s"] / 3600, 1),
            food=food and dict(food, cost=_r(food["cost"])), food_kind=a["food"], feed_cost=_r(feed),
            offspring=round(chance, 4), offspring_value=_r(offspring_value),
            grown_net=_r(grown["net"]), sell_city=grown["city"], home_net=_r(grown["home_net"]),
            profit=_r(profit), profit_day_per_spot=_r(profit / days), profit_day=_r(profit / days * s["animals"]),
            focus=a["focus"] if s["animal_focus"] else 0, volume=round(grown["volume"]),
            feed_unknown=feed is None, no_history=not grown["volume"],
        ))
    return out


# ---------------------------------------------------------------- gathering
def _zones_for(data: dict[str, Any], region: str, rtype: str, tier: int) -> list[dict[str, Any]]:
    biome = set(data.get("biome", {}).get(region, []))
    out = []
    for z in data.get("zones", []):
        if z["region"] != region:
            continue
        res = z["resources"].get(rtype)
        if res is not None:
            if any(t == tier for t, _ in res):
                out.append({"name": z["name"], "tier": z["tier"], "kind": z["kind"],
                            "nodes": sum(n for t, n in res if t == tier)})
        elif not z["resources"] and z["tier"] == tier and rtype in biome:
            out.append({"name": z["name"], "tier": z["tier"], "kind": z["kind"], "nodes": None})
    return sorted(out, key=lambda z: (z["kind"] != "blue", -(z["nodes"] or 0)))


def gathering_rows(data: dict[str, Any], prices: Prices, s: dict[str, Any]) -> list[dict[str, Any]]:
    region = s["home"]
    items = [(f"T{t}_{b}", t, b) for t in range(2, 9) for b in RESOURCE_TYPES]
    items += [(f, int(f[1]), "FISH") for f in data.get("fish", [])]
    out = []
    for item_id, tier, rtype in items:
        sale = _sale(prices, item_id, s)
        if sale is None:
            continue
        rate = s["fish_rate"] if rtype == "FISH" else s["rates"].get(tier, 0)
        here = sale["home_net"]
        out.append({
            "id": item_id, "name": name(item_id, data), "tier": tier, "type": rtype,
            "rate": rate, "home_net": _r(here), "best_net": _r(sale["net"]), "best_city": sale["city"],
            "per_hour_home": _r(here * rate) if here else None, "per_hour_best": _r(sale["net"] * rate),
            "volume": round(sale["volume"]),
            "in_biome": rtype == "FISH" or rtype in data.get("biome", {}).get(region, []),
            "zones": [] if rtype == "FISH" else _zones_for(data, region, rtype, tier)[:8],
        })
    return out


# ---------------------------------------------------------------- everything
def island_payload(store: FlipDataStore, quotes: list[tuple[str, str, int, int, int]], s: dict[str, Any],
                   data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = load_data() if data is None else data
    cfg = store.last_config
    tax = cfg.active_tax_rate if cfg else 0.04
    setup = cfg.setup_fee_rate if cfg else 0.025
    prices = Prices(quotes, store.reference, s["markets"], tax, setup)
    foods = cheapest_food(data, prices, s["home"])
    return {
        "settings": s,
        "tax_rate": tax, "setup_fee_rate": setup,
        "crops": crop_rows(data, prices, s),
        "animals": animal_rows(data, prices, s),
        "gathering": gathering_rows(data, prices, s),
        "food": [dict(f, price=_r(f["price"]), per_point=round(f["per_point"], 2)) for f in foods[:5]],
        "biome": data.get("biome", {}).get(s["home"], []),
        "zones": [z for z in data.get("zones", []) if z["region"] == s["home"]],
        "source": data.get("source", ""),
    }


__all__ = ["load_data", "island_items", "parse_settings", "Prices", "crop_rows", "animal_rows",
           "gathering_rows", "island_payload", "cheapest_food"]
