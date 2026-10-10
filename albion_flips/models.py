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


