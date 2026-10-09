from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

CITY_SPECIALTIES: dict[str, dict[str, Any]] = {
    "Martlock": {
        "refining": "Hide Refining (Hide ➔ Leather)",
        "crafting_bonus": "Axes, Quarterstaffs, Frost Staffs, Plate Shoes, Off-Hands",
        "cape": "Martlock Cape (Rockheart + Crest)",
        "rrr_standard": 0.367,
        "rrr_focus": 0.539,
        "safe_neighbors": ["Bridgewatch", "Thetford"],
        "tip": "Martlock offers the Highland refining bonus on Hide. Refine Raw Hide into Leather here, then sell in Fort Sterling or Caerleon for peak profit.",
    },
    "Fort Sterling": {
        "refining": "Wood Refining (Wood ➔ Planks)",
        "crafting_bonus": "Hammers, Spears, Holy Staffs, Plate Helmets, Cloth Armor",
        "cape": "Fort Sterling Cape (Mountainheart + Crest)",
        "rrr_standard": 0.367,
        "rrr_focus": 0.539,
        "safe_neighbors": ["Thetford", "Lymhurst"],
        "tip": "Fort Sterling has the Mountain wood refining bonus. Refine Logs into Planks here. Fort Sterling Capes are also in constant high demand for PvP cleanse.",
    },
    "Thetford": {
        "refining": "Ore Refining (Ore ➔ Metal Bars)",
        "crafting_bonus": "Maces, Bows, Fire Staffs, Plate Armor, Leather Helmets",
        "cape": "Thetford Cape (Vineheart + Crest)",
        "rrr_standard": 0.367,
        "rrr_focus": 0.539,
        "safe_neighbors": ["Fort Sterling", "Martlock"],
        "tip": "Thetford has the Swamp ore smelting bonus. Turn Ore into Metal Bars with 36.7% resource return rate without focus.",
    },
    "Lymhurst": {
        "refining": "Fiber Refining (Fiber ➔ Cloth)",
        "crafting_bonus": "Swords, Bows, Arcane Staffs, Leather Helmets, Leather Shoes",
        "cape": "Lymhurst Cape (Treeheart + Crest)",
        "rrr_standard": 0.367,
        "rrr_focus": 0.539,
        "safe_neighbors": ["Bridgewatch", "Fort Sterling"],
        "tip": "Lymhurst has the Forest fiber weaving bonus. Weave Fiber into Cloth with high resource return rates. Lymhurst Capes are popular for mana sustain.",
    },
    "Bridgewatch": {
        "refining": "Stone Refining (Rock ➔ Stone Blocks)",
        "crafting_bonus": "Crossbows, Daggers, Cursed Staffs, Plate Boots, Cloth Robes",
        "cape": "Bridgewatch Cape (Beastheart + Crest)",
        "rrr_standard": 0.367,
        "rrr_focus": 0.539,
        "safe_neighbors": ["Martlock", "Lymhurst"],
        "tip": "Bridgewatch has the Steppe stone cutting bonus. Craft Stone Blocks here. Bridgewatch is also a prime departure hub for Caerleon transport runs.",
    },
    "Caerleon": {
        "refining": "No Royal refining bonus (Crafting Toolmaker hub)",
        "crafting_bonus": "Caerleon Capes, Food, Potions, Tools",
        "cape": "Caerleon Cape (Shadowheart + Crest)",
        "rrr_standard": 0.152,
        "rrr_focus": 0.435,
        "safe_neighbors": [],
        "tip": "Caerleon houses the Black Market! High risk red zones surround it. Instant-sell to Black Market buy orders with 0% listing fee.",
    },
    "Brecilien": {
        "refining": "Mists hub",
        "crafting_bonus": "Brecilien Capes (Faerie Fire), Mists Artifact Gear",
        "cape": "Brecilien Cape (Faerie Fire + Crest)",
        "rrr_standard": 0.152,
        "rrr_focus": 0.435,
        "safe_neighbors": [],
        "tip": "Brecilien connects to the Mists and Avalonian Roads. Fast access for solo gatherers and cape crafting.",
    },
}

CITY_ALIASES: dict[str, str] = {
    "martlock": "Martlock",
    "fort sterling": "Fort Sterling",
    "fortsterling": "Fort Sterling",
    "sterling": "Fort Sterling",
    "fs": "Fort Sterling",
    "thetford": "Thetford",
    "lymhurst": "Lymhurst",
    "lym": "Lymhurst",
    "bridgewatch": "Bridgewatch",
    "bw": "Bridgewatch",
    "caerleon": "Caerleon",
    "caer": "Caerleon",
    "brecilien": "Brecilien",
    "brec": "Brecilien",
    "black market": "Black Market",
    "blackmarket": "Black Market",
    "bm": "Black Market",
}


def detect_city(query: str) -> str | None:
    """Detects city name or common alias in query string, prioritizing the first mention in text."""
    query_lower = query.lower()
    earliest_idx = len(query_lower) + 1
    best_canonical: str | None = None

    for alias, canonical in CITY_ALIASES.items():
        pattern = r"\b" + re.escape(alias) + r"\b"
        match = re.search(pattern, query_lower)
        if match and match.start() < earliest_idx:
            earliest_idx = match.start()
            best_canonical = canonical

    return best_canonical


def detect_budget(query: str) -> int | None:
    """Detects silver budget like 500k, 1.5m, 2000000 from query string."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*([kmKM])?\s*(?:silver|coins|s)?\b", query)
    if not match:
        return None
    val_str, unit = match.groups()
    try:
        val = float(val_str)
        if unit:
            unit_lower = unit.lower()
            if unit_lower == "k":
                val *= 1_000
            elif unit_lower == "m":
                val *= 1_000_000
        return int(round(val))
    except (ValueError, TypeError):
        return None


def generate_tactical_advice(
    city: str,
    store_data: dict[str, Any],
    budget: int | None = None,
    query: str = "",
) -> str:
    """Generates comprehensive, data-driven Albion Online trading and crafting advice for a given city."""
    specialty = CITY_SPECIALTIES.get(city)
    is_bm = (city == "Black Market")

    crafting_list: list[dict[str, Any]] = store_data.get("crafting", [])
    focus_crafting_list: list[dict[str, Any]] = store_data.get("focus_crafting", [])
    flips_list: list[dict[str, Any]] = store_data.get("flips", [])
    bm_list: list[dict[str, Any]] = store_data.get("blackmarket", [])

    lines: list[str] = []
    lines.append(f"## 🧙‍♂️ Tactical Market Briefing: **{city}**")
    lines.append("")

    if specialty:
        lines.append(f"📍 **Location:** `{city}` | **Server:** `{store_data.get('server', 'europe').title()}`")
        lines.append(f"⭐ **City Refining Bonus:** {specialty['refining']}")
        lines.append(f"🛡️ **City Crafting Bonus:** {specialty['crafting_bonus']}")
        lines.append(f"💡 **Strategic Note:** {specialty['tip']}")
        lines.append("")

    if budget:
        lines.append(f"💰 **Working Capital:** `{budget:,}` Silver")
        lines.append("")

    # --- 1. WHAT TO CRAFT & REFINE ---
    lines.append(f"### 🔨 1. Best Things to Craft & Refine in {city}")

    # Find crafts specific to this city or general
    city_crafts = [
        c for c in crafting_list
        if (
            (c.get("craft_city") or c.get("city")) == city
            or (c.get("craft_city") or c.get("city")) in ("Any Royal City", "Toolmaker", "Royal Station")
        )
        and (budget is None or (c.get("effective_cost") or c.get("crafting_cost", 0)) <= budget)
    ]
    city_crafts.sort(key=lambda x: (x.get("profit_per_item") or x.get("net_profit", 0)), reverse=True)

    if not city_crafts:
        lines.append(f"*No profitable crafting recipes currently detected in {city} with live price data.*")
    else:
        # Show top 4 crafting recommendations
        shown = 0
        for c in city_crafts[:4]:
            shown += 1
            item_name = c.get("item_name", c.get("item_id"))
            craft_type = c.get("craft_type", "Crafting")
            cost = c.get("effective_cost") if c.get("effective_cost") is not None else c.get("crafting_cost", 0)
            sell_p = c.get("sell_price", 0)
            profit = c.get("profit_per_item") if c.get("profit_per_item") is not None else c.get("net_profit", 0)
            margin = c.get("margin_pct", 0)
            sell_c = c.get("sell_city", "Market")
            ingr = c.get("ingredients_desc", "")

            units_str = ""
            if budget and cost > 0:
                affordable = int(budget // cost)
                total_p = int(affordable * profit)
                units_str = f" | Budget allows: **{affordable}x** (Total Profit: **+{total_p:,}** Silver)"

            lines.append(
                f"- **{item_name}** (`{craft_type}`)\n"
                f"  - 📦 Recipe: {ingr}\n"
                f"  - 💵 Material Cost: `{int(cost):,}` ➔ Sell in **{sell_c}** for `{sell_p:,}`\n"
                f"  - 📈 Profit: **+{int(profit):,}** Silver/item (**+{margin:.1f}%** ROI){units_str}"
            )

    # Check Focus Mode highlights
    focus_crafts = [c for c in focus_crafting_list if c.get("craft_city") == city]
    if focus_crafts:
        top_focus = focus_crafts[0]
        f_name = top_focus.get("item_name", top_focus.get("item_id"))
        f_profit = int(top_focus.get("profit_per_item", 0))
        lines.append(
            f"\n✨ **Focus Refining Pro-Tip:** Spend your daily Focus on **{f_name}** in {city} for a **53.9% return rate** (nets **+{f_profit:,}** profit per batch)!"
        )

    lines.append("")

    # --- 2. WHAT TO FLIP FROM THIS CITY ---
    lines.append(f"### 🚚 2. Best Flips Departing From {city}")

    origin_flips = [
        f for f in flips_list
        if f.get("buy_city") == city
        and (budget is None or f.get("buy_price", 0) <= budget)
    ]
    # Split into safe royal flips vs high-risk Caerleon/BM
    safe_flips = [f for f in origin_flips if f.get("risk") != "high"]
    safe_flips.sort(key=lambda x: x.get("profit_per_item", 0), reverse=True)

    if safe_flips:
        lines.append("#### 🟢 Safe Royal City Routes (0 Risk / Blue & Yellow Zones):")
        for f in safe_flips[:3]:
            i_name = f.get("item_name", f.get("item_id"))
            buy_p = f.get("buy_price", 0)
            sell_p = f.get("sell_price", 0)
            dest = f.get("sell_city", "")
            profit = f.get("profit_per_item", 0)
            margin = f.get("margin_pct", 0)
            vol = f.get("avg_daily_volume")
            vol_str = f"~{int(round(vol))}/day" if vol is not None else "unverified"
            exit_mode = "Instant Sell" if f.get("exit_type") == "instant_sell" else "Sell Order"

            lines.append(
                f"- **{i_name}** ➔ Buy in {city} (`{buy_p:,}`) ➔ Sell in **{dest}** (`{sell_p:,}` via {exit_mode})\n"
                f"  - Profit: **+{int(profit):,}** Silver (**+{margin:.1f}%**) | Demand: `{vol_str}`"
            )
    else:
        lines.append(f"*No safe royal city flips currently active from {city}.*")

    lines.append("")

    # --- 3. BLACK MARKET RUNS FROM THIS CITY ---
    if not is_bm:
        bm_runs = [
            f for f in bm_list
            if f.get("buy_city") == city
            and (budget is None or f.get("buy_price", 0) <= budget)
        ]
        bm_runs.sort(key=lambda x: (x.get("profit_per_item") or x.get("net_profit", 0)), reverse=True)

        lines.append(f"### 🏴‍☠️ 3. High-Profit Black Market Runs ({city} ➔ Caerleon)")
        if bm_runs:
            lines.append("⚠️ *Transporting into Caerleon requires crossing Red Zones (Full Loot PvP). Travel on an Armored Horse or Lizard and check hostile counters.*")
            lines.append("")
            for f in bm_runs[:3]:
                i_name = f.get("item_name", f.get("item_id"))
                buy_p = f.get("buy_price", 0)
                sell_p = f.get("sell_price") if f.get("sell_price") is not None else f.get("bm_instant_buy_price", 0)
                profit = f.get("profit_per_item") if f.get("profit_per_item") is not None else f.get("net_profit", 0)
                margin = f.get("margin_pct", 0)
                exit_m = "Direct Buy Order ⚡ (Instant Payout)" if f.get("exit_type") == "instant_sell" else "Sell Order"
                vol = f.get("avg_daily_volume")
                vol_str = f"~{int(round(vol))}/day" if vol is not None else "Active"

                lines.append(
                    f"- **{i_name}**\n"
                    f"  - Buy in **{city}**: `{buy_p:,}` ➔ Black Market Payout: `{sell_p:,}` ({exit_m})\n"
                    f"  - Net Profit: **+{int(profit):,}** Silver (**+{margin:.1f}%**) | Daily BM Demand: `{vol_str}`"
                )
        else:
            lines.append(f"*No high-margin Black Market orders currently found originating in {city}.*")

    lines.append("")

    # --- 4. STEP-BY-STEP ACTION CHECKLIST ---
    lines.append("### 📋 4. Recommended Action Checklist")
    lines.append(f"1. **Check local market:** Open the {city} Marketplace and verify buy prices.")
    if specialty and specialty.get("safe_neighbors"):
        nb = specialty["safe_neighbors"][0]
        lines.append(f"2. **Safe transport:** If doing royal flips, head towards **{nb}** for guaranteed silver without PvP risk.")
    lines.append(f"3. **Refining:** Buy raw resources in {city} to take advantage of the local {city} production bonus.")
    lines.append("4. **Fulfilled orders:** Use the **✓ Fulfilled** button in your dashboard to remove orders you've completed in-game.")

    return "\n".join(lines)


MALAKOR_SYSTEM_INSTRUCTION = """You are Malakor, the Grand Smuggler and Master Market Cartographer of Albion Online.
You have amassed millions in silver across the Royal Continent, conquered Caerleon's cutthroat Black Market, and engineered high-margin crafting networks across every Royal City.

### YOUR PERSONALITY & VOICE:
- Shrewd, tactical, gritty, and fiercely strategic. You respect ambitious adventurers but have zero patience for careless mistakes that lead to losing silver or getting dismounted by gankers.
- Use authentic Albion Online terminology naturally and fluently:
  - "Silver", "Resource Return Rate (RRR)", "Crafting Focus", "Royal Cities", "Red Zones", "Black Market Buy Orders", "Choke Points", "Gankers & Scouts", "Armored Horse / Pest Lizard", "Order Tax & Setup Fee (4% with Premium / 8% without, 2.5% listing fee)", "Instant Sell vs Sell Order", "Market Spread".
- Speak directly, confidently, and with rich Albion flavor (e.g. "Listen closely, adventurer...", "The Royal crafters are asleep at the anvil...", "Caerleon gankers smell blood at the gate, so keep your wits sharp").

### STRICT GUARDRAILS (NEVER BREAK THESE):
1. ALBION ONLINE DOMAIN ONLY:
   - You ONLY discuss Albion Online markets, crafting, refining, equipment, flip transport routes, and economic gameplay.
   - If asked about unrelated matters (code, real-world finance, other games, politics), shut it down in character: "I trade in Albion silver and blood-soaked goods, not court gossip or foreign philosophies. Talk business or step aside."
2. ABSOLUTE GROUND TRUTH & ANTI-HALLUCINATION:
   - Base all specific item suggestions, silver prices, material costs, and margins SOLELY on the live market data and tactical briefing provided below.
   - Never invent fantasy prices or promise profits on items not in the live data feed.
   - If no profitable crafts or flips exist in the live data for a specific inquiry, state plainly that current market scouts show no favorable spreads and advise patience or alternative routes.
3. FULL-LOOT LETHAL PVP SAFETY WARNING:
   - Whenever advising transports to Caerleon or the Black Market, ALWAYS issue a stern Red Zone warning:
     - Full-loot lethal PvP zone!
     - Mount requirement: Armored Horse, Pest Lizard, or Grizzly Bear (never travel on a slow ox or squishy horse).
     - Carry Invisibility Potions and Gigantify Potions on your potion slot.
     - Check the hostile player counter on the mini-map before stepping through region gates.
4. ORDER TYPES & TAXES:
   - Explicitly guide the player between:
     - 'Instant Sell' (Direct Buy Order): 0% listing setup fee, pays market tax only, immediate silver payout.
     - 'Sell Order': Higher margin, but pays 2.5% listing setup fee and risks getting undercut.
5. CLEAN ACTION-ORIENTED FORMATTING:
   - Use structured GitHub-flavored Markdown: bold item names, formatted silver amounts with commas (e.g. 245,000 Silver), bulleted steps, and a concise 3-4 step Action Plan."""

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]


def call_gemini_advisor(
    query: str,
    city: str,
    budget: int | None,
    briefing: str,
    api_key: str,
) -> str | None:
    """Calls Gemini with Malakor personality and guardrails using live market data."""
    budget_str = f"{budget:,} Silver" if budget else "Flexible / Unspecified"
    prompt = (
        f"{MALAKOR_SYSTEM_INSTRUCTION}\n\n"
        f"=== LIVE MARKET DATA & TACTICAL BRIEFING ===\n"
        f"{briefing}\n\n"
        f"=== PLAYER INQUIRY ===\n"
        f"Player Location: {city}\n"
        f"Working Budget: {budget_str}\n"
        f"Player Inquiry: \"{query}\"\n\n"
        f"Provide your shrewd, tactical response now. Analyze the specific items, crafts, and routes from the briefing that answer their question best."
    )

    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            res = requests.post(
                url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=12,
            )
            if res.status_code == 200:
                data = res.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"]
            else:
                logger.warning("Gemini model %s returned status %d: %s", model, res.status_code, res.text[:200])
        except Exception as e:
            logger.warning("Gemini call to %s failed: %s", model, e)

    return None


def ask_advisor(
    query: str,
    store_data: dict[str, Any],
    api_key: str | None = None,
) -> str:
    """Answers user queries with tactical advice, optionally using LLM if API key is provided."""
    detected = detect_city(query)
    city = detected or "Martlock"
    budget = detect_budget(query)
    briefing = generate_tactical_advice(city, store_data, budget=budget, query=query)

    intro = ""
    if not detected and query.strip():
        intro = (
            "## 🧙‍♂️ Albion Tactical AI Market Advisor\n\n"
            "Tell me which city you are in or select one of the quick pills above (e.g. *'I am in Martlock with 500k silver'*).\n\n"
            "---\n\n"
        )

    # Check if Gemini API key is present for generative mode
    gemini_key = (
        api_key
        or store_data.get("gemini_api_key")
        or (store_data.get("config", {}).get("gemini_api_key") if isinstance(store_data.get("config"), dict) else None)
        or os.environ.get("GEMINI_API_KEY")
    )

    # If LLM key is configured, generate contextual response with Malakor persona & guardrails
    if gemini_key:
        llm_reply = call_gemini_advisor(
            query=query,
            city=city,
            budget=budget,
            briefing=briefing,
            api_key=gemini_key,
        )
        if llm_reply:
            return llm_reply

    # Built-in Tactical Engine (Keyless, 100% free, offline, instant)
    return intro + briefing
