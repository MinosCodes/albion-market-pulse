from __future__ import annotations

from albion_flips.advisor import (
    CITY_SPECIALTIES,
    ask_advisor,
    detect_budget,
    detect_city,
    generate_tactical_advice,
)


def test_detect_city() -> None:
    assert detect_city("I am currently in Martlock") == "Martlock"
    assert detect_city("Any good flips from BW to FS?") == "Bridgewatch"
    assert detect_city("Best crafts in Fort Sterling right now?") == "Fort Sterling"
    assert detect_city("What to do in caerleon?") == "Caerleon"
    assert detect_city("How to make silver in Brecilien?") == "Brecilien"
    assert detect_city("Can I flip in Lymhurst?") == "Lymhurst"
    assert detect_city("Hello there world") is None


def test_detect_budget() -> None:
    assert detect_budget("I have 1.5m silver to invest") == 1_500_000
    assert detect_budget("My budget is 500k") == 500_000
    assert detect_budget("Starting with 250000 silver") == 250_000
    assert detect_budget("Just looking around") is None


def test_generate_tactical_advice_with_mock_data() -> None:
    mock_data = {
        "server": "europe",
        "crafting": [
            {
                "item_id": "T4_MAIN_AXE",
                "city": "Martlock",
                "crafting_cost": 2000,
                "sell_price": 3200,
                "net_profit": 1000,
                "margin_pct": 50.0,
                "rrr": 0.248,
                "has_city_bonus": True,
            },
            {
                "item_id": "T4_BAG",
                "city": "Martlock",
                "crafting_cost": 1000,
                "sell_price": 1400,
                "net_profit": 300,
                "margin_pct": 30.0,
                "rrr": 0.152,
                "has_city_bonus": False,
            },
        ],
        "flips": [
            {
                "item_id": "T4_LEATHER",
                "buy_city": "Martlock",
                "sell_city": "Thetford",
                "buy_price": 500,
                "sell_price": 750,
                "profit_per_item": 200,
                "margin_pct": 40.0,
                "risk": "low",
                "exit_type": "sell_order",
            },
            {
                "item_id": "T5_ARMOR_PLATE_SET1",
                "buy_city": "Martlock",
                "sell_city": "Caerleon",
                "buy_price": 10000,
                "sell_price": 16000,
                "profit_per_item": 4000,
                "margin_pct": 40.0,
                "risk": "high",
                "exit_type": "sell_order",
            },
        ],
        "blackmarket": [
            {
                "item_id": "T4_CAPEITEM_FW_MARTLOCK",
                "buy_city": "Martlock",
                "buy_price": 25000,
                "bm_instant_buy_price": 38000,
                "net_profit": 11000,
                "margin_pct": 44.0,
            }
        ],
    }

    advice = generate_tactical_advice(city="Martlock", store_data=mock_data, budget=500_000)
    assert "Martlock" in advice
    assert "Hide Refining" in advice
    assert "T4_MAIN_AXE" in advice
    assert "T4_CAPEITEM_FW_MARTLOCK" in advice
    assert "500,000" in advice
    assert "Action Checklist" in advice


def test_ask_advisor_fallback_and_prompt() -> None:
    reply = ask_advisor(query="What should I craft in Lymhurst?", store_data={})
    assert "Lymhurst" in reply
    assert "Fiber Refining" in reply

    reply_general = ask_advisor(query="Hello", store_data={})
    assert "Albion Tactical AI Market Advisor" in reply_general
