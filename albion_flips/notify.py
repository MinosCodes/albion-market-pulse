from __future__ import annotations

import logging
import platform
import subprocess
import sys
from typing import Set

from albion_flips.models import FlipOpportunity

logger = logging.getLogger(__name__)


class Notifier:
    """Manages notifications for high-profit flip opportunities."""

    def __init__(
        self,
        threshold_silver: int | None = None,
        max_alerts_per_batch: int = 1,
        notify_every_refresh: bool = False,
    ) -> None:
        self.threshold_silver = threshold_silver
        self.max_alerts_per_batch = max_alerts_per_batch
        self.notify_every_refresh = notify_every_refresh
        self._notified_keys: Set[tuple[str, str, str, str]] = set()

    def check_and_notify(
        self,
        flips: list[FlipOpportunity],
        item_names: dict[str, str] | None = None,
    ) -> list[FlipOpportunity]:
        """Checks flips against threshold and sends alerts for top opportunities."""
        if not flips or self.threshold_silver is None or self.threshold_silver <= 0:
            return []

        qualifying = [f for f in flips if f.total_profit >= self.threshold_silver]
        if not qualifying:
            return []

        # Sort descending by total profit so best flips are first
        qualifying.sort(key=lambda x: x.total_profit, reverse=True)

        notified_this_batch: list[FlipOpportunity] = []

        if self.notify_every_refresh:
            top_flip = qualifying[0]
            key = (top_flip.item_id, top_flip.buy_city, top_flip.sell_city, top_flip.exit_type.value)
            self._notified_keys.add(key)
            name = None
            if item_names:
                name = item_names.get(top_flip.item_id) or item_names.get(top_flip.item_id.split("@")[0])
            self._send_alert(top_flip, name=name)
            notified_this_batch.append(top_flip)
            return notified_this_batch

        alerts_sent = 0
        for flip in qualifying:
            key = (flip.item_id, flip.buy_city, flip.sell_city, flip.exit_type.value)
            if key not in self._notified_keys:
                self._notified_keys.add(key)
                notified_this_batch.append(flip)
                if alerts_sent < self.max_alerts_per_batch:
                    name = None
                    if item_names:
                        name = item_names.get(flip.item_id) or item_names.get(flip.item_id.split("@")[0])
                    self._send_alert(flip, name=name)
                    alerts_sent += 1

        return notified_this_batch

    def _send_alert(self, flip: FlipOpportunity, name: str | None = None) -> None:
        display_name = name or flip.item_id
        tier_suffix = f" {flip.item_id.split('@')[-1]}" if "@" in flip.item_id else ""
        message = (
            f"{display_name}{tier_suffix}: {flip.buy_city} -> {flip.sell_city} | "
            f"+{int(flip.total_profit):,} silver ({flip.margin_pct:.1f}%)"
        )
        logger.info("NOTIFICATION: %s", message)
