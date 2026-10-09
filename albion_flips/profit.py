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
) -> float:
    """Calculates net profit for crafting or refining an item after taxes and fees.

    Formula:
        net_revenue = sell_price * (1.0 - tax_rate - setup_fee_rate)
        profit = net_revenue - effective_material_cost
    """
    net_revenue = sell_price * (1.0 - tax_rate - setup_fee_rate)
    return float(net_revenue - effective_material_cost)

