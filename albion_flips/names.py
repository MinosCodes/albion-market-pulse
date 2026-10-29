"""Human-readable item names (data/item_names.json, generated from ao-bin-dumps)."""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from albion_flips.paths import data_file

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_item_names() -> dict[str, str]:
    try:
        with open(data_file("item_names.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("Could not load item_names.json: %s", e)
        return {}


def human_name(item_id: str, names: dict[str, str] | None = None) -> str:
    names = load_item_names() if names is None else names
    if item_id in names:
        return names[item_id]
    base_id = item_id.split("@")[0]
    if base_id in names:
        return names[base_id]
    if "_LEVEL" in item_id:
        clean = item_id.split("_LEVEL")[0]
        if clean in names:
            return names[clean]
    return item_id.replace("_", " ").title()
