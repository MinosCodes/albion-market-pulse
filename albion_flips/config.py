from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_SERVERS = {"europe", "americas", "asia"}
KNOWN_CITIES = {
    "Bridgewatch",
    "Fort Sterling",
    "Lymhurst",
    "Martlock",
    "Thetford",
    "Caerleon",
    "Brecilien",
    "Black Market",
}


class ConfigError(ValueError):
    """Raised when configuration validation fails."""
    pass


@dataclass(slots=True)
class AppConfig:
    server: str = "europe"
    cities: list[str] = field(
        default_factory=lambda: [
            "Bridgewatch",
            "Fort Sterling",
            "Lymhurst",
            "Martlock",
            "Thetford",
            "Caerleon",
            "Black Market",
        ]
    )
    qualities: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    premium: bool = True
    tax_rate_premium: float = 0.04
    tax_rate_standard: float = 0.08
    setup_fee_rate: float = 0.025
    min_profit_silver: int = 500
    min_margin_pct: float = 5.0
    max_margin_pct: float = 300.0
    min_daily_volume: int = 50
    volume_days: int = 3
    max_price_age_minutes: int = 120
    stack_size: int = 100
    refresh_seconds: int = 180
    top_n: int = 25
    risk: dict[str, str] = field(
        default_factory=lambda: {
            "Bridgewatch": "low",
            "Fort Sterling": "low",
            "Lymhurst": "low",
            "Martlock": "low",
            "Thetford": "low",
            "Brecilien": "medium",
            "Caerleon": "high",
            "Black Market": "high",
        }
    )
    notify_profit_silver: int | None = None
    web_enabled: bool = False
    web_port: int = 8765
    gemini_api_key: str | None = None

    @property
    def active_tax_rate(self) -> float:
        return self.tax_rate_premium if self.premium else self.tax_rate_standard

    def validate(self) -> None:
        if self.server not in VALID_SERVERS:
            raise ConfigError(
                f"Unknown server '{self.server}'. Valid servers are: {', '.join(sorted(VALID_SERVERS))}"
            )

        for city in self.cities:
            if city not in KNOWN_CITIES:
                raise ConfigError(
                    f"Unknown city '{city}'. Valid cities are: {', '.join(sorted(KNOWN_CITIES))}"
                )

        if self.tax_rate_premium < 0:
            raise ConfigError("tax_rate_premium cannot be negative")
        if self.tax_rate_standard < 0:
            raise ConfigError("tax_rate_standard cannot be negative")
        if self.setup_fee_rate < 0:
            raise ConfigError("setup_fee_rate cannot be negative")
        if self.min_profit_silver < 0:
            raise ConfigError("min_profit_silver cannot be negative")
        if self.min_margin_pct < 0:
            raise ConfigError("min_margin_pct cannot be negative")
        if self.max_margin_pct < self.min_margin_pct:
            raise ConfigError("max_margin_pct cannot be less than min_margin_pct")
        if self.volume_days <= 0:
            raise ConfigError("volume_days must be positive")
        if self.max_price_age_minutes <= 0:
            raise ConfigError("max_price_age_minutes must be positive")
        if self.stack_size <= 0:
            raise ConfigError("stack_size must be positive")
        if self.refresh_seconds <= 0:
            raise ConfigError("refresh_seconds must be positive")
        if self.top_n <= 0:
            raise ConfigError("top_n must be positive")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        web_dict = data.get("web", {})
        web_enabled = bool(web_dict.get("enabled", False))
        web_port = int(web_dict.get("port", 8765))

        cfg = cls(
            server=data.get("server", "europe"),
            cities=list(data.get("cities", [
                "Bridgewatch",
                "Fort Sterling",
                "Lymhurst",
                "Martlock",
                "Thetford",
                "Caerleon",
            ])),
            qualities=list(data.get("qualities", [1, 2, 3, 4, 5])),
            premium=bool(data.get("premium", True)),
            tax_rate_premium=float(data.get("tax_rate_premium", 0.04)),
            tax_rate_standard=float(data.get("tax_rate_standard", 0.08)),
            setup_fee_rate=float(data.get("setup_fee_rate", 0.025)),
            min_profit_silver=int(data.get("min_profit_silver", 500)),
            min_margin_pct=float(data.get("min_margin_pct", 5.0)),
            max_margin_pct=float(data.get("max_margin_pct", 300.0)),
            min_daily_volume=int(data.get("min_daily_volume", 50)),
            volume_days=int(data.get("volume_days", 3)),
            max_price_age_minutes=int(data.get("max_price_age_minutes", 120)),
            stack_size=int(data.get("stack_size", 100)),
            refresh_seconds=int(data.get("refresh_seconds", 180)),
            top_n=int(data.get("top_n", 25)),
            risk=dict(data.get("risk", {
                "Bridgewatch": "low",
                "Fort Sterling": "low",
                "Lymhurst": "low",
                "Martlock": "low",
                "Thetford": "low",
                "Brecilien": "medium",
                "Caerleon": "high",
            })),
            notify_profit_silver=data.get("notify_profit_silver"),
            web_enabled=web_enabled,
            web_port=web_port,
            gemini_api_key=data.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY"),
        )
        cfg.validate()
        return cfg


def load_config(path: Path | str | None = None) -> AppConfig:
    """Loads and validates configuration from path or config.json / config.example.json."""
    candidate_paths: list[Path] = []
    if path:
        candidate_paths.append(Path(path))
    else:
        candidate_paths.extend([Path("config.json"), Path("config.example.json")])

    for p in candidate_paths:
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AppConfig.from_dict(data)

    raise FileNotFoundError(f"Configuration file not found. Checked: {[str(p) for p in candidate_paths]}")


def expand_item_group(bases: list[str], tiers: list[int], enchants: list[int]) -> list[str]:
    """Expands base resource names, tiers, and enchants into AODP item IDs."""
    expanded: list[str] = []
    for tier in tiers:
        for base in bases:
            for enchant in enchants:
                if enchant == 0:
                    expanded.append(f"T{tier}_{base}")
                else:
                    expanded.append(f"T{tier}_{base}_LEVEL{enchant}@{enchant}")
    return expanded


def expand_items(data: dict[str, Any]) -> list[str]:
    """Expands groups and extra_items from items dictionary into ordered, unique list of item IDs."""
    result: list[str] = []
    seen: set[str] = set()

    for group in data.get("groups", []):
        bases = group.get("bases", [])
        tiers = group.get("tiers", [])
        enchants = group.get("enchants", [0])
        for item_id in expand_item_group(bases, tiers, enchants):
            if item_id not in seen:
                seen.add(item_id)
                result.append(item_id)

    for item_id in data.get("extra_items", []):
        if item_id not in seen:
            seen.add(item_id)
            result.append(item_id)

    return result


def load_items(path: Path | str = "data/items.json") -> list[str]:
    """Loads and expands item definitions from JSON file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Items file not found: {path}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return expand_items(data)
