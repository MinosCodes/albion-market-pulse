"""Persistent market history in SQLite (standard library only).

Why
---
The desktop version keeps everything in memory and only averages the last
``volume_days`` (default 3) of AODP history. On a server that runs for months
we want:

* a stable 7- and 30-day reference price per item/city/quality, used by
  ``robust.py`` to spot bait prices and price spikes,
* price changes kept over time (also from the live Windows client), and
* a warm start after a restart instead of an empty dashboard.

Storage stays small: price samples are only written when a price actually
changes and are kept 7 days; daily points are one row per item/city/quality/day
and kept 120 days.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from albion_flips.models import HistoryRecord, PriceRecord
from albion_flips.robust import Reference

DAY_S = 86400


def _parse_ts(value: str) -> int | None:
    """AODP timestamps are naive UTC ISO strings; '0001-01-01...' means no data."""
    if not value or value.startswith("0001"):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class MarketStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS latest (
              item_id TEXT, city TEXT, quality INTEGER,
              sell_min INTEGER, sell_ts INTEGER, buy_max INTEGER, buy_ts INTEGER, source TEXT,
              PRIMARY KEY (item_id, city, quality));
            CREATE TABLE IF NOT EXISTS samples (
              item_id TEXT, city TEXT, quality INTEGER, ts INTEGER,
              sell_min INTEGER, buy_max INTEGER, source TEXT);
            CREATE INDEX IF NOT EXISTS samples_key ON samples(item_id, city, quality, ts);
            CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
            CREATE TABLE IF NOT EXISTS daily (
              item_id TEXT, city TEXT, quality INTEGER, day INTEGER,
              avg_price REAL, item_count INTEGER,
              PRIMARY KEY (item_id, city, quality, day));
            CREATE INDEX IF NOT EXISTS daily_day ON daily(day);
            """
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------- writing ----------
    def record_prices(self, prices: Iterable[PriceRecord], source: str = "api") -> int:
        """Stores current prices; writes a sample row only when sell or buy price changed.

        Returns the number of changed price points.
        """
        changed = 0
        with self._lock:
            cur = self._db.cursor()
            for p in prices:
                sell_ts = _parse_ts(p.sell_price_min_date) if p.sell_price_min > 0 else None
                buy_ts = _parse_ts(p.buy_price_max_date) if p.buy_price_max > 0 else None
                sell = p.sell_price_min if sell_ts else 0
                buy = p.buy_price_max if buy_ts else 0
                if not sell and not buy:
                    continue
                row = cur.execute(
                    "SELECT sell_min, sell_ts, buy_max, buy_ts FROM latest WHERE item_id=? AND city=? AND quality=?",
                    (p.item_id, p.city, p.quality),
                ).fetchone()
                if row is not None:
                    # Never overwrite a newer observation with an older one (e.g. API lagging behind the live client).
                    if sell_ts and row[1] and sell_ts < row[1]:
                        sell, sell_ts = row[0], row[1]
                    if buy_ts and row[3] and buy_ts < row[3]:
                        buy, buy_ts = row[2], row[3]
                    if not sell_ts:
                        sell, sell_ts = row[0], row[1]
                    if not buy_ts:
                        buy, buy_ts = row[2], row[3]
                    if (sell, buy) == (row[0], row[2]):
                        cur.execute(
                            "UPDATE latest SET sell_ts=?, buy_ts=? WHERE item_id=? AND city=? AND quality=?",
                            (sell_ts, buy_ts, p.item_id, p.city, p.quality),
                        )
                        continue
                ts = max(sell_ts or 0, buy_ts or 0)
                cur.execute(
                    "INSERT OR REPLACE INTO latest VALUES (?,?,?,?,?,?,?,?)",
                    (p.item_id, p.city, p.quality, sell, sell_ts, buy, buy_ts, source),
                )
                cur.execute(
                    "INSERT INTO samples VALUES (?,?,?,?,?,?,?)",
                    (p.item_id, p.city, p.quality, ts, sell, buy, source),
                )
                changed += 1
            self._db.commit()
        return changed

    def record_history(self, records: Iterable[HistoryRecord]) -> int:
        """Upserts AODP daily history points (time-scale 24)."""
        n = 0
        with self._lock:
            cur = self._db.cursor()
            for rec in records:
                for pt in rec.data:
                    ts = _parse_ts(pt.timestamp)
                    if ts is None or pt.item_count <= 0:
                        continue
                    cur.execute(
                        "INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?)",
                        (rec.item_id, rec.location, rec.quality, ts // DAY_S, float(pt.avg_price), int(pt.item_count)),
                    )
                    n += 1
            self._db.commit()
        return n

    def prune(self, now: datetime, raw_days: int = 7, daily_days: int = 120) -> None:
        cutoff_raw = int(now.timestamp()) - raw_days * DAY_S
        cutoff_day = int(now.timestamp()) // DAY_S - daily_days
        with self._lock:
            self._db.execute("DELETE FROM samples WHERE ts < ?", (cutoff_raw,))
            self._db.execute("DELETE FROM daily WHERE day < ?", (cutoff_day,))
            self._db.commit()

    # ---------- reading ----------
    def references(self, now: datetime) -> dict[tuple[str, str, int], Reference]:
        """7- and 30-day volume-weighted averages from full past days (today is excluded, it is incomplete)."""
        today = int(now.timestamp()) // DAY_S
        out: dict[tuple[str, str, int], Reference] = {}
        with self._lock:
            rows = self._db.execute(
                """
                SELECT item_id, city, quality,
                  SUM(CASE WHEN day >= ? THEN avg_price * item_count END) / NULLIF(SUM(CASE WHEN day >= ? THEN item_count END), 0),
                  SUM(avg_price * item_count) / NULLIF(SUM(item_count), 0),
                  COALESCE(SUM(CASE WHEN day >= ? THEN item_count END), 0) / 7.0
                FROM daily WHERE day >= ? AND day < ?
                GROUP BY item_id, city, quality
                """,
                (today - 7, today - 7, today - 7, today - 30, today),
            ).fetchall()
        for item_id, city, quality, avg7, avg30, vol7 in rows:
            out[(item_id, city, int(quality))] = Reference(avg7=avg7, avg30=avg30, vol7=vol7)
        return out

    def daily_series(self, item_id: str, quality: int, since_day: int) -> dict[str, list[tuple[int, float, int]]]:
        """Daily average price and traded count per city: {city: [(day, avg, count), ...]}."""
        with self._lock:
            rows = self._db.execute(
                "SELECT city, day, avg_price, item_count FROM daily WHERE item_id=? AND quality=? AND day>=? ORDER BY day",
                (item_id, quality, since_day),
            ).fetchall()
        out: dict[str, list[tuple[int, float, int]]] = {}
        for city, day, avg, cnt in rows:
            out.setdefault(city, []).append((int(day), float(avg), int(cnt)))
        return out

    def sample_series(self, item_id: str, quality: int, since_ts: int) -> dict[str, list[tuple[int, int, int]]]:
        """Order book changes per city: {city: [(ts, sell_min, buy_max), ...]} (kept 7 days)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT city, ts, sell_min, buy_max FROM samples WHERE item_id=? AND quality=? AND ts>=? ORDER BY ts",
                (item_id, quality, since_ts),
            ).fetchall()
        out: dict[str, list[tuple[int, int, int]]] = {}
        for city, ts, sell, buy in rows:
            out.setdefault(city, []).append((int(ts), int(sell or 0), int(buy or 0)))
        return out

    def latest_for(self, item_id: str, quality: int) -> dict[str, tuple[int, int | None, int, int | None]]:
        """{city: (sell_min, sell_ts, buy_max, buy_ts)} for one item."""
        with self._lock:
            rows = self._db.execute(
                "SELECT city, sell_min, sell_ts, buy_max, buy_ts FROM latest WHERE item_id=? AND quality=?",
                (item_id, quality),
            ).fetchall()
        return {r[0]: (int(r[1] or 0), r[2], int(r[3] or 0), r[4]) for r in rows}

    def load_latest(self) -> list[PriceRecord]:
        """Last known prices, used to fill the dashboard right after a restart."""
        def iso(ts: int | None) -> str:
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") if ts else "0001-01-01T00:00:00"

        with self._lock:
            rows = self._db.execute("SELECT item_id, city, quality, sell_min, sell_ts, buy_max, buy_ts FROM latest").fetchall()
        return [
            PriceRecord(
                item_id=r[0], city=r[1], quality=int(r[2]),
                sell_price_min=int(r[3] or 0), sell_price_min_date=iso(r[4]),
                sell_price_max=int(r[3] or 0), sell_price_max_date=iso(r[4]),
                buy_price_min=int(r[5] or 0), buy_price_min_date=iso(r[6]),
                buy_price_max=int(r[5] or 0), buy_price_max_date=iso(r[6]),
            )
            for r in rows
        ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "latest": self._db.execute("SELECT COUNT(*) FROM latest").fetchone()[0],
                "samples": self._db.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
                "daily": self._db.execute("SELECT COUNT(*) FROM daily").fetchone()[0],
            }
