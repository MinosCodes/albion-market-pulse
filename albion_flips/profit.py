from __future__ import annotations


def calculate_sell_order_profit(
    buy_price: int,
    sell_price: int,
    tax_rate: float,
    setup_fee_rate: float,
) -> float:
    """Calculates net profit per item when exiting via a sell order.

    Formula:
        profit = sell_price * (1 - tax_rate - setup_fee_rate) - buy_price
    """
    return float(sell_price * (1.0 - tax_rate - setup_fee_rate) - buy_price)


def calculate_instant_profit(
    buy_price: int,
    buyer_bid: int,
    tax_rate: float,
) -> float:
    """Calculates net profit per item when exiting via instant sell into an existing buy order.

    Formula:
        profit = buyer_bid * (1 - tax_rate) - buy_price
    """
    return float(buyer_bid * (1.0 - tax_rate) - buy_price)


def calculate_margin(profit: float, buy_price: int) -> float:
    """Calculates profit margin percentage relative to purchase cost.

    Formula:
        margin% = 100 * profit / buy_price
    """
    if buy_price <= 0:
        return 0.0
    return float(100.0 * profit / buy_price)


def calculate_total_profit(profit_per_item: float, stack_size: int) -> float:
    """Calculates total profit for a given stack size."""
    return float(profit_per_item * stack_size)


def calculate_crafting_profit(
    sell_price: int,
    effective_material_cost: float,
    tax_rate: float,
    setup_fee_rate: float = 0.025,
    station_fee: float = 0.0,
) -> float:
    """Calculates net profit for crafting or refining an item after taxes, fees, and station tax.

    Formula:
        net_revenue = sell_price * (1.0 - tax_rate - setup_fee_rate)
        profit = net_revenue - effective_material_cost - station_fee
    """
    net_revenue = sell_price * (1.0 - tax_rate - setup_fee_rate)
    return float(net_revenue - effective_material_cost - station_fee)


def calculate_profit_per_kg(profit_per_item: float, weight: float) -> float:
    """Calculates silver profit density per kilogram of carry weight.

    Formula:
        profit_per_kg = profit_per_item / weight
    """
    if weight <= 0:
        return float(profit_per_item)
    return float(profit_per_item / weight)


def calculate_mount_trip(
    profit_per_item: float,
    weight: float,
    mount_capacity_kg: float,
    max_units: int | None = None,
) -> tuple[int, float]:
    """Calculates max carry units and total profit for a given mount carry capacity.

    Returns:
        (units_carried, total_trip_profit)
    """
    if weight <= 0:
        units = max_units or 9999
    else:
        units = int(mount_capacity_kg // weight)
        if max_units is not None:
            units = min(units, max_units)
    return units, float(units * profit_per_item)


def calculate_station_usage_fee(item_value: float, fee_per_100_nutrition: float) -> float:
    """Calculates station usage fee for crafting or refining an item.

    In Albion Online:
        Nutrition consumed per item = item_value * 0.1125
        Fee = Nutrition * (fee_per_100_nutrition / 100) = item_value * 0.001125 * fee_per_100_nutrition
    """
    return float(item_value * 0.001125 * fee_per_100_nutrition)


