"""World map and travel routes (server mode).

``data/world.json`` holds every zone of the Royal Continent and the Outlands with its position,
exits, tier, colour (blue, yellow, red, black, city), region, biome and resource nodes, taken from
the official game files. No map images are used: the dashboard draws the map from these numbers.

Routes are the fastest way through the zone graph. Each zone costs a few minutes to cross
(``minutes_per_zone``, about 2.5 on a riding mount); yellow zones count 10 % more and red zones 50 %
more because you ride carefully there. Red zones are only used when you allow them, black zones
(full loot, Outlands) never. Entering the destination city counts half a zone.
"""
from __future__ import annotations

import heapq
import itertools
import json
from pathlib import Path
from typing import Any, Iterable

WORLD_FILE = Path(__file__).resolve().parent.parent / "data" / "world.json"
COST = {"blue": 1.0, "city": 1.0, "yellow": 1.1, "red": 1.5}
MAX_TOUR_STOPS = 7

_DATA: dict[str, Any] | None = None


def load_world(path: Path | str | None = None) -> dict[str, Any]:
    global _DATA
    if path is not None:
        return _prepare(json.loads(Path(path).read_text(encoding="utf-8")))
    if _DATA is None:
        _DATA = _prepare(json.loads(WORLD_FILE.read_text(encoding="utf-8")) if WORLD_FILE.is_file() else {"zones": {}, "cities": {}})
    return _DATA


def _prepare(data: dict[str, Any]) -> dict[str, Any]:
    """Exits work both ways (a few are only listed on one side in the game files)."""
    links: dict[str, set[str]] = {zid: set(z["exits"]) for zid, z in data["zones"].items()}
    for zid, z in data["zones"].items():
        for e in z["exits"]:
            if e in links:
                links[e].add(zid)
    data["links"] = {zid: sorted(v & set(data["zones"])) for zid, v in links.items()}
    return data


def resolve(world: dict[str, Any], ref: str) -> str | None:
    """Zone id, city name or exact zone name -> zone id."""
    ref = (ref or "").strip()
    if ref in world["zones"]:
        return ref
    if ref in world["cities"]:
        return world["cities"][ref]
    low = ref.lower()
    for zid, z in world["zones"].items():
        if z["name"].lower() == low:
            return zid
    return None


def zone_cost(z: dict[str, Any], allow_red: bool, minutes: float) -> float | None:
    kind = z["kind"]
    if kind == "black" or (kind == "red" and not allow_red):
        return None
    return COST.get(kind, 1.0) * minutes


def route(world: dict[str, Any], start: str, goal: str, allow_red: bool = False,
          minutes_per_zone: float = 2.5) -> dict[str, Any] | None:
    """Fastest path from zone ``start`` to zone ``goal`` (ids). None when there is no allowed way."""
    zones, links = world["zones"], world["links"]
    if start not in zones or goal not in zones:
        return None
    if start == goal:
        return {"path": [start], "minutes": 0.0, "zones": 0, "kinds": {}}
    dist = {start: 0.0}
    prev: dict[str, str] = {}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == goal:
            break
        if d > dist.get(u, float("inf")):
            continue
        for v in links.get(u, ()):
            z = zones[v]
            c = minutes_per_zone * 0.5 if v == goal and z["kind"] == "city" else zone_cost(z, allow_red, minutes_per_zone)
            if c is None and v == goal:
                c = zone_cost(z, True, minutes_per_zone) if z["kind"] != "black" else None  # you may end in a red zone you picked
            if c is None:
                continue
            nd = d + c
            if nd < dist.get(v, float("inf")):
                dist[v], prev[v] = nd, u
                heapq.heappush(heap, (nd, v))
    if goal not in dist:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    kinds: dict[str, int] = {}
    for zid in path[1:]:
        kinds[zones[zid]["kind"]] = kinds.get(zones[zid]["kind"], 0) + 1
    return {"path": path, "minutes": round(dist[goal], 1), "zones": len(path) - 1, "kinds": kinds}


def tour(world: dict[str, Any], stops: Iterable[str], allow_red: bool = False, minutes_per_zone: float = 2.5,
         round_trip: bool = True) -> dict[str, Any] | None:
    """Best order to visit all stops (zone ids, city or zone names), starting at the first one
    and back to it for a round trip."""
    ids = [resolve(world, s) for s in stops]
    if any(i is None for i in ids):
        raise ValueError("unknown stop")
    stops = list(dict.fromkeys(i for i in ids if i))
    if len(stops) < 2:
        return None
    if len(stops) > MAX_TOUR_STOPS:
        raise ValueError(f"at most {MAX_TOUR_STOPS} stops")
    legs: dict[tuple[str, str], dict[str, Any] | None] = {}

    def leg(a: str, b: str) -> dict[str, Any] | None:
        if (a, b) not in legs:
            legs[(a, b)] = route(world, a, b, allow_red, minutes_per_zone)
        return legs[(a, b)]

    best = None
    first, rest = stops[0], stops[1:]
    for perm in itertools.permutations(rest):
        seq = [first, *perm] + ([first] if round_trip else [])
        parts = [leg(a, b) for a, b in zip(seq, seq[1:])]
        if any(p is None for p in parts):
            continue
        total = sum(p["minutes"] for p in parts)  # type: ignore[index]
        if best is None or total < best["minutes"]:
            best = {"order": seq, "minutes": round(total, 1), "legs": parts}
    return best


def travel_minutes(world: dict[str, Any], city_a: str, city_b: str, allow_red: bool = False,
                   minutes_per_zone: float = 2.5) -> float | None:
    a, b = world["cities"].get(city_a), world["cities"].get(city_b)
    r = route(world, a, b, allow_red, minutes_per_zone) if a and b else None
    return r["minutes"] if r else None


def resource_zones(world: dict[str, Any], rtype: str, tier: int, kinds: Iterable[str] = ("blue", "yellow")) -> list[dict[str, Any]]:
    """Zones with nodes of this resource and tier, most nodes first."""
    kinds = set(kinds)
    out = []
    for zid, z in world["zones"].items():
        if z["kind"] not in kinds:
            continue
        nodes = sum(n for t, n in z["resources"].get(rtype, []) if t == tier)
        if nodes:
            out.append({"id": zid, "name": z["name"], "tier": z["tier"], "kind": z["kind"], "region": z["region"], "nodes": nodes})
    return sorted(out, key=lambda r: (-r["nodes"], r["name"]))


def public_world(world: dict[str, Any]) -> dict[str, Any]:
    """What the map needs, without the derived link table."""
    return {"cities": world["cities"], "zones": world["zones"], "source": world.get("source", "")}


__all__ = ["load_world", "resolve", "route", "tour", "travel_minutes", "resource_zones", "public_world"]
