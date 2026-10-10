from __future__ import annotations

import re

# Item weight defaults (in kilograms)
# Based on Albion Online item definitions
RESOURCE_WEIGHTS: dict[str, float] = {
    # Refined resources
    "PLANKS": 0.3,
    "METALBAR": 0.3,
    "LEATHER": 0.3,
    "CLOTH": 0.3,
    "STONEBLOCK": 0.3,
    # Raw resources
    "WOOD": 0.2,
    "ORE": 0.2,
    "HIDE": 0.2,
    "FIBER": 0.2,
    "ROCK": 0.2,
}

# Mount capacity presets (in kilograms)
MOUNT_CAPACITIES: dict[str, float] = {
    "T3 Transport Ox": 672.0,
    "T4 Transport Ox": 1075.0,
    "T5 Transport Ox": 1607.0,
    "T6 Transport Ox": 2364.0,
    "T7 Transport Ox": 3425.0,
    "T8 Transport Ox": 4923.0,
    "T5 Armored Horse (Fast/Safe)": 800.0,
    "T7 Pest Lizard (Unslowable)": 1100.0,
    "T8 Grizzly Bear (Tank)": 2500.0,
}


def parse_tier(item_id: str) -> int:
    """Extracts tier number (1-8) from item ID, defaulting to 4."""
    match = re.match(r"^T(\d)", item_id)
    if match:
        return int(match.group(1))
    return 4


def parse_enchant(item_id: str) -> int:
    """Extracts enchantment level (0-4) from item ID."""
    if "@" in item_id:
        try:
            return int(item_id.split("@")[-1])
        except ValueError:
            return 0
    return 0


def get_item_weight(item_id: str) -> float:
    """Estimates the weight of an Albion item in kilograms.
    
    Weights are realistic approximations based on item category and tier:
    - Raw & Refined Resources: 0.2kg - 0.7kg
    - Runes, Souls, Relics, Tomes: 0.05kg - 0.1kg
    - Potions & Food: 0.25kg - 0.5kg
    - Bags & Capes: 1.5kg
    - Off-hands (Shield, Tome): 1.5kg - 2.5kg
    - One-Handed Weapons: 2.5kg - 3.5kg
    - Two-Handed Weapons: 4.5kg - 5.5kg
    - Cloth Armor: 1.0kg - 2.0kg
    - Leather Armor: 1.5kg - 3.5kg
    - Plate Armor: 2.0kg - 6.0kg
    - Mounts: 25.0kg - 45.0kg
    """
    clean_id = item_id.upper().split("@")[0]
    tier = parse_tier(clean_id)
    tier_factor = max(0.8, 0.7 + 0.1 * tier)

    # 1. Runes, Souls, Relics, Tomes
    if any(k in clean_id for k in ("_RUNE", "_SOUL", "_RELIC", "_SHARD", "_TOME", "QUESTITEM_EXP")):
        return 0.1

    # 2. Food & Potions
    if any(k in clean_id for k in ("_POTION", "_MEAL", "_FOOD", "_FISH", "CONSUMABLE")):
        return 0.3

    # 3. Bags & Capes
    if "_BAG" in clean_id or "_CAPE" in clean_id:
        return 1.5

    # 4. Resources (Planks, Metalbars, Leather, Cloth, Stone, Ores, etc.)
    for res_name, base_w in RESOURCE_WEIGHTS.items():
        if res_name in clean_id:
            # Slightly scale with tier
            return round(base_w * (1.0 + 0.12 * max(0, tier - 4)), 2)

    # 5. Mounts
    if any(k in clean_id for k in ("_MOUNT", "_HORSE", "_OX", "_STAG", "_WOLF", "_BEAR")):
        return round(20.0 + 3.0 * tier, 1)

    # 6. Offhands
    if "_OFF_" in clean_id or any(k in clean_id for k in ("_SHIELD", "_TORCH", "_BOOK", "_TOTEM")):
        return round(1.8 * tier_factor, 1)

    # 7. Armor: Plate
    if "_PLATE" in clean_id or "ARMOR_PLATE" in clean_id:
        if "_ARMOR" in clean_id:
            return round(5.5 * tier_factor, 1)
        return round(2.2 * tier_factor, 1)  # Helm or Boots

    # 8. Armor: Leather
    if "_LEATHER" in clean_id or "ARMOR_LEATHER" in clean_id:
        if "_ARMOR" in clean_id:
            return round(3.2 * tier_factor, 1)
        return round(1.5 * tier_factor, 1)  # Helm or Shoes

    # 9. Armor: Cloth
    if "_CLOTH" in clean_id or "ARMOR_CLOTH" in clean_id:
        if "_ARMOR" in clean_id:
            return round(2.0 * tier_factor, 1)
        return round(1.0 * tier_factor, 1)  # Cowl or Sandals

    # 10. Weapons
    if "_2H_" in clean_id:
        return round(4.8 * tier_factor, 1)
    if "_MAIN_" in clean_id or any(k in clean_id for k in ("_SWORD", "_AXE", "_DAGGER", "_BOW", "_CROSSBOW", "_MACE", "_HAMMER", "_STAFF")):
        return round(2.8 * tier_factor, 1)

    # Fallback based on tier
    return round(1.0 + 0.25 * max(0, tier - 4), 1)


def get_item_value(item_id: str) -> float:
    """Calculates approximate in-game Item Value for station nutrition fee calculation.
    
    Base Item Value progression in Albion Online:
    - Tier 4: ~16
    - Tier 5: ~64
    - Tier 6: ~256
    - Tier 7: ~1024
    - Tier 8: ~4096
    Each enchantment tier multiplies Item Value by 2x.
    """
    tier = parse_tier(item_id)
    enchant = parse_enchant(item_id)
    
    # Base scale: 4**(tier - 2)
    base_val = float(4 ** max(0, tier - 2))
    enchant_mult = float(2 ** enchant)
    return round(base_val * enchant_mult, 1)
