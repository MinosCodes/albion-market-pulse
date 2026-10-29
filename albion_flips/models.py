from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ExitType(str, Enum):
    SELL_ORDER = "sell_order"
    INSTANT_SELL = "instant_sell"


@dataclass(slots=True)
class PriceRecord:
    item_id: str
    city: str
    quality: int
    sell_price_min: int
    sell_price_min_date: str
    sell_price_max: int
    sell_price_max_date: str
    buy_price_min: int
    buy_price_min_date: str
    buy_price_max: int
    buy_price_max_date: str

    @classmethod
    def from_dict(cls, data: dict) -> PriceRecord:
        return cls(
            item_id=data.get("item_id", ""),
            city=data.get("city", ""),
            quality=int(data.get("quality", 1)),
            sell_price_min=int(data.get("sell_price_min", 0)),
            sell_price_min_date=data.get("sell_price_min_date", ""),
            sell_price_max=int(data.get("sell_price_max", 0)),
            sell_price_max_date=data.get("sell_price_max_date", ""),
            buy_price_min=int(data.get("buy_price_min", 0)),
            buy_price_min_date=data.get("buy_price_min_date", ""),
            buy_price_max=int(data.get("buy_price_max", 0)),
            buy_price_max_date=data.get("buy_price_max_date", ""),
        )


@dataclass(slots=True)
class HistoryPoint:
    item_count: int
    avg_price: int
    timestamp: str

    @classmethod
    def from_dict(cls, data: dict) -> HistoryPoint:
        return cls(
            item_count=int(data.get("item_count", 0)),
            avg_price=int(data.get("avg_price", 0)),
            timestamp=data.get("timestamp", ""),
        )


@dataclass(slots=True)
class HistoryRecord:
    location: str
    item_id: str
    quality: int
    data: list[HistoryPoint] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> HistoryRecord:
        raw_points = data.get("data", [])
        points = [HistoryPoint.from_dict(p) for p in raw_points]
        return cls(
            location=data.get("location", ""),
            item_id=data.get("item_id", ""),
            quality=int(data.get("quality", 1)),
            data=points,
        )


@dataclass(slots=True)
class FlipOpportunity:
    item_id: str
    buy_city: str
    sell_city: str
    buy_price: int
    sell_price: int
    exit_type: ExitType
    profit_per_item: float
    margin_pct: float
    total_profit: float
    avg_daily_volume: float | None
    data_age_minutes: float
    risk: str
    quality: int = 1
    history_missing: bool = False
    est_daily_profit: float = 0.0
    weight: float = 0.5
    profit_per_kg: float = 0.0
    # Robust pricing (robust.py): what the market showed vs. what we calculated with
    raw_buy_price: int = 0
    raw_sell_price: int = 0
    price_note: str = ""            # "bait", "inverted_book", "sell_cap", combined with "+"
    ref_avg7: float | None = None   # 7-day average in the destination city
    units_per_day: float = 0.0      # realistic units per day (market share, capital)


@dataclass(slots=True)
class CraftingOpportunity:
    item_id: str
    item_name: str
    craft_type: str
    craft_city: str
    sell_city: str
    material_cost: float
    effective_cost: float
    sell_price: int
    profit_per_item: float
    margin_pct: float
    resource_return_rate: float
    ingredients_desc: str
    city_bonus: bool = False
    focus: bool = False
    tier: int | None = None
    enchant: int = 0
    item_value: float = 0.0
    station_fee: float = 0.0
    data_age_minutes: float | None = None  # oldest price used in the calculation
    daily_volume: float | None = None  # avg units sold per day at sell_city (None = no history)
    # [{id, name, count, unit_price, city, returnable}] per craft, for icons and the cost breakdown
    ingredients: list[dict] = field(default_factory=list)
    returned_value: float = 0.0  # silver saved per item by the resource return rate
    craft_silver: float = 0.0  # silver cost per item charged by the recipe itself
    tax_paid: float = 0.0  # market tax + setup fee taken from the sale, per item
    sell_instant: bool = False
    city_return_rates: dict[str, float] = field(default_factory=dict)  # return rate at every crafting city
    # Other profitable craft cities: [{city, effective_cost, profit_per_item, margin_pct, prices, cities, ...}]
    city_options: list[dict] = field(default_factory=list)
    # Net silver for a crafted item of quality 1..5 (best exit it can fill) and where.
    quality_nets: list[float] = field(default_factory=list)
    quality_exits: list[str] = field(default_factory=list)
    expected_profit: float = 0.0  # average over base quality odds (one roll)


@dataclass(slots=True)
class EnchantingOpportunity:
    base_item_id: str
    target_item_id: str
    item_name: str
    tier: int
    from_enchant: int
    to_enchant: int
    city: str
    sell_city: str
    base_item_price: int
    enchant_mat_id: str
    enchant_mat_name: str
    enchant_mat_qty: int
    enchant_mat_unit_price: int
    enchant_mat_total_cost: int
    total_cost: int
    sell_price: int
    net_revenue: int
    profit_per_item: int
    margin_pct: float
    is_profitable: bool
    mass_batch_size: int = 10
    mass_profit: int = 0
    item_type: str = "Bag"
    data_age_minutes: float = 0.0
    is_live_sniffer: bool = False
    sniffer_age_seconds: int | None = None
    target_daily_volume: float | None = None
    target_history_missing: bool = True
    liquidity_status: str = "untracked"
    quality: int = 1


